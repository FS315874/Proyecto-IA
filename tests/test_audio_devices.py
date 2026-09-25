import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop_agent.audio_capture import AudioCaptureError
from desktop_agent.audio_devices import InputDevice, create_recorder, list_input_devices, resolve_input_device
from tests.test_audio_capture import FakeStream, pcm_block


class AudioDevicesTests(unittest.TestCase):
    def test_enumeration_filters_outputs_without_recording(self):
        sd = SimpleNamespace(
            query_hostapis=lambda: [{"name": "WASAPI"}],
            query_devices=lambda: [
                {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
                {"name": "USB mic", "hostapi": 0, "max_input_channels": 1, "default_samplerate": 48000},
            ], RawInputStream=Mock(),
        )
        with patch.dict("sys.modules", sounddevice=sd):
            devices = list_input_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].key, "WASAPI::USB mic")
        self.assertEqual(devices[0].index, 1)
        sd.RawInputStream.assert_not_called()

    def test_missing_or_duplicate_device_never_falls_back(self):
        device = InputDevice(1, "WASAPI::USB", "USB", 8000)
        for devices in ((), (device, device)):
            with self.subTest(devices=devices), self.assertRaises(AudioCaptureError):
                resolve_input_device(device.key, devices)

    def test_saved_device_is_resolved_again_after_index_changes(self):
        device1 = InputDevice(1, "WASAPI::USB", "USB", 8000)
        device2 = InputDevice(4, "WASAPI::USB", "USB", 8000)
        sd = SimpleNamespace(RawInputStream=Mock(side_effect=lambda **kwargs: FakeStream([pcm_block(2000, 400)] * 6)))
        recorder = create_recorder(device1.key, max_seconds=1, initial_silence_seconds=.25,
                                   trailing_silence_seconds=.1, block_seconds=.05)
        with patch("desktop_agent.audio_devices.list_input_devices", side_effect=[(device1,), (device2,)]), patch.dict("sys.modules", sounddevice=sd):
            recorder.capture(threading.Event())
            recorder.capture(threading.Event())
        self.assertEqual([call.kwargs["device"] for call in sd.RawInputStream.call_args_list], [1, 4])


if __name__ == "__main__":
    unittest.main()
