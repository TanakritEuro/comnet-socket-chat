"""Headless tests for server.py: a real server on a free port, plus raw-socket test clients.

Each test starts a fresh server, so tests don't share any users or groups.
Run all tests with:  python -m unittest discover -s tests -v
"""

import io
import json
import logging
import os
import socket
import struct
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "import server" works from any folder

import ai
from protocol import (
    ALREADY_MEMBER, BAD_NAME, BAD_REQUEST, BUSY, DM, ERROR, GROUP_CREATE, GROUP_EXISTS, GROUP_JOIN,
    GROUP_LEAVE, GROUP_MSG, GROUPS, HELLO, MSG, NAME_TAKEN, NO_SUCH_GROUP, NO_SUCH_USER,
    NOT_MEMBER, SUMMARIZE, SUMMARY, USERS, WELCOME, recv_msg, send_msg,
)
from server import ChatServer

logging.getLogger("ai").addHandler(logging.NullHandler())   # keep expected warnings off the console

TIMEOUT = 2.0       # seconds to wait for a message we expect
QUIET_TIME = 0.3    # seconds to listen when checking that a message does NOT arrive


class TestClient:
    """A raw-socket client that speaks our protocol, standing in for client.py."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT)
        self.received = []                  # every message this client has read, for leak checks

    def send(self, type_, **fields):
        send_msg(self.sock, {"type": type_, **fields})

    def wait_for(self, type_, **fields):
        """Read until a message of this type (with these field values) arrives; skip any others."""
        try:
            while True:
                msg = recv_msg(self.sock)
                if msg is None:
                    raise AssertionError(f"server closed the connection while waiting for {type_}")
                self.received.append(msg)
                if msg["type"] == type_ and all(msg.get(k) == v for k, v in fields.items()):
                    return msg
        except socket.timeout:
            raise AssertionError(f"timed out waiting for {type_} {fields}") from None

    def assert_no(self, type_):
        """Listen for QUIET_TIME seconds and fail if a message of this type arrives."""
        self.sock.settimeout(QUIET_TIME)
        try:
            while True:
                msg = recv_msg(self.sock)
                if msg is None:
                    return
                self.received.append(msg)
                if msg["type"] == type_:
                    raise AssertionError(f"should not have received: {msg}")
        except socket.timeout:
            pass                            # silence is what we wanted
        finally:
            self.sock.settimeout(TIMEOUT)

    def login(self, name):
        self.send(HELLO, name=name)
        return self.wait_for(WELCOME)

    def close(self):
        self.sock.close()

    def crash(self):
        """Close with a TCP RST instead of a FIN, like a killed process.
        The server then sees ConnectionResetError instead of an empty recv()."""
        linger_off = struct.pack("hh" if os.name == "nt" else "ii", 1, 0)   # SO_LINGER on, 0 seconds
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger_off)
        self.sock.close()


def offline_summarizer(room, messages):
    """Default for test servers: never read the real .env key, never call the real API."""
    return ai.summarize(room, messages, ai.Config(api_key="", models=[]))


class ServerTestCase(unittest.TestCase):
    """Starts a fresh server on a free port before each test."""

    summarizer = staticmethod(offline_summarizer)

    def setUp(self):
        self.server = ChatServer("127.0.0.1", 0, summarizer=self.summarizer)   # port 0: OS picks a free port
        self.port = self.server.start()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.stop()

    def connect(self, name=None):
        """Open a test client; log it in if a name is given."""
        client = TestClient(self.port)
        self.clients.append(client)
        if name is not None:
            client.login(name)
        return client

    def create_group(self, creator, group):
        """Create a group and wait until the server's GROUPS broadcast includes it."""
        creator.send(GROUP_CREATE, group=group)
        while not any(g["name"] == group for g in creator.wait_for(GROUPS)["groups"]):
            pass                                        # an older GROUPS (e.g. the one sent at login)


class R3UniqueNameTests(ServerTestCase):
    def test_welcome_echoes_the_trimmed_name(self):
        client = self.connect()
        client.send(HELLO, name="  alice  ")
        self.assertEqual(client.wait_for(WELCOME)["name"], "alice")

    def test_duplicate_name_is_rejected_then_retry_works(self):
        self.connect("alice")
        second = self.connect()
        second.send(HELLO, name="alice")
        self.assertEqual(second.wait_for(ERROR)["code"], NAME_TAKEN)
        # Same connection, new name: accepted.
        self.assertEqual(second.login("alice2")["name"], "alice2")

    def test_invalid_names_are_rejected(self):
        client = self.connect()
        for bad in ["", "   ", "a|b", "x" * 21, 123]:
            client.send(HELLO, name=bad)
            self.assertEqual(client.wait_for(ERROR)["code"], BAD_NAME, bad)

    def test_other_messages_before_hello_are_rejected(self):
        client = self.connect()
        client.send(DM, to="bob", text="hi")
        self.assertEqual(client.wait_for(ERROR)["code"], BAD_REQUEST)

    def test_name_is_free_again_after_disconnect(self):
        watcher = self.connect("watcher")
        alice = self.connect("alice")
        watcher.wait_for(USERS, users=["alice", "watcher"])
        alice.close()
        watcher.wait_for(USERS, users=["watcher"])      # server has removed alice
        self.assertEqual(self.connect().login("alice")["name"], "alice")


class R4UserListTests(ServerTestCase):
    def test_list_includes_own_name(self):
        alice = self.connect("alice")
        alice.wait_for(USERS, users=["alice"])

    def test_everyone_gets_updates_on_join_and_leave(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.wait_for(USERS, users=["alice", "bob"])
        bob.wait_for(USERS, users=["alice", "bob"])
        bob.close()                                     # normal close (TCP FIN)
        alice.wait_for(USERS, users=["alice"])

    def test_crashed_client_is_removed(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.wait_for(USERS, users=["alice", "bob"])
        self.clients.remove(bob)
        bob.crash()                                     # abrupt close (TCP RST)
        alice.wait_for(USERS, users=["alice"])


class R7PrivateMessageTests(ServerTestCase):
    def test_dm_reaches_only_sender_and_recipient(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        carol = self.connect("carol")
        alice.send(DM, to="bob", text="secret")
        expected = {"room": "dm:alice|bob", "from": "alice", "text": "secret"}
        bob.wait_for(MSG, **expected)
        msg = alice.wait_for(MSG, **expected)           # the sender gets a copy for its own window
        self.assertIsInstance(msg["ts"], float)
        carol.assert_no(MSG)                            # a third client must NOT see it

    def test_dm_to_offline_user(self):
        alice = self.connect("alice")
        alice.send(DM, to="nobody", text="hi")
        self.assertEqual(alice.wait_for(ERROR)["code"], NO_SUCH_USER)

    def test_dm_to_self_and_empty_text_are_rejected(self):
        alice = self.connect("alice")
        self.connect("bob")
        alice.send(DM, to="alice", text="hi")
        self.assertEqual(alice.wait_for(ERROR)["code"], BAD_REQUEST)
        alice.send(DM, to="bob", text="   ")
        self.assertEqual(alice.wait_for(ERROR)["code"], BAD_REQUEST)


class R8CreateGroupTests(ServerTestCase):
    def test_creator_is_the_only_member(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.send(GROUP_CREATE, group="netlab")
        expected = [{"name": "netlab", "creator": "alice", "members": ["alice"]}]
        alice.wait_for(GROUPS, groups=expected)
        bob.wait_for(GROUPS, groups=expected)           # bob sees it but was not added

    def test_duplicate_group_name(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        self.create_group(alice, "netlab")
        bob.send(GROUP_CREATE, group="netlab")
        self.assertEqual(bob.wait_for(ERROR)["code"], GROUP_EXISTS)

    def test_invalid_group_name(self):
        alice = self.connect("alice")
        alice.send(GROUP_CREATE, group="   ")
        self.assertEqual(alice.wait_for(ERROR)["code"], BAD_NAME)


class R9GroupListTests(ServerTestCase):
    def test_list_shows_all_groups_with_members(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        self.create_group(alice, "netlab")
        self.create_group(bob, "anime")
        bob.send(GROUP_JOIN, group="netlab")
        alice.wait_for(GROUPS, groups=[
            {"name": "anime", "creator": "bob", "members": ["bob"]},
            {"name": "netlab", "creator": "alice", "members": ["alice", "bob"]},
        ])

    def test_new_client_receives_existing_groups(self):
        alice = self.connect("alice")
        self.create_group(alice, "netlab")
        late = self.connect()
        late.send(HELLO, name="late")
        late.wait_for(WELCOME)
        late.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": ["alice"]}])


class R10JoinGroupTests(ServerTestCase):
    def test_join_adds_only_the_sender(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        carol = self.connect("carol")
        self.create_group(alice, "netlab")
        # Even if a client tries to name someone else, only the sender is added.
        bob.send(GROUP_JOIN, group="netlab", name="carol", user="carol")
        expected = [{"name": "netlab", "creator": "alice", "members": ["alice", "bob"]}]
        carol.wait_for(GROUPS, groups=expected)
        alice.wait_for(GROUPS, groups=expected)

    def test_join_errors(self):
        alice = self.connect("alice")
        alice.send(GROUP_JOIN, group="missing")
        self.assertEqual(alice.wait_for(ERROR)["code"], NO_SUCH_GROUP)
        self.create_group(alice, "netlab")
        alice.send(GROUP_JOIN, group="netlab")          # the creator is already a member
        self.assertEqual(alice.wait_for(ERROR)["code"], ALREADY_MEMBER)

    def test_leave(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        self.create_group(alice, "netlab")
        bob.send(GROUP_LEAVE, group="netlab")
        self.assertEqual(bob.wait_for(ERROR)["code"], NOT_MEMBER)
        bob.send(GROUP_JOIN, group="netlab")
        bob.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": ["alice", "bob"]}])
        bob.send(GROUP_LEAVE, group="netlab")
        bob.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": ["alice"]}])


class R11GroupMessageTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.connect("alice")      # creator and member
        self.bob = self.connect("bob")          # joins below
        self.carol = self.connect("carol")      # never joins
        self.create_group(self.alice, "netlab")
        self.bob.send(GROUP_JOIN, group="netlab")
        self.carol.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": ["alice", "bob"]}])

    def test_message_reaches_members_only(self):
        self.bob.send(GROUP_MSG, group="netlab", text="hello group")
        expected = {"room": "group:netlab", "from": "bob", "text": "hello group"}
        self.alice.wait_for(MSG, **expected)
        self.bob.wait_for(MSG, **expected)
        self.carol.assert_no(MSG)

    def test_non_member_message_is_rejected_and_not_delivered(self):
        self.carol.send(GROUP_MSG, group="netlab", text="let me in")
        self.assertEqual(self.carol.wait_for(ERROR)["code"], NOT_MEMBER)
        self.alice.assert_no(MSG)
        self.bob.assert_no(MSG)

    def test_member_who_left_stops_receiving(self):
        self.bob.send(GROUP_LEAVE, group="netlab")
        self.alice.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": ["alice"]}])
        self.alice.send(GROUP_MSG, group="netlab", text="bob is gone")
        self.alice.wait_for(MSG, text="bob is gone")
        self.bob.assert_no(MSG)

    def test_message_to_missing_group(self):
        self.alice.send(GROUP_MSG, group="missing", text="hi")
        self.assertEqual(self.alice.wait_for(ERROR)["code"], NO_SUCH_GROUP)


class Special1SummarizeTests(ServerTestCase):
    """AI summaries: privacy checks, requester-only replies, and no key leaks. The AI itself is faked."""

    def setUp(self):
        self.calls = []                     # (room, [texts]) the summarizer was asked about
        self.release = threading.Event()
        self.release.set()                  # set = answer immediately; clear() to make it "slow"
        super().setUp()
        self.alice = self.connect("alice")
        self.bob = self.connect("bob")
        self.carol = self.connect("carol")

    def summarizer(self, room, messages):
        self.calls.append((room, [m["text"] for m in messages]))
        self.release.wait(TIMEOUT)
        return f"{len(messages)} messages", "fake-model"

    def test_summary_goes_only_to_the_requester(self):
        self.alice.send(DM, to="bob", text="secret plan")
        self.bob.wait_for(MSG, text="secret plan")
        self.alice.send(SUMMARIZE, room="dm:alice|bob")
        reply = self.alice.wait_for(SUMMARY)
        self.assertEqual(reply, {"type": SUMMARY, "room": "dm:alice|bob", "text": "1 messages", "model": "fake-model"})
        self.assertEqual(self.calls, [("dm:alice|bob", ["secret plan"])])
        self.bob.assert_no(SUMMARY)
        self.carol.assert_no(SUMMARY)

    def test_non_member_cannot_summarize_a_group(self):
        self.create_group(self.alice, "netlab")
        self.alice.send(GROUP_MSG, group="netlab", text="members only")
        self.alice.wait_for(MSG, text="members only")
        self.carol.send(SUMMARIZE, room="group:netlab")
        self.assertEqual(self.carol.wait_for(ERROR)["code"], NOT_MEMBER)
        self.carol.assert_no(SUMMARY)
        self.assertEqual(self.calls, [])            # the AI never saw the group's messages

    def test_cannot_summarize_someone_elses_dm(self):
        self.alice.send(DM, to="bob", text="just us")
        self.bob.wait_for(MSG, text="just us")
        self.carol.send(SUMMARIZE, room="dm:alice|bob")
        self.assertEqual(self.carol.wait_for(ERROR)["code"], NOT_MEMBER)
        self.assertEqual(self.calls, [])

    def test_bad_room_ids(self):
        for room, code in [("lobby", BAD_REQUEST), (None, BAD_REQUEST), ("group:missing", NO_SUCH_GROUP)]:
            self.alice.send(SUMMARIZE, room=room)
            self.assertEqual(self.alice.wait_for(ERROR)["code"], code, room)

    def test_late_joiner_only_gets_messages_from_after_joining(self):
        self.create_group(self.alice, "netlab")
        self.alice.send(GROUP_MSG, group="netlab", text="before bob joined")
        self.alice.wait_for(MSG, text="before bob joined")
        self.bob.send(GROUP_JOIN, group="netlab")
        self.alice.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": ["alice", "bob"]}])
        self.alice.send(GROUP_MSG, group="netlab", text="after bob joined")
        self.bob.wait_for(MSG, text="after bob joined")
        self.bob.send(SUMMARIZE, room="group:netlab")
        self.bob.wait_for(SUMMARY)
        self.assertEqual(self.calls, [("group:netlab", ["after bob joined"])])

    def test_buffer_keeps_only_the_last_50_messages(self):
        for i in range(55):
            self.alice.send(DM, to="bob", text=f"m{i}")
        self.bob.wait_for(MSG, text="m54")
        self.alice.send(SUMMARIZE, room="dm:alice|bob")
        self.alice.wait_for(SUMMARY)
        texts = self.calls[0][1]
        self.assertEqual((len(texts), texts[0], texts[-1]), (50, "m5", "m54"))

    def test_server_keeps_serving_while_the_ai_is_slow(self):
        self.release.clear()                        # the fake AI now blocks until we release it
        self.alice.send(DM, to="bob", text="hello")
        self.bob.wait_for(MSG, text="hello")
        self.alice.send(SUMMARIZE, room="dm:alice|bob")
        self.alice.send(SUMMARIZE, room="dm:alice|bob")
        self.assertEqual(self.alice.wait_for(ERROR)["code"], BUSY)   # one summary at a time per user
        self.bob.send(DM, to="alice", text="still chatting")         # handled while the AI "thinks"
        self.alice.wait_for(MSG, text="still chatting")
        self.release.set()
        self.alice.wait_for(SUMMARY)


class Special1KeyLeakTests(ServerTestCase):
    """Real ai.summarize with a fake key and a mocked HTTP layer: the key must never reach a client."""

    KEY = "TEST-SECRET-KEY-123"

    def summarizer(self, room, messages):
        return ai.summarize(room, messages, ai.Config(api_key=self.KEY, models=["model-a", "model-b"]))

    def summarize_with(self, urlopen_result):
        alice, bob = self.connect("alice"), self.connect("bob")
        alice.send(DM, to="bob", text="hi")
        bob.wait_for(MSG, text="hi")
        with mock.patch("ai.urllib.request.urlopen", side_effect=urlopen_result) as urlopen:
            alice.send(SUMMARIZE, room="dm:alice|bob")
            reply = alice.wait_for(SUMMARY)
        everything = json.dumps(alice.received + bob.received)
        self.assertNotIn(self.KEY, everything)
        for call in urlopen.call_args_list:         # and the key only ever travels in the header
            self.assertNotIn(self.KEY, call.args[0].full_url)
        return reply

    def test_key_not_leaked_on_success(self):
        body = {"candidates": [{"content": {"parts": [{"text": "- alice said hi"}]}}]}
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps(body).encode())
        self.assertEqual(self.summarize_with([response])["model"], "model-a")

    def test_key_not_leaked_when_every_model_fails(self):
        errors = [urllib.error.HTTPError("https://example.invalid", 429, f"quota for {self.KEY}", {}, io.BytesIO())
                  for _ in range(2)]
        reply = self.summarize_with(errors)
        self.assertEqual(reply["model"], ai.OFFLINE)


class RobustnessTests(ServerTestCase):
    def test_unknown_type_gets_error_but_connection_stays(self):
        alice = self.connect("alice")
        alice.send("FLY_TO_MOON")
        self.assertEqual(alice.wait_for(ERROR)["code"], BAD_REQUEST)
        self.connect("bob")
        alice.wait_for(USERS, users=["alice", "bob"])   # still connected and receiving

    def test_garbage_frame_gets_error_then_disconnect(self):
        client = self.connect()
        client.sock.sendall(struct.pack("!I", 8) + b"not json")
        self.assertEqual(client.wait_for(ERROR)["code"], BAD_REQUEST)
        self.assertIsNone(recv_msg(client.sock))        # server hung up


if __name__ == "__main__":
    unittest.main()
