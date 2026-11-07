import tempfile
import unittest
from pathlib import Path

from kenpi.core.config import KenpiConfig, load_config
from kenpi.core.errors import ConfigurationError
from kenpi.core.models import CapabilityState, EvidenceLevel, OperationResult
from kenpi.core.capabilities import Capability, INITIAL_CAPABILITIES


class ConfigurationTests(unittest.TestCase):
    def test_production_binds_loopback_by_default(self) -> None:
        self.assertEqual(KenpiConfig().api_host, "127.0.0.1")

    def test_production_rejects_public_bind(self) -> None:
        with self.assertRaises(ConfigurationError):
            KenpiConfig(api_host="0.0.0.0")

    def test_rejects_wrong_toml_value_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kenpi.toml"
            path.write_text("[kenpi]\napi_port = true\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_config(path)

    def test_initial_device_capabilities_are_not_claimed_available(self) -> None:
        self.assertTrue(
            all(item.state is not CapabilityState.AVAILABLE for item in INITIAL_CAPABILITIES)
        )

    def test_available_capability_requires_reproduced_evidence(self) -> None:
        with self.assertRaises(ValueError):
            Capability("camera", CapabilityState.AVAILABLE, EvidenceLevel.E3, "not reproduced")


class OperationResultTests(unittest.TestCase):
    def test_rejects_response_without_transmission(self) -> None:
        with self.assertRaises(ValueError):
            OperationResult(request_created=True, response_received=True)

    def test_does_not_infer_acknowledgement_or_verification(self) -> None:
        result = OperationResult(request_created=True, transmitted=True, response_received=True)
        self.assertFalse(result.acknowledged)
        self.assertIsNone(result.state_verified)


if __name__ == "__main__":
    unittest.main()
