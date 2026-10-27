# Slide Notes

Material for the "Implementation Explanation" slides. Update this in the same PR that implements each requirement.
For each item, give: **how it works** (1–2 sentences), **code snapshot** (file → function, line range), and **AI prompt used** (quoted, or "none").

Line ranges are as of the PR that last touched each section. Re-check them before taking screenshots.

## AI prompts used

All code so far was written by Claude Code (Opus 5.5) from these prompts. Each requirement below says which prompt it came from.

**Prompt P1** (server + protocol, PR `feat/protocol-and-server`):

> Read CLAUDE.md, docs/SPEC.md and docs/PROTOCOL.md first. They describe our socket-programming
> chat app term project and the decisions we've already made. Then do the following, one stage at a time.
>
> […Stage 0: repo setup…]
>
> STAGE 1: Part 1 + server core  (branch: feat/protocol-and-server)
> - protocol.py: framing (send_msg/recv_msg with a looped exact recv), message type constants.
> - server.py: threaded TCP server implementing HELLO/WELCOME/ERROR (R3), USERS broadcast (R4),
>   DM (R7), GROUP_CREATE / GROUP_JOIN / GROUP_LEAVE / GROUP_MSG / GROUPS broadcast (R8–R11),
>   with every privacy rule enforced server-side. CLI args --host/--port.
> - tests/: unittest tests that start the server on a free port and connect raw-socket clients, covering
>   each of R3, R4, R7–R11, including the negative cases (duplicate name, a third client must NOT
>   receive a DM, a non-member's group message is rejected and not delivered).
> - Update docs/SLIDE_NOTES.md for R2, R3, R4, R7–R11 (server side), and quote this prompt as the AI prompt.
> - Open the PR and give me the link. If the PR isn't merged when you're ready for the next stage, ask me
>   whether to wait or to stack the next branch on top of it.
>
> […Stages 2–3…] Keep the code simple and commented: all 4 of us have to explain it live.

## R1: System architecture
- Diagram: _TODO (server, ≥2 client machines, LAN or cloud, Gemini API)_
- Protocols (what / why / how):
  - TCP: _TODO_
  - IP (IPv4, LAN / Internet): _TODO_
  - Custom application protocol (length-prefixed JSON, see PROTOCOL.md): _TODO_
  - HTTPS (TLS over TCP) to the Gemini API: _TODO_

## R2: Socket programming only
- **How:** All chat traffic goes over one plain TCP socket per client (`socket.AF_INET`, `SOCK_STREAM`) from the
  Python standard library, with no Socket.IO, WebSocket or HTTP. TCP is a byte stream, so each JSON message has a 4-byte
  length prefix in front of it. `recv_exact` loops on `recv()` until it has exactly that many bytes, because a single `recv()` can return half a
  message, or two messages at once.
- **Code snapshot (mandatory):** `protocol.py` → `send_msg` (L68–76), `recv_exact` (L79–93), `recv_msg` (L96–117)
- **Code snapshot 2:** `server.py` → `ChatServer.start` (L61–77: socket/bind/listen) and `serve_forever` (L79–89: `accept()` + one thread per client)
- **Proof:** `tests/test_protocol.py` → `test_frame_arriving_in_small_pieces`, `test_two_frames_in_one_send`
- **AI prompt:** P1

## R3: Unique name
- **How:** The first message must be `HELLO {name}`. Under the lock, the server checks the name against the dict of
  online clients. If the name is taken it replies `ERROR NAME_TAKEN`, and the client can try again on the same connection. A name is
  released when its client disconnects.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.login` (L169–195)
- **Code snapshot 2:** `protocol.py` → `clean_name` (L124–135: 1–20 chars, no `|`)
- **Proof:** `tests/test_server.py` → `R3UniqueNameTests` (L108–139)
- **AI prompt:** P1

## R4: Online user list (incl. self)
- **How:** Whenever someone logs in or disconnects, the server sends every client the same
  `USERS` message: the sorted list of **all** online names. Because the list is the same for everyone, each client also sees itself.
  A crash (TCP RST → `ConnectionResetError`) is handled like a normal close.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.broadcast_users` (L144–149)
- **Code snapshot 2:** `server.py` → `ChatServer.handle_client` (L97–126: the `finally:` block calls `remove_client`) and `remove_client` (L197–205)
- **Proof:** `tests/test_server.py` → `R4UserListTests` (L142–161)
- **AI prompt:** P1

## R5: Separate chat room per chat
## R6: Chat box + chat window

## R7: Private message
- **Server side:** For `DM {to, text}`, the server builds one `MSG` with room id `dm:<a>|<b>` (names sorted) and
  writes it to exactly two sockets, the sender's and the recipient's. It never touches any other socket, so privacy does not depend on the
  client behaving.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.handle_dm` (L212–226)
- **Code snapshot 2:** `protocol.py` → `dm_room` (L138–141)
- **Proof:** `tests/test_server.py` → `test_dm_reaches_only_sender_and_recipient` (a third client, carol, receives nothing)
- **AI prompt:** P1
- **Client side:** _TODO (Stage 2)_

## R8: Create group (creator is only member)
- **Server side:** `GROUP_CREATE` stores `{"creator": name, "members": [name]}`, so the only member is the creator. The server then
  broadcasts the new `GROUPS` list to everyone. Duplicate names get `GROUP_EXISTS`.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.handle_group_create` (L228–238)
- **Proof:** `tests/test_server.py` → `R8CreateGroupTests` (L190–209)
- **AI prompt:** P1
- **Client side:** _TODO (Stage 2)_

## R9: List all groups with members
- **Server side:** `groups_message` builds the list of every group with its creator and members. It is broadcast after
  each create, join or leave, and also sent to each new client right after login, so late joiners see the existing groups.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.groups_message` + `broadcast_groups` (L151–163)
- **Proof:** `tests/test_server.py` → `R9GroupListTests` (L212–230)
- **AI prompt:** P1
- **Client side:** _TODO (Stage 2)_

## R10: Join group by yourself
- **Server side:** `GROUP_JOIN` has only a `group` field. The server appends the name tied to **this connection**,
  so no message can add someone else. A test sends extra `name`/`user` fields naming carol, and only the sender is added.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.handle_group_join` (L240–252)
- **Code snapshot 2:** `server.py` → `handle_group_leave` (L254–265)
- **Proof:** `tests/test_server.py` → `test_join_adds_only_the_sender`
- **AI prompt:** P1
- **Client side:** _TODO (Stage 2)_

## R11: Group message (members only)
- **Server side:** For `GROUP_MSG`, the server first checks that the sender is in the member list. If not, it replies `ERROR NOT_MEMBER`
  and delivers nothing. Otherwise it writes the `MSG` (room `group:<name>`) only to the members' sockets.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.handle_group_msg` (L267–283)
- **Proof:** `tests/test_server.py` → `R11GroupMessageTests` (L265–297): non-members get nothing, a non-member's message is rejected and
  reaches nobody, and a member who left stops receiving
- **AI prompt:** P1
- **Client side:** _TODO (Stage 2)_

## Q&A prep: concurrency
- One thread per client (`serve_forever` → `threading.Thread(target=handle_client)`), and one `threading.Lock` guards
  `clients` and `groups`. Sends also happen under the lock, so two threads never interleave bytes on one socket, and every
  client sees updates in the same order. See the `ChatServer` class docstring (L28–38).

## Special 1: AI chat summarization
- AI model: _TODO (model name + link to docs)_
## Special 2: File / image sending
## Special 3: Chat history + unread badges
## Special 4: Typing indicator + online status
## (Optional) Cloud deployment
