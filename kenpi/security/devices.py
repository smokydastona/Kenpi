"""Per-device enrolment, opaque credentials, rotation and revocation.

Design (ADR 0002): one-time enrolment challenges, a device key pair whose public
half is registered (ECDSA P-256 / SHA-256, as produced by Android Keystore), short-lived
opaque access tokens and rotating refresh credentials. Only keyed hashes of secrets are
stored. State lives in a 0600 JSON file guarded by an flock so the gateway process and the
local administrator CLI can share it; every operation reloads the file.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

CHALLENGE_TTL_SECONDS = 300
COLLECT_WINDOW_SECONDS = 600
ACCESS_TTL_SECONDS = 300
REFRESH_TTL_SECONDS = 90 * 24 * 3600
SCOPES = frozenset({"status.read", "vehicle.read", "camera.read", "media.read"})
DEFAULT_SCOPES = frozenset({"status.read", "vehicle.read"})
ENROLL_CONTEXT = b"kenpi-enroll-v1"
COLLECT_CONTEXT = b"kenpi-collect-v1"
REFRESH_CONTEXT = b"kenpi-refresh-v1"


class AuthError(Exception):
    """Authentication or authorisation failed; ``code`` is safe to return to clients."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RateLimiter:
    """Sliding-window limiter keyed by caller (for example peer address)."""

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit, self._window, self._clock = limit, window, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
            hits = self._hits[key]
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise AuthError("bad_encoding") from exc


def enroll_message(eid: str, challenge: str, name: str, public_key: str) -> bytes:
    return b"\n".join(
        part.encode() if isinstance(part, str) else part
        for part in (ENROLL_CONTEXT, eid, challenge, name, public_key)
    )


def verify_signature(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    try:
        key = serialization.load_der_public_key(b64d(public_key_b64))
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            return False
        key.verify(b64d(signature_b64), message, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, AuthError):
        return False
    return True


@dataclass(frozen=True, slots=True)
class Device:
    device_id: str
    name: str
    scopes: frozenset[str]
    allow_remote: bool


@dataclass(frozen=True, slots=True)
class Tokens:
    device_id: str
    access_token: str
    access_expires_in: int
    refresh_token: str


class DeviceRegistry:
    def __init__(
        self, state_dir: str | Path, clock: Callable[[], float] = time.time
    ) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._file = self._dir / "devices.json"
        self._lock_file = self._dir / "devices.lock"
        self._audit_file = self._dir / "audit.log"
        self._clock = clock
        self._secret = self._load_secret()

    def _load_secret(self) -> bytes:
        path = self._dir / "secret.key"
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(secrets.token_bytes(32))
        data = path.read_bytes()
        if len(data) != 32:
            raise RuntimeError("secret.key is corrupt")
        return data

    def _hash(self, value: str) -> str:
        return hmac.new(self._secret, value.encode(), hashlib.sha256).hexdigest()

    @contextmanager
    def _txn(self, write: bool = True) -> Iterator[dict]:
        fd = os.open(self._lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                state = json.loads(self._file.read_text())
            except FileNotFoundError:
                state = {"version": 1, "enrollments": {}, "devices": {}, "access": {}}
            yield state
            if write:
                self._prune(state)
                tmp = tempfile.NamedTemporaryFile(
                    "w", dir=self._dir, delete=False, prefix=".devices-"
                )
                try:
                    os.fchmod(tmp.fileno(), 0o600)
                    json.dump(state, tmp)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    tmp.close()
                    os.replace(tmp.name, self._file)
                except BaseException:
                    os.unlink(tmp.name)
                    raise
        finally:
            os.close(fd)

    def _prune(self, state: dict) -> None:
        now = self._clock()
        state["access"] = {h: a for h, a in state["access"].items() if a["exp"] > now}
        state["enrollments"] = {
            k: e
            for k, e in state["enrollments"].items()
            if now < e["expires"] + COLLECT_WINDOW_SECONDS + 3600
        }

    def audit(self, action: str, **fields: object) -> None:
        record = {"t": self._clock(), "action": action, **fields}
        fd = os.open(self._audit_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    # ---- administrator operations -------------------------------------------------

    def begin_pairing(
        self,
        scopes: frozenset[str] = DEFAULT_SCOPES,
        allow_remote: bool = False,
        wireguard: bool = False,
    ) -> tuple[str, str, float]:
        """Create a single-use challenge; returns (enrollment_id, challenge, expires_at)."""
        if not scopes or not scopes <= SCOPES:
            raise ValueError("unknown or empty scopes")
        eid, challenge = uuid.uuid4().hex, secrets.token_urlsafe(32)
        expires = self._clock() + CHALLENGE_TTL_SECONDS
        with self._txn() as state:
            state["enrollments"][eid] = {
                "challenge": self._hash(challenge),
                "state": "open",
                "expires": expires,
                "scopes": sorted(scopes),
                "allow_remote": allow_remote,
            }
        self.audit("pairing_started", enrollment=eid)
        return eid, challenge, expires

    def pending(self) -> list[dict[str, object]]:
        with self._txn(False) as state:
            return [
                {
                    "enrollment_id": k,
                    "name": e["name"],
                    "key_fingerprint": e["fingerprint"],
                    "scopes": e["scopes"],
                    "allow_remote": e["allow_remote"],
                }
                for k, e in state["enrollments"].items()
                if e["state"] == "submitted"
            ]

    def approve(self, eid: str) -> str:
        with self._txn() as state:
            e = state["enrollments"].get(eid)
            if e is None or e["state"] != "submitted":
                raise AuthError("not_pending")
            if self._clock() > e["collect_deadline"]:
                e["state"] = "expired"
                raise AuthError("expired")
            e["state"] = "approved"
            e["device_id"] = uuid.uuid4().hex
        self.audit("enrolment_approved", enrollment=eid, device=e["device_id"])
        return e["device_id"]

    def deny(self, eid: str) -> None:
        with self._txn() as state:
            e = state["enrollments"].get(eid)
            if e is None or e["state"] not in ("open", "submitted"):
                raise AuthError("not_pending")
            e["state"] = "denied"
        self.audit("enrolment_denied", enrollment=eid)

    def list_devices(self) -> list[dict[str, object]]:
        with self._txn(False) as state:
            return [
                {
                    "device_id": d_id,
                    "name": d["name"],
                    "enrolled_at": d["enrolled_at"],
                    "last_seen": d["last_seen"],
                    "scopes": d["scopes"],
                    "allow_remote": d["allow_remote"],
                    "revoked": d["revoked_at"] is not None,
                }
                for d_id, d in state["devices"].items()
            ]

    def revoke(self, device_id: str, reason: str = "administrator") -> None:
        with self._txn() as state:
            device = state["devices"].get(device_id)
            if device is None:
                raise AuthError("unknown_device")
            self._revoke(state, device_id, reason)

    def revoke_all(self, reason: str) -> int:
        with self._txn() as state:
            active = [i for i, d in state["devices"].items() if d["revoked_at"] is None]
            for device_id in active:
                self._revoke(state, device_id, reason)
        return len(active)

    def _revoke(self, state: dict, device_id: str, reason: str) -> None:
        device = state["devices"][device_id]
        if device["revoked_at"] is None:
            device["revoked_at"] = self._clock()
        device["refresh"] = device["refresh_prev"] = None
        state["access"] = {
            h: a for h, a in state["access"].items() if a["device"] != device_id
        }
        self.audit("device_revoked", device=device_id, reason=reason)

    def set_remote(self, device_id: str, allowed: bool) -> None:
        with self._txn() as state:
            if device_id not in state["devices"]:
                raise AuthError("unknown_device")
            state["devices"][device_id]["allow_remote"] = allowed
        self.audit("remote_access_set", device=device_id, allowed=allowed)

    def wireguard_peers(self, network: str) -> dict[str, tuple[str, str]]:
        """Active remote-permitted devices with a WireGuard key -> (public key, address).

        Addresses are allocated once from ``network`` (excluding the first host, which is
        the gateway) and persisted so a peer keeps its address when others are revoked.
        """
        import ipaddress

        net = ipaddress.ip_network(network)
        with self._txn() as state:
            used = {
                d["wg_address"]
                for d in state["devices"].values()
                if d.get("wg_address")
            }
            free = (str(h) for h in list(net.hosts())[1:] if str(h) not in used)
            peers = {}
            for device_id, d in state["devices"].items():
                if d["revoked_at"] is not None or not d["allow_remote"]:
                    continue
                if not d.get("wireguard_key"):
                    continue
                if not d.get("wg_address"):
                    try:
                        d["wg_address"] = next(free)
                    except StopIteration:
                        raise RuntimeError("WireGuard network is full") from None
                peers[device_id] = (d["wireguard_key"], d["wg_address"])
            return peers

    # ---- device-facing operations -------------------------------------------------

    def submit_enrollment(
        self,
        eid: str,
        challenge: str,
        name: str,
        public_key: str,
        signature: str,
        wireguard_key: str | None = None,
    ) -> None:
        """Consume the challenge (single use) and register a pending device key."""
        name = name.strip()
        if not 1 <= len(name) <= 64 or not name.isprintable():
            raise AuthError("bad_name")
        if not verify_signature(
            public_key, enroll_message(eid, challenge, name, public_key), signature
        ):
            raise AuthError("bad_proof")
        with self._txn() as state:
            e = state["enrollments"].get(eid)
            if e is None or e["state"] != "open":
                self.audit(
                    "enrolment_rejected", enrollment=eid, why="replayed_or_unknown"
                )
                raise AuthError("invalid_challenge")
            if self._clock() > e["expires"]:
                e["state"] = "expired"
                raise AuthError("invalid_challenge")
            if not hmac.compare_digest(e["challenge"], self._hash(challenge)):
                raise AuthError("invalid_challenge")
            e.update(
                state="submitted",
                challenge=None,
                name=name,
                public_key=public_key,
                fingerprint=hashlib.sha256(b64d(public_key)).hexdigest()[:16],
                collect_deadline=self._clock() + COLLECT_WINDOW_SECONDS,
                wireguard_key=wireguard_key,
            )
        self.audit("enrolment_submitted", enrollment=eid, name=name)

    def collect(self, eid: str, signature: str) -> tuple[str, Tokens | None]:
        """Return (state, tokens); tokens are delivered exactly once after approval."""
        with self._txn() as state:
            e = state["enrollments"].get(eid)
            if e is None or e["state"] == "open" or not e.get("public_key"):
                raise AuthError("unknown_enrollment")
            if not verify_signature(
                e["public_key"], COLLECT_CONTEXT + b"\n" + eid.encode(), signature
            ):
                raise AuthError("bad_proof")
            if (
                e["state"] in ("submitted", "approved")
                and self._clock() > e["collect_deadline"]
            ):
                e["state"] = "expired"
            if e["state"] != "approved":
                return e["state"], None
            e["state"] = "collected"
            now = self._clock()
            device_id = e["device_id"]
            state["devices"][device_id] = {
                "name": e["name"],
                "public_key": e["public_key"],
                "scopes": e["scopes"],
                "allow_remote": e["allow_remote"],
                "wireguard_key": e.get("wireguard_key"),
                "enrolled_at": now,
                "last_seen": None,
                "revoked_at": None,
                "refresh": None,
                "refresh_prev": None,
                "refresh_exp": 0,
            }
            tokens = self._issue(state, device_id)
        self.audit("device_enrolled", device=device_id, enrollment=eid)
        return "collected", tokens

    def _issue(self, state: dict, device_id: str) -> Tokens:
        device = state["devices"][device_id]
        now = self._clock()
        access = "kat_" + secrets.token_urlsafe(32)
        refresh = f"krt_{device_id}_{secrets.token_urlsafe(32)}"
        state["access"][self._hash(access)] = {
            "device": device_id,
            "exp": now + ACCESS_TTL_SECONDS,
        }
        device["refresh_prev"] = device["refresh"]
        device["refresh"] = self._hash(refresh)
        device["refresh_exp"] = now + REFRESH_TTL_SECONDS
        return Tokens(device_id, access, ACCESS_TTL_SECONDS, refresh)

    def refresh(self, refresh_token: str, signature: str) -> Tokens:
        """Rotate the refresh credential; presenting a superseded one revokes the device."""
        parts = refresh_token.split("_", 2)
        if len(parts) != 3 or parts[0] != "krt":
            raise AuthError("invalid_credential")
        device_id = parts[1]
        revoked_for_reuse = False
        with self._txn() as state:
            device = state["devices"].get(device_id)
            if device is None or device["revoked_at"] is not None:
                raise AuthError("invalid_credential")
            digest = self._hash(refresh_token)
            current = device["refresh"]
            previous = device["refresh_prev"]
            if previous is not None and hmac.compare_digest(digest, previous):
                self._revoke(state, device_id, "refresh_token_reuse")
                revoked_for_reuse = True
            elif current is None or not hmac.compare_digest(digest, current):
                raise AuthError("invalid_credential")
            elif self._clock() > device["refresh_exp"]:
                raise AuthError("expired_credential")
            elif not verify_signature(
                device["public_key"],
                REFRESH_CONTEXT + b"\n" + refresh_token.encode(),
                signature,
            ):
                raise AuthError("bad_proof")
            else:
                return self._issue(state, device_id)
        assert revoked_for_reuse
        raise AuthError("invalid_credential")

    def authenticate(
        self, access_token: str, remote: bool, remote_enabled: bool
    ) -> Device:
        with self._txn() as state:
            record = state["access"].get(self._hash(access_token))
            if record is None or record["exp"] <= self._clock():
                raise AuthError("invalid_token")
            device = state["devices"].get(record["device"])
            if device is None or device["revoked_at"] is not None:
                raise AuthError("invalid_token")
            if remote and not (remote_enabled and device["allow_remote"]):
                self.audit("remote_denied", device=record["device"])
                raise AuthError("remote_not_permitted")
            device["last_seen"] = self._clock()
            return Device(
                record["device"],
                device["name"],
                frozenset(device["scopes"]),
                device["allow_remote"],
            )

    def token_valid(self, access_token: str) -> bool:
        with self._txn(False) as state:
            record = state["access"].get(self._hash(access_token))
            if record is None or record["exp"] <= self._clock():
                return False
            device = state["devices"].get(record["device"])
            return device is not None and device["revoked_at"] is None

    def token_remote_allowed(self, access_token: str) -> bool:
        with self._txn(False) as state:
            record = state["access"].get(self._hash(access_token))
            device = state["devices"].get(record["device"]) if record else None
            return bool(device and device["allow_remote"])
