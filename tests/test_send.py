"""Tests for how the server sends: one queue + writer thread per connection (see Conn in server.py).

- frames to one socket never interleave, even when many threads send at once
- a slow receiver (or one that never reads) delays only itself, never the other clients
- a client that falls too far behind is disconnected, so the server's memory stays bounded
"""

import base64
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "import server" works from any folder
sys.path.insert(0, str(Path(__file__).resolve().parent))          # so "import test_server" works too

import server
from protocol import DM, FILE, GROUP_JOIN, GROUP_MSG, GROUPS, HELLO, MAX_FILE_BYTES, MSG, USERS, encode_frame, recv_msg, send_msg
from test_server import ServerTestCase

FAST = 1.0      # seconds: a message between two unaffected clients must arrive faster than this


class SleepySocket:
    """Wraps a real socket so every sendall() first sleeps: a client on a very slow link."""

    def __init__(self, real, delay):
        self.real, self.delay = real, delay

    def sendall(self, data):
        time.sleep(self.delay)
        self.real.sendall(data)

    def __getattr__(self, name):            # recv, close, shutdown... go to the real socket
        return getattr(self.real, name)


class ConnTests(unittest.TestCase):
    def test_frames_never_interleave_under_concurrent_sends(self):
        """8 threads queue frames of up to 200 KB on ONE connection at the same time. Only the writer
        thread writes to the socket, so every frame arrives whole and each thread's frames stay in order."""
        a, b = socket.socketpair()
        b.settimeout(10)
        conn = server.Conn(a, "test")
        threads, per_thread = 8, 25

        def flood(t):
            for i in range(per_thread):
                conn.put(encode_frame({"type": "MSG", "t": t, "i": i, "text": "x" * (1000 + 7919 * (i + t) % 200_000)}))

        workers = [threading.Thread(target=flood, args=(t,)) for t in range(threads)]
        for w in workers:
            w.start()
        received = [recv_msg(b) for _ in range(threads * per_thread)]   # recv_msg fails on a mixed-up frame
        for w in workers:
            w.join()
        for t in range(threads):
            self.assertEqual([m["i"] for m in received if m["t"] == t], list(range(per_thread)))
        conn.close()
        self.assertIsNone(recv_msg(b))      # close(): queued frames sent, then the socket is closed
        b.close()


class SlowReceiverTests(ServerTestCase):
    def raw_client(self, name, rcvbuf=4096):
        """A client that logs in and then never reads, with a tiny receive buffer so its socket fills fast."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        sock.connect(("127.0.0.1", self.port))
        send_msg(sock, {"type": HELLO, "name": name})
        self.addCleanup(sock.close)
        return sock

    def assert_fast(self, sender, receiver, text, **send):
        """Send `text` from sender and check that the receiver gets it in under FAST seconds."""
        start = time.monotonic()
        sender.send(DM if "to" in send else GROUP_MSG, text=text, **send)
        receiver.wait_for(MSG, text=text)
        self.assertLess(time.monotonic() - start, FAST, f"{text!r} was delayed")

    def test_a_slow_link_delays_only_that_client(self):
        alice, bob, carol, dave = (self.connect(n) for n in ("alice", "bob", "carol", "dave"))
        self.create_group(alice, "netlab")
        members = ["alice"]
        for member, name in ((bob, "bob"), (carol, "carol")):      # one at a time, so bob is before carol
            member.send(GROUP_JOIN, group="netlab")
            members.append(name)
            alice.wait_for(GROUPS, groups=[{"name": "netlab", "creator": "alice", "members": members}])
        with self.server.lock:              # from now on every write to bob takes 3 seconds
            bob_conn = self.server.clients["bob"]
            bob_conn.sock = SleepySocket(bob_conn.sock, delay=3)
        # bob is the 2nd member, before carol: the old design would make carol wait for bob's 3 s.
        self.assert_fast(alice, carol, "to the group", group="netlab")
        self.assert_fast(carol, dave, "carol to dave", to="dave")
        self.assert_fast(dave, carol, "dave to carol", to="carol")
        bob.sock.settimeout(10)                     # bob still gets it, just 3 s later
        bob.wait_for(MSG, text="to the group")

    def test_a_client_that_never_reads_delays_nobody(self):
        alice, carol, dave = self.connect("alice"), self.connect("carol"), self.connect("dave")
        self.raw_client("bob")
        alice.wait_for(USERS, users=["alice", "bob", "carol", "dave"])
        big = base64.b64encode(b"z" * MAX_FILE_BYTES).decode("ascii")
        for i in range(3):                  # about 20 MB that bob never reads: his socket fills up
            alice.send(FILE, room="dm:alice|bob", filename=f"big{i}.bin", size=MAX_FILE_BYTES, data=big)
            alice.wait_for(FILE, filename=f"big{i}.bin")    # alice reads her own echo
        self.assert_fast(carol, dave, "still fast", to="dave")
        self.assert_fast(alice, carol, "alice is not stuck either", to="carol")

    def test_a_client_too_far_behind_is_disconnected(self):
        alice, carol = self.connect("alice"), self.connect("carol")
        self.raw_client("bob")
        alice.wait_for(USERS, users=["alice", "bob", "carol"])
        big = base64.b64encode(b"z" * MAX_FILE_BYTES).decode("ascii")
        with mock.patch("server.MAX_QUEUED_BYTES", 8 * 1024 * 1024):
            for i in range(6):              # bob never reads: after ~8 MiB waiting, the server gives up on him
                alice.send(FILE, room="dm:alice|bob", filename=f"f{i}.bin", size=MAX_FILE_BYTES, data=big)
                alice.wait_for(FILE, filename=f"f{i}.bin")
                if "bob" not in self.server.clients:
                    break
            alice.wait_for(USERS, users=["alice", "carol"])     # everyone is told bob has gone
        self.assert_fast(alice, carol, "life goes on", to="carol")

    def test_concurrent_senders_reach_one_client_whole_and_in_order(self):
        """4 clients send 40 DMs each to alice at the same time; alice's socket gets all 160 frames
        whole, each sender's messages in order, and the ts of each room only goes up."""
        alice = self.connect("alice")
        senders = [self.connect(f"s{i}") for i in range(4)]
        alice.wait_for(USERS, users=sorted(["alice"] + [f"s{i}" for i in range(4)]))
        go = threading.Event()

        def flood(client, i):
            go.wait()
            for n in range(40):
                client.send(DM, to="alice", text=f"s{i}:{n}")

        threads = [threading.Thread(target=flood, args=(c, i)) for i, c in enumerate(senders)]
        for t in threads:
            t.start()
        go.set()
        got = [alice.wait_for(MSG) for _ in range(160)]
        for t in threads:
            t.join()
        for i in range(4):
            mine = [m for m in got if m["from"] == f"s{i}"]
            self.assertEqual([m["text"] for m in mine], [f"s{i}:{n}" for n in range(40)])
            stamps = [m["ts"] for m in mine]
            self.assertEqual(stamps, sorted(stamps))


if __name__ == "__main__":
    unittest.main()
