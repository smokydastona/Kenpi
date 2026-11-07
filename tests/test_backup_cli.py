import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kenpi.security.devices import DeviceRegistry
from kenpi.tools import backup as cli


class BackupCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.etc = self.root / "etc"
        self.state = self.root / "state"
        self.dest = self.root / "usb"
        for d in (self.etc, self.state, self.dest):
            d.mkdir()
        self.config = self.etc / "status.toml"
        self.config.write_text(f'[kenpi]\nstate_directory = "{self.state}"\n')
        (self.state / "data.json").write_text("{}")
        (self.state / "gateway").mkdir()
        DeviceRegistry(self.state / "gateway")
        self.secret = (self.state / "gateway" / "secret.key").read_bytes()

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(
                ["--config", str(self.config), "--dest", f"local:{self.dest}", *args]
            )
        return code, out.getvalue()

    def test_create_list_verify_and_dry_run_restore(self):
        code, out = self.run_cli("create")
        self.assertEqual(code, 0)
        name = json.loads(out)["name"]
        _, listing = self.run_cli("list")
        self.assertIn(name, listing)
        code, out = self.run_cli("verify", name)
        self.assertEqual(code, 0)
        self.assertNotIn(self.secret, (self.dest / name).read_bytes())
        code, out = self.run_cli(
            "restore", name, "--recovery-dir", str(self.root / "rec")
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "dry-run")

    def test_bad_destination_and_missing_backup_fail(self):
        with self.assertRaises(cli.BackupError):
            cli.parse_destination("ftp://x", "", "")
        code, _ = self.run_cli("verify", "kenpi-missing.kbk")
        self.assertEqual(code, 1)

    def test_confirmed_restore_restarts_and_checks_health(self):
        _, out = self.run_cli("create")
        name = json.loads(out)["name"]
        (self.state / "data.json").write_text('{"changed": 1}')
        with (
            mock.patch.object(cli.subprocess, "run") as run,
            mock.patch.object(cli, "_health", return_value=True),
        ):
            code, out = self.run_cli(
                "restore", name, "--confirm", "--recovery-dir", str(self.root / "rec")
            )
        self.assertEqual(code, 0, out)
        run.assert_called_once()
        self.assertEqual((self.state / "data.json").read_text(), "{}")


if __name__ == "__main__":
    unittest.main()
