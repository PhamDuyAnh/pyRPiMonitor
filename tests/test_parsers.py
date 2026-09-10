"""Linux fixtures and isolated tests; no Pi, broker, network or root required."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import py_rpi_monitor as monitor


class CpuParserTests(unittest.TestCase):
    def test_guest_not_counted_twice_and_iowait_is_idle(self):
        parsed = monitor.parse_proc_stat("cpu 100 20 30 400 50 6 7 8 10 2\ncpu0 1 2 3 4\nintr 100\n")
        self.assertEqual(parsed["cpu"], (621, 450))
        self.assertEqual(parsed["cpu0"], (10, 4))

    def test_usage_deltas(self):
        self.assertEqual(monitor.cpu_percent((100, 50), (200, 75)), 75.0)
        self.assertEqual(monitor.cpu_percent((100, 50), (200, 150)), 0.0)

    def test_reset_and_zero_delta_are_unknown(self):
        for previous, current in [((100, 50), (100, 50)), ((100, 50), (10, 5)), ((100, 50), (200, 40)), ((100, 50), (110, 70))]:
            with self.subTest(current=current):
                self.assertIsNone(monitor.cpu_percent(previous, current))

    def test_bad_stat(self):
        for value in ("intr 1", "cpu 1 2", "cpu -1 0 0 0", "cpu invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                monitor.parse_proc_stat(value)


class MemoryParserTests(unittest.TestCase):
    def test_available_memory_and_swap(self):
        result = monitor.parse_meminfo("MemTotal: 8192 kB\nMemFree: 1024 kB\nMemAvailable: 6144 kB\nSwapTotal: 2048 kB\nSwapFree: 512 kB\n")
        self.assertEqual(result, {"total_mb": 8.0, "used_mb": 2.0, "available_mb": 6.0, "usage_percent": 25.0,
                                  "swap_total_mb": 2.0, "swap_used_mb": 1.5, "swap_usage_percent": 75.0})

    def test_no_swap(self):
        result = monitor.parse_meminfo("MemTotal: 1024 kB\nMemAvailable: 512 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB")
        self.assertEqual(result["swap_usage_percent"], 0.0)

    def test_missing_or_invalid_memory(self):
        with self.assertRaises(KeyError):
            monitor.parse_meminfo("MemTotal: 1024 kB")
        with self.assertRaises(ValueError):
            monitor.parse_meminfo("MemTotal: 10\nMemAvailable: 20\nSwapTotal: 0\nSwapFree: 0")


class NetworkParserTests(unittest.TestCase):
    def test_counter_columns(self):
        result = monitor.parse_net_dev("Inter-| Receive | Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n wlan0: 1000 20 3 4 5 6 7 8 2000 30 9 10 11 12 13 14\n lo: 1 2 0 0 0 0 0 0 1 2 0 0 0 0 0 0")
        self.assertEqual(result["wlan0"], {"rx_bytes": 1000, "rx_errors": 3, "rx_dropped": 4,
                                          "tx_bytes": 2000, "tx_errors": 9, "tx_dropped": 10})
        self.assertIn("lo", result)

    def test_short_or_negative_line(self):
        for text in ("wlan0: 1 2", "wlan0: -1 " + "0 " * 15):
            with self.assertRaises(ValueError):
                monitor.parse_net_dev(text)


class ThrottledParserTests(unittest.TestCase):
    def test_zero(self):
        self.assertFalse(any(monitor.parse_throttled("throttled=0x0\n").values()))

    def test_independent_flags(self):
        keys = {0: "undervoltage_now", 2: "throttled_now", 16: "undervoltage_occurred", 18: "throttled_occurred"}
        for bit, key in keys.items():
            with self.subTest(bit=bit):
                result = monitor.parse_throttled(f"throttled={1 << bit:#x}")
                self.assertTrue(result[key])
                self.assertEqual(sum(result.values()), 1)

    def test_frequency_cap_and_temperature_limit_are_not_throttled_bits(self):
        self.assertFalse(any(monitor.parse_throttled("throttled=0xa000a").values()))

    def test_all_flags_and_invalid(self):
        self.assertTrue(all(monitor.parse_throttled("throttled=0x50005").values()))
        with self.assertRaises(ValueError):
            monitor.parse_throttled("throttled=unknown")


class JournalTests(unittest.TestCase):
    def test_all_required_patterns(self):
        for message in ("ERROR", "CRITICAL", "failure", "USB disconnect", "reset high-speed USB device", "device not found",
                        "No SDR Devices", "PLL not locked", "samples lost", "I/O error", "OOM", "undervoltage", "EXT4-fs error"):
            with self.subTest(message=message):
                self.assertIsNotNone(monitor.summarize_journal_entry({"MESSAGE": message, "__REALTIME_TIMESTAMP": "1000000"}))

    def test_no_raw_credentials_or_traceback(self):
        raw = "\x1b[31mERROR username=fixture-user password=fixture-secret token=fixture-token\nTraceback (most recent call last):\n arbitrary-private-data\x00"
        result = monitor.summarize_journal_entry({"MESSAGE": raw, "__REALTIME_TIMESTAMP": "1000000"})
        encoded = json.dumps(result)
        for secret in ("fixture-user", "fixture-secret", "fixture-token", "arbitrary-private-data", "Traceback", "\\u001b", "\\u0000"):
            self.assertNotIn(secret, encoded)
        self.assertLessEqual(len(result["message"]), 400)

    def test_priority_and_non_error(self):
        self.assertIsNotNone(monitor.summarize_journal_entry({"MESSAGE": "private detail", "PRIORITY": "3", "__REALTIME_TIMESTAMP": "1"}))
        self.assertIsNone(monitor.summarize_journal_entry({"MESSAGE": "Connected", "PRIORITY": "6", "__REALTIME_TIMESTAMP": "1"}))

    def test_stream_count_latest_five_and_exclusive_boundary(self):
        entries = [{"MESSAGE": "ERROR", "__REALTIME_TIMESTAMP": str(t)} for t in range(10, 19)]
        process = MagicMock()
        process.stdout = io.BytesIO(b"\n".join(json.dumps(e).encode() for e in entries))
        process.stderr = io.BytesIO()
        process.returncode = 0
        process.poll.return_value = 0
        with patch.object(monitor.subprocess, "Popen", return_value=process) as spawn:
            result = monitor.read_journal(10, 17)
        self.assertEqual(result["count"], 7)
        self.assertEqual(len(result["latest_errors"]), 5)
        self.assertEqual(result["latest_errors"][0]["timestamp"], monitor.utc_iso(17 / 1_000_000))
        self.assertIn("--since=@0.000010", spawn.call_args.args[0])

    def test_permission_hint_is_not_zero_errors(self):
        process = MagicMock()
        process.stdout, process.stderr = io.BytesIO(), io.BytesIO(b"Permission denied")
        process.returncode = 0
        process.poll.return_value = 0
        with patch.object(monitor.subprocess, "Popen", return_value=process), self.assertRaises(RuntimeError):
            monitor.read_journal(0, 1)


class ApiAndCollectorTests(unittest.TestCase):
    def test_api_allowlist(self):
        result = monitor.api_summary({"version": "1.2.113", "sdrs": [{}, {}], "max_clients": 5, "receiver": {"password": "private"}},
                                     {"openwebrx": {"users": 3}, "token": "private"}, {"one": {"secret": "private"}})
        self.assertEqual(result["users"], 3)
        self.assertEqual(result["sdr_count"], 2)
        self.assertEqual(result["version"], "1.2.113")
        self.assertEqual(result["api_summary"]["feature_count"], 1)
        self.assertNotIn("private", json.dumps(result))

    def test_unknown_api_fields_stay_null(self):
        result = monitor.api_summary({}, {}, None)
        for name in ("version", "users", "sdr_count"):
            self.assertIsNone(result[name])

    def test_failure_isolation_and_window(self):
        instance = monitor.Monitor()
        with patch.object(monitor.time, "time_ns", side_effect=[1_000_000_000_000, 1_060_000_000_000]), \
             patch.object(instance, "cpu", side_effect=RuntimeError("private credential")), \
             patch.object(instance, "network", return_value={"interface": "wlan0"}), \
             patch.object(instance, "openwebrx", return_value={}), \
             patch.object(Path, "read_text", side_effect=OSError("private credential")), \
             patch.object(monitor, "read_journal", return_value={"count": 0, "latest_errors": []}) as journal:
            first, second = instance.collect(), instance.collect()
        self.assertEqual(journal.call_args_list[0].args[:2], (700_000_000, 1_000_000_000))
        self.assertEqual(journal.call_args_list[2].args[:2], (1_000_000_000, 1_060_000_000))
        self.assertIsNone(first["cpu"]["usage_percent"])
        self.assertEqual(second["network"]["interface"], "wlan0")
        self.assertIn("cpu", first["collector_errors"])
        self.assertNotIn("private credential", json.dumps(first))


class CliTests(unittest.TestCase):
    def test_stdout_no_mqtt_or_credentials(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(monitor.Monitor, "collect", return_value={"ok": True}), \
             patch.object(monitor, "publish_mqtt") as publish, patch.object(monitor.signal, "signal"), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(monitor.main(["--once", "--stdout"]), 0)
        publish.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {"ok": True})

    def test_publish_missing_credentials_fails_before_collect(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(monitor.Monitor, "collect") as collect, \
             contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(monitor.main(["--once", "--publish"]), 2)
        collect.assert_not_called()
        self.assertIn("RPIMONITOR_MQTT_PASSWORD", output.getvalue())

    def test_continuous_publish_recovers_next_cycle(self):
        stop = MagicMock()
        stop.is_set.side_effect = [False, False, True]
        with patch.dict(os.environ, {"RPIMONITOR_MQTT_USERNAME": "fixture-user", "RPIMONITOR_MQTT_PASSWORD": "fixture-secret"}), \
             patch.object(monitor.Monitor, "collect", side_effect=[{"sample": 1}, {"sample": 2}]), \
             patch.object(monitor, "publish_mqtt", side_effect=[monitor.PublishFailure(), None]) as publish, \
             patch.object(monitor.threading, "Event", return_value=stop), patch.object(monitor.signal, "signal"), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(monitor.main(["--interval", "60"]), 0)
        self.assertEqual(publish.call_count, 2)
        self.assertEqual(json.loads(publish.call_args_list[1].args[0]), {"sample": 2})
        self.assertEqual(stop.wait.call_count, 2)

    def test_once_publish_failure_exits(self):
        with patch.dict(os.environ, {"RPIMONITOR_MQTT_USERNAME": "fixture-user", "RPIMONITOR_MQTT_PASSWORD": "fixture-secret"}), \
             patch.object(monitor.Monitor, "collect", return_value={}), \
             patch.object(monitor, "publish_mqtt", side_effect=monitor.PublishFailure()), \
             patch.object(monitor.signal, "signal"), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(monitor.main(["--once", "--publish"]), 1)

    def test_interval_environment_and_override(self):
        with patch.dict(os.environ, {"RPIMONITOR_INTERVAL": "30"}):
            self.assertEqual(monitor.parse_args([]).interval, 30)
            self.assertEqual(monitor.parse_args(["--interval", "60"]).interval, 60)
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                monitor.parse_args(["--interval", value])


class MqttTests(unittest.TestCase):
    def mqtt_module(self, rc=0):
        module = types.ModuleType("paho.mqtt.client")
        module.MQTTv311, module.MQTT_ERR_SUCCESS = 4, 0
        client = MagicMock()
        client.connect.return_value = 0
        client.loop.side_effect = lambda **kwargs: (client.on_connect(client, None, {}, rc), 0)[1]
        info = client.publish.return_value
        info.rc = 0
        info.is_published.return_value = True
        module.Client = MagicMock(return_value=client)
        parent = types.ModuleType("paho")
        package = types.ModuleType("paho.mqtt")
        parent.mqtt, package.client = package, module
        return {"paho": parent, "paho.mqtt": package, "paho.mqtt.client": module}, client

    def test_qos_retain_no_will_and_ack(self):
        modules, client = self.mqtt_module()
        with patch.dict(sys.modules, modules):
            monitor.publish_mqtt("{}", "127.0.0.1", 1883, "fixture-user", "fixture-secret")
        client.publish.assert_called_once_with("RPiMonitor/status", "{}", qos=1, retain=True)
        client.will_set.assert_not_called()
        client.publish.return_value.is_published.assert_called()
        client.disconnect.assert_called_once()

    def test_auth_rejection_retries_are_bounded(self):
        modules, client = self.mqtt_module(rc=5)
        with patch.dict(sys.modules, modules), patch.object(monitor.time, "sleep"), self.assertRaises(monitor.PublishFailure):
            monitor.publish_mqtt("{}", "127.0.0.1", 1883, "fixture-user", "fixture-secret")
        self.assertEqual(client.connect.call_count, 3)
        client.publish.assert_not_called()

    def test_publish_return_code_checked(self):
        modules, client = self.mqtt_module()
        client.publish.return_value.rc = 4
        with patch.dict(sys.modules, modules), patch.object(monitor.time, "sleep"), self.assertRaises(monitor.PublishFailure):
            monitor.publish_mqtt("{}", "127.0.0.1", 1883, "fixture-user", "fixture-secret")
        self.assertEqual(client.publish.call_count, 3)

    def test_missing_puback_is_failure(self):
        modules, client = self.mqtt_module()
        client.publish.return_value.is_published.return_value = False
        clock = iter(range(0, 1000, 10))
        with patch.dict(sys.modules, modules), patch.object(monitor.time, "sleep"), \
             patch.object(monitor.time, "monotonic", side_effect=lambda: next(clock)), \
             self.assertRaises(monitor.PublishFailure):
            # Invoke on_connect synchronously so the simulated clock tests PUBACK.
            client.connect.side_effect = lambda *args, **kwargs: (client.on_connect(client, None, {}, 0), 0)[1]
            monitor.publish_mqtt("{}", "127.0.0.1", 1883, "fixture-user", "fixture-secret")
        self.assertEqual(client.publish.call_count, 3)


if __name__ == "__main__":
    unittest.main()
