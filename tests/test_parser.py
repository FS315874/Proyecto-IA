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
            "Pausá YouTube.",
            "Pausá lo que estaba sonando en YouTube.",
            "pausa lo que está sonando en youtube",
            "Poné pausa al video que estoy mirando en YouTube.",
            "Poné en pausa el video que estaba viendo en YouTube",
        ):
            with self.subTest(command=command):
                action = parse_command(command)

                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.intent, Intent.BROWSER_NAVIGATION)
                self.assertEqual(action.tool_name, "stop_youtube")
                self.assertEqual(action.arguments, {})

    def test_does_not_turn_a_negated_pause_into_an_action(self) -> None:
        self.assertIsNone(parse_command("No pauses YouTube"))

    def test_parses_youtube_resume_without_turning_it_into_a_search(self) -> None:
        for command in (
            "Reproducí lo que estaba mirando en YouTube.",
            "Seguí reproduciendo lo que estoy viendo en YouTube",
            "Reanudá el video de YouTube",
            "Poné play al video de YouTube",
        ):
            with self.subTest(command=command):
                action = parse_command(command)

                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.intent, Intent.BROWSER_NAVIGATION)
                self.assertEqual(action.tool_name, "resume_youtube")
                self.assertEqual(action.arguments, {})

    def test_does_not_turn_a_negated_resume_into_an_action(self) -> None:
        self.assertIsNone(parse_command("No reanudes el video de YouTube"))

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

    def test_parses_spotify_tracks_playlists_and_searches_as_distinct_actions(self) -> None:
        cases = {
            "Poné la canción Qué tan malo puedo ser en Spotify.": (
                "play_spotify_track",
                {"query": "Qué tan malo puedo ser"},
            ),
            "Poné en Spotify As It Was": (
                "play_spotify_track",
                {"query": "As It Was"},
            ),
            "Poné mi playlist 7 W7 en Spotify": (
                "play_spotify_playlist",
                {"name": "7 W7"},
            ),
            "Reproducí en Spotify mi playlist 7W7": (
                "play_spotify_playlist",
                {"name": "7W7"},
            ),
            "Buscá la canción As It Was en Spotify": (
                "search_spotify_track",
                {"query": "As It Was"},
            ),
        }
        for command, (tool_name, arguments) in cases.items():
            with self.subTest(command=command):
                action = parse_command(command)
                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.intent, Intent.MEDIA_PLAYBACK)
                self.assertEqual(action.tool_name, tool_name)
                self.assertEqual(action.arguments, arguments)

    def test_spotify_controls_never_become_searches(self) -> None:
        cases = {
            "Pausá Spotify": "pause_spotify",
            "Poné pausa a la canción de Spotify": "pause_spotify",
            "Seguí reproduciendo Spotify": "resume_spotify",
            "Poné play a Spotify": "resume_spotify",
            "Siguiente canción en Spotify": "next_spotify",
            "Volvé a la canción anterior en Spotify": "previous_spotify",
        }
        for command, tool_name in cases.items():
            with self.subTest(command=command):
                action = parse_command(command)
                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action.intent, Intent.MEDIA_PLAYBACK)
                self.assertEqual(action.tool_name, tool_name)
                self.assertEqual(action.arguments, {})

        self.assertIsNone(parse_command("No pauses Spotify"))
        self.assertIsNone(parse_command("No sigas reproduciendo Spotify"))

    def test_parses_and_bounds_spotify_volume(self) -> None:
        action = parse_command("Poné el volumen de Spotify al 35 %")
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.tool_name, "set_spotify_volume")
        self.assertEqual(action.arguments, {"percent": "35"})
        self.assertIsNone(parse_command("Poné el volumen de Spotify al 101 %"))

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
