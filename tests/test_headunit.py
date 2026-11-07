import asyncio
import json
import unittest
from urllib.request import urlopen

from kenpi.api.app import KenpiApiService
from kenpi.core.config import KenpiConfig
from kenpi.core.errors import ConfigurationError
from kenpi.core.models import EvidenceLevel
from kenpi.headunit import PROFILES, HeadUnitProfile, TouchPath, get_profile


class HeadUnitProfileTests(unittest.TestCase):
    def test_default_config_selects_known_profile(self) -> None:
        self.assertIn(KenpiConfig().head_unit, PROFILES)

    def test_unknown_head_unit_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            KenpiConfig(head_unit="nope")

    def test_pioneer_profile_selectable(self) -> None:
        config = KenpiConfig(head_unit="pioneer-dmh-w4660nex")
        self.assertEqual(get_profile(config.head_unit).brand, "Pioneer")

    def test_no_profile_claims_verification_or_touch(self) -> None:
        for profile in PROFILES.values():
            with self.subTest(profile=profile.key):
                self.assertLess(profile.evidence, EvidenceLevel.E4)
                self.assertIs(profile.touch_path, TouchPath.NONE)

    def test_verified_evidence_is_rejected_in_registry(self) -> None:
        with self.assertRaises(ValueError):
            HeadUnitProfile(
                "x", "B", "M", (), TouchPath.NONE, True, None, EvidenceLevel.E4, "n"
            )


class HeadUnitApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_head_units_route_lists_profiles(self) -> None:
        service = KenpiApiService(port=0, head_unit="pioneer-dmh-w4660nex")
        await service.start()
        try:
            response = await asyncio.to_thread(
                urlopen, f"http://127.0.0.1:{service.bound_port}/api/v1/head-units"
            )
            payload = json.loads(response.read())
        finally:
            await service.stop()
        self.assertEqual(payload["selected"], "pioneer-dmh-w4660nex")
        self.assertEqual(len(payload["head_units"]), len(PROFILES))
