"""Tests for sending files and images (special 2): FILE {room, filename, size, data} -> FILE {..., from, ts}.

Uses the same real-server-plus-raw-socket setup as test_server.py. No AI calls are made.
"""

import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "import server" works from any folder
sys.path.insert(0, str(Path(__file__).resolve().parent))          # so "import test_server" works too

import ai
import server
from protocol import (
    BAD_REQUEST, ERROR, FILE, HISTORY, MAX_FILE_BYTES, MSG, NO_SUCH_GROUP, NO_SUCH_USER, NOT_MEMBER,
    SUMMARIZE, SUMMARY, TOO_LARGE, clean_filename,
)
from test_server import ServerTestCase


def b64(raw):
    return base64.b64encode(raw).decode("ascii")


class FilenameTests(unittest.TestCase):
    def test_folders_are_stripped(self):
        self.assertEqual(clean_filename("notes.txt"), "notes.txt")
        self.assertEqual(clean_filename("C:\\Users\\me\\notes.txt"), "notes.txt")
        self.assertEqual(clean_filename("../../etc/passwd"), "passwd")
        self.assertEqual(clean_filename("  photo one.png "), "photo one.png")

    def test_bad_names(self):
        for bad in (None, 5, "", "   ", "dir/", "..", ".", "x" * 101):
            self.assertIsNone(clean_filename(bad), bad)


class Special2FileTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.connect("alice")
        self.bob = self.connect("bob")
        self.carol = self.connect("carol")

    def send_file(self, client, room, raw=b"hello file", filename="hi.txt", **extra):
        client.send(FILE, room=room, filename=filename, size=len(raw), data=b64(raw), **extra)

    def history(self, client, room):
        client.send(HISTORY, room=room)
        return client.wait_for(HISTORY, room=room)["messages"]

    def test_dm_file_reaches_only_sender_and_recipient(self):
        self.send_file(self.alice, "dm:alice|bob", b"\x00\x01binary\xff", "data.bin")
        for client in (self.alice, self.bob):                   # the sender gets an echo too
            got = client.wait_for(FILE)
            self.assertEqual((got["room"], got["from"], got["filename"], got["size"]),
                             ("dm:alice|bob", "alice", "data.bin", 9))
            self.assertEqual(base64.b64decode(got["data"]), b"\x00\x01binary\xff")   # the bytes arrive unchanged
            self.assertIsInstance(got["ts"], float)
        self.carol.assert_no(FILE)

    def test_server_measures_the_size_and_cleans_the_name(self):
        self.alice.send(FILE, room="dm:alice|bob", filename="..\\..\\secret\\evil.exe", size=1, data=b64(b"12345"))
        got = self.bob.wait_for(FILE)
        self.assertEqual((got["filename"], got["size"]), ("evil.exe", 5))   # not the client's "1", and no folders

    def test_group_file_reaches_members_only(self):
        self.create_group(self.alice, "netlab")
        self.bob.send("GROUP_JOIN", group="netlab")
        self.bob.wait_for("GROUPS")
        self.send_file(self.alice, "group:netlab", b"for the group")
        self.assertEqual(self.alice.wait_for(FILE)["room"], "group:netlab")
        self.assertEqual(self.bob.wait_for(FILE)["from"], "alice")
        self.carol.assert_no(FILE)

    def test_non_member_cannot_send_to_a_group(self):
        self.create_group(self.alice, "netlab")
        self.send_file(self.carol, "group:netlab")
        error = self.carol.wait_for(ERROR)
        self.assertEqual((error["code"], error["request"]), (NOT_MEMBER, FILE))
        self.alice.assert_no(FILE)

    def test_cannot_send_into_someone_elses_dm(self):
        self.send_file(self.carol, "dm:alice|bob")
        self.assertEqual(self.carol.wait_for(ERROR)["code"], NOT_MEMBER)
        self.alice.assert_no(FILE)
        self.bob.assert_no(FILE)

    def test_room_errors(self):
        self.send_file(self.alice, "group:nothing")
        self.assertEqual(self.alice.wait_for(ERROR)["code"], NO_SUCH_GROUP)
        self.send_file(self.alice, "not a room")
        self.assertEqual(self.alice.wait_for(ERROR)["code"], BAD_REQUEST)
        self.send_file(self.alice, "dm:alice|zed")                # zed is not online
        self.assertEqual(self.alice.wait_for(ERROR)["code"], NO_SUCH_USER)
        self.send_file(self.alice, "dm:alice|alice")              # a DM with yourself
        self.assertEqual(self.alice.wait_for(ERROR)["code"], BAD_REQUEST)

    def test_bad_requests_are_refused_and_delivered_to_nobody(self):
        self.alice.send(FILE, room="dm:alice|bob", filename="", size=1, data=b64(b"x"))
        self.assertEqual(self.alice.wait_for(ERROR)["code"], BAD_REQUEST)
        self.alice.send(FILE, room="dm:alice|bob", filename="a.txt", size=1, data="not base64 !!")
        self.assertEqual(self.alice.wait_for(ERROR)["code"], BAD_REQUEST)
        self.alice.send(FILE, room="dm:alice|bob", filename="a.txt", size=1, data=123)
        self.assertEqual(self.alice.wait_for(ERROR)["code"], BAD_REQUEST)
        self.alice.send(FILE, room="dm:alice|bob", filename="a.txt", size=0, data="")      # an empty file
        self.assertEqual(self.alice.wait_for(ERROR)["code"], BAD_REQUEST)
        self.bob.assert_no(FILE)

    def test_file_over_the_limit_is_refused_but_the_limit_itself_is_fine(self):
        self.send_file(self.alice, "dm:alice|bob", b"x" * (MAX_FILE_BYTES + 1), "big.bin")
        error = self.alice.wait_for(ERROR)
        self.assertEqual((error["code"], error["request"]), (TOO_LARGE, FILE))
        self.bob.assert_no(FILE)
        # The connection stays open and a file of exactly 5 MiB still goes through.
        self.send_file(self.alice, "dm:alice|bob", b"y" * MAX_FILE_BYTES, "max.bin")
        self.assertEqual(self.bob.wait_for(FILE)["size"], MAX_FILE_BYTES)

    def test_small_file_is_kept_in_history_with_its_bytes(self):
        self.send_file(self.alice, "dm:alice|bob", b"keep me", "keep.txt")
        sent = self.bob.wait_for(FILE)
        self.assertEqual(self.history(self.bob, "dm:alice|bob"), [sent])      # exactly what was delivered

    def test_big_file_stays_in_history_without_its_bytes(self):
        raw = b"z" * (server.HISTORY_FILE_MAX + 1)
        self.send_file(self.alice, "dm:alice|bob", raw, "big.bin")
        sent = self.bob.wait_for(FILE)
        self.assertEqual(base64.b64decode(sent["data"]), raw)               # live delivery has the bytes
        (kept,) = self.history(self.bob, "dm:alice|bob")
        self.assertEqual((kept["filename"], kept["size"], kept["data"], kept["ts"]),
                         ("big.bin", len(raw), None, sent["ts"]))           # history: name and size only

    def test_history_of_files_respects_privacy(self):
        self.send_file(self.alice, "dm:alice|bob")
        self.bob.wait_for(FILE)
        self.carol.send(HISTORY, room="dm:alice|bob")
        self.assertEqual(self.carol.wait_for(ERROR)["code"], NOT_MEMBER)


class FileAndAiTests(ServerTestCase):
    """A file must reach the AI as one short line, never as base64 bytes."""

    def summarizer(self, room, messages):
        self.prompt = ai.build_prompt(room, messages)
        return "fake summary", "fake-model"

    def test_summary_sees_the_file_name_but_not_its_bytes(self):
        alice, bob = self.connect("alice"), self.connect("bob")
        secret = b"TOP-SECRET-BYTES" * 10
        alice.send(FILE, room="dm:alice|bob", filename="plan.pdf", size=len(secret), data=b64(secret))
        bob.wait_for(FILE)
        alice.wait_for(FILE)
        alice.send(SUMMARIZE, room="dm:alice|bob")
        alice.wait_for(SUMMARY)
        self.assertIn("[sent a file: plan.pdf]", self.prompt)
        self.assertNotIn(b64(secret), self.prompt)

    def test_offline_summary_describes_a_file(self):
        messages = [{"type": FILE, "from": "alice", "filename": "plan.pdf", "data": "AAAA", "ts": 1.0},
                    {"type": MSG, "from": "bob", "text": "thanks", "ts": 2.0}]
        text = ai.offline_summary(messages, "test")
        self.assertIn("- alice: [sent a file: plan.pdf]", text)
        self.assertNotIn("AAAA", text)


if __name__ == "__main__":
    unittest.main()
