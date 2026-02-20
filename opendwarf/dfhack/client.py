"""DFHack RPC client implementing the binary protocol over TCP."""

from __future__ import annotations

import logging
import socket
import struct
from dataclasses import dataclass, field

from opendwarf.proto import CoreProtocol_pb2 as proto

logger = logging.getLogger(__name__)

HANDSHAKE_MAGIC = b"DFHack?\n"
HANDSHAKE_REPLY = b"DFHack!\n"
HANDSHAKE_VERSION = 1

HEADER_FORMAT = "<hhI"  # id (int16), padding (int16), size (uint32) — 8 bytes total
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Special reply IDs
REPLY_RESULT = -1
REPLY_FAIL = -2
REPLY_TEXT = -3
REPLY_QUIT = -4

# Well-known bind ID for BindMethod itself
BIND_METHOD_ID = 0

DEFAULT_TIMEOUT = 10.0


class DFHackError(Exception):
    pass


class DFHackConnectionError(DFHackError):
    pass


class DFHackCommandError(DFHackError):
    pass


@dataclass
class RPCReply:
    """Result of an RPC call."""
    result_id: int
    payload: bytes
    text_notifications: list[proto.CoreTextNotification] = field(default_factory=list)


class DFHackClient:
    """Low-level DFHack RPC client."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5000, timeout: float = DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._next_bind_id: int = 1
        self._bound_methods: dict[str, int] = {}
        self._run_command_id: int | None = None
        self._run_lua_id: int | None = None

    def connect(self) -> None:
        """Connect to DFHack and perform handshake."""
        logger.info("Connecting to DFHack at %s:%d", self.host, self.port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        try:
            self._sock.connect((self.host, self.port))
        except OSError as e:
            self._sock = None
            raise DFHackConnectionError(f"Failed to connect to DFHack: {e}") from e

        # Handshake: send magic + version, expect magic + version back
        self._sock.sendall(HANDSHAKE_MAGIC + struct.pack("<I", HANDSHAKE_VERSION))
        reply = self._recv_exact(len(HANDSHAKE_REPLY) + 4)
        magic = reply[:len(HANDSHAKE_REPLY)]
        version = struct.unpack("<I", reply[len(HANDSHAKE_REPLY):])[0]
        if magic != HANDSHAKE_REPLY:
            raise DFHackConnectionError(f"Bad handshake reply: {magic!r}")
        logger.info("DFHack handshake OK, server version %d", version)

        # Bind core methods
        self._run_command_id = self._bind_method(
            "RunCommand", "dfproto.CoreRunCommandRequest", "dfproto.EmptyMessage", ""
        )
        self._run_lua_id = self._bind_method(
            "RunLua", "dfproto.CoreRunLuaRequest", "dfproto.StringListMessage", ""
        )

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._bound_methods.clear()
            self._run_command_id = None
            self._run_lua_id = None

    def reconnect(self) -> None:
        """Disconnect and reconnect."""
        logger.warning("Reconnecting to DFHack...")
        self.disconnect()
        self.connect()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def run_command(self, command: str, arguments: list[str] | None = None) -> list[str]:
        """Run a DFHack command. Returns text output lines."""
        if self._run_command_id is None:
            raise DFHackError("Not connected")
        req = proto.CoreRunCommandRequest()
        req.command = command
        if arguments:
            req.arguments.extend(arguments)
        reply = self._call(self._run_command_id, req.SerializeToString())
        return self._extract_text(reply)

    def run_lua(self, module: str, function: str, arguments: list[str] | None = None) -> list[str]:
        """Run a Lua function via RPC. Returns string list result."""
        if self._run_lua_id is None:
            raise DFHackError("Not connected")
        req = proto.CoreRunLuaRequest()
        req.module = module
        req.function = function
        if arguments:
            req.arguments.extend(arguments)
        reply = self._call(self._run_lua_id, req.SerializeToString())
        # Parse StringListMessage from the result payload
        result = proto.StringListMessage()
        if reply.payload:
            result.ParseFromString(reply.payload)
        return list(result.value)

    # -- Internal protocol methods --

    def _bind_method(self, method: str, input_msg: str, output_msg: str, plugin: str) -> int:
        """Bind a remote method, returning its assigned ID."""
        req = proto.CoreBindRequest()
        req.method = method
        req.input_msg = input_msg
        req.output_msg = output_msg
        req.plugin = plugin
        reply = self._call(BIND_METHOD_ID, req.SerializeToString())
        bind_reply = proto.CoreBindReply()
        bind_reply.ParseFromString(reply.payload)
        assigned_id = bind_reply.assigned_id
        self._bound_methods[method] = assigned_id
        logger.debug("Bound %s -> id %d", method, assigned_id)
        return assigned_id

    def _call(self, method_id: int, payload: bytes) -> RPCReply:
        """Send an RPC request and read the reply (handling TEXT and FAIL)."""
        if not self._sock:
            raise DFHackConnectionError("Not connected")

        # Send: id + padding(0) + size + payload
        header = struct.pack(HEADER_FORMAT, method_id, 0, len(payload))
        self._sock.sendall(header + payload)

        # Read replies until we get RESULT or FAIL
        text_notifications: list[proto.CoreTextNotification] = []
        while True:
            try:
                resp_header = self._recv_exact(HEADER_SIZE)
            except (socket.timeout, TimeoutError) as e:
                logger.error("RPC timeout waiting for reply (method_id=%d)", method_id)
                self.reconnect()
                raise DFHackError(f"RPC timeout: {e}") from e

            resp_id, _, resp_size = struct.unpack(HEADER_FORMAT, resp_header)
            resp_payload = self._recv_exact(resp_size) if resp_size > 0 else b""

            if resp_id == REPLY_TEXT:
                notif = proto.CoreTextNotification()
                notif.ParseFromString(resp_payload)
                text_notifications.append(notif)
            elif resp_id == REPLY_RESULT:
                return RPCReply(resp_id, resp_payload, text_notifications)
            elif resp_id == REPLY_FAIL:
                text = self._extract_text(RPCReply(resp_id, resp_payload, text_notifications))
                raise DFHackCommandError(
                    f"RPC call failed (method_id={method_id}): {' '.join(text)}"
                )
            elif resp_id == REPLY_QUIT:
                self.disconnect()
                raise DFHackConnectionError("DFHack sent QUIT")
            else:
                # Unknown reply ID — likely an error; skip
                logger.warning("Unknown reply id %d, size %d", resp_id, resp_size)

    def _recv_exact(self, size: int) -> bytes:
        """Read exactly `size` bytes from the socket."""
        if not self._sock:
            raise DFHackConnectionError("Not connected")
        buf = bytearray()
        while len(buf) < size:
            chunk = self._sock.recv(size - len(buf))
            if not chunk:
                raise DFHackConnectionError("Connection closed by DFHack")
            buf.extend(chunk)
        return bytes(buf)

    @staticmethod
    def _extract_text(reply: RPCReply) -> list[str]:
        """Extract text lines from REPLY_TEXT notifications."""
        lines = []
        for notif in reply.text_notifications:
            for frag in notif.fragments:
                text = frag.text
                if isinstance(text, bytes):
                    text = text.decode("utf-8", errors="replace")
                lines.append(text)
        return lines
