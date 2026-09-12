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
        for command in (
            "detené youtube",
            "detener YouTube",
            "parar youtube",
            "Detener YouTube.",
        ):
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

    def test_removes_only_sentence_ending_punctuation_from_search_query(self) -> None:
        action = parse_command("Poné en YouTube qué tan malo puedo ser.")

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(
            action.arguments,
            {"query": "qué tan malo puedo ser"},
        )

    def test_parses_each_supported_site(self) -> None:
        expected_urls = {
            "abrir youtube": "https://www.youtube.com/",
            "abrir google": "https://www.google.com/",
            "abrir github": "https://github.com/",
            "abrir spotify": "https://open.spotify.com/",
            "abrir spotify web": "https://open.spotify.com/",
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

    def test_accepts_natural_sentence_boundary_punctuation(self) -> None:
        cases = {
            "Abrir calculadora.": ("open_application", "calculator"),
            "¡Abrí Spotify!": ("open_url", "https://open.spotify.com/"),
            "¿abrir YouTube?": ("open_url", "https://www.youtube.com/"),
        }

        for command, (tool_name, target) in cases.items():
            with self.subTest(command=command):
                action = parse_command(command)

                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.tool_name, tool_name)
                self.assertIn(target, action.arguments.values())

    def test_accepts_spoken_opening_variants_and_optional_article(self) -> None:
        for command in ("abrí la calculadora", "abre la calculadora"):
            with self.subTest(command=command):
                action = parse_command(command)

                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.arguments["name"], "calculator")

    def test_parses_each_supported_application(self) -> None:
        expected_applications = {
            "abrir chrome": "chrome",
            "abrir vscode": "vscode",
            "abrir calculadora": "calculator",
            "abrir spotify app": "spotify",
            "abrir spotify escritorio": "spotify",
            "abrir steam": "steam",
            "abrir voicemeeter banana": "voicemeeter",
            "abrir league of legends": "league_of_legends",
            "abrir god of war ragnarok": "god_of_war_ragnarok",
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

    def test_parses_short_game_and_audio_aliases(self) -> None:
        expected = {
            "Abrí el League": "league_of_legends",
            "Abrí LOL": "league_of_legends",
            "Abrí God of War": "god_of_war_ragnarok",
            "Abrí Voice Meeter": "voicemeeter",
        }

        for command, name in expected.items():
            with self.subTest(command=command):
                action = parse_command(command)

                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.arguments["name"], name)

    def test_rejects_unknown_command(self) -> None:
        self.assertIsNone(parse_command("preparame un café"))

    def test_rejects_unknown_site(self) -> None:
        self.assertIsNone(parse_command("abrir sitio-inventado"))


if __name__ == "__main__":
    unittest.main()
