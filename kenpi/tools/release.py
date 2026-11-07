"""Version stamping and release-bundle assembly.

The version is ``<VERSION file>.<commit count>`` so every commit on a branch
gets a higher, reproducible number with no manual edits. The bundle is a single
zip holding every build output plus a manifest and SHA-256 checksums. Missing
components are recorded in the manifest rather than silently skipped.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_RE = re.compile(r"^\d+\.\d+$")

# component -> (artifact directory name, description)
COMPONENTS = {
    "python": "Python wheel (kenpi gateway)",
    "firmware": "ESP32-S3 auxiliary controller firmware",
    "android": "Android companion APK (debug-signed)",
    "os-image": "Raspberry Pi OS image (xz-compressed)",
    "deploy": "Installation scripts, systemd unit and example configuration",
}


def read_base(root: Path = ROOT) -> str:
    base = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not BASE_RE.match(base):
        raise ValueError(f"VERSION must look like MAJOR.MINOR, got {base!r}")
    return base


def compute_version(base: str, commit_count: int) -> str:
    if not BASE_RE.match(base):
        raise ValueError("base version must be MAJOR.MINOR")
    if commit_count < 1:
        raise ValueError("commit count must be positive")
    return f"{base}.{commit_count}"


def stamp(root: Path, version: str) -> None:
    """Rewrite the Python version fields in a (CI) working tree."""
    pyproject = root / "pyproject.toml"
    text, n = re.subn(
        r'(?m)^version = "[^"]*"',
        f'version = "{version}"',
        pyproject.read_text(encoding="utf-8"),
        1,
    )
    if n != 1:
        raise ValueError("pyproject.toml version line not found")
    pyproject.write_text(text, encoding="utf-8")
    init = root / "kenpi" / "__init__.py"
    text, n = re.subn(
        r'(?m)^__version__ = "[^"]*"',
        f'__version__ = "{version}"',
        init.read_text(encoding="utf-8"),
        1,
    )
    if n != 1:
        raise ValueError("kenpi/__init__.py __version__ line not found")
    init.write_text(text, encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assemble(artifacts: Path, out_zip: Path, version: str, commit: str) -> dict:
    """Zip ``artifacts/<component>/...`` into one bundle. Returns the manifest."""
    manifest: dict = {
        "name": "kenpi",
        "version": version,
        "commit": commit,
        "components": {},
        "notice": (
            "Build outputs only. Nothing here is hardware-verified; see "
            "docs/evidence-and-validation.md. Not production ready."
        ),
    }
    members: list[tuple[Path, str]] = []
    for component, description in COMPONENTS.items():
        directory = artifacts / component
        files = (
            sorted(p for p in directory.rglob("*") if p.is_file())
            if directory.is_dir()
            else []
        )
        entries = []
        for path in files:
            arcname = f"{component}/{path.relative_to(directory).as_posix()}"
            members.append((path, arcname))
            entries.append(
                {"path": arcname, "sha256": sha256(path), "bytes": path.stat().st_size}
            )
        manifest["components"][component] = {
            "description": description,
            "status": "present" if entries else "missing",
            "files": entries,
        }
    sums = "".join(
        f"{e['sha256']}  {e['path']}\n"
        for c in manifest["components"].values()
        for e in c["files"]
    )
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_STORED) as bundle:
        for path, arcname in members:
            bundle.write(path, arcname)
        bundle.writestr(
            "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        bundle.writestr("SHA256SUMS", sums)
    return manifest


def missing(manifest: dict) -> list[str]:
    return [
        name for name, c in manifest["components"].items() if c["status"] != "present"
    ]


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "version":
        print(compute_version(read_base(), int(argv[1])))
        return 0
    if len(argv) == 2 and argv[0] == "stamp":
        stamp(ROOT, argv[1])
        return 0
    if len(argv) == 5 and argv[0] == "assemble":
        manifest = assemble(Path(argv[1]), Path(argv[2]), argv[3], argv[4])
        gaps = missing(manifest)
        if gaps:
            print(f"bundle incomplete, missing: {', '.join(gaps)}", file=sys.stderr)
            return 1
        return 0
    print(
        "usage: release version <count> | stamp <version> | "
        "assemble <artifacts> <zip> <version> <commit>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
