import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from kenpi.recovery.backup import (
    FORMAT_VERSION,
    Manifest,
    apply_retention,
    check_compatibility,
    create_backup,
    is_protected,
    list_backups,
    restore_backup,
    verify_archive,
)
from kenpi.recovery.destinations import (
    BackupError,
    LocalDirectoryDestination,
    SftpDestination,
)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cfg, self.state, self.media = (
            self.tmp / n for n in ("etc", "var", "media")
        )
        for d in (
            self.cfg,
            self.state / "gateway",
            self.state / "evidence",
            self.media,
        ):
            d.mkdir(parents=True)
        (self.cfg / "kenpi.toml").write_text("[kenpi]\n")
        (self.state / "evidence" / "a.json").write_text('{"a":1}')
        (self.state / "gateway" / "devices.json").write_text('{"secret": 1}')
        (self.state / "gateway" / "secret.key").write_bytes(b"k" * 32)
        (self.state / "tls.key").write_text("private")
        (self.media / "clip1.mp4").write_bytes(b"original-1")
        self.roots = {"config": self.cfg, "state": self.state, "media": self.media}
        self.usb = self.tmp / "usb"
        self.usb.mkdir()
        self.dest = LocalDirectoryDestination(self.usb)
        self.recovery = self.tmp / "recovery"

    def backup(self, **kw):
        return create_backup(self.roots, self.dest, "0.2.5", **kw)

    def archive(self, info):
        return self.usb / info.name

    def restore(self, info, **kw):
        kw.setdefault("dry_run", False)
        kw.setdefault("confirm", True)
        return restore_backup(
            self.archive(info), self.roots, "0.2.9", self.recovery, **kw
        )

    def mutate(self):
        (self.cfg / "kenpi.toml").write_text("[kenpi]\nenvironment = 'test'\n")
        (self.state / "evidence" / "a.json").write_text('{"a":2}')


class BackupTests(Base):
    def test_roundtrip_and_manifest(self):
        info = self.backup()
        manifest = verify_archive(self.archive(info))
        self.assertEqual(manifest.format_version, FORMAT_VERSION)
        self.assertFalse(manifest.includes_media)
        self.assertIn("payload/config/kenpi.toml", manifest.checksums)
        self.assertNotIn("media", manifest.components)

    def test_credentials_and_keys_never_archived(self):
        names = verify_archive(self.archive(self.backup(include_media=True))).checksums
        for name in names:
            self.assertFalse(is_protected(tuple(name.split("/")[2:])), name)
        self.assertFalse(
            any("secret" in n or "devices" in n or ".key" in n for n in names)
        )
        self.assertIn("payload/media/clip1.mp4", names)

    def test_names_unique_and_never_overwritten(self):
        a, b = self.backup(), self.backup()
        self.assertNotEqual(a.name, b.name)
        with self.assertRaises(BackupError):
            self.dest.put(self.archive(a), a.name)
        self.assertEqual(len(list_backups(self.dest)["complete"]), 2)

    def test_insufficient_space_refused(self):
        class Tiny(LocalDirectoryDestination):
            def free_bytes(self):
                return 10

        (self.state / "evidence" / "big").write_bytes(b"x" * 4_000_000)
        with self.assertRaises(BackupError):
            create_backup(self.roots, Tiny(self.usb), "0.2.5")
        self.assertEqual(self.dest.list_names(), [])

    def test_interrupted_upload_detected_not_listed_as_complete(self):
        (self.usb / ".kenpi-backup-20250101T000000Z-ab12.kbk.partial").write_bytes(
            b"half"
        )
        listing = list_backups(self.dest)
        self.assertEqual(listing["complete"], [])
        self.assertEqual(len(listing["interrupted"]), 1)
        self.dest.remove_partial(listing["interrupted"][0])
        self.assertEqual(self.dest.list_partials(), [])

    def test_retention(self):
        for _ in range(4):
            self.backup()
        self.assertEqual(len(apply_retention(self.dest, 2)), 2)
        self.assertEqual(len(self.dest.list_names()), 2)
        with self.assertRaises(BackupError):
            apply_retention(self.dest, 0)

    def test_missing_destination(self):
        with self.assertRaises(BackupError):
            LocalDirectoryDestination(self.tmp / "nope")

    def test_excluded_paths_and_symlinks_skipped(self):
        (self.state / "backups").mkdir()
        (self.state / "backups" / "old.kbk").write_bytes(b"x")
        os.symlink("/etc/passwd", self.state / "evidence" / "link")
        names = verify_archive(
            self.archive(self.backup(exclude_paths=(self.state / "backups",)))
        ).checksums
        self.assertFalse(any("backups" in n or "link" in n for n in names))


class IntegrityTests(Base):
    def test_corrupt_bytes_detected(self):
        path = self.archive(self.backup())
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 0xFF
        path.write_bytes(bytes(data))
        with self.assertRaises(BackupError):
            verify_archive(path)

    def test_truncated_archive_detected(self):
        path = self.archive(self.backup())
        path.write_bytes(path.read_bytes()[:-60])
        with self.assertRaises(BackupError):
            verify_archive(path)

    def _rewrite(self, info, mutate):
        path = self.archive(info)
        out = self.tmp / "forged.kbk"
        with tarfile.open(path) as src, tarfile.open(out, "w:gz") as dst:
            for member in src:
                data = src.extractfile(member).read()
                result = mutate(member, data)
                if result is None:
                    continue
                member, data = result
                member.size = len(data)
                dst.addfile(member, io.BytesIO(data))
        return out

    def test_checksum_mismatch_detected(self):
        forged = self._rewrite(
            self.backup(),
            lambda m, d: (m, b"tampered") if m.name.endswith("kenpi.toml") else (m, d),
        )
        with self.assertRaises(BackupError):
            verify_archive(forged)

    def test_missing_manifest_detected(self):
        forged = self._rewrite(
            self.backup(), lambda m, d: None if m.name == "manifest.json" else (m, d)
        )
        with self.assertRaises(BackupError):
            verify_archive(forged)

    def test_path_traversal_member_rejected(self):
        def mutate(member, data):
            if member.name.endswith("kenpi.toml"):
                member.name = "payload/config/../../escape"
            return member, data

        with self.assertRaises(BackupError):
            verify_archive(self._rewrite(self.backup(), mutate))

    def test_symlink_member_rejected(self):
        def mutate(member, data):
            if member.name.endswith("kenpi.toml"):
                member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
                return member, b""
            return member, data

        with self.assertRaises(BackupError):
            verify_archive(self._rewrite(self.backup(), mutate))

    def test_credentials_smuggled_into_archive_rejected(self):
        def mutate(member, data):
            if member.name.endswith("kenpi.toml"):
                member.name = "payload/state/gateway/devices.json"
            return member, data

        with self.assertRaises(BackupError):
            verify_archive(self._rewrite(self.backup(), mutate))

    def test_manifest_validation(self):
        for bad in (
            b"{}",
            b"nope",
            json.dumps({"format": "kenpi-backup", "format_version": 99}).encode(),
        ):
            with self.assertRaises(BackupError):
                Manifest.parse(bad)

    def test_version_compatibility(self):
        manifest = verify_archive(self.archive(self.backup()))
        self.assertEqual(check_compatibility(manifest, "0.2.5"), [])
        with self.assertRaises(BackupError):
            check_compatibility(manifest, "0.1.0")
        manifest.machine = "other-arch"
        self.assertTrue(check_compatibility(manifest, "0.3.0"))


class RestoreTests(Base):
    def test_dry_run_changes_nothing(self):
        info = self.backup()
        self.mutate()
        before = (self.cfg / "kenpi.toml").read_text()
        report = self.restore(info, dry_run=True, confirm=False)
        self.assertEqual(report.status, "dry-run")
        self.assertIn("kenpi.toml", report.overwrites["config"])
        self.assertEqual((self.cfg / "kenpi.toml").read_text(), before)
        self.assertFalse(self.recovery.exists())

    def test_confirmation_required(self):
        with self.assertRaises(BackupError):
            self.restore(self.backup(), confirm=False)

    def test_successful_restore_snapshot_and_credential_policy(self):
        info = self.backup()
        self.mutate()
        calls = []
        report = self.restore(
            info,
            restart_services=lambda: calls.append("restart"),
            health_check=lambda: calls.append("health") or True,
            after_restore=lambda: calls.append("after"),
        )
        self.assertEqual(report.status, "restored")
        self.assertEqual((self.cfg / "kenpi.toml").read_text(), "[kenpi]\n")
        self.assertEqual((self.state / "evidence" / "a.json").read_text(), '{"a":1}')
        self.assertEqual(calls, ["restart", "health", "after"])
        # Credentials are carried over from the live installation, never taken from a backup.
        self.assertEqual(
            (self.state / "gateway" / "secret.key").read_bytes(), b"k" * 32
        )
        # The pre-restore snapshot preserves the previous state.
        self.assertIn(
            "environment", (report.snapshot / "config" / "kenpi.toml").read_text()
        )
        self.assertEqual([p.name for p in self.tmp.glob(".*kenpi-*")], [])

    def test_health_failure_rolls_back_everything(self):
        info = self.backup()
        self.mutate()
        restarts = []
        report = self.restore(
            info,
            restart_services=lambda: restarts.append(1),
            health_check=lambda: False,
        )
        self.assertEqual(report.status, "rolled_back")
        self.assertIn("health", report.reason)
        self.assertIn("environment", (self.cfg / "kenpi.toml").read_text())
        self.assertEqual((self.state / "evidence" / "a.json").read_text(), '{"a":2}')
        self.assertEqual(len(restarts), 2)
        self.assertEqual([p.name for p in self.tmp.glob(".*kenpi-*")], [])

    def test_health_exception_rolls_back(self):
        info = self.backup()
        self.mutate()

        def boom():
            raise RuntimeError("down")

        self.assertEqual(self.restore(info, health_check=boom).status, "rolled_back")
        self.assertIn("environment", (self.cfg / "kenpi.toml").read_text())

    def test_staged_validation_failure_never_touches_live_files(self):
        info = self.backup()
        self.mutate()

        def reject(staged):
            raise BackupError("config invalid")

        report = self.restore(info, validate_staged=reject)
        self.assertEqual(report.status, "rolled_back")
        self.assertIn("environment", (self.cfg / "kenpi.toml").read_text())

    def test_activation_failure_rolls_back_partial_swap(self):
        info = self.backup()
        self.mutate()
        real = os.rename
        calls = {"n": 0}

        def flaky(src, dst, *a, **k):
            if ".kenpi-stage-" in str(src) and "var" in str(src):
                calls["n"] += 1
                raise OSError("disk error")
            return real(src, dst, *a, **k)

        os.rename = flaky
        try:
            report = self.restore(info)
        finally:
            os.rename = real
        self.assertEqual(calls["n"], 1)
        self.assertEqual(report.status, "rolled_back")
        self.assertIn("environment", (self.cfg / "kenpi.toml").read_text())
        self.assertEqual((self.state / "evidence" / "a.json").read_text(), '{"a":2}')

    def test_restore_to_fresh_machine_has_no_credentials(self):
        info = self.backup()
        import shutil

        shutil.rmtree(self.state)
        shutil.rmtree(self.cfg)
        self.assertEqual(self.restore(info).status, "restored")
        self.assertFalse((self.state / "gateway").exists())
        self.assertTrue((self.cfg / "kenpi.toml").exists())

    def test_media_never_overwritten_and_only_when_requested(self):
        info = self.backup(include_media=True)
        (self.media / "clip1.mp4").write_bytes(b"changed-after-backup")
        (self.media / "clip2.mp4").write_bytes(b"new")
        report = self.restore(info)  # include_media defaults to False
        self.assertEqual(report.media_skipped, [])
        report = self.restore(info, include_media=True)
        self.assertEqual(report.media_skipped, ["clip1.mp4"])
        self.assertEqual(
            (self.media / "clip1.mp4").read_bytes(), b"changed-after-backup"
        )
        self.assertEqual((self.media / "clip2.mp4").read_bytes(), b"new")
        (self.media / "clip1.mp4").unlink()
        self.restore(info, include_media=True)
        self.assertEqual((self.media / "clip1.mp4").read_bytes(), b"original-1")

    def test_media_added_by_failed_restore_is_removed(self):
        info = self.backup(include_media=True)
        (self.media / "clip1.mp4").unlink()
        report = self.restore(info, include_media=True, health_check=lambda: False)
        self.assertEqual(report.status, "rolled_back")
        self.assertFalse((self.media / "clip1.mp4").exists())

    def test_corrupt_archive_aborts_before_any_change(self):
        info = self.backup()
        self.mutate()
        path = self.archive(info)
        path.write_bytes(path.read_bytes()[:-60])
        with self.assertRaises(BackupError):
            self.restore(info)
        self.assertIn("environment", (self.cfg / "kenpi.toml").read_text())
        self.assertFalse(self.recovery.exists())

    def test_recovery_dir_inside_root_refused(self):
        info = self.backup()
        with self.assertRaises(BackupError):
            restore_backup(
                self.archive(info), self.roots, "0.2.9", self.state / "recovery",
                dry_run=False, confirm=True,
            )  # fmt: skip

    def test_newer_backup_refused(self):
        info = self.backup()
        with self.assertRaises(BackupError):
            restore_backup(self.archive(info), self.roots, "0.1.0", self.recovery)


class SftpTests(unittest.TestCase):
    def make(self, responses=None):
        self.calls = []
        responses = responses or {}

        def runner(argv, batch):
            self.calls.append((argv, batch))
            for key, value in responses.items():
                if key in batch:
                    return value
            return 0, ""

        return SftpDestination(
            "pi.example", "kenpi", "/srv/backups", "/k/id", "/k/known", 2222, runner
        )

    def test_strict_host_checking_and_no_password(self):
        dest = self.make()
        dest.list_names()
        argv = self.calls[0][0]
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("kenpi@pi.example", argv)

    def test_put_uses_partial_then_nonoverwriting_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp, "a.kbk")
            local.write_bytes(b"12345")
            partial = "/srv/backups/.kenpi-backup-2.kbk.partial"
            dest = self.make(
                {
                    "ls -a1": (0, "kenpi-backup-1.kbk\n"),
                    "ls -lan": (0, f"-rw-r--r-- ? 1 1 5 Nov 7 20:00 {partial}\n"),
                }
            )
            dest.put(local, "kenpi-backup-2.kbk")
            batches = [c[1] for c in self.calls]
            self.assertIn("put", batches[1])
            self.assertIn("rename", batches[-1])
            self.assertIn(".kenpi-backup-2.kbk.partial", batches[-1])
            short = self.make(
                {"ls -lan": (0, f"-rw-r--r-- ? 1 1 2 Nov 7 20:00 {partial}\n")}
            )
            with self.assertRaises(BackupError):
                short.put(local, "kenpi-backup-3.kbk")
            self.assertFalse(any("rename" in c[1] for c in self.calls[-3:]))

    def test_put_refuses_existing_name(self):
        dest = self.make({"ls -a1": (0, "kenpi-backup-2.kbk\n")})
        with self.assertRaises(BackupError):
            dest.put(Path("/nonexistent/a.kbk"), "kenpi-backup-2.kbk")

    def test_listing_and_failure(self):
        dest = self.make(
            {"ls -a1": (0, "kenpi-backup-1.kbk\nother\n.kenpi-backup-3.kbk.partial\n")}
        )
        self.assertEqual(dest.list_names(), ["kenpi-backup-1.kbk"])
        self.assertEqual(len(dest.list_partials()), 1)
        with self.assertRaises(BackupError):
            self.make({"ls": (1, "")}).list_names()

    def test_free_space_parse_and_unknown(self):
        dest = self.make(
            {
                "df": (
                    0,
                    "    Size     Used    Avail   (root)    %Capacity\n 1000 400 600 600 40%\n",
                )
            }
        )
        self.assertEqual(dest.free_bytes(), 600 * 1024)
        self.assertIsNone(self.make({"df": (1, "")}).free_bytes())

    def test_unsafe_inputs_rejected(self):
        for args in (
            ("bad host", "u", "/p"),
            ("h", "u;rm", "/p"),
            ("h", "u", "/p/../x"),
            ("h", "u", "/p q"),
        ):
            with self.assertRaises(BackupError):
                SftpDestination(*args, "/k", "/kh")
        with self.assertRaises(BackupError):
            self.make().get("../../etc/passwd", Path("/tmp/x"))


class VoiceAssetProtectionTests(unittest.TestCase):
    def test_enrolled_voice_material_is_protected(self) -> None:
        for name in ("reference.wav", "speaker.pth"):
            self.assertTrue(
                is_protected(("assistant", "voice", "brooklyn_gentleman", name))
            )
        self.assertFalse(is_protected(("assistant", "preferences.json")))


if __name__ == "__main__":
    unittest.main()
