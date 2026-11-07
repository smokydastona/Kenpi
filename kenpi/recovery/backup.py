"""Versioned, integrity-checked backup archives and transactional restore (ADR 0004).

Archive layout (``.kbk`` = gzip tar): ``payload/<component>/<relative path>`` members plus a
final ``manifest.json`` holding SHA-256 checksums. Credentials, private keys and the
gateway state directory are never archived. Restore verifies, snapshots, stages, swaps each
root with two renames (undone automatically), runs a health check and rolls back on failure.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import secrets
import shutil
import tarfile
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from kenpi.recovery.destinations import BackupDestination, BackupError, valid_name

FORMAT = "kenpi-backup"
FORMAT_VERSION = 1
MEDIA = "media"
SECRET_SUFFIXES = (".key", ".pem", ".p12", ".pfx")
SECRET_DIRS = frozenset({"gateway", "tls", "wireguard"})
SECRET_NAMES = frozenset({"devices.json", "devices.lock", "secret.key", "remote.json"})
MAX_MEMBERS = 200_000


def is_protected(parts: tuple[str, ...]) -> bool:
    """True for credentials/keys/gateway state that must never be archived or restored."""
    name = parts[-1].lower()
    return (
        any(p in SECRET_DIRS for p in parts[:-1])
        or parts[-1] in SECRET_DIRS
        or name in SECRET_NAMES
        or name.endswith(SECRET_SUFFIXES)
    )


def _version(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in text.split(".")[:3])
    except ValueError:
        raise BackupError(f"unparseable kenpi_version {text!r}") from None


@dataclass(slots=True)
class Manifest:
    kenpi_version: str
    created_at: str
    components: dict[str, dict[str, int]]
    checksums: dict[str, str]
    includes_media: bool
    machine: str
    format: str = FORMAT
    format_version: int = FORMAT_VERSION

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), indent=1, sort_keys=True).encode()

    @classmethod
    def parse(cls, data: bytes) -> "Manifest":
        try:
            raw = json.loads(data)
            if not isinstance(raw, dict) or raw.get("format") != FORMAT:
                raise BackupError("not a kenpi backup")
            if (
                type(raw.get("format_version")) is not int
                or raw["format_version"] > FORMAT_VERSION
            ):
                raise BackupError("unsupported backup format version")
            comps, sums = raw["components"], raw["checksums"]
            if (
                not isinstance(comps, dict) or not isinstance(sums, dict)
                or not all(isinstance(v, str) and len(v) == 64 for v in sums.values())
                or type(raw["includes_media"]) is not bool
                or not isinstance(raw["kenpi_version"], str)
                or not isinstance(raw["created_at"], str)
            ):  # fmt: skip
                raise BackupError("malformed manifest")
            return cls(raw["kenpi_version"], raw["created_at"], comps, sums,
                       raw["includes_media"], str(raw.get("machine", "")),
                       raw["format"], raw["format_version"])  # fmt: skip
        except (ValueError, KeyError, TypeError) as exc:
            raise BackupError("malformed manifest") from exc


@dataclass(slots=True)
class BackupInfo:
    name: str
    files: int
    bytes: int
    includes_media: bool


def _walk(root: Path, skip: set[Path]) -> Iterator[tuple[str, Path]]:
    for base, dirs, names in os.walk(root, followlinks=False):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if (base_path / d) not in skip and not (base_path / d).is_symlink()
        )
        for name in sorted(names):
            path = base_path / name
            rel = path.relative_to(root)
            if path.is_symlink() or not path.is_file() or is_protected(rel.parts):
                continue
            if path in skip or any(parent in skip for parent in path.parents):
                continue
            yield rel.as_posix(), path


class _HashingReader:
    def __init__(self, handle, size: int) -> None:
        self.handle, self.remaining, self.digest = handle, size, hashlib.sha256()

    def read(self, n: int = -1) -> bytes:
        n = self.remaining if n < 0 else min(n, self.remaining)
        chunk = self.handle.read(n)
        if n and not chunk:
            raise BackupError("file shrank while being backed up")
        self.remaining -= len(chunk)
        self.digest.update(chunk)
        return chunk


def create_backup(
    roots: dict[str, Path],
    destination: BackupDestination,
    kenpi_version: str,
    include_media: bool = False,
    exclude_paths: tuple[Path, ...] = (),
    clock: Callable[[], float] = time.time,
) -> BackupInfo:
    selected = {k: Path(v) for k, v in roots.items() if include_media or k != MEDIA}
    skip = {Path(p) for p in exclude_paths}
    plan = [
        (comp, rel, path)
        for comp, root in selected.items()
        if root.is_dir()
        for rel, path in _walk(root, skip)
    ]
    total = sum(p.stat().st_size for _, _, p in plan)
    free = destination.free_bytes()
    if free is not None and free < int(total * 1.1) + (1 << 20):
        raise BackupError("insufficient space at the backup destination")
    stamp = datetime.fromtimestamp(clock(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"kenpi-backup-{stamp}-{secrets.token_hex(2)}.kbk"
    with tempfile.TemporaryDirectory(prefix="kenpi-backup-") as tmp:
        if shutil.disk_usage(tmp).free < total + (1 << 20):
            raise BackupError("insufficient local temporary space")
        archive = Path(tmp, name)
        checksums: dict[str, str] = {}
        components = {c: {"files": 0, "bytes": 0} for c in selected}
        with tarfile.open(archive, "w:gz") as tar:
            for comp, rel, path in plan:
                with open(path, "rb") as handle:
                    st = os.fstat(handle.fileno())
                    info = tarfile.TarInfo(f"payload/{comp}/{rel}")
                    info.size, info.mtime = st.st_size, int(st.st_mtime)
                    info.mode = (
                        st.st_mode & 0o755
                    )  # drop setuid/setgid and write bits for others
                    reader = _HashingReader(handle, st.st_size)
                    tar.addfile(info, reader)
                checksums[info.name] = reader.digest.hexdigest()
                components[comp]["files"] += 1
                components[comp]["bytes"] += st.st_size
            manifest = Manifest(
                kenpi_version,
                datetime.fromtimestamp(clock(), timezone.utc).isoformat(),
                components, checksums, include_media and MEDIA in selected, platform.machine(),
            )  # fmt: skip
            data = manifest.to_json()
            member = tarfile.TarInfo("manifest.json")
            member.size, member.mtime = len(data), int(clock())
            tar.addfile(member, io.BytesIO(data))
        verify_archive(archive)
        destination.put(archive, name)
    return BackupInfo(name, len(plan), total, manifest.includes_media)


def _check_member_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name or "\\" in name or "\0" in name or path.is_absolute()
        or any(p in ("", ".", "..") for p in name.split("/"))
    ):  # fmt: skip
        raise BackupError(f"unsafe archive member {name!r}")
    return path


def _members(tar: tarfile.TarFile) -> Iterator[tarfile.TarInfo]:
    for index, member in enumerate(tar):
        if index > MAX_MEMBERS:
            raise BackupError("archive has too many members")
        if not member.isreg():
            raise BackupError(f"unsupported member type: {member.name!r}")
        _check_member_name(member.name)
        yield member


def verify_archive(archive: Path) -> Manifest:
    """Full integrity check: manifest, every member's SHA-256, no missing or extra files."""
    manifest: Manifest | None = None
    seen: dict[str, str] = {}
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in _members(tar):
                handle = tar.extractfile(member)
                assert handle is not None
                if member.name == "manifest.json":
                    if member.size > 64 << 20:
                        raise BackupError("manifest too large")
                    manifest = Manifest.parse(handle.read())
                    continue
                if not member.name.startswith("payload/") or member.name.count("/") < 2:
                    raise BackupError(f"unexpected member {member.name!r}")
                digest = hashlib.sha256()
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
                seen[member.name] = digest.hexdigest()
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise BackupError(f"archive is corrupt or truncated: {exc}") from exc
    if manifest is None:
        raise BackupError("manifest missing (interrupted or truncated archive)")
    if seen != manifest.checksums:
        bad = sorted(
            set(seen) ^ set(manifest.checksums)
            | {
                k
                for k in seen
                if k in manifest.checksums and seen[k] != manifest.checksums[k]
            }
        )
        raise BackupError(
            f"integrity check failed for {len(bad)} file(s), e.g. {bad[0]}"
        )
    for name in seen:
        if name.split("/")[1] not in manifest.components:
            raise BackupError(f"file for undeclared component: {name}")
        if is_protected(PurePosixPath(name).parts[2:]):
            raise BackupError(f"archive contains protected credential material: {name}")
    return manifest


def check_compatibility(manifest: Manifest, current_version: str) -> list[str]:
    """Raise BackupError when the archive is unusable here; return non-fatal warnings."""
    if _version(manifest.kenpi_version) > _version(current_version):
        raise BackupError(
            f"backup made by newer kenpi {manifest.kenpi_version} (running {current_version})"
        )
    warnings = []
    if manifest.machine and manifest.machine != platform.machine():
        warnings.append(
            f"backup machine {manifest.machine} differs from {platform.machine()}"
        )
    return warnings


def list_backups(destination: BackupDestination) -> dict[str, list[str]]:
    return {
        "complete": destination.list_names(),
        "interrupted": destination.list_partials(),
    }


def apply_retention(destination: BackupDestination, keep: int) -> list[str]:
    if keep < 1:
        raise BackupError("retention must keep at least one backup")
    names = destination.list_names()
    removed = names[:-keep] if len(names) > keep else []
    for name in removed:
        destination.delete(name)
    return removed


def fetch(destination: BackupDestination, name: str, directory: Path) -> Path:
    if not valid_name(name):
        raise BackupError("invalid backup name")
    target = directory / name
    destination.get(name, target)
    return target


@dataclass(slots=True)
class RestoreReport:
    status: str  # "dry-run" | "restored" | "rolled_back"
    manifest: Manifest
    warnings: list[str] = field(default_factory=list)
    overwrites: dict[str, list[str]] = field(default_factory=dict)
    media_skipped: list[str] = field(default_factory=list)
    snapshot: Path | None = None
    reason: str = ""


def _extract(
    archive: Path, component: str, target: Path, manifest: Manifest
) -> list[str]:
    written: list[str] = []
    prefix = f"payload/{component}/"
    base = target.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in _members(tar):
            if not member.name.startswith(prefix):
                continue
            rel = member.name[len(prefix) :]
            dest = (base / rel).resolve()
            if base not in dest.parents:
                raise BackupError(f"unsafe path {rel!r}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            handle = tar.extractfile(member)
            assert handle is not None
            fd = os.open(
                dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            digest = hashlib.sha256()
            with os.fdopen(fd, "wb") as out:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(dest, member.mode & 0o755 or 0o600)
            if digest.hexdigest() != manifest.checksums[member.name]:
                raise BackupError(f"staged file failed verification: {rel}")
            written.append(rel)
    return written


def restore_backup(
    archive: Path,
    roots: dict[str, Path],
    current_version: str,
    recovery_dir: Path,
    *,
    dry_run: bool = True,
    confirm: bool = False,
    include_media: bool = False,
    validate_staged: Callable[[dict[str, Path]], None] | None = None,
    restart_services: Callable[[], None] = lambda: None,
    health_check: Callable[[], bool] = lambda: True,
    after_restore: Callable[[], None] = lambda: None,
) -> RestoreReport:
    manifest = verify_archive(archive)
    warnings = check_compatibility(manifest, current_version)
    wanted = [
        c
        for c in manifest.components
        if c != MEDIA or (include_media and manifest.includes_media)
    ]
    unknown = [c for c in wanted if c not in roots]
    if unknown:
        raise BackupError(f"no restore target configured for: {', '.join(unknown)}")
    overwrites: dict[str, list[str]] = {}
    media_skipped: list[str] = []
    for name in manifest.checksums:
        comp, rel = name.split("/", 2)[1:]
        if comp not in wanted:
            continue
        exists = (Path(roots[comp]) / rel).exists()
        if comp == MEDIA and exists:
            media_skipped.append(rel)
        elif exists:
            overwrites.setdefault(comp, []).append(rel)
    report = RestoreReport("dry-run", manifest, warnings, overwrites, media_skipped)
    if dry_run:
        return report
    if not confirm:
        raise BackupError("restore requires explicit confirmation")

    resolved = recovery_dir.resolve()
    if any(Path(r).resolve() in (resolved, *resolved.parents) for r in roots.values()):
        raise BackupError("recovery directory must be outside every restore root")
    run = secrets.token_hex(4)
    config_roots = [c for c in wanted if c != MEDIA]
    need = sum(manifest.components[c]["bytes"] for c in config_roots)
    recovery_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if shutil.disk_usage(recovery_dir).free < 2 * need + (1 << 20):
        raise BackupError("insufficient space for the pre-restore snapshot")
    snapshot = recovery_dir / f"pre-restore-{int(time.time())}-{run}"
    for comp in config_roots:
        root = Path(roots[comp])
        if root.is_dir():
            shutil.copytree(root, snapshot / comp, symlinks=True)
    report.snapshot = snapshot

    staged: dict[str, Path] = {}
    activated: list[tuple[Path, Path | None]] = []
    media_added: list[Path] = []
    try:
        for comp in config_roots:
            root = Path(roots[comp])
            stage = root.parent / f".{root.name}.kenpi-stage-{run}"
            stage.mkdir(mode=0o700, parents=True)
            staged[comp] = stage
            _extract(archive, comp, stage, manifest)
            if (
                root.is_dir()
            ):  # carry over credentials and keys; they are never restored
                for rel, path in _walk_protected(root):
                    dest = stage / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest)
            if root.is_dir():
                shutil.copystat(root, stage)
        if validate_staged is not None:
            validate_staged(staged)
        for comp in config_roots:
            root, stage = Path(roots[comp]), staged[comp]
            old: Path | None = None
            if root.exists():
                old = root.parent / f".{root.name}.kenpi-old-{run}"
                os.rename(root, old)
            activated.append((root, old))
            os.rename(stage, root)
        if MEDIA in wanted:
            media_added = _restore_media(archive, Path(roots[MEDIA]), manifest)
        restart_services()
        if health_check() is not True:
            raise BackupError("post-restore health check failed")
    except BaseException as exc:
        _rollback(activated, media_added, staged)
        try:
            restart_services()
        except Exception:
            pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        report.status, report.reason = "rolled_back", str(exc) or type(exc).__name__
        return report
    for _, old in activated:
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    after_restore()
    report.status = "restored"
    return report


def _walk_protected(root: Path) -> Iterator[tuple[str, Path]]:
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
        for name in names:
            path = Path(base) / name
            rel = path.relative_to(root)
            if path.is_file() and not path.is_symlink() and is_protected(rel.parts):
                yield rel.as_posix(), path


def _restore_media(archive: Path, root: Path, manifest: Manifest) -> list[Path]:
    """Add missing media only; existing original files are never overwritten."""
    added: list[Path] = []
    prefix = f"payload/{MEDIA}/"
    root.mkdir(parents=True, exist_ok=True)
    base = root.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in _members(tar):
            if not member.name.startswith(prefix):
                continue
            dest = (base / member.name[len(prefix) :]).resolve()
            if base not in dest.parents:
                raise BackupError("unsafe media path")
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            handle = tar.extractfile(member)
            assert handle is not None
            tmp = dest.with_name(f".{dest.name}.restore-partial")
            try:
                with open(tmp, "wb") as out:
                    shutil.copyfileobj(handle, out, 1 << 20)
                    out.flush()
                    os.fsync(out.fileno())
                os.link(tmp, dest)
                added.append(dest)
            except FileExistsError:
                pass
            finally:
                tmp.unlink(missing_ok=True)
    return added


def _rollback(
    activated: list[tuple[Path, Path | None]],
    media_added: list[Path],
    staged: dict[str, Path],
) -> None:
    for path in media_added:
        path.unlink(missing_ok=True)
    for root, old in reversed(activated):
        failed = root.parent / f".{root.name}.kenpi-failed"
        if root.exists():
            os.rename(root, failed)
        if old is not None:
            os.rename(old, root)
        shutil.rmtree(failed, ignore_errors=True)
    for stage in staged.values():
        shutil.rmtree(stage, ignore_errors=True)
