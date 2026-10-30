"""Shared protocol code used by both server.py and client.py.

Every message is a JSON object sent over TCP inside a frame:

    +----------------------+------------------------------+
    | length: 4 bytes      | body: <length> bytes         |
    | unsigned, big-endian | UTF-8 JSON object            |
    +----------------------+------------------------------+

See docs/PROTOCOL.md for the full list of message types.
"""

import json
import struct

# ---------------------------------------------------------------------------
# Framing constants
# ---------------------------------------------------------------------------

HEADER = struct.Struct("!I")    # "!" = network byte order (big-endian), "I" = 4-byte unsigned int
MAX_FRAME = 10 * 1024 * 1024    # 10 MiB: refuse bigger frames so a bad peer can't make us allocate huge buffers
MAX_NAME_LEN = 20               # max length of a user name or group name
MAX_FILE_BYTES = 5 * 1024 * 1024    # 5 MiB of raw file data; base64 makes it ~6.7 MB, still under MAX_FRAME
MAX_FILENAME_LEN = 100          # max length of a file name

# ---------------------------------------------------------------------------
# Message types (the "type" field of every JSON object)
# ---------------------------------------------------------------------------

# Client -> Server
HELLO = "HELLO"                 # {"name"}                 log in with a unique name (R3)
DM = "DM"                       # {"to", "text"}           private message (R7)
GROUP_CREATE = "GROUP_CREATE"   # {"group"}                creator becomes the only member (R8)
GROUP_JOIN = "GROUP_JOIN"       # {"group"}                adds the sender only (R10)
GROUP_LEAVE = "GROUP_LEAVE"     # {"group"}
GROUP_MSG = "GROUP_MSG"         # {"group", "text"}        members only (R11)
HISTORY = "HISTORY"             # {"room"} -> {"room", "messages"}   past messages you may read (special 3)
FILE = "FILE"                   # {"room", "filename", "size", "data"}   send a file or image (special 2); the server
                                # sends the same type back to the room: {"room", "from", "filename", "size", "data", "ts"}
SUMMARIZE = "SUMMARIZE"         # {"room"}                 AI summary of a room you can see (special 1)
BOT_ASK = "BOT_ASK"             # {"text", "room"?}        ask the chatbot; "room" adds that room's recent chat as context
BOT_CLEAR = "BOT_CLEAR"         # {}                       forget this connection's chatbot conversation (no reply)
BYE = "BYE"                     # {}                       clean disconnect

# Server -> Client
WELCOME = "WELCOME"             # {"name"}                 HELLO accepted
ERROR = "ERROR"                 # {"code", "message"}
USERS = "USERS"                 # {"users": [...]}         everyone online, including you (R4)
GROUPS = "GROUPS"               # {"groups": [{"name", "creator", "members"}]}   (R9)
MSG = "MSG"                     # {"room", "from", "text", "ts"}
SUMMARY = "SUMMARY"             # {"room", "text", "model"}   only to the requester
BOT_REPLY = "BOT_REPLY"         # {"text", "model", "ts"}     only to the asker; model "offline" = no AI was used

# ERROR codes
NAME_TAKEN = "NAME_TAKEN"
BAD_NAME = "BAD_NAME"
BAD_REQUEST = "BAD_REQUEST"
NO_SUCH_USER = "NO_SUCH_USER"
NO_SUCH_GROUP = "NO_SUCH_GROUP"
GROUP_EXISTS = "GROUP_EXISTS"
ALREADY_MEMBER = "ALREADY_MEMBER"
NOT_MEMBER = "NOT_MEMBER"
TOO_LARGE = "TOO_LARGE"
BUSY = "BUSY"
AI_FAILED = "AI_FAILED"         # an AI request failed and there is no fallback answer


class ProtocolError(Exception):
    """The peer sent bytes that are not a valid frame. `code` is one of the ERROR codes."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Framing: send_msg / recv_msg
# ---------------------------------------------------------------------------

def send_msg(sock, msg):
    """Encode `msg` (a dict) as JSON and send it as one length-prefixed frame."""
    # ensure_ascii=False keeps Thai and other non-English text as real UTF-8 (shorter, readable in Wireshark).
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise ProtocolError(TOO_LARGE, f"message is {len(body)} bytes, limit is {MAX_FRAME}")
    # sendall() keeps calling send() until every byte is handed to the OS.
    # A plain send() may send only part of the buffer.
    sock.sendall(HEADER.pack(len(body)) + body)


def recv_exact(sock, n):
    """Read exactly n bytes from the socket, or return None if the peer closed the connection.

    TCP is a byte stream, not a message stream: one recv() can return fewer bytes than
    we asked for (e.g. half a frame), or bytes from two frames at once. So we loop,
    asking only for the bytes still missing, until we have all n.
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            # recv() returns b"" only when the peer has closed its side (TCP FIN).
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_msg(sock):
    """Receive one frame and return it as a dict, or None if the connection was closed.

    Raises ProtocolError if the frame is too big or the body is not a JSON object with a "type".
    """
    header = recv_exact(sock, HEADER.size)          # step 1: the 4-byte length
    if header is None:
        return None
    (length,) = HEADER.unpack(header)
    if length > MAX_FRAME:
        raise ProtocolError(TOO_LARGE, f"frame of {length} bytes is over the {MAX_FRAME}-byte limit")

    body = recv_exact(sock, length)                  # step 2: exactly `length` bytes of JSON
    if body is None:
        return None
    try:
        msg = json.loads(body.decode("utf-8"))
    except ValueError:                               # bad UTF-8 or bad JSON
        raise ProtocolError(BAD_REQUEST, "frame body is not valid UTF-8 JSON") from None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError(BAD_REQUEST, 'every message must be a JSON object with a "type" field')
    return msg


# ---------------------------------------------------------------------------
# Small helpers shared by server and client
# ---------------------------------------------------------------------------

def clean_name(raw):
    """Return the trimmed user/group name if it is valid, else None.

    Valid = a string of 1..MAX_NAME_LEN characters after trimming spaces, with no "|"
    ("|" is the separator inside DM room ids like "dm:alice|bob").
    """
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not 1 <= len(name) <= MAX_NAME_LEN or "|" in name:
        return None
    return name


def clean_filename(raw):
    """Return just the file name (no folders) if it is valid, else None.

    A client could send "..\\..\\evil.exe" or "/etc/passwd". Keeping only what follows the last
    "/" or "\\" means a receiver who saves the file under this name never gets a path out of it.
    """
    if not isinstance(raw, str):
        return None
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not 1 <= len(name) <= MAX_FILENAME_LEN or name in (".", ".."):
        return None
    return name


def dm_room(a, b):
    """Room id of the private chat between users a and b. Sorted, so both sides get the same id."""
    first, second = sorted([a, b])
    return f"dm:{first}|{second}"


def dm_partner(room, me):
    """For a DM room id like "dm:alice|bob", return the name that isn't `me`."""
    first, second = room.removeprefix("dm:").split("|")
    return second if first == me else first


def group_room(group):
    """Room id of a group chat."""
    return f"group:{group}"
