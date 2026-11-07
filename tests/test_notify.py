import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from kenpi.core.errors import ConfigurationError
from kenpi.core.events import Event, EventBus, EventType
from kenpi.notify.notifier import Notifier, validate_url


class Sink(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        Sink.received.append((self.path, dict(self.headers), self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class NotifierTests(unittest.TestCase):
    def setUp(self):
        Sink.received = []
        self.server = HTTPServer(("127.0.0.1", 0), Sink)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def wait(self, count):
        deadline = time.monotonic() + 5
        while len(Sink.received) < count and time.monotonic() < deadline:
            time.sleep(0.05)

    def test_failure_reaches_ntfy_and_webhook_over_real_http(self):
        bus = EventBus()
        notifier = Notifier(bus, self.base + "/topic", self.base + "/hook")
        notifier.start()
        try:
            bus.publish(EventType.SERVICE_FAILED, "camera", reason="boom")
            bus.publish(EventType.SERVICE_STARTED, "camera")  # not forwarded
            self.wait(2)
        finally:
            notifier.stop()
            bus.close()
        paths = {entry[0]: entry for entry in Sink.received}
        self.assertEqual(set(paths), {"/topic", "/hook"})
        self.assertEqual(paths["/topic"][1]["Priority"], "high")
        self.assertIn(b"boom", paths["/topic"][2])
        self.assertEqual(json.loads(paths["/hook"][2])["type"], "system.service_failed")
        self.assertEqual(notifier.delivered, 2)

    def test_unreachable_sink_is_counted_not_raised(self):
        bus = EventBus()
        notifier = Notifier(bus, "http://127.0.0.1:9/t", timeout=0.5)
        notifier.deliver(Event(1, "x", 0.0, EventType.SAFE_MODE_ENTERED, "main"))
        self.assertEqual((notifier.delivered, notifier.failed), (0, 1))

    def test_inactive_without_urls(self):
        self.assertFalse(Notifier(EventBus()).active)

    def test_url_validation(self):
        for bad in ("ftp://x/y", "http://", "http://u:p@host/t", "nonsense"):
            with self.assertRaises(ValueError):
                validate_url(bad)
        self.assertEqual(validate_url("https://ntfy.sh/kenpi"), "https://ntfy.sh/kenpi")

    def test_config_rejects_bad_url(self):
        from kenpi.core.config import KenpiConfig

        with self.assertRaises(ConfigurationError):
            KenpiConfig(notify_ntfy_url="file:///etc/passwd")


if __name__ == "__main__":
    unittest.main()
