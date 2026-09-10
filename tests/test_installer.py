"""Execute installer against a disposable home and mocked system commands."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")


def shell_path(path):
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


@unittest.skipUnless(BASH and Path(BASH).exists(), "Bash required for isolated installer tests")
class InstallerTests(unittest.TestCase):
    def scenario(self, existing_new=False, deny_linger=False):
        with tempfile.TemporaryDirectory(prefix=".installer-test-", dir=ROOT) as temporary:
            root = Path(temporary).resolve()
            self.assertEqual(root.parent, ROOT)
            source, home, commands = root / "source", root / "home", root / "bin"
            for directory in (source, home, commands):
                directory.mkdir()
            for name in ("py_rpi_monitor.py", "requirements.txt", "pyrpimonitor.service", "pyRPiMonitor.env.example", "install.sh", "uninstall.sh", "README.md"):
                shutil.copyfile(ROOT / name, source / name)
            script = (source / "install.sh").read_text(encoding="utf-8")
            script = script.replace("/home/pi", shell_path(home)).replace("/usr/bin/python3", "probe-python")
            (source / "install.sh").write_text(script, encoding="utf-8", newline="\n")
            mocks = {
                "id": 'if [ "$1" = -u ]; then echo 1000; else echo pi; fi',
                "probe-python": "exit 0",
                "systemctl": 'printf "%s\\n" "$*" >> "$MOCK_LOG"',
                "loginctl": 'if [ "$1" = show-user ]; then if [ "$DENY_LINGER" = yes ]; then echo no; else echo yes; fi; else exit 1; fi',
            }
            for name, body in mocks.items():
                file = commands / name
                file.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
                file.chmod(0o700)
            unit_dir = home / ".config/systemd/user"
            unit_dir.mkdir(parents=True)
            old_unit = unit_dir / "rpi-monitor.service"
            old_unit.write_text("old unit")
            old_config = home / ".config/rpi-monitor/rpi-monitor.env"
            old_config.parent.mkdir(parents=True)
            old_config.write_text("fixture-private-content")
            config = home / ".config/pyRPiMonitor/pyRPiMonitor.env"
            if existing_new:
                config.parent.mkdir(parents=True)
                config.write_text("existing-private-content")
            log = root / "commands.log"
            env = {**os.environ, "MOCK_LOG": shell_path(log), "DENY_LINGER": "yes" if deny_linger else "no"}
            result = subprocess.run([BASH, "-c", 'export PATH="$1:$PATH"; sh "$2" --migrate', "test",
                                     shell_path(commands), shell_path(source / "install.sh")],
                                    env=env, capture_output=True, text=True, timeout=20)
            if deny_linger:
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(old_unit.exists())
                self.assertFalse((unit_dir / "pyrpimonitor.service").exists())
                self.assertFalse(config.exists())
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(config.read_text(), "existing-private-content" if existing_new else "fixture-private-content")
                self.assertEqual(old_config.read_text(), "fixture-private-content")
                self.assertFalse(old_unit.exists())
                self.assertEqual(len(list(unit_dir.glob("rpi-monitor.service.backup.*"))), 1)
                self.assertTrue((unit_dir / "pyrpimonitor.service").exists())
                calls = log.read_text()
                self.assertIn("--user disable --now rpi-monitor.service", calls)
                self.assertNotIn("--user start", calls)
            self.assertNotIn("private-content", result.stdout + result.stderr)

    def test_migration_preserves_original_credential_and_backup(self):
        self.scenario()

    def test_migration_does_not_overwrite_new_credential(self):
        self.scenario(existing_new=True)

    def test_linger_denied_keeps_old_installation(self):
        self.scenario(deny_linger=True)
