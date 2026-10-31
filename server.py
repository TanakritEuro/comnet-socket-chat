"""TCP chat server.

Run:  python server.py --host 0.0.0.0 --port 5050

One thread per connected client. The server is the only place that decides who
receives a message, so privacy (R7, R11) cannot be bypassed by a modified client.
"""

import argparse
import base64
import binascii
import json
import logging
import os
import socket
import threading
import time
from collections import OrderedDict, defaultdict, deque

import ai
import ai_bot
import ai_translate
import ai_sentiment
from protocol import (
    AI_FAILED, ALREADY_MEMBER, BAD_NAME, BAD_REQUEST, BOT_ASK, BOT_CLEAR, BOT_REPLY, BUSY, BYE,
    DM, ERROR, FILE, GROUP_CREATE, GROUP_EXISTS, GROUP_JOIN, GROUP_LEAVE, GROUP_MSG, GROUPS, HELLO, HISTORY,
    MAX_FILE_BYTES, MAX_NAME_LEN, MSG, NAME_TAKEN, NO_SUCH_GROUP, NO_SUCH_MESSAGE, NO_SUCH_USER, NOT_MEMBER,
    SENTIMENT, SENTIMENT_RESULT, SUMMARIZE, SUMMARY, TOO_LARGE, TRANSLATE, TRANSLATION, USERS, WELCOME,
    ProtocolError, clean_filename, clean_name, dm_room, group_room, recv_msg, send_msg,
)

log = logging.getLogger("server")

NAME_RULES = f"must be 1-{MAX_NAME_LEN} characters and must not contain '|'"
BUFFER_SIZE = 200       # messages kept per room (chat history, special 3)
AI_CONTEXT = 50         # newest messages handed to the AI, so a long history doesn't cost more tokens
HISTORY_MAX_BYTES = 1024 * 1024   # one HISTORY reply stays far below the 10 MiB frame limit
HISTORY_FILE_MAX = 256 * 1024     # files bigger than this are not kept in history (200 x 5 MiB would fill the RAM)
TRANSLATING_MAX = 3     # translations one user may have in progress at once (protects the tiny API quota)
TRANSLATION_CACHE = 200  # finished translations kept, so the same click again costs no API call
ANALYZING_MAX = 3       # mood analyses one user may have in progress at once (protects the tiny API quota)
MOOD_CACHE = 500        # AI moods kept, so the same message clicked again (by anyone) costs no API call

class MessageBuffer:
    """The last BUFFER_SIZE chat messages of every room, in memory only (lost on restart).

    Stores the exact MSG dicts that were delivered, so HISTORY sends them back unchanged and a
    message keeps its id (room + ts). AI summaries and the chatbot read the newest AI_CONTEXT
    of them. Caller must hold ChatServer.lock.
    """

    def __init__(self, size=BUFFER_SIZE):
        # deque(maxlen=N) drops the oldest message automatically when a new one arrives.
        self.rooms = defaultdict(lambda: deque(maxlen=size))

    def add(self, msg):
        if msg["type"] == FILE and msg["size"] > HISTORY_FILE_MAX:
            # Keep the name and size so history still shows that the file was sent, but drop the bytes.
            msg = {**msg, "data": None}
        self.rooms[msg["room"]].append(msg)

    def recent(self, room, since=0.0, limit=AI_CONTEXT):
        """Messages of a room sent at or after `since` (a Unix time), oldest first.
        Only the newest `limit` of them; limit=None means all that are kept (used by HISTORY)."""
        messages = [m for m in self.rooms.get(room, ()) if m["ts"] >= since]
        return messages if limit is None else messages[-limit:]

    def find(self, room, ts):
        """The message of `room` whose id (server timestamp) is `ts`, or None if it isn't kept any more."""
        return next((m for m in self.rooms.get(room, ()) if m["ts"] == ts), None)

    def next_ts(self, room):
        """Timestamp for a new message in `room`, strictly after the room's previous one, so room + ts is
        a unique id. time.time() can repeat: on Windows it only ticks about every 15 ms, so two messages
        sent at once could get the same value. Then we use the previous ts + 1 microsecond."""
        now = time.time()
        previous = self.rooms.get(room)
        if previous and now <= previous[-1]["ts"]:
            return previous[-1]["ts"] + 0.000001
        return now


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

    def __init__(self, host="0.0.0.0", port=5050, summarizer=ai.summarize, bot=ai_bot.ask,
                 translator=ai_translate.translate, analyzer=ai_sentiment.analyze):
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.clients = {}   # name -> socket of that client's TCP connection
        self.groups = {}    # group name -> {"creator": name, "members": [names, in join order],
                            #                "joined": {name: Unix time they joined}}
        self.buffer = MessageBuffer()
        self.summarizer = summarizer    # function(room, messages) -> (text, model); tests swap in a fake
        self.summarizing = set()        # names with a summary in progress (protects the tiny API quota)
        self.bot = bot                  # function(history, text, room, messages) -> (answer, model); tests swap in a fake
        self.bot_history = {}           # name -> [(question, answer), ...] the chatbot remembers for that user
        self.asking = set()             # names with a chatbot question in progress
        self.translator = translator    # function(text, to) -> (translation, source, model); tests swap in a fake
        self.translating = set()        # (name, room, ts) of translations in progress
        self.translations = OrderedDict()   # (room, ts, to) -> (translation, source, model), oldest first
        self.analyzer = analyzer        # function(text) -> (label, model); tests swap in a fake
        self.analyzing = set()          # (name, room, ts) of mood analyses in progress
        self.moods = OrderedDict()      # (room, ts) -> (label, model) from the AI, oldest first
        self.listen_sock = None
        self.running = False
        # Which method handles which message type (after login).
        self.handlers = {
            DM: self.handle_dm,
            GROUP_CREATE: self.handle_group_create,
            GROUP_JOIN: self.handle_group_join,
            GROUP_LEAVE: self.handle_group_leave,
            GROUP_MSG: self.handle_group_msg,
            HISTORY: self.handle_history,
            FILE: self.handle_file,
            SUMMARIZE: self.handle_summarize,
            TRANSLATE: self.handle_translate,
            SENTIMENT: self.handle_sentiment,
            BOT_ASK: self.handle_bot_ask,
            BOT_CLEAR: self.handle_bot_clear,
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

    def send_error(self, sock, code, text, **extra):
        """Send an ERROR to one client. `extra` adds fields such as request="BOT_ASK", so the
        client can tell which of its requests failed (see PROTOCOL.md)."""
        self.send(sock, {"type": ERROR, "code": code, "message": text, **extra})

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
            self.bot_history.pop(name, None)    # the chatbot conversation belongs to this connection
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
            room = dm_room(name, to)
            out = {"type": MSG, "room": room, "from": name, "text": text, "ts": self.buffer.next_ts(room)}
            self.buffer.add(out)
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
            # Nobody else is added. "joined" lets summaries (and later HISTORY) skip older messages.
            self.groups[group] = {"creator": name, "members": [name],
                                  "joined": {name: self.buffer.next_ts(f"group:{group}")}}
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
            # Not time.time(): on Windows it ticks only every ~15 ms, so a message sent just before the join
            # could get the same time and count as "after you joined". next_ts() is strictly after the
            # room's newest message.
            info["joined"][name] = self.buffer.next_ts(f"group:{group}")
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
            del info["joined"][name]
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
            room = group_room(group)
            out = {"type": MSG, "room": room, "from": name, "text": text, "ts": self.buffer.next_ts(room)}
            self.buffer.add(out)
            for member in info["members"]:
                if member in self.clients:      # members who are offline right now miss it
                    self.send(self.clients[member], out)

    def handle_file(self, name, msg):
        """Special 2: deliver a file or image to a room, with the same privacy as text (R7, R11).

        The bytes travel inside the JSON frame as base64 text (binary can't be put in JSON). The
        server checks everything itself and never trusts the client's "size" or path in "filename":
        it decodes the data, measures it, and cleans the name before anyone else sees it.
        """
        room, data = msg.get("room"), msg.get("data")
        with self.lock:
            me = self.clients[name]
            # Same room check as HISTORY: a DM only for its two people, a group only for its members.
            if self.visible_since(me, name, room, request=FILE) is None:
                return                          # an ERROR was already sent
            filename = clean_filename(msg.get("filename"))
            if filename is None:
                return self.send_error(me, BAD_REQUEST, "filename must be 1-100 characters", request=FILE)
            if not isinstance(data, str):
                return self.send_error(me, BAD_REQUEST, "data must be base64 text", request=FILE)
            if len(data) > MAX_FILE_BYTES * 4 // 3 + 4:     # base64 is 4/3 the size of the bytes: check before decoding
                return self.send_error(me, TOO_LARGE, f"file is over {MAX_FILE_BYTES // 1024 // 1024} MiB", request=FILE)
            try:
                raw = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                return self.send_error(me, BAD_REQUEST, "data is not valid base64", request=FILE)
            if not raw:
                return self.send_error(me, BAD_REQUEST, "the file is empty", request=FILE)
            if len(raw) > MAX_FILE_BYTES:
                return self.send_error(me, TOO_LARGE, f"file is over {MAX_FILE_BYTES // 1024 // 1024} MiB", request=FILE)

            if room.startswith("dm:"):
                partner = next((n for n in room.removeprefix("dm:").split("|") if n != name), name)
                if partner == name:
                    return self.send_error(me, BAD_REQUEST, "you cannot send a file to yourself", request=FILE)
                if partner not in self.clients:
                    return self.send_error(me, NO_SUCH_USER, f"{partner!r} is not online", request=FILE)
                receivers = [self.clients[partner]]
            else:
                members = self.groups[room.removeprefix("group:")]["members"]
                receivers = [self.clients[m] for m in members if m in self.clients and m != name]
            out = {"type": FILE, "room": room, "from": name, "filename": filename, "size": len(raw),
                   "data": data, "ts": self.buffer.next_ts(room)}
            self.buffer.add(out)
            self.send(me, out)                  # echo, so the sender's window shows it too
            for sock in receivers:
                self.send(sock, out)
            log.info("%s sent %s (%d bytes) to %s", name, filename, len(raw), room)

    def handle_history(self, name, msg):
        """Special 3: send a room's past messages back to the requester only.

        Same privacy check as SUMMARIZE (visible_since): a DM only to its two people, a group only
        to current members and only messages from after they joined. Anyone else gets an ERROR.
        """
        room = msg.get("room")
        with self.lock:
            me = self.clients[name]
            since = self.visible_since(me, name, room, request=HISTORY)
            if since is None:
                return                          # an ERROR was already sent
            messages = self.buffer.recent(room, since, limit=None)
            # The whole history travels in one frame. If a few huge messages would make it too big,
            # leave out the oldest ones rather than fail (send_msg refuses frames over MAX_FRAME).
            sizes = [len(json.dumps(m, ensure_ascii=False).encode("utf-8")) for m in messages]
            while messages and sum(sizes) > HISTORY_MAX_BYTES:
                messages.pop(0)
                sizes.pop(0)
            self.send(me, {"type": HISTORY, "room": room, "messages": messages})

    def handle_summarize(self, name, msg):
        """Special 1: AI summary of a room the sender can see, sent back to the sender only.

        The Gemini call can take seconds, so it runs in a worker thread *outside* the lock:
        everyone keeps chatting while we wait, including the requester.
        """
        room = msg.get("room")
        with self.lock:
            me = self.clients[name]
            since = self.visible_since(me, name, room)
            if since is None:
                return                          # an ERROR was already sent
            if name in self.summarizing:
                return self.send_error(me, BUSY, "your previous summary is still being prepared")
            self.summarizing.add(name)
            messages = self.buffer.recent(room, since)
        threading.Thread(target=self.summarize_worker, args=(name, me, room, messages), daemon=True).start()

    def summarize_worker(self, name, sock, room, messages):
        """Worker thread: ask the AI (slow, no lock held), then reply to the requester only."""
        try:
            text, model = self.summarizer(room, messages)
        except Exception:                       # a bug must not leave the user waiting forever
            log.exception("summarizer crashed")
            text, model = ai.offline_summary(messages, "summarizer error"), ai.OFFLINE
        with self.lock:
            self.summarizing.discard(name)
            if self.clients.get(name) is sock:  # still the same connection (not gone, not a new login)
                self.send(sock, {"type": SUMMARY, "room": room, "text": text, "model": model})
        log.info("summary for %s in %s: %d messages, model %s", name, room, len(messages), model)

    def handle_translate(self, name, msg):
        """AI translation of ONE message, sent back to the requester only.

        The message is named by its room and server timestamp `ts`, and the text is taken from the
        server's own buffer, never from the request: a modified client can't make the server translate
        arbitrary text, or a message from a room it may not read (same check as HISTORY, `visible_since`).
        Like SUMMARIZE, the slow Gemini call runs in a worker thread outside the lock.
        """
        room, ts, to = msg.get("room"), msg.get("ts"), msg.get("to")
        about = {"request": TRANSLATE, "room": room, "ts": ts}      # echoed in an ERROR so the client resets the right message
        with self.lock:
            me = self.clients[name]
            since = self.visible_since(me, name, room, **about)
            if since is None:
                return                          # an ERROR was already sent
            if not isinstance(to, str) or to not in ai_translate.LANGUAGES:
                return self.send_error(me, BAD_REQUEST, f"'to' must be one of {', '.join(ai_translate.LANGUAGES)}", **about)
            if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                return self.send_error(me, BAD_REQUEST, "ts must be a message timestamp", **about)
            original = self.buffer.find(room, ts)
            if original is None or original["ts"] < since:      # unknown, too old, or sent before you joined
                return self.send_error(me, NO_SUCH_MESSAGE, "that message is not available to translate", **about)
            text = original.get("text")
            if not isinstance(text, str) or not text.strip():
                return self.send_error(me, BAD_REQUEST, "that message has no text to translate", **about)
            if len(text) > ai_translate.MAX_CHARS:
                return self.send_error(me, BAD_REQUEST, f"that message is over {ai_translate.MAX_CHARS} characters", **about)

            cached = self.translations.get((room, ts, to))
            if cached is not None:              # translated before (maybe by someone else): no API call
                self.translations.move_to_end((room, ts, to))
                return self.send(me, self.translation_message(room, ts, to, *cached))
            if (name, room, ts) in self.translating:
                return self.send_error(me, BUSY, "that message is already being translated", **about)
            if sum(1 for who, _, _ in self.translating if who == name) >= TRANSLATING_MAX:
                return self.send_error(me, BUSY, "wait for your other translations to finish", **about)
            self.translating.add((name, room, ts))
        threading.Thread(target=self.translate_worker, args=(name, me, room, ts, to, text), daemon=True).start()

    def translate_worker(self, name, sock, room, ts, to, text):
        """Worker thread: ask the AI (slow, no lock held), then reply to the requester only."""
        result = None
        try:
            result = self.translator(text, to)
        except ai_translate.TranslateError as e:
            log.info("translation for %s failed: %s", name, e)
        except Exception:                       # a bug must not leave the user on "Translating…"
            log.exception("translator crashed")
        with self.lock:
            self.translating.discard((name, room, ts))
            if result is not None:
                self.translations[(room, ts, to)] = result
                while len(self.translations) > TRANSLATION_CACHE:
                    self.translations.popitem(last=False)       # forget the oldest
            if self.clients.get(name) is not sock:
                return                          # the requester left (or logged in again): nobody to answer
            if result is None:
                return self.send_error(sock, AI_FAILED, "the translation couldn't be made right now, try again",
                                       request=TRANSLATE, room=room, ts=ts)
            self.send(sock, self.translation_message(room, ts, to, *result))
        log.info("translation for %s in %s to %s: model %s", name, room, to, result[2])

    @staticmethod
    def translation_message(room, ts, to, text, source, model):
        """The TRANSLATION reply. `room` + `ts` name the original message, so the client shows it under that message."""
        return {"type": TRANSLATION, "room": room, "ts": ts, "to": to, "source": source, "text": text, "model": model}
    def handle_sentiment(self, name, msg):
        """AI mood of ONE message, sent back to the requester only. Same checks as TRANSLATE."""
        room, ts = msg.get("room"), msg.get("ts")
        about = {"request": SENTIMENT, "room": room, "ts": ts}
        with self.lock:
            me = self.clients[name]
            since = self.visible_since(me, name, room, **about)
            if since is None:
                return                          # an ERROR was already sent
            if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                return self.send_error(me, BAD_REQUEST, "ts must be a message timestamp", **about)
            original = self.buffer.find(room, ts)
            if original is None or original["ts"] < since:      # unknown, too old, or sent before you joined
                return self.send_error(me, NO_SUCH_MESSAGE, "that message is not available to analyze", **about)
            text = original.get("text")
            if not isinstance(text, str) or not text.strip():   # e.g. a file
                return self.send_error(me, BAD_REQUEST, "that message has no text to analyze", **about)
            if len(text) > ai_sentiment.MAX_CHARS:
                return self.send_error(me, BAD_REQUEST, f"that message is over {ai_sentiment.MAX_CHARS} characters", **about)

            cached = self.moods.get((room, ts))
            if cached is not None:              # analyzed before: no API call
                self.moods.move_to_end((room, ts))
                return self.send(me, self.sentiment_message(room, ts, *cached))
            if (name, room, ts) in self.analyzing:
                return self.send_error(me, BUSY, "that message is already being analyzed", **about)
            if sum(1 for who, _, _ in self.analyzing if who == name) >= ANALYZING_MAX:
                return self.send_error(me, BUSY, "wait for your other mood analyses to finish", **about)
            self.analyzing.add((name, room, ts))
        threading.Thread(target=self.sentiment_worker, args=(name, me, room, ts, text), daemon=True).start()

    def sentiment_worker(self, name, sock, room, ts, text):
        """Worker thread: ask the AI (slow, no lock held), then reply to the requester only."""
        result = None
        try:
            result = self.analyzer(text)
        except Exception:                       # a bug must not leave the user on "Analyzing…"
            log.exception("analyzer crashed")
        with self.lock:
            self.analyzing.discard((name, room, ts))
            if result is not None and result[1] != ai.OFFLINE:   # cache AI answers only
                self.moods[(room, ts)] = result
                while len(self.moods) > MOOD_CACHE:
                    self.moods.popitem(last=False)
            if self.clients.get(name) is not sock:
                return                          # the requester left
            if result is None:
                return self.send_error(sock, AI_FAILED, "the mood couldn't be analyzed right now, try again",
                                       request=SENTIMENT, room=room, ts=ts)
            self.send(sock, self.sentiment_message(room, ts, *result))
        log.info("mood for %s in %s: %s, model %s", name, room, result[0], result[1])

    @staticmethod
    def sentiment_message(room, ts, label, model):
        return {"type": SENTIMENT_RESULT, "room": room, "ts": ts, "label": label, "model": model}    
    def handle_bot_ask(self, name, msg):
        """Special 4: ask the chatbot. The answer goes back to the asker only.

        The chatbot is not a room: questions and answers are never written to the message buffer
        and never sent to anyone else. The server remembers the last few pairs per user as context.
        With "room", that room's recent messages are added too, after the same privacy check as SUMMARIZE.
        Like SUMMARIZE, the slow Gemini call runs in a worker thread outside the lock.
        """
        text, room = msg.get("text"), msg.get("room")
        with self.lock:
            me = self.clients[name]
            if not is_text(text):
                return self.send_error(me, BAD_REQUEST, "text must be a non-empty string", request=BOT_ASK)
            if name in self.asking:
                return self.send_error(me, BUSY, "the assistant is still answering your previous question",
                                       request=BOT_ASK)
            messages = None
            if room is not None:
                since = self.visible_since(me, name, room, request=BOT_ASK)
                if since is None:
                    return                      # an ERROR was already sent
                messages = self.buffer.recent(room, since)
            history = self.bot_history.setdefault(name, [])
            self.asking.add(name)
            context = list(history)             # a copy for the worker, so later changes can't race with it
        threading.Thread(target=self.bot_worker, args=(name, me, text, room, messages, history, context),
                         daemon=True).start()

    def bot_worker(self, name, sock, text, room, messages, history, context):
        """Worker thread: ask the AI (slow, no lock held), then reply to the asker only."""
        answer = model = None
        try:
            answer, model = self.bot(context, text, room, messages)
        except ai_bot.BotError as e:
            log.info("chatbot for %s failed: %s", name, e)
        except Exception:                       # a bug must not leave the user stuck on "Thinking…"
            log.exception("chatbot crashed")
        with self.lock:
            self.asking.discard(name)
            if self.clients.get(name) is not sock:
                return                          # the asker left (or logged in again): nobody to answer
            if answer is None:
                return self.send_error(sock, AI_FAILED, "the assistant couldn't answer right now, try again",
                                       request=BOT_ASK)
            # Remember the pair, except the "no API key" notice, which isn't part of the conversation.
            # If the user cleared the chat (or left) while we waited, `history` is no longer in
            # self.bot_history, so this only changes a list nobody reads: the answer is forgotten too.
            if model != ai.OFFLINE:
                history.append((text, answer))
                del history[:-ai_bot.MAX_PAIRS]     # keep only the newest pairs
            self.send(sock, {"type": BOT_REPLY, "text": answer, "model": model, "ts": time.time()})
        log.info("chatbot answer for %s: model %s", name, model)

    def handle_bot_clear(self, name, msg):
        """Special 4: forget this user's chatbot conversation. There is no reply."""
        with self.lock:
            self.bot_history.pop(name, None)

    def visible_since(self, sock, name, room_id, **extra):
        """Privacy check for reading a room's past messages (same rules as delivery, R7/R11).

        Returns the Unix time from which `name` may read the room `room_id`: 0 for their own DMs, their
        join time for a group they are in. Otherwise sends an ERROR (with the `extra` fields, which may
        include "room" and "ts" for a per-message request) and returns None. Caller holds the lock.
        """
        if not isinstance(room_id, str) or not room_id.startswith(("dm:", "group:")):
            self.send_error(sock, BAD_REQUEST, f"{room_id!r} is not a room id", **extra)
            return None
        if room_id.startswith("dm:"):
            if name not in room_id.removeprefix("dm:").split("|"):
                self.send_error(sock, NOT_MEMBER, "you can only read your own private chats", **extra)
                return None
            return 0.0
        info = self.find_group(sock, room_id.removeprefix("group:"), **extra)
        if info is None:
            return None
        if name not in info["members"]:
            self.send_error(sock, NOT_MEMBER, f"join {room_id.removeprefix('group:')!r} first", **extra)
            return None
        return info["joined"][name]             # only messages from after they joined

    def find_group(self, sock, group, **extra):
        """Return the group's info dict, or send NO_SUCH_GROUP and return None. Caller holds the lock."""
        info = self.groups.get(group) if isinstance(group, str) else None
        if info is None:
            self.send_error(sock, NO_SUCH_GROUP, f"there is no group {group!r}", **extra)
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
    log.info("AI summaries: %s", ai.describe(ai.load_config()))
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
