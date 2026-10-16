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


if __name__ == "__main__":
    unittest.main()
