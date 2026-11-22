"""Experimental Responses relay. Only a trusted loopback fixture can be upstream."""
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import socket
import struct
import time

ROUTE = '/v1/responses'
MODEL = 'gpt-5.4'
MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
CHUNK = 16384
TURN_LIMIT = 8
ATTEMPT_SECONDS = 45
IO_SECONDS = 3
FIELDS = frozenset({
    'model', 'instructions', 'input', 'tools', 'tool_choice', 'parallel_tool_calls',
    'reasoning', 'store', 'stream', 'include', 'prompt_cache_key', 'text',
    'client_metadata',
})


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate JSON key')
    return result


def local_function(tool):
    return (isinstance(tool, dict) and tool.get('type') == 'function'
            and not tool.keys() - {'type', 'name', 'description', 'parameters', 'strict'})


def local_tool(tool):
    if local_function(tool):
        return True
    return (isinstance(tool, dict) and tool.get('type') == 'namespace'
            and not tool.keys() - {'type', 'name', 'description', 'tools'}
            and isinstance(tool.get('tools'), list) and all(map(local_function, tool['tools'])))


def request_body(raw):
    body = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    if (not isinstance(body, dict) or body.keys() - FIELDS
            or body.get('model') != MODEL or body.get('stream') is not True
            or body.get('store') is not False or not isinstance(body.get('input'), list)
            or body.get('tool_choice', 'auto') not in ('auto', 'none', 'required')):
        raise ValueError('Unsupported Responses payload')
    tools = body.get('tools', [])
    if not isinstance(tools, list) or not all(map(local_tool, tools)):
        raise ValueError('Only local function declarations are allowed')
    return json.dumps(body, allow_nan=False).encode('utf-8')


def remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError('Relay deadline')
    return min(IO_SECONDS, left)


def receive(channel, size, deadline):
    data = bytearray()
    while len(data) < size:
        channel.settimeout(remaining(deadline))
        chunk = channel.recv(size - len(data))
        if not chunk:
            raise EOFError
        data.extend(chunk)
    return bytes(data)


def send(channel, data, deadline):
    channel.settimeout(remaining(deadline))
    channel.sendall(data)


def frame(channel, data, deadline):
    send(channel, struct.pack('!I', len(data)) + data, deadline)


def frame_size(channel, deadline):
    return struct.unpack('!I', receive(channel, 4, deadline))[0]


def stream_response(channel, port, body, deadline):
    upstream = http.client.HTTPConnection('127.0.0.1', port, timeout=remaining(deadline))
    try:
        upstream.request('POST', ROUTE, body, {'Content-Type': 'application/json',
                                               'Accept': 'text/event-stream'})
        transport = upstream.sock
        # Keep the transport even when HTTPConnection detaches a closing response.
        with upstream.getresponse() as response:
            if response.status != 200 or response.headers.get_content_type() != 'text/event-stream':
                send(channel, b'\x00', deadline)
                return
            send(channel, b'\x01', deadline)
            size = 0
            while True:
                if response.isclosed():
                    chunk = b''
                else:
                    transport.settimeout(remaining(deadline))
                    chunk = response.read1(CHUNK)
                if not chunk and response.length:
                    raise ValueError('Truncated Responses stream')
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise ValueError('Responses stream too large')
                frame(channel, chunk, deadline)
                if not chunk:
                    return
    finally:
        upstream.close()


def serve_fixture(channel, port):
    """Own one preconnected capability; close it permanently on malformed framing."""
    with channel:
        if type(port) is not int or not 0 < port <= 65535:
            raise ValueError('Invalid fixture port')
        deadline = time.monotonic() + ATTEMPT_SECONDS
        try:
            for _ in range(TURN_LIMIT):
                # Allow Codex to execute tools between turns, bounded by attempt lifetime.
                left = deadline - time.monotonic()
                if left <= 0:
                    return
                channel.settimeout(left)
                first = channel.recv(1)
                if not first:
                    return
                size = struct.unpack('!I', first + receive(channel, 3, deadline))[0]
                if not 0 < size <= MAX_REQUEST:
                    return
                raw = receive(channel, size, deadline)
                try:
                    body = request_body(raw)
                except (ValueError, RecursionError):
                    send(channel, b'\x00', deadline)
                    continue
                if len(body) > MAX_REQUEST:
                    send(channel, b'\x00', deadline)
                    continue
                stream_response(channel, port, body, deadline)
        except (OSError, EOFError, ValueError, http.client.HTTPException):
            # Do not log content, headers, or upstream error bodies.
            return


class WorkerAdapter(HTTPServer):
    """Expose the capability only on the worker namespace's private loopback."""

    def __init__(self, channel):
        self.channel = channel
        super().__init__(('127.0.0.1', 0), WorkerRequest)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(IO_SECONDS)
        return connection, address

    def handle_error(self, request, client_address):
        pass


class WorkerRequest(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        lengths = self.headers.get_all('Content-Length', [])
        if (self.path != ROUTE or len(lengths) != 1 or not lengths[0].isascii()
                or not lengths[0].isdecimal() or len(lengths[0]) > 7
                or self.headers.get('Transfer-Encoding')
                or self.headers.get_content_type() != 'application/json'):
            self.send_error(400)
            return
        length = int(lengths[0])
        if not 0 < length <= MAX_REQUEST:
            self.send_error(413)
            return
        deadline = time.monotonic() + ATTEMPT_SECONDS
        raw = self.rfile.read(length)
        if len(raw) != length:
            return
        channel = self.server.channel
        started = False
        try:
            frame(channel, raw, deadline)
            if receive(channel, 1, deadline) != b'\x01':
                self.send_error(502)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            started = True
            while True:
                size = frame_size(channel, deadline)
                if size > CHUNK:
                    raise ValueError('Invalid response frame')
                data = receive(channel, size, deadline)
                self.connection.settimeout(remaining(deadline))
                # A missing final chunk tells Codex that the upstream stream failed.
                self.wfile.write(f'{size:x}\r\n'.encode() + data + b'\r\n')
                if not size:
                    return
        except (EOFError, OSError, ValueError):
            # A partial turn cannot be resynchronized or retried on this capability.
            channel.close()
            if not started:
                self.send_error(502)

    protocol_version = 'HTTP/1.1'
    # Use one local connection per turn regardless of client preference.
    def end_headers(self):
        self.close_connection = True
        self.send_header('Connection', 'close')
        super().end_headers()
