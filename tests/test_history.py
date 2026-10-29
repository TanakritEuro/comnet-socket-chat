"""Tests for chat history (special 3): HISTORY {room} -> HISTORY {room, messages}.

Uses the same real-server-plus-raw-socket setup as test_server.py. No AI calls are made.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "import server" works from any folder
sys.path.insert(0, str(Path(__file__).resolve().parent))          # so "import test_server" works too

import server
from protocol import (
    DM, ERROR, GROUP_JOIN, GROUP_LEAVE, GROUP_MSG, GROUPS, HISTORY, MSG, NO_SUCH_GROUP, NOT_MEMBER,
    SUMMARIZE, SUMMARY, USERS,
)
from test_server import ServerTestCase


class Special3HistoryTests(ServerTestCase):
    def setUp(self):
        self.summarized = []                # what the (fake) AI was given
        super().setUp()
        self.alice = self.connect("alice")
        self.bob = self.connect("bob")
        self.carol = self.connect("carol")

    def summarizer(self, room, messages):
        self.summarized.append([m["text"] for m in messages])
        return "fake summary", "fake-model"

    def history(self, client, room):
        client.send(HISTORY, room=room)
        return client.wait_for(HISTORY, room=room)["messages"]

    def dm(self, sender, to, receiver, text):
        """Send a DM and read it on both ends. The sender must read its echo too: a client that stops
        reading fills its TCP buffer and stalls the server (see the ChatServer docstring)."""
        sender.send(DM, to=to, text=text)
        sender.wait_for(MSG, text=text)
        return receiver.wait_for(MSG, text=text)

    def test_dm_history_returns_the_exact_messages_that_were_delivered(self):
        first = self.dm(self.alice, "bob", self.bob, "hi bob")
        second = self.dm(self.bob, "alice", self.alice, "hi alice")
        self.assertEqual(self.history(self.alice, "dm:alice|bob"), [first, second])   # same dicts, same ts ids
        self.assertEqual(self.history(self.bob, "dm:alice|bob"), [first, second])
        self.carol.assert_no(HISTORY)                           # a reply goes to the requester only

    def test_dm_history_survives_a_reconnect(self):
        self.dm(self.alice, "bob", self.bob, "before you left")
        self.clients.remove(self.alice)
        self.alice.close()
        self.bob.wait_for(USERS, users=["bob", "carol"])         # the server has removed alice
        alice_again = self.connect("alice")
        texts = [m["text"] for m in self.history(alice_again, "dm:alice|bob")]
        self.assertEqual(texts, ["before you left"])

    def test_someone_elses_dm_history_is_refused(self):
        self.dm(self.alice, "bob", self.bob, "just us")
        self.carol.send(HISTORY, room="dm:alice|bob")
        error = self.carol.wait_for(ERROR)
        self.assertEqual((error["code"], error["request"]), (NOT_MEMBER, HISTORY))
        self.carol.assert_no(HISTORY)                           # and no messages at all

    def test_non_member_cannot_read_group_history(self):
        self.create_group(self.alice, "netlab")
        self.alice.send(GROUP_MSG, group="netlab", text="members only")
        self.alice.wait_for(MSG, text="members only")
        self.carol.send(HISTORY, room="group:netlab")
        self.assertEqual(self.carol.wait_for(ERROR)["code"], NOT_MEMBER)
        self.carol.assert_no(HISTORY)

    def test_late_joiner_does_not_see_older_group_messages(self):
        self.create_group(self.alice, "netlab")
        self.alice.send(GROUP_MSG, group="netlab", text="before bob joined")
        self.alice.wait_for(MSG, text="before bob joined")
        self.bob.send(GROUP_JOIN, group="netlab")
        self.bob.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": ["alice", "bob"]}])
        self.alice.send(GROUP_MSG, group="netlab", text="after bob joined")
        self.bob.wait_for(MSG, text="after bob joined")
        self.assertEqual([m["text"] for m in self.history(self.bob, "group:netlab")], ["after bob joined"])
        self.assertEqual([m["text"] for m in self.history(self.alice, "group:netlab")],
                         ["before bob joined", "after bob joined"])

    def test_leaving_and_rejoining_starts_the_history_again(self):
        self.create_group(self.alice, "netlab")
        self.bob.send(GROUP_JOIN, group="netlab")
        self.alice.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": ["alice", "bob"]}])
        self.alice.send(GROUP_MSG, group="netlab", text="while bob was in")
        self.bob.wait_for(MSG, text="while bob was in")
        self.bob.send(GROUP_LEAVE, group="netlab")
        self.bob.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": ["alice"]}])
        self.bob.send(HISTORY, room="group:netlab")
        self.assertEqual(self.bob.wait_for(ERROR)["code"], NOT_MEMBER)   # not a member any more
        self.bob.send(GROUP_JOIN, group="netlab")
        self.bob.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": ["alice", "bob"]}])
        self.assertEqual(self.history(self.bob, "group:netlab"), [])      # history restarts at the new join

    def test_bad_room_ids(self):
        for room, code in [("lobby", "BAD_REQUEST"), (None, "BAD_REQUEST"), ("group:missing", NO_SUCH_GROUP)]:
            self.alice.send(HISTORY, room=room)
            self.assertEqual(self.alice.wait_for(ERROR)["code"], code, room)

    def test_cap_drops_the_oldest_messages(self):
        for i in range(server.BUFFER_SIZE + 5):
            self.alice.send(DM, to="bob", text=f"m{i}")
        self.bob.wait_for(MSG, text=f"m{server.BUFFER_SIZE + 4}")
        texts = [m["text"] for m in self.history(self.bob, "dm:alice|bob")]
        self.assertEqual(len(texts), server.BUFFER_SIZE)
        self.assertEqual((texts[0], texts[-1]), ("m5", f"m{server.BUFFER_SIZE + 4}"))

    def test_summarize_still_reads_only_the_newest_50(self):
        for i in range(120):
            self.alice.send(DM, to="bob", text=f"m{i}")
        self.bob.wait_for(MSG, text="m119")
        self.assertEqual(len(self.history(self.alice, "dm:alice|bob")), 120)   # history has them all
        self.alice.send(SUMMARIZE, room="dm:alice|bob")
        self.alice.wait_for(SUMMARY)
        given = self.summarized[0]
        self.assertEqual((len(given), given[0], given[-1]), (server.AI_CONTEXT, "m70", "m119"))

    def test_huge_messages_are_left_out_so_the_reply_fits_in_one_frame(self):
        big = "x" * 600_000                                     # two of these exceed HISTORY_MAX_BYTES
        self.dm(self.alice, "bob", self.bob, "old " + big)
        self.dm(self.alice, "bob", self.bob, "new " + big)
        self.dm(self.alice, "bob", self.bob, "small")
        texts = [m["text"][:4] for m in self.history(self.bob, "dm:alice|bob")]
        self.assertEqual(texts, ["new ", "smal"])               # only the oldest was dropped


class MessageBufferTests(unittest.TestCase):
    def test_find_by_room_and_ts(self):
        buffer = server.MessageBuffer(size=3)
        for i in range(5):
            buffer.add({"type": MSG, "room": "dm:a|b", "from": "a", "text": f"m{i}", "ts": float(i)})
        self.assertEqual(buffer.find("dm:a|b", 4.0)["text"], "m4")
        self.assertIsNone(buffer.find("dm:a|b", 1.0))          # dropped by the cap
        self.assertIsNone(buffer.find("group:x", 4.0))
        self.assertEqual([m["text"] for m in buffer.recent("dm:a|b", limit=None)], ["m2", "m3", "m4"])
        self.assertEqual([m["text"] for m in buffer.recent("dm:a|b", since=3.0)], ["m3", "m4"])


if __name__ == "__main__":
    unittest.main()
