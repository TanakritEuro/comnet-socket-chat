"""Headless tests for server.py: a real server on a free port, plus raw-socket test clients.

Each test starts a fresh server, so tests don't share any users or groups.
Run all tests with:  python -m unittest discover -s tests -v
"""

import os
import socket
import struct
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "import server" works from any folder

from protocol import (
    ALREADY_MEMBER, BAD_NAME, BAD_REQUEST, DM, ERROR, GROUP_CREATE, GROUP_EXISTS, GROUP_JOIN,
    GROUP_LEAVE, GROUP_MSG, GROUPS, HELLO, MSG, NAME_TAKEN, NO_SUCH_GROUP, NO_SUCH_USER,
    NOT_MEMBER, USERS, WELCOME, recv_msg, send_msg,
)
from server import ChatServer

TIMEOUT = 2.0       # seconds to wait for a message we expect
QUIET_TIME = 0.3    # seconds to listen when checking that a message does NOT arrive


class TestClient:
    """A raw-socket client that speaks our protocol, standing in for client.py."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT)

    def send(self, type_, **fields):
        send_msg(self.sock, {"type": type_, **fields})

    def wait_for(self, type_, **fields):
        """Read until a message of this type (with these field values) arrives; skip any others."""
        try:
            while True:
                msg = recv_msg(self.sock)
                if msg is None:
                    raise AssertionError(f"server closed the connection while waiting for {type_}")
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


class ServerTestCase(unittest.TestCase):
    """Starts a fresh server on a free port before each test."""

    def setUp(self):
        self.server = ChatServer("127.0.0.1", 0)    # port 0: the OS picks a free port
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
