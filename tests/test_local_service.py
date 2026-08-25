import io
import logging
import threading
import time
import unittest

from desktop_agent.command_processor import (
    CommandExecution,
    CommandProgress,
    CommandStage,
)
from desktop_agent.executor import ActionExecutor
from desktop_agent.interpretation import (
    InterpretationPath,
    InterpretationResult,
    InterpretationStatus,
)
from desktop_agent.local_controller import LocalAgentController
from desktop_agent.local_service import (
    DesktopAgentService,
    PolicyActionCoordinator,
    ServiceError,
    ServiceState,
    TaskState,
)
from desktop_agent.models import Action, Intent, RiskLevel, ToolResult
from desktop_agent.permissions import (
    CapabilityRule,
    ConfirmationChannel,
    ConfirmationDecision,
    Effect,
    PermissionBroker,
    PolicyRegistry,
    PolicySubject,
)
from desktop_agent.windows_session import SessionAvailability


def command_execution(
    success: bool = True,
    message: str = "Completado.",
) -> CommandExecution:
    interpretation = InterpretationResult(
        action=None,
        path=InterpretationPath.DETERMINISTIC,
        status=InterpretationStatus.SUCCESS,
        duration_ms=1.0,
        provider_configured=False,
    )
    return CommandExecution(
        success,
        (message,),
        interpretation,
        2.0,
        ToolResult(success, message) if success else None,
        "qa_command_tool",
    )


class FakeProcessor:
    def __init__(
        self,
        result: CommandExecution | None = None,
        blocking: bool = False,
    ) -> None:
        self.result = result or command_execution()
        self.blocking = blocking
        self.started = threading.Event()
        self.release = threading.Event()
        self.commands: list[str] = []

    def execute(self, command, output=print, progress=None):
        self.commands.append(command)
        if progress is not None:
            progress(CommandProgress(CommandStage.INTERPRETING))
            progress(CommandProgress(CommandStage.EXECUTING, "qa_command_tool"))
        self.started.set()
        if self.blocking:
            self.release.wait(2)
        if progress is not None:
            progress(CommandProgress(CommandStage.FINISHED, "qa_command_tool"))
        return self.result


class FakePlayback:
    def __init__(self) -> None:
        self.active = False
        self.stop_calls = 0

    @property
    def has_active_session(self) -> bool:
        return self.active

    def stop(self) -> ToolResult:
        self.stop_calls += 1
        self.active = False
        return ToolResult(True, "Detenido.")


class FakeSessionMonitor:
    def __init__(
        self,
        availability: SessionAvailability = SessionAvailability.AVAILABLE,
    ) -> None:
        self.availability = availability
        self.calls = 0

    def current(self) -> SessionAvailability:
        self.calls += 1
        return self.availability


class MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ServiceTestSupport:
    def setUp(self) -> None:
        self.stream = io.StringIO()
        self.logger = logging.Logger(self.id())
        self.logger.addHandler(logging.StreamHandler(self.stream))
        self.controllers: list[LocalAgentController] = []
        self.services: list[DesktopAgentService] = []

    def tearDown(self) -> None:
        for service in self.services:
            service.close(2)

    def build_service(
        self,
        processor: FakeProcessor | None = None,
        *,
        history_limit: int = 100,
        monitor: FakeSessionMonitor | None = None,
        clock=None,
        policy: PolicyActionCoordinator | None = None,
    ) -> tuple[DesktopAgentService, FakeProcessor, FakePlayback]:
        selected_processor = processor or FakeProcessor()
        playback = FakePlayback()
        controller = LocalAgentController(
            selected_processor,
            playback,
            self.logger,
        )
        service = DesktopAgentService(
            controller,
            self.logger,
            policy,
            monitor,
            history_limit,
            1.0,
            clock or time.monotonic,
        )
        self.controllers.append(controller)
        self.services.append(service)
        return service, selected_processor, playback

    @staticmethod
    def wait_for(service, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            service.poll()
            snapshot = service.snapshot
            if predicate(snapshot):
                return snapshot
            time.sleep(0.01)
        raise AssertionError(f"No llegó el estado esperado: {service.snapshot!r}")

    def build_policy(self):
        rules = (
            CapabilityRule(
                "safe-local",
                Effect.OPEN_KNOWN_RESOURCE,
                (Intent.OPEN_URL,),
                ("qa_safe",),
                ("target",),
                "target",
            ),
            CapabilityRule(
                "modify-client",
                Effect.MODIFY_LOCAL_DATA,
                (Intent.MODIFY_LOCAL_DATA,),
                ("qa_modify",),
                ("target", "content"),
                "target",
            ),
            CapabilityRule(
                "blocked-code",
                Effect.EXECUTE_ARBITRARY_CODE,
                (Intent.SYSTEM_CHANGE,),
                ("qa_shell",),
                ("target",),
                "target",
            ),
        )
        broker = PermissionBroker(PolicyRegistry(rules), self.logger)
        effects: list[str] = []

        def tool(**arguments: str) -> ToolResult:
            effects.append(arguments["target"])
            return ToolResult(True, "Resultado observable QA.")

        executor = ActionExecutor(
            {"qa_safe": tool, "qa_modify": tool},
            self.logger,
            broker,
        )
        return PolicyActionCoordinator(broker, executor), effects

    @staticmethod
    def policy_subject(
        capability: str = "modify-client",
        *,
        action_id: str = "policy-action-1",
        private: str = "Cliente Ficticio Privado",
    ) -> PolicySubject:
        definitions = {
            "safe-local": Action(
                Intent.OPEN_URL,
                "qa_safe",
                {"target": "safe-target"},
                RiskLevel.SAFE,
                False,
            ),
            "modify-client": Action(
                Intent.MODIFY_LOCAL_DATA,
                "qa_modify",
                {"target": "record-qa-001", "content": private},
                RiskLevel.CAUTION,
                True,
            ),
            "blocked-code": Action(
                Intent.SYSTEM_CHANGE,
                "qa_shell",
                {"target": "fictitious-script"},
                RiskLevel.DANGEROUS,
                True,
            ),
        }
        return PolicySubject(action_id, capability, definitions[capability])


class DesktopAgentServiceCommandTests(ServiceTestSupport, unittest.TestCase):
    def test_tracks_history_stage_active_tool_result_and_evidence(self) -> None:
        service, _, _ = self.build_service()

        task_id = service.submit("crear cliente ficticio")
        snapshot = self.wait_for(
            service,
            lambda value: value.history
            and value.history[-1].state is TaskState.SUCCEEDED,
        )

        entry = snapshot.history[-1]
        self.assertEqual(entry.task_id, task_id)
        self.assertEqual(entry.label, "crear cliente ficticio")
        self.assertEqual(entry.stage, CommandStage.FINISHED)
        self.assertEqual(entry.result, "Completado.")
        self.assertIsNotNone(entry.evidence)
        assert entry.evidence is not None
        self.assertTrue(entry.evidence.observable_success)
        self.assertEqual(entry.evidence.tool_name, "qa_command_tool")
        self.assertIsNone(snapshot.active_tool)
        self.assertNotIn("crear cliente ficticio", self.stream.getvalue())

    def test_history_is_memory_only_bounded_and_evicts_terminal_oldest(self) -> None:
        service, _, _ = self.build_service(history_limit=2)

        ids = []
        for index in range(3):
            ids.append(service.submit(f"orden ficticia {index}"))
            self.wait_for(
                service,
                lambda value: any(
                    item.task_id == ids[-1] and item.state.terminal
                    for item in value.history
                ),
            )

        self.assertEqual(
            [item.task_id for item in service.snapshot.history],
            ids[1:],
        )

    def test_cancels_queued_task_and_rejects_active_phase_cancellation(self) -> None:
        processor = FakeProcessor(blocking=True)
        service, _, _ = self.build_service(processor)
        active = service.submit("activa")
        pending = service.submit("pendiente")
        self.assertTrue(processor.started.wait(1))

        with self.assertRaises(ServiceError):
            service.cancel_task(active)
        self.assertTrue(service.cancel_task(pending))
        processor.release.set()
        snapshot = self.wait_for(
            service,
            lambda value: all(item.state.terminal for item in value.history),
        )

        states = {item.task_id: item.state for item in snapshot.history}
        self.assertEqual(states[active], TaskState.SUCCEEDED)
        self.assertEqual(states[pending], TaskState.CANCELLED)
        self.assertEqual(processor.commands, ["activa"])

    def test_exposes_active_tool_while_owner_worker_is_blocked(self) -> None:
        processor = FakeProcessor(blocking=True)
        service, _, _ = self.build_service(processor)
        service.submit("activa")
        self.assertTrue(processor.started.wait(1))

        service.poll()

        self.assertEqual(service.snapshot.active_tool, "qa_command_tool")
        self.assertEqual(
            service.snapshot.history[-1].active_tool,
            "qa_command_tool",
        )
        processor.release.set()
        self.wait_for(
            service,
            lambda value: value.history[-1].state.terminal,
        )

    def test_close_is_manual_idempotent_and_rejects_new_commands(self) -> None:
        service, _, _ = self.build_service()

        self.assertTrue(service.close(2))
        self.assertTrue(service.close(2))
        self.assertEqual(service.snapshot.state, ServiceState.CLOSED)
        with self.assertRaises(ServiceError):
            service.submit("orden posterior")


class DesktopAgentServiceSessionTests(ServiceTestSupport, unittest.TestCase):
    def test_lock_or_unknown_session_suspends_and_never_auto_resumes(self) -> None:
        for availability in (
            SessionAvailability.LOCKED,
            SessionAvailability.UNKNOWN,
        ):
            with self.subTest(availability=availability):
                clock = MutableClock()
                monitor = FakeSessionMonitor(availability)
                service, _, _ = self.build_service(
                    monitor=monitor,
                    clock=clock,
                )

                service.poll()
                self.assertEqual(service.snapshot.state, ServiceState.SUSPENDED)
                with self.assertRaises(ServiceError):
                    service.submit("no debe aceptarse")
                monitor.availability = SessionAvailability.AVAILABLE
                clock.advance(2)
                service.poll()
                self.assertEqual(service.snapshot.state, ServiceState.SUSPENDED)
                service.resume()
                self.assertEqual(service.snapshot.state, ServiceState.ACTIVE)
                service.close(2)

    def test_resume_requires_available_interactive_session(self) -> None:
        monitor = FakeSessionMonitor(SessionAvailability.LOCKED)
        service, _, _ = self.build_service(monitor=monitor)
        service.suspend("manual-test")

        with self.assertRaises(ServiceError):
            service.resume()

        monitor.availability = SessionAvailability.AVAILABLE
        service.resume()
        self.assertEqual(service.snapshot.state, ServiceState.ACTIVE)


class DesktopAgentServicePermissionTests(ServiceTestSupport, unittest.TestCase):
    def test_confirmation_panel_data_approves_and_executes_exact_action(self) -> None:
        coordinator, effects = self.build_policy()
        service, _, _ = self.build_service(policy=coordinator)
        private = "PRIVATE-CLIENT-CONTENT-42"
        subject = self.policy_subject(private=private)

        task_id = service.submit_policy_action(subject)
        pending = service.snapshot.pending_confirmation
        assert pending is not None
        self.assertEqual(task_id, subject.action_id)
        self.assertEqual(pending.request.destination, "record-qa-001")
        self.assertEqual(
            service.snapshot.history[-1].state,
            TaskState.AWAITING_CONFIRMATION,
        )
        decision = ConfirmationDecision(
            pending.request.request_id,
            pending.request.challenge,
            pending.request.subject_fingerprint,
            True,
            ConfirmationChannel.LOCAL,
        )

        execution = service.resolve_confirmation(task_id, decision)

        self.assertIsNotNone(execution.tool_result)
        self.assertEqual(effects, ["record-qa-001"])
        self.assertEqual(service.snapshot.history[-1].state, TaskState.SUCCEEDED)
        self.assertIsNone(service.snapshot.pending_confirmation)
        self.assertNotIn(private, self.stream.getvalue())

    def test_rejection_and_emergency_cancel_pending_without_effect(self) -> None:
        for emergency in (False, True):
            with self.subTest(emergency=emergency):
                coordinator, effects = self.build_policy()
                service, _, _ = self.build_service(policy=coordinator)
                subject = self.policy_subject(
                    action_id=f"policy-action-{int(emergency)}"
                )
                task_id = service.submit_policy_action(subject)
                pending = service.snapshot.pending_confirmation
                assert pending is not None
                if emergency:
                    service.request_stop(emergency=True)
                else:
                    decision = ConfirmationDecision(
                        pending.request.request_id,
                        pending.request.challenge,
                        pending.request.subject_fingerprint,
                        False,
                        ConfirmationChannel.LOCAL,
                    )
                    service.resolve_confirmation(task_id, decision)

                self.assertEqual(effects, [])
                self.assertEqual(
                    service.snapshot.history[-1].state,
                    TaskState.CANCELLED,
                )
                self.assertIsNone(service.snapshot.pending_confirmation)
                service.close(2)

    def test_safe_and_blocked_actions_do_not_open_panel(self) -> None:
        coordinator, effects = self.build_policy()
        service, _, _ = self.build_service(policy=coordinator)

        safe = self.policy_subject(
            "safe-local",
            action_id="policy-safe-1",
        )
        blocked = self.policy_subject(
            "blocked-code",
            action_id="policy-blocked-1",
        )
        service.submit_policy_action(safe)
        service.submit_policy_action(blocked)

        states = {item.task_id: item.state for item in service.snapshot.history}
        self.assertEqual(states[safe.action_id], TaskState.SUCCEEDED)
        self.assertEqual(states[blocked.action_id], TaskState.FAILED)
        self.assertEqual(effects, ["safe-target"])
        self.assertIsNone(service.snapshot.pending_confirmation)

    def test_only_one_confirmation_can_be_pending(self) -> None:
        coordinator, _ = self.build_policy()
        service, _, _ = self.build_service(policy=coordinator)
        service.submit_policy_action(self.policy_subject())

        with self.assertRaises(ServiceError):
            service.submit_policy_action(
                self.policy_subject(action_id="policy-action-2")
            )


if __name__ == "__main__":
    unittest.main()
