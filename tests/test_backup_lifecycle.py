"""Clean-install and backup/restore behaviour of the assistant's real state files."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kenpi.assistant import AssistantService
from kenpi.core.capabilities import INITIAL_CAPABILITIES
from kenpi.recovery.backup import create_backup, restore_backup
from kenpi.recovery.destinations import LocalDirectoryDestination

ROOT = Path(__file__).resolve().parents[1]


def assistant(state: Path) -> AssistantService:
    return AssistantService(state / "assistant", None, lambda: {}, INITIAL_CAPABILITIES)


class CleanInstallTests(unittest.TestCase):
    def test_assistant_starts_on_empty_state_without_writing_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state"
            service = assistant(state)
            self.assertEqual(service.memory.all(), {})
            response = service.orchestrator.handle("what can you do", "s")
            self.assertTrue(response.outcomes[0].executed)
            files = {p.name for p in state.rglob("*") if p.is_file()}
            self.assertFalse({"reference.wav", "speaker.pth"} & files)

    def test_cli_refuses_when_feature_disabled_on_clean_machine(self):
        with tempfile.TemporaryDirectory() as d:
            done = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kenpi.assistant_cli",
                    "--state-directory",
                    d,
                    "ask",
                    "hello",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
            )
            self.assertEqual(done.returncode, 2)
            self.assertIn("disabled", done.stderr)
            self.assertEqual(list(Path(d).iterdir()), [])


class AssistantBackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "state"
        self.usb = self.tmp / "usb"
        self.usb.mkdir()
        self.dest = LocalDirectoryDestination(self.usb)

    def roots(self, state):
        return {"state": state}

    def test_preferences_and_history_survive_restore_but_voice_material_does_not(self):
        service = assistant(self.state)
        service.orchestrator.handle("set the volume to loud", "s")
        service.diagnostics.run()
        voice = self.state / "assistant" / "voice" / "profiles" / "me"
        voice.mkdir(parents=True)
        (voice / "reference.wav").write_bytes(b"RIFFvoice")
        (voice / "speaker.pth").write_bytes(b"embedding")
        info = create_backup(self.roots(self.state), self.dest, "0.2.5")

        fresh = self.tmp / "fresh-state"
        report = restore_backup(
            self.usb / info.name,
            self.roots(fresh),
            "0.2.5",
            self.tmp / "rec",
            dry_run=False,
            confirm=True,
        )
        self.assertEqual(report.status, "restored")
        restored = assistant(fresh)
        self.assertEqual(restored.memory.get("volume"), "loud")
        history = fresh / "assistant" / "diagnostic-history.jsonl"
        self.assertTrue(history.is_file())
        self.assertTrue(json.loads(history.read_text().splitlines()[0]))
        names = {p.name for p in fresh.rglob("*") if p.is_file()}
        self.assertFalse({"reference.wav", "speaker.pth"} & names)

    def test_restore_over_modified_state_returns_original_preferences(self):
        service = assistant(self.state)
        service.orchestrator.handle("set the volume to loud", "s")
        info = create_backup(self.roots(self.state), self.dest, "0.2.5")
        service.orchestrator.handle("set the volume to quiet", "s")
        restore_backup(
            self.usb / info.name,
            self.roots(self.state),
            "0.2.5",
            self.tmp / "rec",
            dry_run=False,
            confirm=True,
        )
        self.assertEqual(assistant(self.state).memory.get("volume"), "loud")


if __name__ == "__main__":
    unittest.main()
