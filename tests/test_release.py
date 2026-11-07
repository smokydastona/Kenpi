import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from kenpi.tools import release


class ReleaseTests(unittest.TestCase):
    def test_version_increments_with_commit_count(self):
        self.assertEqual(release.compute_version("0.2", 7), "0.2.7")
        self.assertEqual(release.compute_version("0.2", 8), "0.2.8")
        with self.assertRaises(ValueError):
            release.compute_version("0.2.1", 1)
        with self.assertRaises(ValueError):
            release.compute_version("0.2", 0)

    def test_repository_version_file_is_valid(self):
        self.assertRegex(release.read_base(), r"^\d+\.\d+$")

    def test_stamp_rewrites_python_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "kenpi").mkdir()
            (root / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
            (root / "kenpi" / "__init__.py").write_text('__version__ = "0.1.0"\n')
            release.stamp(root, "0.2.9")
            self.assertIn('version = "0.2.9"', (root / "pyproject.toml").read_text())
            self.assertIn('"0.2.9"', (root / "kenpi" / "__init__.py").read_text())

    def test_stamp_fails_loudly_without_version_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "kenpi").mkdir()
            (root / "pyproject.toml").write_text("[project]\n")
            (root / "kenpi" / "__init__.py").write_text('__version__ = "1"\n')
            with self.assertRaises(ValueError):
                release.stamp(root, "0.2.9")

    def test_assemble_single_zip_with_manifest_and_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for component in release.COMPONENTS:
                (tmp / "a" / component).mkdir(parents=True)
                (tmp / "a" / component / "file.bin").write_bytes(component.encode())
            manifest = release.assemble(
                tmp / "a", tmp / "out" / "k.zip", "0.2.3", "abc"
            )
            self.assertEqual(release.missing(manifest), [])
            with zipfile.ZipFile(tmp / "out" / "k.zip") as bundle:
                names = set(bundle.namelist())
                self.assertIn("manifest.json", names)
                self.assertIn("SHA256SUMS", names)
                self.assertIn("firmware/file.bin", names)
                loaded = json.loads(bundle.read("manifest.json"))
            self.assertEqual(loaded["version"], "0.2.3")

    def test_missing_component_is_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "a" / "python").mkdir(parents=True)
            (tmp / "a" / "python" / "w.whl").write_bytes(b"x")
            manifest = release.assemble(tmp / "a", tmp / "k.zip", "0.2.1", "abc")
            self.assertEqual(
                set(release.missing(manifest)),
                {"firmware", "android", "os-image", "deploy"},
            )
            self.assertEqual(
                release.main(
                    ["assemble", str(tmp / "a"), str(tmp / "k2.zip"), "0.2.1", "abc"]
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
