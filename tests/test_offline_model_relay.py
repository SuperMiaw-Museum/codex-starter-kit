"""Offline transport proof with a real npm launcher and a disposable project."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from test_codex_exec_sandbox import fixture_config, responses_fixture

TOOLS = Path(__file__).resolve().parents[1] / 'bootstrap/tools'
sys.path.insert(0, str(TOOLS))
from bubblewrap_exec import OfflineBubblewrap
import offline_model_relay as relay


def payload(**extra):
    return json.dumps(dict({'model': relay.MODEL, 'input': [],
                            'stream': True, 'store': False}, **extra)).encode()


class RelayTests(unittest.TestCase):
    def test_fixed_payload_policy(self):
        self.assertEqual(json.loads(relay.request_body(payload()))['model'], relay.MODEL)
        for raw in (
            payload(model='other'), payload(stream=False), payload(store=True),
            payload(url='http://127.0.0.1/private'), payload(tools=[{'type': 'mcp'}]),
            payload(tools=[{'type': 'web_search'}]), payload(previous_response_id='other-turn'),
            payload(tools=[{'type': 'namespace', 'tools': [{'type': 'mcp'}]}]),
            payload(tool_choice={'type': 'mcp'}), b'{"input":[],"input":[]}',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                relay.request_body(raw)

    def test_redirect_is_refused_and_oversize_frame_closes_channel(self):
        seen = []

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(self.path)
                self.send_response(307)
                self.send_header('Location', '/private')
                self.end_headers()

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Redirect) as upstream:
            server = threading.Thread(target=upstream.serve_forever, daemon=True)
            server.start()
            host, worker = socket.socketpair()
            thread = threading.Thread(target=relay.serve_fixture, args=(host, upstream.server_port))
            thread.start()
            try:
                with worker:
                    deadline = time.monotonic() + 4
                    relay.frame(worker, payload(), deadline)
                    self.assertEqual(relay.receive(worker, 1, deadline), b'\x00')
                    relay.send(worker, struct.pack('!I', relay.MAX_REQUEST + 1), deadline)
                    self.assertEqual(worker.recv(1), b'')
                self.assertEqual(seen, [relay.ROUTE])
            finally:
                thread.join(5)
                upstream.shutdown()
                server.join()
            self.assertFalse(thread.is_alive())

    def test_stream_delivers_first_event_before_upstream_finishes(self):
        proceed = threading.Event()
        event = b'data: first\n\n'

        class Streaming(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(event) * 2))
                self.send_header('Set-Cookie', 'canary=upstream-only')
                self.end_headers()
                self.wfile.write(event)
                if proceed.wait(2):
                    self.wfile.write(event)

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Streaming) as upstream:
            server = threading.Thread(target=upstream.serve_forever, daemon=True)
            server.start()
            host, worker = socket.socketpair()
            thread = threading.Thread(target=relay.serve_fixture, args=(host, upstream.server_port))
            thread.start()
            try:
                with worker:
                    deadline = time.monotonic() + 4
                    relay.frame(worker, payload(), deadline)
                    self.assertEqual(relay.receive(worker, 1, deadline), b'\x01')
                    self.assertEqual(relay.receive(worker, relay.frame_size(worker, deadline),
                                                   deadline), event)
                    proceed.set()
                    self.assertEqual(relay.receive(worker, relay.frame_size(worker, deadline),
                                                   deadline), event)
                    self.assertEqual(relay.frame_size(worker, deadline), 0)
            finally:
                proceed.set()
                thread.join(5)
                upstream.shutdown()
                server.join()
            self.assertFalse(thread.is_alive())

    def test_disconnect_before_request_closes_host_capability(self):
        host, worker = socket.socketpair()
        thread = threading.Thread(target=relay.serve_fixture, args=(host, 1))
        thread.start()
        worker.close()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(host.fileno(), -1)


DRIVER = """
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

# The boundary transports only this preconnected capability on stdin.
# Codex gets a separate prompt pipe and no inherited capability descriptor.
channel = socket.socket(fileno=0)
with channel, WorkerAdapter(channel) as adapter:
    url = f'http://127.0.0.1:{adapter.server_port}/v1'
    Path(os.environ['CODEX_HOME'], 'config.toml').write_text(CONFIG.replace('FIXTURE_URL', url))
    thread = threading.Thread(target=adapter.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(sys.argv[1:], input=b'Run the fixture tool.', timeout=24)
    finally:
        adapter.shutdown()
        thread.join()
    sys.exit(result.returncode)
"""


@unittest.skipUnless(os.environ.get('CODEX_TEST_REAL_NPM') and shutil.which('bwrap'),
                     'requires explicit local npm Codex and Bubblewrap')
class RealRelayTests(unittest.TestCase):
    def test_real_npm_uses_host_fixture_across_private_namespace(self):
        with tempfile.TemporaryDirectory(prefix='kit-relay-') as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            canary = root / 'control'
            canary.write_text('untouched')
            launcher = os.environ['CODEX_TEST_REAL_NPM']
            fixture = 'web_search = "disabled"\n' + fixture_config('FIXTURE_URL', 'danger-full-access')
            source = Path(relay.__file__).read_text()

            class Profile(OfflineBubblewrap):
                def codex_argv(self, resume):
                    driver = source + '\nCONFIG = ' + repr(fixture) + '\n' + DRIVER
                    return ['/usr/bin/python3', '-IB', '-c', driver, *super().codex_argv(resume)]

            profile = Profile(project, launcher, (canary,))
            profile.validate(project)
            host_network = os.readlink('/proc/self/ns/net')
            command = (f'test ! -e {canary} && test -z "$LOT7_HOST_CANARY" '
                       f"&& test \"$(readlink /proc/self/ns/net)\" != '{host_network}' "
                       '&& printf worked > /project/result')
            with responses_fixture(command) as url:
                port = int(url.rsplit(':', 1)[1].split('/')[0])
                host, worker = socket.socketpair()
                try:
                    with worker:
                        child = subprocess.Popen(profile.command(None), stdin=worker,
                                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                 env={'LOT7_HOST_CANARY': 'host-only'})
                except BaseException:
                    host.close()
                    raise
                thread = threading.Thread(target=relay.serve_fixture, args=(host, port))
                thread.start()
                try:
                    out, err = child.communicate(timeout=28)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()
                    thread.join(5)
                    if thread.is_alive():
                        host.shutdown(socket.SHUT_RDWR)
                        thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(child.returncode, 0, (out, err))
                self.assertIn(b'turn.completed', out)
                self.assertEqual((project / 'result').read_text(), 'worked')
                self.assertEqual(canary.read_text(), 'untouched')
