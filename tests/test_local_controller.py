import io
import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path

from desktop_agent.command_processor import (
    CommandExecution,
    CommandProgress,
    CommandStage,
)
from desktop_agent.interpretation import (
    InterpretationPath,
    InterpretationResult,
    InterpretationStatus,
    MonthlyUsageSnapshot,
)
from desktop_agent.local_controller import (
    AlreadyRunningError,
    ControllerState,
    ControllerUpdate,
    ControllerUpdateKind,
    LocalAgentController,
    LocalControllerError,
    SingleInstanceLock,
)
from desktop_agent.models import ToolResult


def usage_snapshot(total_tokens: int = 0) -> MonthlyUsageSnapshot:
    return MonthlyUsageSnapshot(
        month="2026-08",
        request_count=0 if total_tokens == 0 else 1,
        input_tokens=total_tokens,
        output_tokens=0,
        total_tokens=total_tokens,
        cached_input_tokens=0,
        cache_write_tokens=0,
        estimated_cost_usd=0.0,
        budget_usd=1.0,
        remaining_usd=1.0,
    )


def execution(
    message: str = "Completado.",
    monthly_usage: MonthlyUsageSnapshot | None = None,
) -> CommandExecution:
    interpretation = InterpretationResult(
        action=None,
        path=InterpretationPath.DETERMINISTIC,
        status=InterpretationStatus.SUCCESS,
        duration_ms=1.0,
        provider_configured=False,
        monthly_usage=monthly_usage,
    )
    return CommandExecution(
        success=True,
        messages=(message,),
        interpretation=interpretation,
        duration_ms=2.0,
    )


class FakeProcessor:
    def __init__(self, result: CommandExecution | None = None) -> None:
        self.result = result or execution()
        self.commands: list[str] = []
        self.thread_ids: list[int] = []

    def execute(self, command, output=print, progress=None):
        self.commands.append(command)
        self.thread_ids.append(threading.get_ident())
        if progress is not None:
            progress(CommandProgress(CommandStage.INTERPRETING))
            progress(CommandProgress(CommandStage.FINISHED))
        return self.result


class BlockingProcessor(FakeProcessor):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, command, output=print, progress=None):
        self.commands.append(command)
        self.thread_ids.append(threading.get_ident())
        self.started.set()
        self.release.wait(2.0)
        return self.result


class FakePlaybackController:
    def __init__(self, active: bool = False) -> None:
        self.active = active
        self.stop_calls = 0
        self.stop_thread_ids: list[int] = []

    @property
    def has_active_session(self) -> bool:
        return self.active

    def stop(self) -> ToolResult:
        self.stop_calls += 1
        self.stop_thread_ids.append(threading.get_ident())
        self.active = False
        return ToolResult(True, "Reproducción detenida.")


class LocalAgentControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))
        self.controllers: list[LocalAgentController] = []

    def tearDown(self) -> None:
        for controller in self.controllers:
            controller.close(2.0)

    def build_controller(self, processor, playback=None, initial_usage=None):
        controller = LocalAgentController(
            processor,
            playback or FakePlaybackController(),
            self.logger,
            initial_usage=initial_usage,
        )
        self.controllers.append(controller)
        return controller

    def wait_for_updates(self, controller, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        updates: list[ControllerUpdate] = []
        while time.monotonic() < deadline:
            updates.extend(controller.drain_updates())
            if predicate(updates):
                return updates
            time.sleep(0.01)
        self.fail(f"No llegó la actualización esperada: {updates!r}")

    def test_serializes_commands_on_one_persistent_worker(self) -> None:
        processor = FakeProcessor()
        controller = self.build_controller(processor)

        first = controller.submit("abrir youtube")
        second = controller.submit("abrir calculadora")
        updates = self.wait_for_updates(
            controller,
            lambda values: len(
                [v for v in values if v.kind is ControllerUpdateKind.RESULT]
            )
            == 2,
        )

        self.assertEqual(
            processor.commands, ["abrir youtube", "abrir calculadora"]
        )
        self.assertEqual(len(set(processor.thread_ids)), 1)
        result_ids = [
            value.request_id
            for value in updates
            if value.kind is ControllerUpdateKind.RESULT
        ]
        self.assertEqual(result_ids, [first, second])
        self.assertIs(controller.snapshot.state, ControllerState.IDLE)

    def test_rejects_empty_oversized_and_post_close_commands(self) -> None:
        controller = self.build_controller(FakeProcessor())

        with self.assertRaises(LocalControllerError):
            controller.submit("   ")
        with self.assertRaises(LocalControllerError):
            controller.submit("x" * 501)
        self.assertTrue(controller.close(2.0))
        with self.assertRaises(LocalControllerError):
            controller.submit("abrir youtube")

    def test_stop_uses_the_same_owner_thread_as_playback(self) -> None:
        processor = FakeProcessor()
        playback = FakePlaybackController(active=True)
        controller = self.build_controller(processor, playback)
        controller.submit("abrir youtube")
        self.wait_for_updates(
            controller,
            lambda values: any(
                v.kind is ControllerUpdateKind.RESULT for v in values
            ),
        )

        controller.request_stop()
        updates = self.wait_for_updates(
            controller,
            lambda values: any(
                v.kind is ControllerUpdateKind.STOP_RESULT for v in values
            ),
        )

        self.assertEqual(playback.stop_calls, 1)
        self.assertEqual(playback.stop_thread_ids, processor.thread_ids)
        self.assertIn(
            "Reproducción detenida.",
            [value.message for value in updates],
        )

    def test_emergency_cancels_pending_work_then_stops(self) -> None:
        processor = BlockingProcessor()
        playback = FakePlaybackController(active=True)
        controller = self.build_controller(processor, playback)
        first = controller.submit("primera")
        second = controller.submit("segunda")
        self.assertTrue(processor.started.wait(1.0))

        controller.request_stop(emergency=True)
        processor.release.set()
        updates = self.wait_for_updates(
            controller,
            lambda values: any(
                v.kind is ControllerUpdateKind.STOP_RESULT for v in values
            ),
        )

        self.assertEqual(processor.commands, ["primera"])
        self.assertTrue(
            any(
                value.kind is ControllerUpdateKind.CANCELLED
                and value.request_id == second
                for value in updates
            )
        )
        self.assertTrue(
            any(
                value.kind is ControllerUpdateKind.RESULT
                and value.request_id == first
                for value in updates
            )
        )
        self.assertEqual(playback.stop_calls, 1)

    def test_close_cancels_pending_stops_playback_and_is_idempotent(self) -> None:
        playback = FakePlaybackController(active=True)
        controller = self.build_controller(FakeProcessor(), playback)

        self.assertTrue(controller.close(2.0))
        self.assertTrue(controller.close(2.0))

        self.assertEqual(playback.stop_calls, 1)
        self.assertIs(controller.snapshot.state, ControllerState.CLOSED)

    def test_updates_visible_monthly_usage_from_execution(self) -> None:
        initial = usage_snapshot()
        current = usage_snapshot(12)
        controller = self.build_controller(
            FakeProcessor(execution(monthly_usage=current)),
            initial_usage=initial,
        )

        controller.submit("una orden")
        self.wait_for_updates(
            controller,
            lambda values: any(
                v.kind is ControllerUpdateKind.RESULT for v in values
            ),
        )

        self.assertEqual(controller.snapshot.monthly_usage, current)


class SingleInstanceLockTests(unittest.TestCase):
    def test_second_instance_is_rejected_until_first_releases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.lock"
            first = SingleInstanceLock(path)
            second = SingleInstanceLock(path)
            first.acquire()
            self.addCleanup(first.close)

            with self.assertRaises(AlreadyRunningError):
                second.acquire()

            first.close()
            second.acquire()
            second.close()


if __name__ == "__main__":
    unittest.main()
