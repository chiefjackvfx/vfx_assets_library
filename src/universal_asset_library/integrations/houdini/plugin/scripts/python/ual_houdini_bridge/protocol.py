from __future__ import annotations

import json
import struct


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024


def encode_message(document):
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("Bridge response exceeds the message limit.")
    return struct.pack("!I", len(payload)) + payload


def receive_message(connection):
    length = struct.unpack("!I", _receive_exact(connection, 4))[0]
    if length < 2 or length > MAX_MESSAGE_BYTES:
        raise ValueError("Bridge message has an invalid length.")
    document = json.loads(_receive_exact(connection, length).decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Bridge request must be a JSON object.")
    return document


def _receive_exact(connection, length):
    chunks = []
    remaining = length
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ConnectionError("Connection closed before the request completed.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
