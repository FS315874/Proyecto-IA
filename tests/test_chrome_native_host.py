import io
import json
import struct
import unittest

from desktop_agent.browser_bridge import BrowserBridgeError
from desktop_agent.chrome_native_host import (
    ALLOWED_EXTENSION_ORIGIN,
    read_native_message,
    run_native_host,
    write_native_message,
)


def native_message(value: object) -> bytes:
    payload = json.dumps(value).encode("utf-8")
    return struct.pack("=I", len(payload)) + payload


class NativeMessageFramingTests(unittest.TestCase):
    def test_reads_and_writes_native_messaging_frames(self) -> None:
        framed = native_message({"type": "hello"})
        self.assertEqual(
            read_native_message(io.BytesIO(framed)),
            json.dumps({"type": "hello"}).encode("utf-8"),
        )
        output = io.BytesIO()
        write_native_message(output, b'{"ok":true}')
        self.assertEqual(read_native_message(io.BytesIO(output.getvalue())), b'{"ok":true}')

    def test_rejects_truncated_or_oversized_frames(self) -> None:
        for value in (
            b"x",
            struct.pack("=I", 0),
            struct.pack("=I", 40000),
            struct.pack("=I", 5) + b"{}",
        ):
            with self.subTest(value=value):
                with self.assertRaises(BrowserBridgeError):
                    read_native_message(io.BytesIO(value))

    def test_rejects_every_origin_except_the_pinned_extension(self) -> None:
        self.assertEqual(run_native_host([]), 2)
        self.assertEqual(
            run_native_host(["chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"]),
            2,
        )
        self.assertTrue(ALLOWED_EXTENSION_ORIGIN.startswith("chrome-extension://"))


if __name__ == "__main__":
    unittest.main()
