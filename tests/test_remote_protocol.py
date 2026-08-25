import dataclasses
import unittest

from desktop_agent.remote_protocol import (
    CancelPayload,
    CommandPayload,
    EncryptedEnvelope,
    RemoteCodec,
    RemoteDirection,
    RemoteMessage,
    RemoteMessageType,
    RemoteProtocolError,
    StatusPayload,
)


SECRET = bytes(range(32))


class RemoteProtocolTests(unittest.TestCase):
    def codec(self, direction=RemoteDirection.CLIENT_TO_AGENT):
        return RemoteCodec(
            direction,
            nonce_factory=lambda size: b"n" * size,
            message_id_factory=lambda: "msg-1",
            clock=lambda: 1000,
        )

    def test_authenticated_round_trip(self) -> None:
        message = RemoteMessage(
            RemoteMessageType.COMMAND,
            "request-1",
            CommandPayload("  abrir   youtube "),
        )
        envelope = self.codec().encrypt("device-1", SECRET, 1, message)

        decoded = self.codec().decrypt(envelope, SECRET)

        self.assertEqual(decoded.payload.command, "abrir youtube")
        self.assertNotIn("abrir", str(envelope.to_dict()))
        self.assertNotIn(SECRET.hex(), str(envelope.to_dict()))

    def test_directions_derive_different_keys(self) -> None:
        message = RemoteMessage(
            RemoteMessageType.STATUS,
            "request-1",
            StatusPayload(),
        )
        envelope = self.codec().encrypt("device-1", SECRET, 1, message)

        with self.assertRaisesRegex(RemoteProtocolError, "auténtico"):
            self.codec(RemoteDirection.AGENT_TO_CLIENT).decrypt(envelope, SECRET)

    def test_tampered_header_is_rejected(self) -> None:
        message = RemoteMessage(
            RemoteMessageType.CANCEL,
            "request-1",
            CancelPayload("task-1"),
        )
        envelope = self.codec().encrypt("device-1", SECRET, 1, message)
        tampered = dataclasses.replace(envelope, sequence=2)

        with self.assertRaisesRegex(RemoteProtocolError, "auténtico"):
            self.codec().decrypt(tampered, SECRET)

    def test_wrong_secret_is_rejected(self) -> None:
        message = RemoteMessage(
            RemoteMessageType.STATUS,
            "request-1",
            StatusPayload(),
        )
        envelope = self.codec().encrypt("device-1", SECRET, 1, message)

        with self.assertRaises(RemoteProtocolError):
            self.codec().decrypt(envelope, b"x" * 32)

    def test_sensitive_command_is_rejected(self) -> None:
        for command in (
            "mi password es demo",
            "usar api_key abc",
            "abrir https://user:pass@example.test",
        ):
            with self.subTest(command=command), self.assertRaises(
                RemoteProtocolError
            ):
                CommandPayload(command)

    def test_envelope_rejects_extra_fields(self) -> None:
        value = {
            "schema_version": 1,
            "device_id": "device-1",
            "message_id": "msg-1",
            "sequence": 1,
            "issued_at": 1000,
            "nonce": "bm5ubm5ubm5ubm5u",
            "ciphertext": "eA==",
            "extra": True,
        }
        with self.assertRaises(RemoteProtocolError):
            EncryptedEnvelope.from_dict(value)

    def test_payload_type_must_match_message_type(self) -> None:
        with self.assertRaises(RemoteProtocolError):
            RemoteMessage(
                RemoteMessageType.STATUS,
                "request-1",
                CancelPayload("task-1"),
            )

    def test_nonce_must_be_unique_sized(self) -> None:
        codec = RemoteCodec(
            RemoteDirection.CLIENT_TO_AGENT,
            nonce_factory=lambda _size: b"short",
        )
        with self.assertRaises(RemoteProtocolError):
            codec.encrypt(
                "device-1",
                SECRET,
                1,
                RemoteMessage(
                    RemoteMessageType.STATUS,
                    "request-1",
                    StatusPayload(),
                ),
            )
