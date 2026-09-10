#!/usr/bin/env python3
"""pyRPiMonitor 0.1.1: read-only collectors and bounded MQTT publishing."""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable
import unicodedata
import urllib.error
import urllib.request

VERSION = "0.1.1"
TOPIC = "RPiMonitor/status"
TIMEOUT = 5.0
CPU_FIELDS = ("usage_percent", "per_core_percent", "load_1m", "load_5m", "load_15m",
              "temperature_c", "frequency_mhz", "throttled_now", "throttled_occurred",
              "undervoltage_now", "undervoltage_occurred")
MEMORY_FIELDS = ("total_mb", "used_mb", "available_mb", "usage_percent",
                 "swap_total_mb", "swap_used_mb", "swap_usage_percent")
NETWORK_FIELDS = ("interface", "local_ip", "rx_bytes", "tx_bytes", "rx_errors",
                  "tx_errors", "rx_dropped", "tx_dropped")


def parse_proc_stat(text: str) -> dict[str, tuple[int, int]]:
    """Return total/idle ticks; guest times are already included in user/nice."""
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or not re.fullmatch(r"cpu\d*", fields[0]):
            continue
        values = [int(value) for value in fields[1:]]
        if len(values) < 4 or any(value < 0 for value in values):
            raise ValueError("invalid CPU counters")
        result[fields[0]] = (sum(values[:8]), values[3] + (values[4] if len(values) > 4 else 0))
    if "cpu" not in result:
        raise ValueError("aggregate CPU counters missing")
    return result


def cpu_percent(previous: tuple[int, int], current: tuple[int, int]) -> float | None:
    total, idle = current[0] - previous[0], current[1] - previous[1]
    if total <= 0 or idle < 0 or idle > total:
        return None
    return round(100.0 * (total - idle) / total, 2)


def parse_meminfo(text: str) -> dict[str, float]:
    values = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        parts = value.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0])
    total, available = values["MemTotal"], values["MemAvailable"]
    swap_total, swap_free = values["SwapTotal"], values["SwapFree"]
    if total <= 0 or not 0 <= available <= total or not 0 <= swap_free <= swap_total:
        raise ValueError("invalid memory counters")
    return {"total_mb": round(total / 1024, 2), "used_mb": round((total - available) / 1024, 2),
            "available_mb": round(available / 1024, 2), "usage_percent": round((total - available) * 100 / total, 2),
            "swap_total_mb": round(swap_total / 1024, 2), "swap_used_mb": round((swap_total - swap_free) / 1024, 2),
            "swap_usage_percent": round((swap_total - swap_free) * 100 / swap_total, 2) if swap_total else 0.0}


def parse_net_dev(text: str) -> dict[str, dict[str, int]]:
    result = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, fields = line.rsplit(":", 1)
        values = [int(value) for value in fields.split()]
        if len(values) != 16 or any(value < 0 for value in values):
            raise ValueError("invalid network counters")
        result[name.strip()] = dict(zip(
            ("rx_bytes", "rx_errors", "rx_dropped", "tx_bytes", "tx_errors", "tx_dropped"),
            (values[0], values[2], values[3], values[8], values[10], values[11])))
    return result


def parse_throttled(text: str) -> dict[str, bool]:
    match = re.fullmatch(r"\s*throttled=(0x[0-9a-fA-F]+)\s*", text)
    if not match:
        raise ValueError("invalid throttled response")
    flags = int(match[1], 16)
    return {"throttled_now": bool(flags & (1 << 2)), "throttled_occurred": bool(flags & (1 << 18)),
            "undervoltage_now": bool(flags & 1), "undervoltage_occurred": bool(flags & (1 << 16))}


def utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=TIMEOUT, check=False,
                            env={**os.environ, "LC_ALL": "C", "SYSTEMD_COLORS": "0"})
    if result.returncode:
        # Exception messages, stdout and stderr can contain credentials.
        raise RuntimeError("command failed")
    return result.stdout


def clean_text(text: str) -> str:
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return " ".join("".join(c if not unicodedata.category(c).startswith("C") else " " for c in text).split())


ERROR_PATTERNS = (
    ("usb_disconnect", r"usb.*disconnect", "USB disconnect reported"),
    ("usb_reset", r"(?:usb.*reset|reset.*usb)", "USB reset reported"),
    ("device_not_found", r"device(?:s)?\s+not\s+found", "Device not found reported"),
    ("no_sdr_devices", r"no\s+sdr\s+devices", "No SDR Devices reported"),
    ("pll_not_locked", r"pll\s+not\s+locked", "PLL not locked reported"),
    ("samples_lost", r"samples\s+lost|lost\s+samples", "Samples lost reported"),
    ("io_error", r"i/o\s+error", "I/O error reported"),
    ("oom", r"\boom\b|oom[-_ ]kill|out\s+of\s+memory", "Out of memory reported"),
    ("undervoltage", r"under[- ]?voltage", "Undervoltage reported"),
    ("filesystem_error", r"filesystem\s+error|file\s+system\s+error|(?:ext[234]|btrfs|xfs|fat-fs).*error", "Filesystem error reported"),
    ("critical", r"\bcritical\b", "CRITICAL journal entry"),
    ("error", r"\berror\b", "ERROR journal entry"),
    ("failure", r"\bfail(?:ure|ed)\b", "Failure reported"),
)


def summarize_journal_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Only emit fixed descriptions: arbitrary log text is never safe to publish."""
    raw = entry.get("MESSAGE", "")
    if isinstance(raw, list) and all(type(item) is int and 0 <= item <= 255 for item in raw):
        raw = bytes(raw).decode("utf-8", "replace")
    message = clean_text(raw if isinstance(raw, str) else "")
    matches = [(name, description) for name, pattern, description in ERROR_PATTERNS if re.search(pattern, message, re.I)]
    try:
        priority = int(entry.get("PRIORITY", 7))
    except (ValueError, TypeError):
        priority = 7
    if not matches and 0 <= priority <= 3:
        matches = [("error", "ERROR journal entry (priority 0-3)")]
    if not matches:
        return None
    return {"timestamp": utc_iso(int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000),
            "categories": [name for name, _ in matches],
            "message": "; ".join(description for _, description in matches)[:400]}


def read_journal(since_us: int, until_us: int, kernel: bool = False) -> dict[str, Any]:
    """Stream one bounded time window, retaining only five classified entries."""
    args = ["journalctl", "--no-pager", "--quiet", "--output=json",
            "--output-fields=MESSAGE,PRIORITY,__REALTIME_TIMESTAMP",
            "--since=@" + format(since_us / 1_000_000, ".6f"),
            "--until=@" + format(until_us / 1_000_000, ".6f")]
    args += ["-k"] if kernel else ["-u", "openwebrx.service"]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={**os.environ, "LC_ALL": "C", "SYSTEMD_COLORS": "0"})
    latest: deque[dict[str, Any]] = deque(maxlen=5)
    state: dict[str, Any] = {"count": 0, "failed": False, "stderr": False}

    def consume() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                entry = json.loads(line)
                timestamp = int(entry["__REALTIME_TIMESTAMP"])
                if since_us < timestamp <= until_us:
                    summary = summarize_journal_entry(entry)
                    if summary is not None:
                        state["count"] += 1
                        latest.append(summary)
        except Exception:
            state["failed"] = True

    def discard_stderr() -> None:
        assert process.stderr is not None
        # A permissions hint can accompany exit=0; never claim that is an empty window.
        while process.stderr.read(4096):
            state["stderr"] = True

    reader = threading.Thread(target=consume, daemon=True)
    errors = threading.Thread(target=discard_stderr, daemon=True)
    reader.start()
    errors.start()
    try:
        process.wait(timeout=TIMEOUT)
        reader.join(timeout=TIMEOUT)
        errors.join(timeout=TIMEOUT)
        if reader.is_alive() or errors.is_alive() or process.returncode or state["failed"] or state["stderr"]:
            raise RuntimeError("journal unavailable or incomplete")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        errors.join(timeout=1)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    return {"count": state["count"], "latest_errors": list(reversed(latest))}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def http_json(url: str) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url, timeout=TIMEOUT) as response:
        body = response.read(1_048_577)
    if len(body) > 1_048_576:
        raise ValueError("HTTP response too large")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def numeric(value: Any) -> int | float | None:
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return value
    return None


def api_summary(status: dict[str, Any] | None, metrics: dict[str, Any] | None,
                features: dict[str, Any] | None) -> dict[str, Any]:
    status, metrics = status or {}, metrics or {}
    version = status.get("version")
    # Avoid arbitrary receiver metadata, labels, URLs and configuration strings.
    version = version if isinstance(version, str) and re.fullmatch(r"v?\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.]+)?", version) else None
    nested = metrics.get("openwebrx")
    nested = nested if isinstance(nested, dict) else {}
    users = numeric(nested.get("users", metrics.get("openwebrx_users", metrics.get("users"))))
    sdrs = status.get("sdrs")
    return {"version": version, "users": users,
            "sdr_count": len(sdrs) if isinstance(sdrs, (list, dict)) else None,
            "api_summary": {"max_clients": numeric(status.get("max_clients")),
                            "feature_count": len(features) if features is not None else None}}


class Monitor:
    def __init__(self, interface: str | None = None) -> None:
        self.interface = interface
        self.previous_cpu: dict[str, tuple[int, int]] | None = None
        self.previous_collection_us: int | None = None
        self.errors: dict[str, str] = {}

    def attempt(self, name: str, operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except Exception as error:
            # Never include exception text, command output, URLs or environment values.
            self.errors[name] = type(error).__name__ + ": unavailable"
            return None

    def cpu(self) -> dict[str, Any]:
        result = dict.fromkeys(CPU_FIELDS)
        def sample() -> None:
            current = parse_proc_stat(Path("/proc/stat").read_text())
            if self.previous_cpu is None:
                self.previous_cpu = current
                time.sleep(0.2)
                current = parse_proc_stat(Path("/proc/stat").read_text())
            result["usage_percent"] = cpu_percent(self.previous_cpu["cpu"], current["cpu"])
            result["per_core_percent"] = [cpu_percent(self.previous_cpu[name], current[name]) if name in self.previous_cpu else None
                                          for name in sorted((name for name in current if name != "cpu"), key=lambda n: int(n[3:]))]
            self.previous_cpu = current
            if result["usage_percent"] is None or None in result["per_core_percent"]:
                self.errors["cpu.usage"] = "CPU delta unavailable"
        self.attempt("cpu.usage", sample)
        def load_average() -> tuple[float, float, float]:
            one, five, fifteen = Path("/proc/loadavg").read_text().split()[:3]
            return float(one), float(five), float(fifteen)
        loads = self.attempt("cpu.load", load_average)
        if loads is not None:
            result.update(zip(("load_1m", "load_5m", "load_15m"), loads))
        def temperature() -> float:
            try:
                return float(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
            except OSError:
                match = re.search(r"temp=([\d.]+)", command(["vcgencmd", "measure_temp"]))
                if not match:
                    raise ValueError("temperature unavailable")
                return float(match[1])
        result["temperature_c"] = self.attempt("cpu.temperature", temperature)
        def frequency() -> float:
            try:
                return float(Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").read_text()) / 1000
            except OSError:
                match = re.search(r"=(\d+)", command(["vcgencmd", "measure_clock", "arm"]))
                if not match:
                    raise ValueError("frequency unavailable")
                return int(match[1]) / 1_000_000
        result["frequency_mhz"] = self.attempt("cpu.frequency", frequency)
        flags = self.attempt("cpu.throttled", lambda: parse_throttled(command(["vcgencmd", "get_throttled"])))
        if flags is not None:
            result.update(flags)
        return result

    def network(self) -> dict[str, Any]:
        result = dict.fromkeys(NETWORK_FIELDS)
        counters = self.attempt("network.counters", lambda: parse_net_dev(Path("/proc/net/dev").read_text()))
        def select_interface() -> str:
            if self.interface:
                if Path("/sys/class/net", self.interface, "operstate").read_text().strip() != "up":
                    raise ValueError("configured interface is not up")
                return self.interface
            routes = json.loads(command(["ip", "-j", "route", "show", "default"]))
            for route in sorted(routes, key=lambda r: r.get("metric", 0)):
                name = route.get("dev")
                if name and Path("/sys/class/net", name, "operstate").read_text().strip() == "up":
                    return name
            for name in sorted(counters or {}, key=lambda n: (n != "wlan0", n)):
                if name != "lo" and Path("/sys/class/net", name, "operstate").read_text().strip() == "up":
                    return name
            raise ValueError("no active interface")
        interface = self.attempt("network.interface", select_interface)
        result["interface"] = interface
        if interface is not None:
            if counters is not None and interface in counters:
                result.update(counters[interface])
            else:
                self.errors["network.counters"] = "Interface counters unavailable"
            def address() -> str:
                devices = json.loads(command(["ip", "-j", "address", "show", "dev", interface]))
                addresses = [item for device in devices for item in device.get("addr_info", [])
                             if item.get("scope") == "global" and item.get("family") in ("inet", "inet6")]
                addresses.sort(key=lambda item: item["family"] != "inet")
                if not addresses:
                    raise ValueError("no local address")
                return str(ipaddress.ip_address(addresses[0]["local"]))
            result["local_ip"] = self.attempt("network.local_ip", address)
        return result

    def openwebrx(self) -> dict[str, Any]:
        result = dict.fromkeys(("service_state", "sub_state", "main_pid", "api_reachable", "version", "users", "sdr_count"))
        def service() -> dict[str, Any]:
            text = command(["systemctl", "show", "openwebrx.service", "--no-pager", "--property=LoadState,ActiveState,SubState,MainPID"])
            values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
            if values.get("LoadState") == "not-found":
                raise ValueError("unit missing")
            return {"service_state": values["ActiveState"], "sub_state": values["SubState"], "main_pid": int(values["MainPID"])}
        state = self.attempt("openwebrx.service", service)
        if state is not None:
            result.update(state)
        responses = {name: self.attempt("openwebrx." + name, lambda path=path: http_json("http://127.0.0.1:8073" + path))
                     for name, path in (("status", "/status.json"), ("metrics", "/metrics.json"), ("features", "/api/features"))}
        result["api_endpoints"] = {name: value is not None for name, value in responses.items()}
        result["api_reachable"] = any(result["api_endpoints"].values())
        result.update(api_summary(**responses))
        for name in ("version", "users", "sdr_count"):
            if result[name] is None:
                self.errors["openwebrx." + name] = "API field unavailable or unsupported"
        for name, value in result["api_summary"].items():
            if value is None:
                self.errors["openwebrx.api_summary." + name] = "API field unavailable or unsupported"
        return result

    def collect(self) -> dict[str, Any]:
        self.errors = {}
        now_us = time.time_ns() // 1000
        since_us = self.previous_collection_us if self.previous_collection_us is not None else now_us - 300_000_000
        self.previous_collection_us = now_us
        if since_us > now_us:
            since_us = now_us
            self.errors["journal.window"] = "Clock moved backwards; empty window"
        result: dict[str, Any] = {"version": VERSION, "timestamp": utc_iso(now_us / 1_000_000),
                                  "hostname": self.attempt("hostname", socket.gethostname),
                                  "uptime": self.attempt("uptime", lambda: float(Path("/proc/uptime").read_text().split()[0]))}
        result["cpu"] = self.attempt("cpu", self.cpu) or dict.fromkeys(CPU_FIELDS)
        result["memory"] = self.attempt("memory", lambda: parse_meminfo(Path("/proc/meminfo").read_text())) or dict.fromkeys(MEMORY_FIELDS)
        def storage() -> dict[str, float]:
            v = os.statvfs("/")
            total, free, available = v.f_blocks * v.f_frsize, v.f_bfree * v.f_frsize, v.f_bavail * v.f_frsize
            used = total - free
            return {"total_gb": round(total / 1024**3, 3), "used_gb": round(used / 1024**3, 3),
                    "free_gb": round(available / 1024**3, 3), "usage_percent": round(used * 100 / (used + available), 2)}
        result["storage"] = self.attempt("storage", storage) or dict.fromkeys(("total_gb", "used_gb", "free_gb", "usage_percent"))
        result["network"] = self.attempt("network", self.network) or dict.fromkeys(NETWORK_FIELDS)
        result["openwebrx"] = self.attempt("openwebrx", self.openwebrx) or dict.fromkeys(("service_state", "sub_state", "main_pid", "api_reachable", "version", "users", "sdr_count", "api_summary", "api_endpoints"))
        result["system"] = {}
        result["journal_window"] = {"since_exclusive": utc_iso(since_us / 1_000_000), "until_inclusive": utc_iso(now_us / 1_000_000)}
        for group, kernel, count_name in (("openwebrx", False, "recent_error_count"), ("system", True, "recent_kernel_error_count")):
            journal = self.attempt(group + ".journal", lambda kernel=kernel: read_journal(since_us, now_us, kernel))
            result[group][count_name] = journal["count"] if journal is not None else None
            result[group]["latest_errors"] = journal["latest_errors"] if journal is not None else None
        result["collector_errors"] = self.errors.copy()
        return result


class PublishFailure(Exception):
    pass


def publish_mqtt(payload: str, host: str, port: int, username: str, password: str, attempts: int = 3) -> None:
    # Lazy import: stdout needs neither MQTT dependencies nor credentials.
    import paho.mqtt.client as mqtt
    for attempt in range(attempts):
        client = mqtt.Client(client_id="", clean_session=True, protocol=mqtt.MQTTv311, reconnect_on_failure=False)
        client.username_pw_set(username, password)
        connected: list[int] = []
        client.on_connect = lambda client, userdata, flags, rc: connected.append(rc)
        try:
            if client.connect(host, port, keepalive=30) != mqtt.MQTT_ERR_SUCCESS:
                raise PublishFailure()
            deadline = time.monotonic() + TIMEOUT
            while not connected and time.monotonic() < deadline:
                if client.loop(timeout=min(0.2, max(0.001, deadline - time.monotonic()))) != mqtt.MQTT_ERR_SUCCESS:
                    raise PublishFailure()
            if not connected or connected[0] != 0:
                raise PublishFailure()
            info = client.publish(TOPIC, payload, qos=1, retain=True)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise PublishFailure()
            deadline = time.monotonic() + TIMEOUT
            while not info.is_published() and time.monotonic() < deadline:
                if client.loop(timeout=min(0.2, max(0.001, deadline - time.monotonic()))) != mqtt.MQTT_ERR_SUCCESS:
                    raise PublishFailure()
            if not info.is_published():
                raise PublishFailure()
            return
        except Exception:
            if attempt + 1 == attempts:
                raise PublishFailure("MQTT failed after bounded attempts; check broker, authentication and topic ACL") from None
            time.sleep(min(attempt + 1, 2))
        finally:
            try:
                client.disconnect()
            except Exception:
                pass


def positive_interval(value: str) -> float:
    try:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError()
        return number
    except ValueError:
        raise argparse.ArgumentTypeError("interval must be a finite positive number") from None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="pyRPiMonitor 0.1.1")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--once", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdout", action="store_true", help="print JSON; never connect MQTT")
    mode.add_argument("--publish", action="store_true", help="publish JSON (default)")
    parser.add_argument("--interval", default=os.getenv("RPIMONITOR_INTERVAL", "60"), type=positive_interval)
    parser.add_argument("--interface", default=os.getenv("RPIMONITOR_INTERFACE") or None)
    parser.add_argument("--mqtt-host", default=os.getenv("RPIMONITOR_MQTT_HOST", "127.0.0.1"))
    parser.add_argument("--mqtt-port", default=os.getenv("RPIMONITOR_MQTT_PORT", "1883"))
    args = parser.parse_args(argv)
    try:
        args.mqtt_port = int(args.mqtt_port)
        if not 1 <= args.mqtt_port <= 65535:
            raise ValueError()
    except ValueError:
        parser.error("MQTT port must be an integer from 1 to 65535")
    if args.interface and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", args.interface):
        parser.error("invalid network interface name")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    username, password = os.getenv("RPIMONITOR_MQTT_USERNAME"), os.getenv("RPIMONITOR_MQTT_PASSWORD")
    if not args.stdout and (not username or not password):
        print("Publish requires RPIMONITOR_MQTT_USERNAME and RPIMONITOR_MQTT_PASSWORD in the environment.", file=sys.stderr)
        return 2
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: stop.set())
    monitor = Monitor(args.interface)
    while not stop.is_set():
        started = time.monotonic()
        payload = json.dumps(monitor.collect(), ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if args.stdout:
            print(payload, flush=True)
        else:
            try:
                assert username is not None and password is not None
                publish_mqtt(payload, args.mqtt_host, args.mqtt_port, username, password)
            except ImportError:
                print("Publish requires paho-mqtt 1.6.1 for this Python.", file=sys.stderr)
                return 2
            except Exception:
                print("MQTT publish failed after bounded attempts; sample dropped. Check broker, authentication and topic ACL.", file=sys.stderr)
                if args.once:
                    return 1
        if args.once:
            return 0
        stop.wait(max(0.0, args.interval - (time.monotonic() - started)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception:
        print("pyRPiMonitor stopped due to an internal error; details omitted to protect credentials.", file=sys.stderr)
        sys.exit(1)
