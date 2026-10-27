"""TCP chat server.

Run:  python server.py --host 0.0.0.0 --port 5050

One thread per connected client. The server is the only place that decides who
receives a message, so privacy (R7, R11) cannot be bypassed by a modified client.
"""

import argparse
import logging
import os
import socket
import threading
import time

from protocol import (
    ALREADY_MEMBER, BAD_NAME, BAD_REQUEST, BYE, DM, ERROR, GROUP_CREATE, GROUP_EXISTS,
    GROUP_JOIN, GROUP_LEAVE, GROUP_MSG, GROUPS, HELLO, MAX_NAME_LEN, MSG, NAME_TAKEN,
    NO_SUCH_GROUP, NO_SUCH_USER, NOT_MEMBER, USERS, WELCOME,
    ProtocolError, clean_name, dm_room, group_room, recv_msg, send_msg,
)

log = logging.getLogger("server")

NAME_RULES = f"must be 1-{MAX_NAME_LEN} characters and must not contain '|'"


class ChatServer:
    """Holds the shared chat state and runs one thread per connected client.

    Locking rule: every read or write of self.clients / self.groups, and every
    send, happens while holding self.lock. Holding the lock while sending means:
      1. two threads never write into the same socket at the same time, which
         would mix the bytes of two frames together, and
      2. every client receives USERS/GROUPS updates in the same order the state changed.
    Trade-off: a client that stops reading could stall the server once its TCP
    send buffer fills up. That is acceptable for a classroom chat.
    """

    def __init__(self, host="0.0.0.0", port=5050):
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.clients = {}   # name -> socket of that client's TCP connection
        self.groups = {}    # group name -> {"creator": name, "members": [names, in join order]}
        self.listen_sock = None
        self.running = False
        # Which method handles which message type (after login).
        self.handlers = {
            DM: self.handle_dm,
            GROUP_CREATE: self.handle_group_create,
            GROUP_JOIN: self.handle_group_join,
            GROUP_LEAVE: self.handle_group_leave,
            GROUP_MSG: self.handle_group_msg,
        }

    # ------------------------------------------------------------------
    # Networking: listen, accept, one thread per client
    # ------------------------------------------------------------------

    def start(self):
        """Create the listening socket. Returns the port (port=0 lets the OS pick a free one)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)   # IPv4 + TCP
        if os.name != "nt":
            # On Linux/macOS this lets us restart the server right away instead of waiting
            # for old connections to leave TIME_WAIT. On Windows the option means something
            # riskier (two programs could share the port), so we skip it there.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen()
        # A timeout on accept() lets serve_forever() check self.running regularly,
        # and lets Ctrl+C work on Windows (a fully blocking accept() ignores it there).
        sock.settimeout(0.5)
        self.listen_sock = sock
        self.port = sock.getsockname()[1]
        self.running = True
        return self.port

    def serve_forever(self):
        """Accept new TCP connections until stop() is called. Each one gets its own thread."""
        while self.running:
            try:
                conn, addr = self.listen_sock.accept()
            except socket.timeout:
                continue                    # no new client in the last 0.5 s; check self.running again
            except OSError:
                break                       # the listening socket was closed by stop()
            conn.settimeout(None)           # client sockets block on recv(); only accept() needs a timeout
            threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()

    def stop(self):
        """Stop accepting new clients."""
        self.running = False
        if self.listen_sock is not None:
            self.listen_sock.close()

    def handle_client(self, sock, addr):
        """Thread body for one client: log in, then handle its messages until it disconnects."""
        log.info("connection from %s:%s", *addr)
        name = None
        try:
            name = self.login(sock)
            if name is None:
                return                      # hung up before logging in (finally still closes the socket)
            while True:
                msg = recv_msg(sock)
                if msg is None or msg["type"] == BYE:
                    break                   # clean disconnect
                handler = self.handlers.get(msg["type"])
                if handler is None:
                    with self.lock:
                        self.send_error(sock, BAD_REQUEST, f"unknown message type {msg['type']!r}")
                else:
                    handler(name, msg)
        except ProtocolError as e:
            # The peer sent bytes that don't follow our framing: say why, then hang up.
            with self.lock:
                self.send_error(sock, e.code, str(e))
        except OSError as e:
            # ConnectionResetError (peer crashed or was killed) and friends are OSError subclasses.
            log.info("%s: connection error: %s", name or addr, e)
        finally:
            if name is not None:
                self.remove_client(name)
            sock.close()
            log.info("%s disconnected", name or f"{addr[0]}:{addr[1]}")

    # ------------------------------------------------------------------
    # Sending helpers (caller must hold self.lock)
    # ------------------------------------------------------------------

    def send(self, sock, msg):
        """Send one message to one socket. A dead socket is ignored here,
        because that client's own thread will notice the disconnect and clean up."""
        try:
            send_msg(sock, msg)
        except OSError:
            pass

    def send_error(self, sock, code, text):
        """Send an ERROR to one client."""
        self.send(sock, {"type": ERROR, "code": code, "message": text})

    def broadcast_users(self):
        """R4: send the full online list to every client. Everyone gets the same list,
        so each client also sees its own name in it."""
        msg = {"type": USERS, "users": sorted(self.clients)}
        for sock in self.clients.values():
            self.send(sock, msg)

    def groups_message(self):
        """R9: build a GROUPS message listing every group with its creator and members."""
        groups = [
            {"name": group, "creator": info["creator"], "members": list(info["members"])}
            for group, info in sorted(self.groups.items())
        ]
        return {"type": GROUPS, "groups": groups}

    def broadcast_groups(self):
        """R9: send the group list to every client after any create/join/leave."""
        msg = self.groups_message()
        for sock in self.clients.values():
            self.send(sock, msg)

    # ------------------------------------------------------------------
    # Login and logout (R3, R4)
    # ------------------------------------------------------------------

    def login(self, sock):
        """R3: wait for a HELLO with a valid name that nobody online is using.

        Returns the accepted name, or None if the client disconnected first.
        After an ERROR the client may simply send another HELLO on the same connection.
        """
        while True:
            msg = recv_msg(sock)
            if msg is None or msg["type"] == BYE:
                return None
            with self.lock:
                if msg["type"] != HELLO:
                    self.send_error(sock, BAD_REQUEST, "send HELLO first")
                    continue
                name = clean_name(msg.get("name"))
                if name is None:
                    self.send_error(sock, BAD_NAME, f"name {NAME_RULES}")
                    continue
                if name in self.clients:
                    self.send_error(sock, NAME_TAKEN, f"the name {name!r} is already in use")
                    continue
                self.clients[name] = sock
                self.send(sock, {"type": WELCOME, "name": name})
                self.broadcast_users()                  # R4: everyone sees the new name
                self.send(sock, self.groups_message())  # R9: the newcomer sees existing groups
                log.info("%s logged in", name)
                return name

    def remove_client(self, name):
        """Forget a disconnected client and send everyone the updated user list (R4).

        Group memberships are kept on purpose: members are listed by name, so someone
        who reconnects with the same name is still in their groups.
        """
        with self.lock:
            self.clients.pop(name, None)
            self.broadcast_users()

    # ------------------------------------------------------------------
    # Message handlers: each takes (sender name, message dict).
    # "return self.send_error(...)" means: reply with an ERROR and stop here.
    # ------------------------------------------------------------------

    def handle_dm(self, name, msg):
        """R7: deliver a private message to exactly two sockets, the sender's and the recipient's."""
        to, text = msg.get("to"), msg.get("text")
        with self.lock:
            me = self.clients[name]
            if not is_text(text):
                return self.send_error(me, BAD_REQUEST, "text must be a non-empty string")
            if to == name:
                return self.send_error(me, BAD_REQUEST, "you cannot send a DM to yourself")
            if not isinstance(to, str) or to not in self.clients:
                return self.send_error(me, NO_SUCH_USER, f"{to!r} is not online")
            out = {"type": MSG, "room": dm_room(name, to), "from": name, "text": text, "ts": time.time()}
            # Privacy is decided here: no other client's socket is ever written to.
            self.send(me, out)                  # echo, so the sender's window shows it too
            self.send(self.clients[to], out)

    def handle_group_create(self, name, msg):
        """R8: create a new group whose only member is its creator."""
        group = clean_name(msg.get("group"))
        with self.lock:
            me = self.clients[name]
            if group is None:
                return self.send_error(me, BAD_NAME, f"group name {NAME_RULES}")
            if group in self.groups:
                return self.send_error(me, GROUP_EXISTS, f"group {group!r} already exists")
            self.groups[group] = {"creator": name, "members": [name]}   # nobody else is added
            self.broadcast_groups()

    def handle_group_join(self, name, msg):
        """R10: add the *sender* to a group. Nobody can add someone else:
        the only name we ever add is the one tied to this connection."""
        group = msg.get("group")
        with self.lock:
            me = self.clients[name]
            info = self.find_group(me, group)
            if info is None:
                return
            if name in info["members"]:
                return self.send_error(me, ALREADY_MEMBER, f"you are already in {group!r}")
            info["members"].append(name)
            self.broadcast_groups()

    def handle_group_leave(self, name, msg):
        """Remove the sender from a group. The group stays listed even if it becomes empty."""
        group = msg.get("group")
        with self.lock:
            me = self.clients[name]
            info = self.find_group(me, group)
            if info is None:
                return
            if name not in info["members"]:
                return self.send_error(me, NOT_MEMBER, f"you are not in {group!r}")
            info["members"].remove(name)
            self.broadcast_groups()

    def handle_group_msg(self, name, msg):
        """R11: deliver a group message to the group's members only.
        A non-member's message is rejected and reaches nobody."""
        group, text = msg.get("group"), msg.get("text")
        with self.lock:
            me = self.clients[name]
            info = self.find_group(me, group)
            if info is None:
                return
            if name not in info["members"]:
                return self.send_error(me, NOT_MEMBER, f"join {group!r} before sending to it")
            if not is_text(text):
                return self.send_error(me, BAD_REQUEST, "text must be a non-empty string")
            out = {"type": MSG, "room": group_room(group), "from": name, "text": text, "ts": time.time()}
            for member in info["members"]:
                if member in self.clients:      # members who are offline right now miss it
                    self.send(self.clients[member], out)

    def find_group(self, sock, group):
        """Return the group's info dict, or send NO_SUCH_GROUP and return None. Caller holds the lock."""
        info = self.groups.get(group) if isinstance(group, str) else None
        if info is None:
            self.send_error(sock, NO_SUCH_GROUP, f"there is no group {group!r}")
        return info


def is_text(value):
    """A chat text must be a string with at least one non-space character."""
    return isinstance(value, str) and value.strip() != ""


def local_ipv4_addresses():
    """This machine's IPv4 addresses, printed at startup so clients know what to type in."""
    try:
        return socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        return []


def main():
    parser = argparse.ArgumentParser(description="Socket chat server")
    parser.add_argument("--host", default="0.0.0.0",
                        help="address to listen on; 0.0.0.0 means every network interface (default)")
    parser.add_argument("--port", type=int, default=5050, help="TCP port to listen on (default 5050)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    server = ChatServer(args.host, args.port)
    port = server.start()
    log.info("listening on %s:%d  (Ctrl+C to stop)", args.host, port)
    # The list may include virtual adapters (WSL, VirtualBox); clients need the Wi-Fi/Ethernet one.
    log.info("this machine's IPv4 addresses: %s", ", ".join(local_ipv4_addresses()) or "unknown")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
