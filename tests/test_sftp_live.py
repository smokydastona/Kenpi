import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from kenpi.recovery.backup import (
    create_backup,
    fetch,
    list_backups,
    restore_backup,
    verify_archive,
)
from kenpi.recovery.destinations import BackupError, SftpDestination
from tests import live_sshd


@unittest.skipUnless(live_sshd.AVAILABLE, "openssh-server (sshd) is not installed")
class SftpLiveTests(unittest.TestCase):
    """Real OpenSSH sshd + real sftp client on loopback; no mocks or runner stubs."""

    @classmethod
    def setUpClass(cls):
        cls.sshd = live_sshd.LiveSshd()
        cls.sshd.start()

    @classmethod
    def tearDownClass(cls):
        cls.sshd.cleanup()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        for child in self.sshd.remote.iterdir():
            child.unlink()
        if self.sshd.proc.poll() is not None:
            self.sshd.start()

    def dest(self, **overrides):
        args = dict(
            host="127.0.0.1",
            user=self.sshd.user,
            path=str(self.sshd.remote),
            identity_file=str(self.sshd.client_key),
            known_hosts=str(self.sshd.known_hosts),
            port=self.sshd.port,
        )
        args.update(overrides)
        return SftpDestination(**args)

    def make_tree(self, name):
        root = self.work / name
        (root / "sub").mkdir(parents=True)
        (root / "a.toml").write_text(f"[kenpi]\n# {name}\n")
        (root / "sub" / "b.json").write_text('{"x": 1}')
        return root

    def test_upload_list_download_round_trip(self):
        data = self.work / "kenpi-backup-t1.kbk"
        data.write_bytes(b"x" * 300_000)
        d = self.dest()
        self.assertIsInstance(d.free_bytes(), int)
        d.put(data, data.name)
        self.assertEqual(d.list_names(), [data.name])
        self.assertEqual(d.list_partials(), [])
        out = self.work / "dl"
        out.mkdir()
        got = fetch(d, data.name, out)
        self.assertEqual(
            hashlib.sha256(got.read_bytes()).digest(),
            hashlib.sha256(data.read_bytes()).digest(),
        )
        d.delete(data.name)
        self.assertEqual(d.list_names(), [])

    def test_refuses_to_overwrite_existing_backup(self):
        f = self.work / "kenpi-backup-t2.kbk"
        f.write_bytes(b"one")
        d = self.dest()
        d.put(f, f.name)
        f.write_bytes(b"two")
        with self.assertRaises(BackupError):
            d.put(f, f.name)
        self.assertEqual((self.sshd.remote / f.name).read_bytes(), b"one")

    def test_interrupted_upload_leaves_partial_not_a_backup(self):
        f = self.work / "kenpi-backup-t3.kbk"
        f.write_bytes(b"y" * 5_000_000)
        killed = []

        def runner(argv, batch):
            proc = subprocess.Popen(
                [
                    *argv[:1],
                    "-l",
                    "400",
                    *argv[1:],
                ],  # ~50 KB/s so the kill lands mid-transfer
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                out, _ = proc.communicate(batch, timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                killed.append(True)
                return -9, ""
            return proc.returncode, out

        d = self.dest(runner=runner)
        with self.assertRaises(BackupError):
            d.put(f, f.name)
        self.assertTrue(killed)
        self.assertEqual(self.dest().list_names(), [])
        partials = self.dest().list_partials()
        self.assertEqual(len(partials), 1)
        self.assertLess((self.sshd.remote / partials[0]).stat().st_size, 5_000_000)
        # Recovery: clean the partial, then a normal retry succeeds intact.
        self.dest().remove_partial(partials[0])
        clean = self.dest()
        clean.put(f, f.name)
        self.assertEqual((self.sshd.remote / f.name).stat().st_size, 5_000_000)

    def test_truncated_upload_is_detected_before_publish(self):
        f = self.work / "kenpi-backup-t4.kbk"
        f.write_bytes(b"z" * 1000)
        calls = []

        def runner(argv, batch):
            calls.append(batch)
            if batch.startswith("ls -lan"):
                # truncate the remote partial between upload and the size check
                for p in self.sshd.remote.glob(".kenpi-backup-t4*"):
                    p.write_bytes(b"z" * 10)
            return real(argv, batch)

        from kenpi.recovery.destinations import _system_sftp as real

        d = self.dest(runner=runner)
        with self.assertRaises(BackupError):
            d.put(f, f.name)
        self.assertEqual(d.list_names(), [])

    def test_authentication_and_host_key_failures(self):
        f = self.work / "kenpi-backup-t5.kbk"
        f.write_bytes(b"a")
        with self.assertRaises(BackupError):
            self.dest(identity_file=str(self.sshd.other_key)).put(f, f.name)
        wrong = self.work / "known"
        wrong.write_text(
            f"[127.0.0.1]:{self.sshd.port} "
            + (self.sshd.tmp / "other_key.pub").read_text().split()[0]
            + " "
            + (self.sshd.tmp / "other_key.pub").read_text().split()[1]
            + "\n"
        )
        with self.assertRaises(BackupError):
            self.dest(known_hosts=str(wrong)).put(f, f.name)
        empty = self.work / "empty"
        empty.write_text("")
        with self.assertRaises(BackupError):
            self.dest(known_hosts=str(empty)).list_names()
        self.assertEqual(list(self.sshd.remote.iterdir()), [])

    def test_server_down_and_missing_directory_fail_cleanly(self):
        f = self.work / "kenpi-backup-t6.kbk"
        f.write_bytes(b"a")
        with self.assertRaises(BackupError):
            self.dest(path=str(self.sshd.remote / "missing")).put(f, f.name)
        self.sshd.stop()
        with self.assertRaises(BackupError):
            self.dest().put(f, f.name)
        with self.assertRaises(BackupError):
            self.dest().list_names()
        self.sshd.start()
        self.dest().put(f, f.name)

    def test_backup_then_restore_into_separate_installation(self):
        source = self.make_tree("source")
        d = self.dest()
        info = create_backup({"config": source}, d, "0.1.0")
        self.assertEqual(list_backups(d)["complete"], [info.name])

        # A different "installation": other roots, pre-existing different content.
        target = self.make_tree("replacement")
        (target / "stale.txt").write_text("old")
        recovery = self.work / "recovery"
        out = self.work / "fetched"
        out.mkdir()
        archive = fetch(d, info.name, out)
        verify_archive(archive)

        dry = restore_backup(archive, {"config": target}, "0.1.0", recovery)
        self.assertEqual(dry.status, "dry-run")
        self.assertTrue((target / "stale.txt").exists())

        # Health check failure -> automatic rollback to the previous installation.
        failed = restore_backup(
            archive, {"config": target}, "0.1.0", recovery,
            dry_run=False, confirm=True, health_check=lambda: False,
        )  # fmt: skip
        self.assertEqual(failed.status, "rolled_back")
        self.assertTrue((target / "stale.txt").exists())
        self.assertIn("replacement", (target / "a.toml").read_text())

        ok = restore_backup(
            archive, {"config": target}, "0.1.0", recovery,
            dry_run=False, confirm=True,
        )  # fmt: skip
        self.assertEqual(ok.status, "restored")
        self.assertFalse((target / "stale.txt").exists())
        self.assertIn("source", (target / "a.toml").read_text())
        self.assertEqual((target / "sub" / "b.json").read_text(), '{"x": 1}')

    def test_tampered_remote_backup_is_rejected_before_restore(self):
        source = self.make_tree("src2")
        d = self.dest()
        info = create_backup({"config": source}, d, "0.1.0")
        remote_file = self.sshd.remote / info.name
        blob = bytearray(remote_file.read_bytes())
        blob[len(blob) // 2] ^= 0xFF
        remote_file.write_bytes(bytes(blob))
        out = self.work / "o"
        out.mkdir()
        target = self.make_tree("tgt2")
        archive = fetch(d, info.name, out)
        with self.assertRaises(Exception):
            restore_backup(
                archive, {"config": target}, "0.1.0", self.work / "rec",
                dry_run=False, confirm=True,
            )  # fmt: skip
        self.assertIn("tgt2", (target / "a.toml").read_text())


if __name__ == "__main__":
    unittest.main()
