import json
import os
import pathlib
import stat
import tempfile
import unittest

from kenpi.security.devices import (
    ACCESS_TTL_SECONDS,
    CHALLENGE_TTL_SECONDS,
    AuthError,
    DeviceRegistry,
    RateLimiter,
)
from tests.gateway_helpers import DeviceKey


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock = Clock()
        self.reg = DeviceRegistry(self._tmp.name, self.clock)
        self.key = DeviceKey()

    def pair(self, name="Pixel", approve=True, **kw):
        eid, challenge, _ = self.reg.begin_pairing(**kw)
        self.reg.submit_enrollment(
            eid,
            challenge,
            name,
            self.key.public,
            self.key.enroll_proof(eid, challenge, name),
        )
        if approve:
            self.reg.approve(eid)
        return eid, challenge

    def collect(self, eid):
        return self.reg.collect(eid, self.key.collect_proof(eid))

    def test_full_enrolment_and_authentication(self):
        eid, _ = self.pair()
        state, tokens = self.collect(eid)
        self.assertEqual(state, "collected")
        device = self.reg.authenticate(tokens.access_token, False, False)
        self.assertEqual(device.name, "Pixel")
        self.assertEqual(self.collect(eid)[1], None)  # delivered exactly once

    def test_unapproved_enrolment_gets_no_credentials(self):
        eid, _ = self.pair(approve=False)
        self.assertEqual(self.collect(eid), ("submitted", None))

    def test_challenge_is_single_use(self):
        eid, challenge = self.pair()
        with self.assertRaises(AuthError) as ctx:
            self.reg.submit_enrollment(
                eid, challenge, "Pixel", self.key.public,
                self.key.enroll_proof(eid, challenge, "Pixel"),
            )  # fmt: skip
        self.assertEqual(ctx.exception.code, "invalid_challenge")

    def test_expired_challenge_rejected(self):
        eid, challenge, _ = self.reg.begin_pairing()
        self.clock.now += CHALLENGE_TTL_SECONDS + 1
        with self.assertRaises(AuthError):
            self.reg.submit_enrollment(
                eid,
                challenge,
                "P",
                self.key.public,
                self.key.enroll_proof(eid, challenge, "P"),
            )

    def test_wrong_key_proof_rejected(self):
        eid, challenge, _ = self.reg.begin_pairing()
        other = DeviceKey()
        with self.assertRaises(AuthError) as ctx:
            self.reg.submit_enrollment(
                eid,
                challenge,
                "P",
                self.key.public,
                other.enroll_proof(eid, challenge, "P"),
            )
        self.assertEqual(ctx.exception.code, "bad_proof")

    def test_wrong_challenge_with_valid_signature_rejected(self):
        eid, _, _ = self.reg.begin_pairing()
        with self.assertRaises(AuthError):
            self.reg.submit_enrollment(
                eid,
                "guess",
                "P",
                self.key.public,
                self.key.enroll_proof(eid, "guess", "P"),
            )

    def test_collect_requires_device_key(self):
        eid, _ = self.pair()
        with self.assertRaises(AuthError):
            self.reg.collect(eid, DeviceKey().collect_proof(eid))

    def test_access_token_expires(self):
        _, tokens = self.collect(self.pair()[0])
        self.clock.now += ACCESS_TTL_SECONDS + 1
        with self.assertRaises(AuthError):
            self.reg.authenticate(tokens.access_token, False, False)

    def test_revocation_is_immediate_and_per_device(self):
        _, a = self.collect(self.pair("A")[0])
        self.key = DeviceKey()
        _, b = self.collect(self.pair("B")[0])
        self.reg.revoke(a.device_id)
        with self.assertRaises(AuthError):
            self.reg.authenticate(a.access_token, False, False)
        self.reg.authenticate(b.access_token, False, False)
        with self.assertRaises(AuthError):
            self.reg.refresh(a.refresh_token, self.key.refresh_proof(a.refresh_token))

    def test_refresh_rotates_and_reuse_revokes_device(self):
        _, first = self.collect(self.pair()[0])
        second = self.reg.refresh(
            first.refresh_token, self.key.refresh_proof(first.refresh_token)
        )
        self.assertNotEqual(first.refresh_token, second.refresh_token)
        with self.assertRaises(AuthError):  # replay of the superseded credential
            self.reg.refresh(
                first.refresh_token, self.key.refresh_proof(first.refresh_token)
            )
        with self.assertRaises(AuthError):  # whole family is now revoked
            self.reg.authenticate(second.access_token, False, False)
        with self.assertRaises(AuthError):
            self.reg.refresh(
                second.refresh_token, self.key.refresh_proof(second.refresh_token)
            )

    def test_refresh_requires_device_signature(self):
        _, tokens = self.collect(self.pair()[0])
        with self.assertRaises(AuthError) as ctx:
            self.reg.refresh(
                tokens.refresh_token, DeviceKey().refresh_proof(tokens.refresh_token)
            )
        self.assertEqual(ctx.exception.code, "bad_proof")

    def test_remote_policy(self):
        _, tokens = self.collect(self.pair()[0])
        with self.assertRaises(AuthError):
            self.reg.authenticate(tokens.access_token, True, True)  # device not allowed
        self.reg.set_remote(tokens.device_id, True)
        with self.assertRaises(AuthError):
            self.reg.authenticate(tokens.access_token, True, False)  # remote disabled
        self.reg.authenticate(tokens.access_token, True, True)

    def test_secrets_stored_hashed_with_private_permissions(self):
        _, tokens = self.collect(self.pair()[0])
        text = pathlib.Path(self._tmp.name, "devices.json").read_text()
        self.assertNotIn(tokens.access_token, text)
        self.assertNotIn(tokens.refresh_token, text)
        for name in ("devices.json", "secret.key"):
            mode = stat.S_IMODE(os.stat(os.path.join(self._tmp.name, name)).st_mode)
            self.assertEqual(mode, 0o600)

    def test_state_survives_restart(self):
        _, tokens = self.collect(self.pair()[0])
        again = DeviceRegistry(self._tmp.name, self.clock)
        again.authenticate(tokens.access_token, False, False)
        self.assertEqual(again.list_devices()[0]["name"], "Pixel")

    def test_revoke_all_and_listing(self):
        self.collect(self.pair()[0])
        self.assertEqual(self.reg.revoke_all("restore"), 1)
        self.assertTrue(self.reg.list_devices()[0]["revoked"])

    def test_deny_and_bad_scopes(self):
        eid, _, _ = self.reg.begin_pairing()
        self.reg.deny(eid)
        with self.assertRaises(ValueError):
            self.reg.begin_pairing(frozenset({"camera.write"}))

    def test_audit_log_has_no_secrets(self):
        _, tokens = self.collect(self.pair()[0])
        text = pathlib.Path(self._tmp.name, "audit.log").read_text()
        self.assertIn("device_enrolled", text)
        self.assertNotIn(tokens.refresh_token, text)
        for line in text.splitlines():
            json.loads(line)


class RateLimiterTests(unittest.TestCase):
    def test_window(self):
        now = [0.0]
        limiter = RateLimiter(2, 10, lambda: now[0])
        self.assertTrue(limiter.allow("a") and limiter.allow("a"))
        self.assertFalse(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))
        now[0] = 11
        self.assertTrue(limiter.allow("a"))


if __name__ == "__main__":
    unittest.main()
