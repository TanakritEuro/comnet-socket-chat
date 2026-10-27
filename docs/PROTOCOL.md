# Chat Protocol (draft v1)

This is the application-layer protocol running over a single **TCP** connection per client.
It is the contract between `server.py` and `client.py`. Change it here first, then in the code, in the same PR.

## Framing

TCP is a byte stream with no message boundaries, so every message is framed like this:

```
+----------------------+-----------------------------------+
| length: 4 bytes      | body: <length> bytes              |
| unsigned, big-endian | UTF-8 JSON object                 |
+----------------------+-----------------------------------+
```

- `send_msg(sock, obj)`: `body = json.dumps(obj).encode()`, then `sock.sendall(struct.pack("!I", len(body)) + body)`
- `recv_msg(sock)`: read exactly 4 bytes, then exactly `length` bytes. `recv` must be **looped** because it may return fewer bytes than asked. An empty read means the peer closed the connection.
- Reject frames larger than `MAX_FRAME` (10 MiB) and close the connection.
- Every JSON object has a `"type"` field. Timestamps (`ts`) are Unix seconds (float), set by the **server**.

## Room ids

| Room | id | Who can see it |
|---|---|---|
| Private chat | `dm:<a>|<b>` with names sorted (`dm:alice|bob`) | exactly those two names |
| Group chat | `group:<groupname>` | current members of the group |

## Client → Server

| type | fields | notes |
|---|---|---|
| `HELLO` | `name` | First message. Name: 1–20 chars, no `|`, unique among connected clients (R3) |
| `DM` | `to`, `text` | Private message (R7). `to` must be online and not yourself |
| `GROUP_CREATE` | `group` | Creator becomes the **only** member (R8). Group names are unique |
| `GROUP_JOIN` | `group` | Only adds **the sender** (R10) |
| `GROUP_LEAVE` | `group` | optional |
| `GROUP_MSG` | `group`, `text` | Rejected unless the sender is a member (R11) |
| `HISTORY` | `room` | Request past messages for a room you're allowed to see |
| `TYPING` | `room`, `on` (bool) | Forwarded to the other room participants |
| `FILE` | `room`, `filename`, `size`, `data` (base64) | Same routing/privacy rules as text. Max 5 MiB raw |
| `SUMMARIZE` | `room` | AI summary of the room's recent history |
| `BYE` | | optional clean disconnect |

## Server → Client

| type | fields | notes |
|---|---|---|
| `WELCOME` | `name` | HELLO accepted |
| `ERROR` | `code`, `message` | e.g. `NAME_TAKEN`, `NOT_MEMBER`, `NO_SUCH_USER`, `NO_SUCH_GROUP`, `GROUP_EXISTS`, `TOO_LARGE` |
| `USERS` | `users: [name, ...]` | Full list incl. the receiver itself (R4). Broadcast on every connect/disconnect |
| `GROUPS` | `groups: [{name, creator, members:[...]}]` | All groups (R9). Broadcast on every create/join/leave |
| `MSG` | `room`, `from`, `text`, `ts` | A chat message in a DM or group room |
| `FILE` | `room`, `from`, `filename`, `size`, `data`, `ts` | |
| `HISTORY` | `room`, `messages: [MSG or FILE objects]` | Group history only includes messages from after the requester joined |
| `TYPING` | `room`, `from`, `on` | |
| `SUMMARY` | `room`, `text`, `model` | `model` = Gemini model name or `"offline"` |
| `NOTICE` | `text` | e.g. "bob joined", "bob left" |

## Example session

```
C→S  {"type":"HELLO","name":"alice"}
S→C  {"type":"WELCOME","name":"alice"}
S→*  {"type":"USERS","users":["alice","bob"]}
C→S  {"type":"GROUP_CREATE","group":"netlab"}
S→*  {"type":"GROUPS","groups":[{"name":"netlab","creator":"alice","members":["alice"]}]}
C→S  {"type":"DM","to":"bob","text":"hi"}
S→alice,bob  {"type":"MSG","room":"dm:alice|bob","from":"alice","text":"hi","ts":1793066669.5}
```

## Security note (for the Q&A)

There is no authentication: a name is just a label while connected, and traffic is plaintext TCP,
so anyone on the path could read it in Wireshark. TLS (`ssl.wrap_socket`) would fix that. It's out of scope, but worth mentioning.
