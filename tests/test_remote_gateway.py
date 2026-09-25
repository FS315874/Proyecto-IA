import io
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from desktop_agent.device_registry import DeviceRegistry
from desktop_agent.local_service import ServiceError, ServiceState
from desktop_agent.models import RiskLevel
from desktop_agent.permissions import (
    AuthorizationStatus,
    ConfirmationChannel,
    Effect,
)
from desktop_agent.remote_gateway import RemoteGateway
from desktop_agent.remote_protocol import (
    CancelPayload,
    CommandPayload,
    ConfirmationPayload,
    RemoteCodec,
    RemoteDirection,
    RemoteMessage,
    RemoteMessageType,
    RemoteResponseStatus,
    StatusPayload,
)


class FakeProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return b"x" + plaintext

    def unprotect(self, protected: bytes) -> bytes:
        return protected[1:]


class FakeService:
    def __init__(self) -> None:
        self.commands = []
        self.cancelled = []
        self.decisions = []
        self.history = []
        self.pending = None
        self.state = ServiceState.ACTIVE
        self.fail_submit = False

    @property
    def snapshot(self):
        return SimpleNamespace(
            state=self.state,
            history=tuple(self.history),
            pending_confirmation=self.pending,
        )

    def submit(self, command: str) -> str:
        if self.fail_submit:
            raise ServiceError("private details")
        self.commands.append(command)
        return f"task-{len(self.commands)}"

    def cancel_task(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        return task_id == "task-1"

    def resolve_confirmation(self, task_id, decision):
        self.decisions.append((task_id, decision))
        self.pending = None
        status = (
            AuthorizationStatus.AUTHORIZED
            if decision.approved
            else AuthorizationStatus.REJECTED
        )
        return SimpleNamespace(authorization=SimpleNamespace(status=status))


class RemoteGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock_value = 1000.0
        clock = lambda: self.clock_value
        self.registry = DeviceRegistry(
            Path(self.temp.name) / "devices.json",
            FakeProtector(),
            clock,
            secret_factory=lambda size: b"s" * size,
            id_factory=lambda: "device-1",
        )
        bundle = self.registry.begin_pairing("Teléfono QA")
        self.secret = bundle.secret
        self.registry.complete_pairing(
            bundle.device_id,
            bundle.proof(),
            approved=True,
        )
        self.service = FakeService()
        self.stream = io.StringIO()
        self.logger = logging.Logger(self.id())
        self.logger.addHandler(logging.StreamHandler(self.stream))
        self.gateway = RemoteGateway(
            self.service,
            self.registry,
            self.logger,
            clock,
        )
        self.sequence = 0

    def send(self, message_type, payload):
        self.sequence += 1
        codec = RemoteCodec(
            RemoteDirection.CLIENT_TO_AGENT,
            message_id_factory=lambda: f"msg-{self.sequence}",
            clock=lambda: self.clock_value,
        )
        request = RemoteMessage(
            message_type,
            f"request-{self.sequence}",
            payload,
        )
        envelope = codec.encrypt(
            "device-1",
            self.secret,
            self.sequence,
            request,
        )
        response_envelope = self.gateway.handle(envelope)
        if response_envelope is None:
            return envelope, None
        response = RemoteCodec(
            RemoteDirection.AGENT_TO_CLIENT
        ).decrypt(response_envelope, self.secret)
        return envelope, response.payload

    def test_command_uses_local_service_and_returns_receipt(self) -> None:
        _envelope, response = self.send(
            RemoteMessageType.COMMAND,
            CommandPayload("abrir youtube"),
        )
        self.assertEqual(self.service.commands, ["abrir youtube"])
        self.assertEqual(response.status, RemoteResponseStatus.ACCEPTED)
        self.assertEqual(response.task_id, "task-1")

    def test_authenticated_replay_has_no_second_effect_or_response(self) -> None:
        envelope, response = self.send(
            RemoteMessageType.COMMAND,
            CommandPayload("abrir youtube"),
        )
        replay_response = self.gateway.handle(envelope)
        self.assertIsNotNone(response)
        self.assertIsNone(replay_response)
        self.assertEqual(self.service.commands, ["abrir youtube"])

    def test_revoked_device_has_no_effect(self) -> None:
        self.registry.revoke("device-1")
        _envelope, response = self.send(
            RemoteMessageType.COMMAND,
            CommandPayload("abrir youtube"),
        )
        self.assertIsNone(response)
        self.assertEqual(self.service.commands, [])

    def test_status_omits_command_labels_and_results(self) -> None:
        self.service.history.append(
            SimpleNamespace(
                task_id="task-1",
                state=SimpleNamespace(value="succeeded"),
                active_tool="open_browser",
                label="texto privado",
                result="resultado privado",
            )
        )
        _envelope, response = self.send(
            RemoteMessageType.STATUS,
            StatusPayload(),
        )
        self.assertEqual(response.status, RemoteResponseStatus.STATUS)
        self.assertEqual(response.tasks[0].tool_name, "open_browser")
        self.assertNotIn("privado", repr(response))

    def test_remote_cancel_is_bounded_to_exact_task(self) -> None:
        _envelope, response = self.send(
            RemoteMessageType.CANCEL,
            CancelPayload("task-1"),
        )
        self.assertEqual(response.status, RemoteResponseStatus.CANCELLED)
        self.assertEqual(self.service.cancelled, ["task-1"])

    def test_remote_confirmation_must_match_and_be_allowed(self) -> None:
        request = SimpleNamespace(
            request_id="confirm-1",
            challenge="challenge-1",
            subject_fingerprint="a" * 64,
            effect=Effect.SEND_EXTERNAL_DATA,
            risk_level=RiskLevel.DANGEROUS,
            destination="example.test",
            allowed_channels=(ConfirmationChannel.REMOTE,),
        )
        self.service.pending = SimpleNamespace(task_id="policy-1", request=request)
        payload = ConfirmationPayload(
            "policy-1",
            "confirm-1",
            "challenge-1",
            "a" * 64,
            True,
        )
        _envelope, response = self.send(RemoteMessageType.CONFIRMATION, payload)
        self.assertEqual(response.status, RemoteResponseStatus.ACCEPTED)
        self.assertEqual(
            self.service.decisions[0][1].channel,
            ConfirmationChannel.REMOTE,
        )

    def test_local_only_confirmation_is_not_exposed_or_accepted(self) -> None:
        request = SimpleNamespace(
            request_id="confirm-1",
            challenge="challenge-1",
            subject_fingerprint="a" * 64,
            effect=Effect.SEND_EXTERNAL_DATA,
            risk_level=RiskLevel.DANGEROUS,
            destination=None,
            allowed_channels=(ConfirmationChannel.LOCAL,),
        )
        self.service.pending = SimpleNamespace(task_id="policy-1", request=request)
        _envelope, status = self.send(
            RemoteMessageType.STATUS,
            StatusPayload(),
        )
        self.assertIsNone(status.pending_confirmation)
        _envelope, response = self.send(
            RemoteMessageType.CONFIRMATION,
            ConfirmationPayload(
                "policy-1",
                "confirm-1",
                "challenge-1",
                "a" * 64,
                True,
            ),
        )
        self.assertEqual(response.status, RemoteResponseStatus.REJECTED)
        self.assertEqual(self.service.decisions, [])

    def test_rate_limit_rejects_eleventh_command(self) -> None:
        responses = [
            self.send(
                RemoteMessageType.COMMAND,
                CommandPayload(f"abrir recurso {index}"),
            )[1]
            for index in range(11)
        ]
        self.assertEqual(len(self.service.commands), 10)
        self.assertEqual(responses[-1].reason, "rate_limited")

    def test_logs_contain_metadata_but_not_command_or_secret(self) -> None:
        self.send(
            RemoteMessageType.COMMAND,
            CommandPayload("abrir youtube"),
        )
        log = self.stream.getvalue()
        self.assertIn("device=device-1", log)
        self.assertIn("type=command", log)
        self.assertNotIn("abrir youtube", log)
        self.assertNotIn(self.secret.hex(), log)

    def test_service_errors_are_redacted(self) -> None:
        self.service.fail_submit = True
        _envelope, response = self.send(
            RemoteMessageType.COMMAND,
            CommandPayload("abrir youtube"),
        )
        self.assertEqual(response.reason, "service_unavailable")
        self.assertNotIn("private", repr(response))
