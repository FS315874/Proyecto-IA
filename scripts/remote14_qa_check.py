"""Aceptación local cifrada de v0.14 sin red ni efectos de escritorio."""

import io
import logging
import tempfile
import time
from pathlib import Path

from desktop_agent.command_processor import (
    CommandExecution,
    CommandProgress,
    CommandStage,
)
from desktop_agent.device_registry import DeviceRegistry
from desktop_agent.executor import ActionExecutor
from desktop_agent.interpretation import (
    InterpretationPath,
    InterpretationResult,
    InterpretationStatus,
)
from desktop_agent.local_controller import LocalAgentController
from desktop_agent.local_service import DesktopAgentService, PolicyActionCoordinator
from desktop_agent.models import Action, Intent, RiskLevel, ToolResult
from desktop_agent.permissions import (
    CapabilityRule,
    ConfirmationChannel,
    Effect,
    PermissionBroker,
    PolicyRegistry,
    PolicySubject,
)
from desktop_agent.remote_gateway import RemoteGateway
from desktop_agent.remote_protocol import (
    CommandPayload,
    ConfirmationPayload,
    RemoteCodec,
    RemoteDirection,
    RemoteMessage,
    RemoteMessageType,
    RemoteResponseStatus,
    StatusPayload,
)


class _Protector:
    def protect(self, plaintext: bytes) -> bytes:
        return b"qa-protected:" + plaintext[::-1]

    def unprotect(self, protected: bytes) -> bytes:
        return protected.removeprefix(b"qa-protected:")[::-1]


class _Processor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command, output=print, progress=None):
        self.commands.append(command)
        if progress is not None:
            progress(CommandProgress(CommandStage.INTERPRETING))
            progress(CommandProgress(CommandStage.EXECUTING, "qa_remote_tool"))
        interpretation = InterpretationResult(
            None,
            InterpretationPath.DETERMINISTIC,
            InterpretationStatus.SUCCESS,
            1.0,
            False,
        )
        return CommandExecution(
            True,
            ("Resultado ficticio comprobado.",),
            interpretation,
            2.0,
            ToolResult(True, "Resultado ficticio comprobado."),
            "qa_remote_tool",
        )


class _Playback:
    @property
    def has_active_session(self):
        return False

    def stop(self):
        return ToolResult(True, "Sin reproducción.")


def _wait_for_result(service: DesktopAgentService) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        service.poll()
        if service.snapshot.history[-1].state.terminal:
            return
        time.sleep(0.01)
    raise AssertionError("La orden remota simulada no terminó.")


def main() -> None:
    stream = io.StringIO()
    logger = logging.Logger("remote14-qa")
    logger.addHandler(logging.StreamHandler(stream))
    processor = _Processor()
    effects: list[str] = []
    rules = (
        CapabilityRule(
            "modify-test-data",
            Effect.MODIFY_LOCAL_DATA,
            (Intent.MODIFY_LOCAL_DATA,),
            ("qa_modify",),
            ("target", "content"),
            "target",
        ),
    )
    broker = PermissionBroker(PolicyRegistry(rules), logger)

    def modify(**arguments: str) -> ToolResult:
        effects.append(arguments["target"])
        return ToolResult(True, "Dato ficticio creado.")

    executor = ActionExecutor({"qa_modify": modify}, logger, broker)
    coordinator = PolicyActionCoordinator(broker, executor)
    controller = LocalAgentController(processor, _Playback(), logger)
    service = DesktopAgentService(controller, logger, coordinator)

    with tempfile.TemporaryDirectory() as directory:
        registry = DeviceRegistry(
            Path(directory) / "devices.json",
            _Protector(),
            secret_factory=lambda size: b"q" * size,
            id_factory=lambda: "device-qa",
        )
        bundle = registry.begin_pairing("Teléfono QA")
        registry.complete_pairing(
            bundle.device_id,
            bundle.proof(),
            approved=True,
        )
        gateway = RemoteGateway(service, registry, logger)
        inbound = RemoteCodec(RemoteDirection.CLIENT_TO_AGENT)
        outbound = RemoteCodec(RemoteDirection.AGENT_TO_CLIENT)
        sequence = 1

        command = RemoteMessage(
            RemoteMessageType.COMMAND,
            "request-command",
            CommandPayload("abrir youtube"),
        )
        envelope = inbound.encrypt(
            bundle.device_id,
            bundle.secret,
            sequence,
            command,
        )
        response_envelope = gateway.handle(envelope)
        assert response_envelope is not None
        response = outbound.decrypt(response_envelope, bundle.secret)
        assert response.payload.status is RemoteResponseStatus.ACCEPTED
        _wait_for_result(service)
        assert processor.commands == ["abrir youtube"]

        assert gateway.handle(envelope) is None
        assert processor.commands == ["abrir youtube"]

        subject = PolicySubject(
            "policy-remote-qa",
            "modify-test-data",
            Action(
                Intent.MODIFY_LOCAL_DATA,
                "qa_modify",
                {"target": "record-qa-1", "content": "Ficticio"},
                RiskLevel.CAUTION,
                True,
            ),
        )
        service.submit_policy_action(
            subject,
            (ConfirmationChannel.LOCAL, ConfirmationChannel.REMOTE),
        )
        sequence += 1
        status_envelope = inbound.encrypt(
            bundle.device_id,
            bundle.secret,
            sequence,
            RemoteMessage(
                RemoteMessageType.STATUS,
                "request-status",
                StatusPayload(),
            ),
        )
        status_response = gateway.handle(status_envelope)
        assert status_response is not None
        status_message = outbound.decrypt(status_response, bundle.secret)
        pending = status_message.payload.pending_confirmation
        assert pending is not None

        sequence += 1
        decision_envelope = inbound.encrypt(
            bundle.device_id,
            bundle.secret,
            sequence,
            RemoteMessage(
                RemoteMessageType.CONFIRMATION,
                "request-confirm",
                ConfirmationPayload(
                    pending.task_id,
                    pending.confirmation_request_id,
                    pending.challenge,
                    pending.subject_fingerprint,
                    True,
                ),
            ),
        )
        decision_response = gateway.handle(decision_envelope)
        assert decision_response is not None
        decoded_decision = outbound.decrypt(decision_response, bundle.secret)
        assert decoded_decision.payload.status is RemoteResponseStatus.ACCEPTED
        assert effects == ["record-qa-1"]

        registry.revoke(bundle.device_id)
        sequence += 1
        revoked = inbound.encrypt(
            bundle.device_id,
            bundle.secret,
            sequence,
            RemoteMessage(
                RemoteMessageType.STATUS,
                "request-revoked",
                StatusPayload(),
            ),
        )
        assert gateway.handle(revoked) is None
        log = stream.getvalue()
        assert "abrir youtube" not in log
        assert bundle.secret.hex() not in log
        assert "Ficticio" not in log

    assert service.close(2)
    print("v0.14 QA OK: pairing, E2E, replay, confirmación y revocación.")


if __name__ == "__main__":
    main()
