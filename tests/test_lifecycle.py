"""Gateway lifecycle helpers: reading the pidfile and probing a port."""
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omni import config
from omni.commands import lifecycle


class ReadRecord(unittest.TestCase):
    def _with_pidfile(self, content: str | None):
        directory = tempfile.mkdtemp()
        path = Path(directory) / "omni.pid"
        if content is not None:
            path.write_text(content)
        return patch.object(config, "PIDFILE", path)

    def test_current_json_record(self):
        with self._with_pidfile(json.dumps({"pid": 123, "host": "h", "port": 1})):
            self.assertEqual(lifecycle._read_record(), {"pid": 123, "host": "h", "port": 1})

    def test_legacy_bare_pid(self):
        with self._with_pidfile("456"):
            self.assertEqual(lifecycle._read_record(), {"pid": 456})

    def test_junk_is_empty(self):
        with self._with_pidfile("not a pid"):
            self.assertEqual(lifecycle._read_record(), {})

    def test_missing_file_is_empty(self):
        with self._with_pidfile(None):
            self.assertEqual(lifecycle._read_record(), {})


class GatewayPid(unittest.TestCase):
    def test_our_own_pid_is_never_the_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "omni.pid"
            path.write_text(json.dumps({"pid": os.getpid()}))
            with patch.object(config, "PIDFILE", path):
                self.assertIsNone(lifecycle.gateway_pid())

    def test_a_dead_pid_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "omni.pid"
            path.write_text(json.dumps({"pid": 2_000_000_000}))
            with patch.object(config, "PIDFILE", path):
                self.assertIsNone(lifecycle.gateway_pid())


class PortInUse(unittest.TestCase):
    def test_sees_a_listening_socket(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertTrue(lifecycle.port_in_use("127.0.0.1", port))
        self.assertFalse(lifecycle.port_in_use("127.0.0.1", port))


class OpenPlayground(unittest.TestCase):
    def test_up_opens_the_playground_when_the_gateway_is_already_running(self):
        record = {"pid": 123, "host": "127.0.0.1", "port": 10000}
        with patch.object(lifecycle, "gateway_pid", return_value=123), \
             patch.object(lifecycle, "_read_record", return_value=record), \
             patch.object(lifecycle.webbrowser, "open") as browser:
            self.assertEqual(lifecycle.up("127.0.0.1", 10000, open_browser=True), 0)
        browser.assert_called_once_with("http://127.0.0.1:10000/")

    def test_up_without_the_flag_leaves_the_browser_alone(self):
        record = {"pid": 123, "host": "127.0.0.1", "port": 10000}
        with patch.object(lifecycle, "gateway_pid", return_value=123), \
             patch.object(lifecycle, "_read_record", return_value=record), \
             patch.object(lifecycle.webbrowser, "open") as browser:
            self.assertEqual(lifecycle.up("127.0.0.1", 10000), 0)
        browser.assert_not_called()

    def test_a_wildcard_bind_opens_loopback(self):
        with patch.object(lifecycle.webbrowser, "open") as browser:
            lifecycle.open_playground("0.0.0.0", 10000)
        browser.assert_called_once_with("http://127.0.0.1:10000/")


if __name__ == "__main__":
    unittest.main()
