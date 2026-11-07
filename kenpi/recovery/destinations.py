"""Backup destination adapters. The engine only talks to ``BackupDestination``."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

SUFFIX = ".kbk"
_NAME = re.compile(r"^kenpi-backup-[0-9A-Za-z._-]{1,80}\.kbk$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/~-]+$")


class BackupError(Exception):
    """A backup or restore precondition, integrity or safety check failed."""


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name))


class BackupDestination(ABC):
    @abstractmethod
    def free_bytes(self) -> int | None:
        """Free space, or None when the destination cannot report it."""

    @abstractmethod
    def list_names(self) -> list[str]:
        """Complete backups only, oldest first."""

    @abstractmethod
    def list_partials(self) -> list[str]:
        """Interrupted uploads left behind by a crash."""

    @abstractmethod
    def put(self, local: Path, name: str) -> None:
        """Store atomically; must raise BackupError rather than overwrite an existing name."""

    @abstractmethod
    def get(self, name: str, local: Path) -> None: ...

    @abstractmethod
    def delete(self, name: str) -> None: ...

    @abstractmethod
    def remove_partial(self, name: str) -> None: ...


class LocalDirectoryDestination(BackupDestination):
    """A directory such as a mounted USB drive. It must already exist."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise BackupError(f"backup directory {self.directory} does not exist")

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.directory).free

    def list_names(self) -> list[str]:
        return sorted(
            p.name
            for p in self.directory.iterdir()
            if valid_name(p.name) and p.is_file()
        )

    def list_partials(self) -> list[str]:
        return sorted(p.name for p in self.directory.glob(".kenpi-backup-*.partial"))

    def put(self, local: Path, name: str) -> None:
        if not valid_name(name):
            raise BackupError("invalid backup name")
        partial, final = self.directory / f".{name}.partial", self.directory / name
        if final.exists():
            raise BackupError(f"{name} already exists; refusing to overwrite")
        try:
            with open(local, "rb") as src, open(partial, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
                dst.flush()
                os.fsync(dst.fileno())
            os.link(
                partial, final
            )  # fails if the name appeared meanwhile: no overwrite
        except FileExistsError:
            raise BackupError(f"{name} already exists; refusing to overwrite") from None
        finally:
            partial.unlink(missing_ok=True)

    def get(self, name: str, local: Path) -> None:
        if not valid_name(name):
            raise BackupError("invalid backup name")
        shutil.copyfile(self.directory / name, local)

    def delete(self, name: str) -> None:
        if not valid_name(name):
            raise BackupError("invalid backup name")
        (self.directory / name).unlink()

    def remove_partial(self, name: str) -> None:
        if not (
            name.startswith(".kenpi-backup-")
            and name.endswith(".partial")
            and "/" not in name
        ):
            raise BackupError("invalid partial name")
        (self.directory / name).unlink(missing_ok=True)


Sftp = Callable[[list[str], str], tuple[int, str]]


def _system_sftp(argv: list[str], batch: str) -> tuple[int, str]:
    done = subprocess.run(
        argv, input=batch, capture_output=True, text=True, timeout=3600, check=False
    )
    return done.returncode, done.stdout


class SftpDestination(BackupDestination):
    """Remote SFTP target driven by the OpenSSH ``sftp`` batch mode.

    Host keys are verified (StrictHostKeyChecking=yes) against ``known_hosts``; password
    prompts are disabled. NOT validated against a live server in this repository's tests.
    """

    def __init__(
        self, host: str, user: str, path: str, identity_file: str, known_hosts: str,
        port: int = 22, runner: Sftp = _system_sftp,
    ) -> None:  # fmt: skip
        if not re.match(r"^[A-Za-z0-9.-]+$", host) or not re.match(
            r"^[A-Za-z0-9._-]+$", user
        ):
            raise BackupError("invalid SFTP host or user")
        if not _SAFE_PATH.match(path) or ".." in path.split("/"):
            raise BackupError("invalid SFTP path")
        self._path, self._run = path.rstrip("/") or "/", runner
        self._argv = [
            "sftp", "-b", "-", "-P", str(int(port)), "-i", identity_file,
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}", f"{user}@{host}",
        ]  # fmt: skip

    def _batch(self, *commands: str) -> str:
        rc, out = self._run(self._argv, "\n".join(commands) + "\n")
        if rc != 0:
            raise BackupError("sftp command failed")
        return out

    def _p(self, name: str) -> str:
        return f"{self._path}/{name}"

    def free_bytes(self) -> int | None:
        try:
            out = self._batch(f"df -k {self._path}")
        except BackupError:
            return None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1].isdigit() and parts[3].isdigit():
                return int(parts[3]) * 1024
        return None

    def _names(self) -> list[str]:
        out = self._batch(f"ls -1 {self._path}")
        return sorted(
            {
                Path(line.strip()).name
                for line in out.splitlines()
                if line.strip() and not line.startswith("sftp>")
            }
        )

    def list_names(self) -> list[str]:
        return [n for n in self._names() if valid_name(n)]

    def list_partials(self) -> list[str]:
        return [
            n
            for n in self._names()
            if n.startswith(".kenpi-backup-") and n.endswith(".partial")
        ]

    def put(self, local: Path, name: str) -> None:
        if not valid_name(name):
            raise BackupError("invalid backup name")
        if name in self.list_names():
            raise BackupError(f"{name} already exists; refusing to overwrite")
        partial = f".{name}.partial"
        # SFTP v3 'rename' fails when the target exists, so it never overwrites.
        self._batch(
            f'put "{local}" {self._p(partial)}',
            f"rename {self._p(partial)} {self._p(name)}",
        )

    def get(self, name: str, local: Path) -> None:
        if not valid_name(name):
            raise BackupError("invalid backup name")
        self._batch(f'get {self._p(name)} "{local}"')

    def delete(self, name: str) -> None:
        if not valid_name(name):
            raise BackupError("invalid backup name")
        self._batch(f"rm {self._p(name)}")

    def remove_partial(self, name: str) -> None:
        if not (
            name.startswith(".kenpi-backup-")
            and name.endswith(".partial")
            and _SAFE_PATH.match(name)
        ):
            raise BackupError("invalid partial name")
        self._batch(f"-rm {self._p(name)}")
