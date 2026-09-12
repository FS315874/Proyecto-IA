import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop_agent.hotkeys import GlobalHotkeys


class HotkeyTests(unittest.TestCase):
    def test_conflict_releases_the_already_registered_shortcut(self):
        user32 = SimpleNamespace(RegisterHotKey=Mock(side_effect=[True, False]), UnregisterHotKey=Mock(), PeekMessageW=Mock())
        hotkeys = GlobalHotkeys()
        with patch("ctypes.WinDLL", return_value=user32, create=True):
            hotkeys._run()
        self.assertIn("otra aplicación", hotkeys.status)
        user32.UnregisterHotKey.assert_called_once_with(None, 1)

    def test_only_explicit_hotkey_messages_are_queued_and_unregistered(self):
        hotkeys = GlobalHotkeys()
        pending = iter([1, 2])

        def peek(message, *args):
            identifier = next(pending, None)
            if identifier is None:
                hotkeys._stop.set()
                return False
            message._obj.wParam = identifier
            return True

        user32 = SimpleNamespace(RegisterHotKey=Mock(return_value=True), UnregisterHotKey=Mock(), PeekMessageW=Mock(side_effect=peek))
        with patch("ctypes.WinDLL", return_value=user32, create=True):
            hotkeys._run()
        self.assertEqual(hotkeys.drain(), ("voice", "emergency"))
        self.assertEqual(hotkeys.drain(), ())
        self.assertEqual(user32.UnregisterHotKey.call_count, 2)
        self.assertEqual(user32.RegisterHotKey.call_args_list[0].args, (None, 1, 0x4003, 0x20))


if __name__ == "__main__":
    unittest.main()
