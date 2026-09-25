import contextlib
import io
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_agent.approved_targets import (
    ApprovedTargetStore,
    TargetError,
    TargetKind,
    open_approved_target,
)
from desktop_agent.cli import build_executor, main
from desktop_agent.interpretation import (
    ProposalIntent,
    build_action_from_proposal,
    validate_proposal,
)
from desktop_agent.models import Intent, RiskLevel, ToolResult
from desktop_agent.parser import parse_command
from desktop_agent.plans import PlanProposalIntent, validate_plan_proposal


class ApprovedTargetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.store = ApprovedTargetStore(base / "targets.json")
        self.project = base / "Proyecto IA"
        self.project.mkdir()
        self.document = base / "guia.md"
        self.document.write_text("# Guía", encoding="utf-8")

    def test_registration_is_explicit_persistent_and_does_not_log_paths(self):
        self.store.add("Proyecto IA", TargetKind.PROJECT, self.project)
        self.store.add("Guía IA", TargetKind.DOCUMENT, self.document)

        loaded = ApprovedTargetStore(self.store.path).list()

        self.assertEqual([item.name for item in loaded], ["Proyecto IA", "Guía IA"])
        self.assertNotIn(str(self.project), repr(loaded))
        self.assertEqual(self.store.resolve("proyecto ia").name, "Proyecto IA")

    def test_rejects_executable_duplicate_and_ambiguous_name(self):
        executable = self.project / "danger.exe"
        executable.write_bytes(b"fake")
        with self.assertRaises(TargetError):
            self.store.add("Peligroso", TargetKind.DOCUMENT, executable)
        self.store.add("Proyecto A", TargetKind.PROJECT, self.project)
        self.store.add("Proyecto B", TargetKind.DOCUMENT, self.document)
        with self.assertRaises(TargetError):
            self.store.add("proyecto a", TargetKind.PROJECT, self.project)
        with self.assertRaises(TargetError):
            self.store.resolve("proyecto")
        with self.assertRaises(TargetError):
            self.store.resolve("a")

    def test_kind_disambiguates_short_natural_name(self):
        self.store.add("Proyecto IA", TargetKind.PROJECT, self.project)
        self.store.add("Guía IA", TargetKind.DOCUMENT, self.document)

        self.assertEqual(
            self.store.resolve("IA", TargetKind.PROJECT).name,
            "Proyecto IA",
        )
        self.assertEqual(
            self.store.resolve("IA", TargetKind.DOCUMENT).name,
            "Guía IA",
        )
        with self.assertRaises(TargetError) as context:
            self.store.resolve("IA")
        self.assertEqual(context.exception.code, "target_ambiguous")

    def test_unicode_names_can_be_resolved_without_path_access(self):
        self.store.add("Проект IA", TargetKind.PROJECT, self.project)
        self.assertEqual(self.store.resolve("проект ia").name, "Проект IA")

    def test_corrupt_catalog_fails_closed_and_is_not_overwritten(self):
        self.store.path.write_text('{"schema_version":1,"targets":[{"name":"x"}]}', encoding="utf-8")
        before = self.store.path.read_bytes()

        with self.assertRaises(TargetError):
            self.store.add("Proyecto IA", TargetKind.PROJECT, self.project)

        self.assertEqual(self.store.path.read_bytes(), before)

    def test_read_only_listing_shows_names_but_not_paths(self):
        self.store.add("Proyecto IA", TargetKind.PROJECT, self.project)
        output = io.StringIO()
        with patch("desktop_agent.cli.ApprovedTargetStore", return_value=self.store):
            with contextlib.redirect_stdout(output):
                result = main(["--targets"])
        self.assertEqual(result, 0)
        self.assertIn("project: Proyecto IA", output.getvalue())
        self.assertNotIn(str(self.project), output.getvalue())

    def test_removed_or_changed_target_is_never_launched(self):
        self.store.add("Guía", TargetKind.DOCUMENT, self.document)
        self.document.unlink()
        calls = []

        result = open_approved_target(
            "Guía", store=self.store,
            finder=lambda _: "C:/Code.exe",
            path_checker=lambda _: False,
            starter=lambda *args, **kwargs: calls.append(args),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "target_unavailable")
        self.assertEqual(calls, [])

    def test_changed_resolved_path_is_rejected_before_launch(self):
        self.store.add("Guía", TargetKind.DOCUMENT, self.document)
        calls = []
        with patch("desktop_agent.approved_targets._validate_path", return_value=self.project):
            result = open_approved_target(
                "Guía", store=self.store,
                finder=lambda _: "C:/Code.exe",
                path_checker=lambda _: False,
                starter=lambda *args, **kwargs: calls.append(args),
            )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "target_changed")
        self.assertEqual(calls, [])

    def test_approved_target_is_literal_argument_to_fixed_vscode_binary(self):
        self.store.add("Proyecto IA", TargetKind.PROJECT, self.project)
        calls = []

        result = open_approved_target(
            "Proyecto IA", store=self.store,
            finder=lambda _: "C:/Code.exe",
            path_checker=lambda _: False,
            starter=lambda *args, **kwargs: calls.append((args, kwargs)),
            process_checker=lambda _: True,
            waiter=lambda _: None,
        )

        self.assertTrue(result.success)
        self.assertEqual(calls[0][0][0], ["C:/Code.exe", "--new-window", str(self.project.resolve())])
        self.assertNotIn("shell", calls[0][1])
        self.assertIn("no se comprobó el contenido", result.message)

    def test_rejects_shell_launcher_fallback(self):
        self.store.add("Proyecto IA", TargetKind.PROJECT, self.project)
        calls = []
        result = open_approved_target(
            "Proyecto IA", store=self.store,
            finder=lambda _: "C:/code.cmd",
            path_checker=lambda _: False,
            starter=lambda *args, **kwargs: calls.append(args),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "app_missing")
        self.assertEqual(calls, [])

    def test_parser_and_ai_proposal_use_name_not_path(self):
        parsed = parse_command("Abrí el proyecto Proyecto IA.")
        self.assertEqual(parsed.tool_name, "open_approved_target")
        self.assertEqual(parsed.arguments["name"], "Proyecto IA")
        self.assertEqual(parsed.arguments["kind"], "project")
        self.assertEqual(parsed.intent, Intent.OPEN_APPLICATION)
        self.assertEqual(parsed.risk_level, RiskLevel.SAFE)
        self.assertFalse(parsed.requires_confirmation)
        document = parse_command("Abrí la documentación de Guía IA.")
        self.assertEqual(
            document.arguments,
            {"name": "Guía IA", "kind": "document"},
        )
        proposal = validate_proposal({
            "schema_version": 1,
            "intent": ProposalIntent.OPEN_APPROVED_TARGET.value,
            "target": "Proyecto IA",
        })
        self.assertEqual(build_action_from_proposal(proposal).tool_name, "open_approved_target")
        with self.assertRaises(ValueError):
            validate_proposal({
                "schema_version": 1,
                "intent": ProposalIntent.OPEN_APPROVED_TARGET.value,
                "target": "C:/arbitrary/path",
            })

    def test_plan_accepts_named_target_but_not_unchecked_path(self):
        plan = validate_plan_proposal({
            "schema_version": 1,
            "steps": [
                {"intent": PlanProposalIntent.OPEN_APPROVED_TARGET.value, "target": "Proyecto IA"},
                {"intent": PlanProposalIntent.OPEN_APPROVED_TARGET.value, "target": "Guía IA"},
            ],
        })
        self.assertEqual(plan.steps[0].action.tool_name, "open_approved_target")
        with self.assertRaises(ValueError):
            validate_plan_proposal({
                "schema_version": 1,
                "steps": [
                    {"intent": PlanProposalIntent.OPEN_APPROVED_TARGET.value, "target": "C:/bad"},
                    {"intent": PlanProposalIntent.OPEN_APPROVED_TARGET.value, "target": "Guía IA"},
                ],
            })

    def test_executor_registers_target_tool_and_fails_without_registration(self):
        class FakePlayback:
            has_active_session = False

            def __call__(self, query):
                return ToolResult(True, "ok")

            def stop(self):
                return ToolResult(True, "ok")

            def resume(self):
                return ToolResult(True, "ok")

        executor, _ = build_executor(
            logging.getLogger(self.id()), FakePlayback(), target_store=self.store,
        )
        action = parse_command("abrí el proyecto Proyecto IA")
        executor.validate(action)
        with self.assertRaisesRegex(Exception, "No hay un proyecto"):
            executor.execute(action)


if __name__ == "__main__":
    unittest.main()
