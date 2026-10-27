"""Tests for the framing in protocol.py (R2): message boundaries over a TCP byte stream."""

import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "import protocol" works from any folder

from protocol import (
    BAD_REQUEST, HEADER, MAX_FRAME, TOO_LARGE,
    ProtocolError, clean_name, dm_partner, dm_room, group_room, recv_msg, send_msg,
)


def frame(obj):
    """Build the raw bytes of one frame by hand, the same way send_msg does."""
    body = json.dumps(obj).encode("utf-8")
    return HEADER.pack(len(body)) + body


class FramingTests(unittest.TestCase):
    def setUp(self):
        # socketpair() gives two connected sockets, like a tiny client/server connection.
        self.a, self.b = socket.socketpair()
        self.b.settimeout(2)

    def tearDown(self):
        self.a.close()
        self.b.close()

    def test_round_trip_with_thai_text(self):
        send_msg(self.a, {"type": "MSG", "text": "สวัสดี hello"})
        self.assertEqual(recv_msg(self.b), {"type": "MSG", "text": "สวัสดี hello"})

    def test_frame_arriving_in_small_pieces(self):
        """recv() may return part of a frame; recv_msg must keep reading until it is whole."""
        data = frame({"type": "MSG", "text": "split across many recv() calls"})

        def trickle():
            for i in range(0, len(data), 3):        # 3 bytes at a time, with pauses
                self.a.sendall(data[i:i + 3])
                time.sleep(0.005)

        threading.Thread(target=trickle).start()
        self.assertEqual(recv_msg(self.b)["text"], "split across many recv() calls")

    def test_two_frames_in_one_send(self):
        """Two frames glued together in one TCP segment must still come out as two messages."""
        self.a.sendall(frame({"type": "A"}) + frame({"type": "B"}))
        self.assertEqual(recv_msg(self.b)["type"], "A")
        self.assertEqual(recv_msg(self.b)["type"], "B")

    def test_closed_connection_returns_none(self):
        self.a.close()
        self.assertIsNone(recv_msg(self.b))

    def test_oversized_frame_is_rejected(self):
        self.a.sendall(HEADER.pack(MAX_FRAME + 1))
        with self.assertRaises(ProtocolError) as ctx:
            recv_msg(self.b)
        self.assertEqual(ctx.exception.code, TOO_LARGE)

    def test_body_that_is_not_a_json_object_is_rejected(self):
        for body in [b"not json", b"[1, 2]", b'{"no_type": 1}']:
            self.a.sendall(HEADER.pack(len(body)) + body)
            with self.assertRaises(ProtocolError) as ctx:
                recv_msg(self.b)
            self.assertEqual(ctx.exception.code, BAD_REQUEST)


class HelperTests(unittest.TestCase):
    def test_dm_room_is_the_same_for_both_users(self):
        self.assertEqual(dm_room("bob", "alice"), "dm:alice|bob")
        self.assertEqual(dm_room("alice", "bob"), "dm:alice|bob")

    def test_dm_partner(self):
        self.assertEqual(dm_partner("dm:alice|bob", "alice"), "bob")
        self.assertEqual(dm_partner("dm:alice|bob", "bob"), "alice")

    def test_group_room(self):
        self.assertEqual(group_room("netlab"), "group:netlab")

    def test_clean_name(self):
        self.assertEqual(clean_name("  alice "), "alice")
        for bad in ["", "   ", "a|b", "x" * 21, None, 42]:
            self.assertIsNone(clean_name(bad), bad)


if __name__ == "__main__":
    unittest.main()
