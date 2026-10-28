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
- Reject frames larger than `MAX_FRAME` (10 MiB): the server replies `ERROR TOO_LARGE` and closes the connection.
- A body that is not UTF-8 JSON, or not an object with a string `"type"`, gets `ERROR BAD_REQUEST` and the connection is closed.
- JSON is sent with `ensure_ascii=False`, so Thai text travels as real UTF-8.
- Every JSON object has a `"type"` field. Timestamps (`ts`) are Unix seconds (float), set by the **server**.

## Room ids

| Room | id | Who can see it |
|---|---|---|
| Private chat | `dm:<a>|<b>` with names sorted (`dm:alice|bob`) | exactly those two names |
| Group chat | `group:<groupname>` | current members of the group |

## Client → Server

| type | fields | notes |
|---|---|---|
| `HELLO` | `name` | First message. Name: 1–20 chars after trimming spaces, no `|`, unique among connected clients (R3). After `NAME_TAKEN`/`BAD_NAME` the client may send another `HELLO` on the same connection |
| `DM` | `to`, `text` | Private message (R7). `to` must be online and not yourself; `text` must not be blank. Delivered as `MSG` to sender **and** recipient only |
| `GROUP_CREATE` | `group` | Creator becomes the **only** member (R8). Group names follow the same rules as user names and are unique |
| `GROUP_JOIN` | `group` | Only adds **the sender** (R10). Any extra field naming another user is ignored |
| `GROUP_LEAVE` | `group` | Removes the sender. The group stays listed even when it has no members |
| `GROUP_MSG` | `group`, `text` | Rejected with `NOT_MEMBER` unless the sender is a member (R11). Delivered as `MSG` to the members who are online, including the sender |
| `HISTORY` | `room` | Request past messages for a room you're allowed to see |
| `TYPING` | `room`, `on` (bool) | Forwarded to the other room participants |
| `FILE` | `room`, `filename`, `size`, `data` (base64) | Same routing/privacy rules as text. Max 5 MiB raw |
| `SUMMARIZE` | `room` | AI summary of a room you can read: your own DM, or a group you're a member of (special 1). Uses the server's buffer (last 50 messages; for a group, only those sent after you joined). Errors: `BAD_REQUEST` (not a room id), `NO_SUCH_GROUP`, `NOT_MEMBER`, `BUSY` (your previous summary isn't finished) |
| `TRANSLATE` | `room`, `ts`, `to` | Translate **one message** (special: translation). The message is named by its room and its server timestamp `ts`; the server finds it in the [message buffer](#message-buffer) and checks you may read that room, so a client cannot make the server translate arbitrary text. `to` is a language code: `en`, `th`, `ja`, `zh` or `es`. Errors: `BAD_REQUEST`, `NO_SUCH_GROUP`, `NOT_MEMBER`, `NO_SUCH_MESSAGE`, `AI_FAILED` |
| `SENTIMENT` | `room`, `ts` | Analyze the mood of **one message**, named the same way as in `TRANSLATE`. Errors: `BAD_REQUEST`, `NO_SUCH_GROUP`, `NOT_MEMBER`, `NO_SUCH_MESSAGE` (and `AI_FAILED` if there is no offline fallback) |
| `BOT_ASK` | `text`, optional `room` | Ask the chatbot. The server keeps the last 10 question/answer pairs of **this connection** as context. If `room` is given, that room's recent messages are added as context, after the same readability check as `SUMMARIZE`. Errors: `BAD_REQUEST` (blank text, bad `room`), `NOT_MEMBER`, `BUSY` (previous question not answered yet) |
| `BOT_CLEAR` | | Forget this connection's chatbot conversation. No reply |
| `BYE` | | optional clean disconnect |

## Server → Client

| type | fields | notes |
|---|---|---|
| `WELCOME` | `name` | HELLO accepted |
| `ERROR` | `code`, `message` | See [Error codes](#error-codes). `message` is human-readable |
| `USERS` | `users: [name, ...]` | Full list incl. the receiver itself (R4). Broadcast on every connect/disconnect |
| `GROUPS` | `groups: [{name, creator, members:[...]}]` | All groups (R9). Broadcast on every create/join/leave |
| `MSG` | `room`, `from`, `text`, `ts` | A chat message in a DM or group room |
| `FILE` | `room`, `from`, `filename`, `size`, `data`, `ts` | |
| `HISTORY` | `room`, `messages: [MSG or FILE objects]` | Group history only includes messages from after the requester joined |
| `TYPING` | `room`, `from`, `on` | |
| `SUMMARY` | `room`, `text`, `model` | Sent **only to the requester**, usually a few seconds later. `model` = the Gemini model that answered (e.g. `gemini-3-flash-preview`) or `"offline"` when no AI was used (no key, no messages, or every model failed) |
| `TRANSLATION` | `room`, `ts`, `to`, `source`, `text`, `model` | Sent **only to the requester**. `room` + `ts` name the original message, `source` is the detected language code (e.g. `th`), `text` the translation, `model` the Gemini model that answered |
| `SENTIMENT_RESULT` | `room`, `ts`, `label`, `model` | Sent **only to the requester**. `label` is `positive`, `neutral` or `negative`. `model` is `"offline"` if a simple fallback was used instead of AI |
| `BOT_REPLY` | `text`, `model`, `ts` | Sent **only to the asker**. `model` is `"offline"` with an explanatory `text` when no API key is set, so the demo never breaks |
| `NOTICE` | `text` | e.g. "bob joined", "bob left" |

Before `WELCOME`, any message other than `HELLO` gets `ERROR BAD_REQUEST`. An unknown `type` after login also gets
`ERROR BAD_REQUEST`, but the connection stays open.

`ERROR` may carry three optional fields when it answers an AI request, so the client can reset the right control:
`request` (the type that failed, e.g. `"TRANSLATE"`), and for per-message requests `room` and `ts` as in the request.

The chatbot is **not a room**: `BOT_ASK` / `BOT_REPLY` never use a room id, are never written to the message buffer
and are never sent to anyone but the asker. UI and per-feature details: [`AI_FEATURES.md`](AI_FEATURES.md).

**Status:** `SUMMARIZE` / `SUMMARY` and `BOT_ASK` / `BOT_CLEAR` / `BOT_REPLY` are implemented. `TRANSLATE` and `SENTIMENT` with
their replies are **specified here but not implemented yet**; until then the server answers them with `ERROR BAD_REQUEST`. `HISTORY`, `TYPING`, `FILE` and `NOTICE` are Part 3 and not implemented
yet; the server currently answers them with `ERROR BAD_REQUEST`.

## Message buffer

The server keeps the last **50** delivered `MSG` objects of every room in memory (`MessageBuffer` in `server.py`, one
`collections.deque(maxlen=50)` per room id). It is lost when the server restarts. Reading from it follows the delivery rules:
a DM room only by its two participants, a group room only by current members, and only messages sent at or after the time
that member joined (the server records join times on create/join and forgets them on leave). `SUMMARIZE` uses it today;
`HISTORY` is meant to reuse the same buffer and the same check (`visible_since`).

## Login sequence

1. Client sends `HELLO`.
2. On success the server sends, in this order: `WELCOME` to the client, `USERS` to **everyone** (including the newcomer),
   then `GROUPS` to the newcomer only, so it sees the groups that already exist (R9).
3. When a client disconnects (`BYE`, closed socket, or connection reset), the server sends `USERS` to everyone left.

Group memberships are stored by **name** and are kept when a member disconnects. Offline members simply don't receive
messages, and someone who reconnects with the same name is still in their groups.

## Error codes

| code | when |
|---|---|
| `NAME_TAKEN` | `HELLO` with a name that is already online |
| `BAD_NAME` | user or group name is empty, longer than 20 chars, contains `|`, or isn't a string |
| `BAD_REQUEST` | message before `HELLO`, unknown type, blank text, DM to yourself, malformed frame |
| `NO_SUCH_USER` | `DM` to a name that is not online |
| `NO_SUCH_GROUP` | join/leave/message a group that doesn't exist |
| `GROUP_EXISTS` | `GROUP_CREATE` with a name already used by a group |
| `ALREADY_MEMBER` | `GROUP_JOIN` on a group you're already in |
| `NOT_MEMBER` | `GROUP_MSG` or `GROUP_LEAVE` on a group you're not in, or `SUMMARIZE` of a room you can't read |
| `TOO_LARGE` | frame over 10 MiB (connection is closed) |
| `BUSY` | `SUMMARIZE` while your previous summary is still being prepared, or `BOT_ASK` while the previous question is unanswered |
| `NO_SUCH_MESSAGE` | `TRANSLATE` / `SENTIMENT` for a `ts` that is not in the room's buffer (older than the last 50, sent before you joined, or never existed) |
| `AI_FAILED` | The AI call for `TRANSLATE`, `SENTIMENT` or `BOT_ASK` failed (no API key, quota used up, network error) and there is no fallback answer |

## Example session

```
C→S  {"type":"HELLO","name":"alice"}
S→C  {"type":"WELCOME","name":"alice"}
S→*  {"type":"USERS","users":["alice","bob"]}
S→C  {"type":"GROUPS","groups":[]}
C→S  {"type":"GROUP_CREATE","group":"netlab"}
S→*  {"type":"GROUPS","groups":[{"name":"netlab","creator":"alice","members":["alice"]}]}
C→S  {"type":"DM","to":"bob","text":"hi"}
S→alice,bob  {"type":"MSG","room":"dm:alice|bob","from":"alice","text":"hi","ts":1793066669.5}
C→S  {"type":"SUMMARIZE","room":"dm:alice|bob"}
S→alice  {"type":"SUMMARY","room":"dm:alice|bob","text":"- alice greeted bob","model":"gemini-3-flash-preview"}
C→S  {"type":"TRANSLATE","room":"dm:alice|bob","ts":1793066669.5,"to":"th"}
S→alice  {"type":"TRANSLATION","room":"dm:alice|bob","ts":1793066669.5,"to":"th","source":"en","text":"สวัสดี","model":"gemini-3-flash-preview"}
C→S  {"type":"SENTIMENT","room":"dm:alice|bob","ts":1793066669.5}
S→alice  {"type":"SENTIMENT_RESULT","room":"dm:alice|bob","ts":1793066669.5,"label":"positive","model":"gemini-3-flash-preview"}
C→S  {"type":"BOT_ASK","text":"How does TCP framing work?"}
S→alice  {"type":"BOT_REPLY","text":"TCP is a byte stream, so ...","model":"gemini-3-flash-preview","ts":1793066700.1}
```

## Security note (for the Q&A)

There is no authentication: a name is just a label while connected, and traffic is plaintext TCP,
so anyone on the path could read it in Wireshark. Because group membership is kept by name, someone who logs in
with an offline member's name receives that group's messages. TLS (`ssl.wrap_socket`) would fix that. It's out of scope, but worth mentioning.
