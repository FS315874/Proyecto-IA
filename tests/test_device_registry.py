import json
import tempfile
import unittest
from pathlib import Path

from desktop_agent.device_registry import (
    DeviceRegistry,
    DeviceRegistryError,
    DeviceStatus,
    pairing_proof,
)
from desktop_agent.remote_protocol import (
    RemoteCodec,
    RemoteDirection,
    RemoteMessage,
    RemoteMessageType,
    StatusPayload,
)
from desktop_agent.windows_secrets import WindowsDpapiProtector


class FakeProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return b"protected:" + plaintext[::-1]

    def unprotect(self, protected: bytes) -> bytes:
        if not protected.startswith(b"protected:"):
            raise RuntimeError("invalid")
        return protected.removeprefix(b"protected:")[::-1]


class MutableClock:
    def __init__(self, value=1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class DeviceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = MutableClock()
        self.path = Path(self.temp.name) / "devices.json"
        self.registry = DeviceRegistry(
            self.path,
            FakeProtector(),
            self.clock,
            secret_factory=lambda size: b"s" * size,
            id_factory=lambda: "device-1",
        )

    def pair(self):
        bundle = self.registry.begin_pairing("Mi teléfono")
        summary = self.registry.complete_pairing(
            bundle.device_id,
            bundle.proof(),
            approved=True,
        )
        return bundle, summary

    def envelope(self, secret: bytes, sequence=1, message_id="msg-1"):
        return RemoteCodec(
            RemoteDirection.CLIENT_TO_AGENT,
            nonce_factory=lambda size: b"n" * size,
            message_id_factory=lambda: message_id,
            clock=self.clock,
        ).encrypt(
            "device-1",
            secret,
            sequence,
            RemoteMessage(
                RemoteMessageType.STATUS,
                "request-1",
                StatusPayload(),
            ),
        )

    def test_pairing_requires_phone_proof_and_local_approval(self) -> None:
        bundle = self.registry.begin_pairing("Mi teléfono")
        with self.assertRaisesRegex(DeviceRegistryError, "aprobación"):
            self.registry.complete_pairing(
                bundle.device_id,
                bundle.proof(),
                approved=False,
            )
        with self.assertRaisesRegex(DeviceRegistryError, "no coincide"):
            self.registry.complete_pairing(
                bundle.device_id,
                "0" * 64,
                approved=True,
            )
        summary = self.registry.complete_pairing(
            bundle.device_id,
            bundle.proof(),
            approved=True,
        )
        self.assertEqual(summary.status, DeviceStatus.ACTIVE)

    def test_pairing_expires(self) -> None:
        bundle = self.registry.begin_pairing("Mi teléfono")
        self.clock.value = bundle.expires_at
        with self.assertRaisesRegex(DeviceRegistryError, "expiró"):
            self.registry.complete_pairing(
                bundle.device_id,
                bundle.proof(),
                approved=True,
            )
        self.assertEqual(
            self.registry.list_devices()[0].status,
            DeviceStatus.REVOKED,
        )

    def test_registry_never_persists_plain_secret(self) -> None:
        bundle, _summary = self.pair()
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn(bundle.secret.hex(), raw)
        self.assertNotIn(bundle.secret.decode("ascii"), raw)
        self.assertNotIn("proof", raw)

    def test_claim_rejects_replay_and_old_sequence(self) -> None:
        bundle, _summary = self.pair()
        first = self.envelope(bundle.secret)
        self.registry.claim_inbound(first)

        with self.assertRaisesRegex(DeviceRegistryError, "secuencia"):
            self.registry.claim_inbound(first)

    def test_claim_rejects_stale_message(self) -> None:
        bundle, _summary = self.pair()
        envelope = self.envelope(bundle.secret)
        self.clock.value += 61
        with self.assertRaisesRegex(DeviceRegistryError, "tiempo"):
            self.registry.claim_inbound(envelope)

    def test_outbound_sequence_persists(self) -> None:
        self.pair()
        self.assertEqual(self.registry.reserve_outbound_sequence("device-1"), 1)
        reloaded = DeviceRegistry(self.path, FakeProtector(), self.clock)
        self.assertEqual(reloaded.reserve_outbound_sequence("device-1"), 2)

    def test_revocation_erases_secret_and_blocks_access(self) -> None:
        self.pair()
        self.registry.revoke("device-1")
        with self.assertRaisesRegex(DeviceRegistryError, "no está activo"):
            self.registry.active_secret("device-1")
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsNone(state["devices"][0]["protected_secret"])

    def test_pairing_proof_is_bound_to_device(self) -> None:
        first = pairing_proof("device-1", b"s" * 32)
        second = pairing_proof("device-2", b"s" * 32)
        self.assertNotEqual(first, second)


@unittest.skipUnless(__import__("os").name == "nt", "DPAPI requiere Windows")
class WindowsDpapiTests(unittest.TestCase):
    def test_current_user_round_trip(self) -> None:
        protector = WindowsDpapiProtector()
        secret = bytes(range(32))
        protected = protector.protect(secret)
        self.assertNotEqual(protected, secret)
        self.assertEqual(protector.unprotect(protected), secret)
