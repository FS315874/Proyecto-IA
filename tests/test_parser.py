import unittest

from desktop_agent.models import Intent, RiskLevel
from desktop_agent.parser import parse_command


class ParseCommandTests(unittest.TestCase):
    def test_parses_youtube_playback_commands_preserving_query_as_data(self) -> None:
        commands = {
            "poné en youtube Qué tan malo puedo ser": "Qué tan malo puedo ser",
            "pone lofi   hip hop en YouTube": "lofi hip hop",
            "reproducir Música de estudio en youtube": "Música de estudio",
        }

        for command, expected_query in commands.items():
            with self.subTest(command=command):
                action = parse_command(command)

                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.intent, Intent.BROWSER_NAVIGATION)
                self.assertEqual(action.tool_name, "play_youtube")
                self.assertEqual(action.arguments, {"query": expected_query})
                self.assertEqual(action.risk_level, RiskLevel.SAFE)
                self.assertFalse(action.requires_confirmation)

    def test_parses_youtube_stop_commands(self) -> None:
        for command in ("detené youtube", "detener YouTube", "parar youtube"):
            with self.subTest(command=command):
                action = parse_command(command)

                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.intent, Intent.BROWSER_NAVIGATION)
                self.assertEqual(action.tool_name, "stop_youtube")
                self.assertEqual(action.arguments, {})

    def test_rejects_invalid_youtube_playback_queries(self) -> None:
        self.assertIsNone(parse_command("poné en youtube   "))
        self.assertIsNone(parse_command(f"poné {'x' * 201} en youtube"))

    def test_parses_each_supported_site(self) -> None:
        expected_urls = {
            "abrir youtube": "https://www.youtube.com/",
            "abrir google": "https://www.google.com/",
            "abrir github": "https://github.com/",
        }

        for command, expected_url in expected_urls.items():
            with self.subTest(command=command):
                action = parse_command(command)
                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.intent, Intent.OPEN_URL)
                self.assertEqual(action.tool_name, "open_url")
                self.assertEqual(action.arguments["url"], expected_url)
                self.assertEqual(action.risk_level, RiskLevel.SAFE)
                self.assertFalse(action.requires_confirmation)

    def test_normalizes_accents_case_and_spaces(self) -> None:
        action = parse_command("  ABRÍ    YouTube  ")

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.arguments["url"], "https://www.youtube.com/")

    def test_parses_each_supported_application(self) -> None:
        expected_applications = {
            "abrir chrome": "chrome",
            "abrir vscode": "vscode",
            "abrir calculadora": "calculator",
        }

        for command, expected_name in expected_applications.items():
            with self.subTest(command=command):
                action = parse_command(command)
                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.intent, Intent.OPEN_APPLICATION)
                self.assertEqual(action.tool_name, "open_application")
                self.assertEqual(action.arguments["name"], expected_name)
                self.assertEqual(action.risk_level, RiskLevel.SAFE)
                self.assertFalse(action.requires_confirmation)

    def test_parses_visual_studio_code_alias(self) -> None:
        action = parse_command("Abrí Visual Studio Code")

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.arguments["name"], "vscode")

    def test_rejects_unknown_command(self) -> None:
        self.assertIsNone(parse_command("preparame un café"))

    def test_rejects_unknown_site(self) -> None:
        self.assertIsNone(parse_command("abrir sitio-inventado"))


if __name__ == "__main__":
    unittest.main()
