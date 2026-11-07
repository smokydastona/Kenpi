import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kenpi.hardware.platform import collect_platform_health, read_temperature


class VirtualFilesystem:
    """In-memory kernel ABI fixtures; tests never create temporary files."""

    def __init__(self) -> None:
        self.root = Path("/virtual/kenpi")
        self.files: dict[Path, bytes] = {}
        self.directories = {self.root}
        self.stack = ExitStack()

    def __enter__(self):
        def read_bytes(path):
            if path not in self.files:
                raise FileNotFoundError("fixture source unavailable")
            return self.files[path]

        def read_text(path, encoding=None, errors=None):
            return read_bytes(path).decode(encoding or "utf-8", errors or "strict")

        def write_bytes(path, data):
            self.files[path] = bytes(data)
            return len(data)

        def write_text(path, data, encoding=None, errors=None, newline=None):
            write_bytes(path, data.encode(encoding or "utf-8", errors or "strict"))
            return len(data)

        def mkdir(path, mode=0o777, parents=False, exist_ok=False):
            self.directories.add(path)
            if parents:
                self.directories.update(path.parents)

        def iterdir(path):
            if path not in self.directories:
                raise FileNotFoundError("fixture source unavailable")
            return iter(
                item
                for item in self.files.keys() | self.directories
                if item.parent == path
            )

        def disk_usage(path):
            if Path(path) not in self.directories:
                raise FileNotFoundError("fixture storage unavailable")
            return SimpleNamespace(total=4096, free=2048, used=2048)

        for name, replacement in (
            ("read_bytes", read_bytes),
            ("read_text", read_text),
            ("write_bytes", write_bytes),
            ("write_text", write_text),
            ("mkdir", mkdir),
            ("iterdir", iterdir),
        ):
            self.stack.enter_context(patch.object(Path, name, replacement))
        self.stack.enter_context(
            patch("kenpi.services.health.shutil.disk_usage", disk_usage)
        )
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


class HardwareTests(unittest.TestCase):
    def setUp(self) -> None:
        filesystem = self.enterContext(VirtualFilesystem())
        self.root = filesystem.root
        self.tree = self.root / "device-tree"
        self.tree.mkdir()
        self.thermal = self.root / "thermal"
        self.thermal.mkdir()
        self.uptime = self.root / "uptime"

    def collect(self) -> dict:
        return collect_platform_health(
            device_tree_paths=(self.tree,),
            thermal_root=self.thermal,
            uptime_path=self.uptime,
        )

    def test_both_pi_generations_use_device_tree_compatibility(self) -> None:
        for generation in (4, 5):
            with self.subTest(generation=generation):
                (self.tree / "model").write_bytes(
                    f"Raspberry Pi {generation} Model B Rev 1.0\0".encode()
                )
                (self.tree / "compatible").write_bytes(
                    f"raspberrypi,{generation}-model-b\0brcm,bcm2712\0".encode()
                )
                result = self.collect()
                self.assertEqual(result["platform"]["state"], "supported")
                self.assertEqual(result["platform"]["board"], f"pi{generation}")
                self.assertEqual(
                    result["capabilities"]["hardware_control"], "unavailable"
                )

    def test_missing_or_unsupported_platform_is_explicit(self) -> None:
        self.assertEqual(self.collect()["platform"]["state"], "unavailable")
        (self.tree / "model").write_bytes(b"Raspberry Pi 6 Model B\0")
        (self.tree / "compatible").write_bytes(b"raspberrypi,6-model-b\0")
        result = self.collect()
        self.assertEqual(result["platform"]["state"], "unsupported")
        self.assertIsNone(result["platform"]["board"])
        self.assertIsNone(result["cpu_temperature_c"])
        self.assertIsNone(result["uptime_seconds"])

    def test_model_name_alone_does_not_claim_support(self) -> None:
        (self.tree / "model").write_bytes(b"Raspberry Pi 5 Model B\0")
        self.assertEqual(self.collect()["platform"]["state"], "unsupported")

    def test_falls_back_to_proc_device_tree(self) -> None:
        (self.tree / "compatible").write_bytes(b"raspberrypi,4-model-b\0")
        result = collect_platform_health(
            device_tree_paths=(self.root / "missing-tree", self.tree),
            thermal_root=self.thermal,
            uptime_path=self.uptime,
        )
        self.assertEqual(result["platform"]["board"], "pi4")

    def test_only_identified_cpu_sensor_is_used(self) -> None:
        for index, sensor_type, value in (
            (0, "gpu-thermal", "90000"),
            (1, "cpu-thermal", "broken"),
            (2, "bcm2835_thermal", "42500"),
        ):
            zone = self.thermal / f"thermal_zone{index}"
            zone.mkdir()
            (zone / "type").write_text(sensor_type)
            (zone / "temp").write_text(value)
        self.uptime.write_text("123.50 456.0\n")
        result = self.collect()
        self.assertEqual(result["cpu_temperature_c"], 42.5)
        self.assertEqual(result["uptime_seconds"], 123.5)
        self.assertEqual(result["capabilities"]["cpu_temperature"], "available")
        self.assertEqual(result["cpu_temperature_sensor"], "thermal_zone2")
        self.assertIn(
            {"metric": "cpu_temperature", "reason": "invalid_reading"},
            result["collection_errors"],
        )

    def test_malformed_readings_remain_unavailable(self) -> None:
        for value in ("", "-1 0", "nan 0", "inf 0", "bad 0"):
            with self.subTest(value=value):
                self.uptime.write_text(value)
                result = self.collect()
                self.assertIsNone(result["uptime_seconds"])
                self.assertTrue(
                    any(
                        error["metric"] == "uptime"
                        for error in result["collection_errors"]
                    )
                )
        temperature = self.root / "temp"
        for value in ("bad", "-274000", "1000001", "NaN", "9" * 400):
            with self.subTest(value=value):
                temperature.write_text(value)
                errors: list[dict[str, str]] = []
                self.assertIsNone(
                    read_temperature(temperature, collection_errors=errors)
                )
                self.assertTrue(errors)

    def test_invalid_device_tree_encoding_is_unavailable(self) -> None:
        (self.tree / "compatible").write_bytes(b"\xff")
        self.assertEqual(self.collect()["platform"]["state"], "unavailable")

    def test_sensor_discovery_permission_failure_is_explicit_and_sanitized(
        self,
    ) -> None:
        with patch(
            "pathlib.Path.iterdir", side_effect=PermissionError("sensitive/path")
        ):
            result = self.collect()
        self.assertIn(
            {"metric": "cpu_temperature_sensor", "reason": "permission_denied"},
            result["collection_errors"],
        )
        self.assertNotIn("sensitive/path", str(result))

    def test_complete_platform_readings_have_no_collection_errors(self) -> None:
        (self.tree / "model").write_bytes(b"Raspberry Pi 4 Model B\0")
        (self.tree / "compatible").write_bytes(b"raspberrypi,4-model-b\0")
        self.uptime.write_text("10.5 4")
        zone = self.thermal / "thermal_zone0"
        zone.mkdir()
        (zone / "type").write_text("cpu-thermal")
        (zone / "temp").write_text("42500")
        self.assertEqual(self.collect()["collection_errors"], [])


class DeploymentTests(unittest.TestCase):
    @staticmethod
    def deployment_inputs() -> tuple[str, str, str]:
        return (
            Path("deploy/install-status.sh").read_text(),
            Path("deploy/kenpi-status.service").read_text(),
            Path("deploy/status.toml").read_text(),
        )

    @staticmethod
    def validate_metadata(script: str, filesystem: VirtualFilesystem) -> None:
        code = script.split("python3 -I -c '\n", 1)[1].split(
            '\' "$backup/status.toml"', 1
        )[0]
        with patch.object(
            sys,
            "argv",
            [
                "metadata-validation",
                str(filesystem.root / "protected/status.toml"),
                str(filesystem.root / "protected/kenpi-status.service"),
            ],
        ):
            exec(compile(code, "<installer-metadata-validation>", "exec"), {})

    def test_root_install_sources_are_protected_before_any_builder_runs(self) -> None:
        script, _, _ = self.deployment_inputs()
        self.assertLess(
            script.index('git -C "$source_root" show "$commit:$relative"'),
            script.index("runuser -u kenpi"),
        )
        self.assertIn('mkdir -m 0700 "$backup"', script)
        self.assertIn("if [[ $mode != 100644 ]]", script)
        self.assertIn("[[ -L $protected || ! -f $protected", script)
        self.assertIn('hash-object "$protected"', script)
        privileged_copies = [
            line for line in script.splitlines() if line.startswith("install -o")
        ]
        self.assertEqual(
            privileged_copies,
            [
                'install -o root -g kenpi -m 0640 "$backup/status.toml" "$config"',
                'install -o root -g root -m 0644 "$backup/kenpi-status.service" "$unit"',
            ],
        )
        self.assertIn('systemd-analyze verify "$unit"', script)

    def test_builder_mutations_cannot_change_privileged_metadata(self) -> None:
        script, unit, config = self.deployment_inputs()
        with VirtualFilesystem() as filesystem:
            (filesystem.root / "protected/status.toml").write_text(config)
            (filesystem.root / "protected/kenpi-status.service").write_text(unit)
            (filesystem.root / "source/status.toml").write_text(
                config.replace("camera_enabled = false", "camera_enabled = true")
            )
            (filesystem.root / "source/kenpi-status.service").write_text(
                unit.replace("User=kenpi", "User=root")
            )
            self.validate_metadata(script, filesystem)

    def test_builder_cannot_precreate_privileged_leaf_directory_symlinks(self) -> None:
        script, _, _ = self.deployment_inputs()
        private_parent = script.index('mkdir -m 0700 "$release"')
        archive = script.index('archive "$commit" > "$release/source.tar"')
        source_and_venv = script.index(
            'install -d -o kenpi -g kenpi -m 0755 "$release/source" "$release/venv"'
        )
        scratch = script.index(
            'install -d -o kenpi -g kenpi -m 0700 "$release/build-tmp"'
        )
        expose_parent = script.index('chmod 0755 "$release"')
        builder = script.index("runuser -u kenpi")
        self.assertLess(private_parent, archive)
        self.assertLess(archive, source_and_venv)
        self.assertLess(source_and_venv, scratch)
        self.assertLess(scratch, expose_parent)
        self.assertLess(expose_parent, builder)
        self.assertNotIn('chown kenpi:kenpi "$release"', script)
        self.assertNotIn('chmod -R go-w "$release"', script)
        self.assertIn('runuser -u kenpi -- chmod -R go-w "$release/venv"', script)
        self.assertIn('chown -hR -P root:root "$release"', script)
        self.assertIn("! -type l -a -perm /6022", script)

    def test_metadata_validator_rejects_unsafe_unit_and_config(self) -> None:
        script, unit, config = self.deployment_inputs()
        for changed_unit, changed_config in (
            (unit.replace("User=kenpi", "User=root"), config),
            (unit.replace("ExecStart=", "ExecStart=+"), config),
            (
                unit.replace("User=kenpi", "User=kenpi\nExecStartPre=+/bin/false"),
                config,
            ),
            (unit, config.replace("camera_enabled = false", "camera_enabled = true")),
        ):
            with self.subTest(unit=changed_unit, config=changed_config):
                with VirtualFilesystem() as filesystem:
                    (filesystem.root / "protected/status.toml").write_text(
                        changed_config
                    )
                    (filesystem.root / "protected/kenpi-status.service").write_text(
                        changed_unit
                    )
                    with self.assertRaises((AssertionError, ValueError)):
                        self.validate_metadata(script, filesystem)


if __name__ == "__main__":
    unittest.main()
