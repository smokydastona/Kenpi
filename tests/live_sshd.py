"""Throwaway real OpenSSH server (loopback, own keys) for SFTP integration tests.

This is a real sshd speaking the real SFTP protocol, but it runs on the test machine's
loopback; it is not evidence about any particular remote host or network.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

SSHD = shutil.which("sshd") or "/usr/sbin/sshd"
AVAILABLE = os.path.exists(SSHD) and shutil.which("ssh-keygen") and shutil.which("sftp")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveSshd:
    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kenpi-sshd-"))
        self.tmp.chmod(0o700)
        self.port = _free_port()
        self.user = subprocess.run(
            ["id", "-un"], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.remote = self.tmp / "remote"
        self.remote.mkdir()
        self.host_key = self.tmp / "host_key"
        self.client_key = self.tmp / "client_key"
        self.other_key = self.tmp / "other_key"
        self.known_hosts = self.tmp / "known_hosts"
        for key in (self.host_key, self.client_key, self.other_key):
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                check=True,
            )
        host_pub = (self.tmp / "host_key.pub").read_text().split()
        self.known_hosts.write_text(
            f"[127.0.0.1]:{self.port} {host_pub[0]} {host_pub[1]}\n"
        )
        (self.tmp / "authorized_keys").write_text(
            (self.tmp / "client_key.pub").read_text()
        )
        (self.tmp / "authorized_keys").chmod(0o600)
        self.config = self.tmp / "sshd_config"
        self.config.write_text(
            f"Port {self.port}\nListenAddress 127.0.0.1\nHostKey {self.host_key}\n"
            f"PidFile {self.tmp / 'sshd.pid'}\nAuthorizedKeysFile {self.tmp / 'authorized_keys'}\n"
            "PasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\n"
            "PermitRootLogin yes\nStrictModes no\n"
            "Subsystem sftp internal-sftp\nLogLevel ERROR\n"
        )
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        Path("/run/sshd").mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [SSHD, "-D", "-e", "-f", str(self.config)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with socket.socket() as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.1)
        raise RuntimeError("sshd did not start")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(10)

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
