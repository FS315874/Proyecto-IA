import json
import logging
import threading
import time
import unittest

from desktop_agent.outbound_relay import (
    HttpsRelayTransport,
    OutboundRelayClient,
    RelayEndpoint,
    RelayError,
    RelayState,
)
from desktop_agent.remote_protocol import EncryptedEnvelope


ENVELOPE = EncryptedEnvelope(
    "device-1",
    "msg-1",
    1,
    1000,
    "bm5ubm5ubm5ubm5u",
    "eA==",
)


class FakeResponse:
    def __init__(self, status=200, body=b"") -> None:
        self.status = status
        self.body = body

    def read(self, _limit):
        return self.body


class FakeConnection:
    def __init__(self, response, calls, *args, **kwargs) -> None:
        self.response = response
        self.calls = calls
        self.calls.append(("connect", args, kwargs))

    def request(self, method, path, body=None, headers=None):
        self.calls.append(("request", method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.calls.append(("close",))


class RelayTransportTests(unittest.TestCase):
    def factory(self, response, calls):
        return lambda *args, **kwargs: FakeConnection(
            response,
            calls,
            *args,
            **kwargs,
        )

    def test_endpoint_rejects_urls_queries_and_short_ids(self) -> None:
        for host, relay_id in (
            ("https://example.test", "a" * 16),
            ("user:pass@example.test", "a" * 16),
            ("example.test", "short"),
        ):
            with self.subTest(host=host), self.assertRaises(RelayError):
                RelayEndpoint(host, relay_id)

    def test_receive_uses_fixed_https_path_and_parses_envelope(self) -> None:
        calls = []
        response = FakeResponse(200, json.dumps(ENVELOPE.to_dict()).encode())
        transport = HttpsRelayTransport(
            RelayEndpoint("relay.example.test", "public_relay_id_1"),
            connection_factory=self.factory(response, calls),
        )
        received = transport.receive()
        self.assertEqual(received, ENVELOPE)
        request = next(call for call in calls if call[0] == "request")
        self.assertEqual(request[1], "GET")
        self.assertEqual(
            request[2],
            "/v1/relay/public_relay_id_1/poll",
        )
        self.assertNotIn("?", request[2])

    def test_send_transports_only_encrypted_envelope(self) -> None:
        calls = []
        transport = HttpsRelayTransport(
            RelayEndpoint("relay.example.test", "public_relay_id_1"),
            connection_factory=self.factory(FakeResponse(204), calls),
        )
        transport.send(ENVELOPE)
        request = next(call for call in calls if call[0] == "request")
        body = json.loads(request[3])
        self.assertEqual(body, ENVELOPE.to_dict())
        self.assertNotIn("Authorization", request[4])

    def test_non_success_response_is_redacted(self) -> None:
        transport = HttpsRelayTransport(
            RelayEndpoint("relay.example.test", "public_relay_id_1"),
            connection_factory=self.factory(
                FakeResponse(500, b"private relay stack"),
                [],
            ),
        )
        with self.assertRaisesRegex(RelayError, "rechazó") as caught:
            transport.receive()
        self.assertNotIn("private", str(caught.exception))


class FakeTransport:
    def __init__(self) -> None:
        self.incoming = [ENVELOPE]
        self.sent = []
        self.received = threading.Event()

    def receive(self):
        self.received.set()
        return self.incoming.pop(0) if self.incoming else None

    def send(self, envelope):
        self.sent.append(envelope)


class FakeGateway:
    def handle(self, envelope):
        return envelope


class OutboundRelayClientTests(unittest.TestCase):
    def test_client_starts_only_explicitly_and_stops(self) -> None:
        transport = FakeTransport()
        client = OutboundRelayClient(
            transport,
            FakeGateway(),
            logging.Logger(self.id()),
            0.05,
        )
        self.assertEqual(client.state, RelayState.OFFLINE)
        self.assertFalse(transport.received.is_set())

        client.start()
        self.assertTrue(transport.received.wait(1))
        deadline = time.monotonic() + 1
        while not transport.sent and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(transport.sent, [ENVELOPE])
        self.assertTrue(client.stop(1))
        self.assertEqual(client.state, RelayState.STOPPED)
