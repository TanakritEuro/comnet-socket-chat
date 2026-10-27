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

**Prompt P2** (GUI client, PR `feat/tkinter-client`). This is the Stage 2 part of the same first prompt; the go-ahead came in a later message that merged PR #1:

> STAGE 2: GUI client  (branch: feat/tkinter-client)
> - client.py: login dialog (host, port, name, re-prompt on NAME_TAKEN), main window with an online
>   users list ("(you)" marker) and a groups list showing members with Create/Join buttons, plus a
>   ttk.Notebook with one tab per room (R5), each with a read-only chat window + entry box + Send (R6).
>   Double-click a user to open a DM. Network receive thread → queue.Queue → root.after() polling.
> - Update SLIDE_NOTES.md for R5, R6 and the client side of R7–R11. Open the PR.

**Prompt P3** (architecture docs, PR `docs/architecture`). This is the Stage 3 part of the same first prompt; the go-ahead came
in the message that merged PR #2:

> STAGE 3: R1 docs  (branch: docs/architecture)
> - Architecture diagram (Mermaid in docs/ARCHITECTURE.md, also rendered to PNG if mmdc/npx is available)
>   showing the server and ≥2 client PCs on the LAN, plus the HTTPS call to Gemini.
> - Explanation of each protocol (TCP, IP, our app protocol, JSON, HTTPS/TLS): what, why, and how it is used here.
> - README "How to demo on 2+ computers" section (find the server IP with ipconfig, Windows firewall inbound rule for the port).
> - Open the PR.

## R1: System architecture
Full write-up: [`docs/ARCHITECTURE.md`](ARCHITECTURE.md). PNGs for the slides are in [`docs/diagrams/`](diagrams/)
(re-render with `python scripts/render_diagrams.py` after editing the Mermaid blocks).

- **Slide "R1: Architecture diagram":** `diagrams/deployment.png`. Talking points: one `server.py` and one `client.py` per user on
  ≥2 physical PCs on the same LAN. Each client keeps **one TCP connection** to `server-IP:5050`, and clients never talk to each
  other directly, so the server enforces every privacy rule. Only the server calls Gemini, over HTTPS, so the API key
  stays on the server.
  - Backup slides / Q&A: `diagrams/threads-server.png` (thread per client + one lock), `diagrams/threads-client.png`
    (receiver thread → queue → Tk thread), `diagrams/sequence.png` (login, DM, rejected group message).
- **Slide "R1: Protocols" (what / why / how):** one line each. Details are in ARCHITECTURE.md §4.
  - **TCP:** reliable, ordered byte stream. *Why:* chat messages must not be lost or reordered, and an open connection
    means "online" (FIN/RST → user list update). *How:* `socket(AF_INET, SOCK_STREAM)`, `bind/listen/accept` on the server, `create_connection` on the client.
  - **IP (IPv4):** addresses and routes packets between the PCs. *Why:* that's how separate machines find each other; IPv4 is what `ipconfig`
    and the LAN give us. *How:* the user types the server's IP; the server binds `0.0.0.0:5050`; IP + port = socket address.
  - **Our application protocol:** 4-byte big-endian length + UTF-8 JSON, with a `type` field. *Why:* TCP has no message
    boundaries; a length prefix is safe for any content and lets us reject huge frames. *How:* `send_msg` / `recv_msg` with a looped `recv_exact`.
  - **JSON:** *Why:* readable in Wireshark, easy to extend, built into Python, same format as the Gemini API. *How:* `json.dumps(..., ensure_ascii=False)`.
  - **HTTPS (HTTP over TLS):** encrypted, authenticated connection to Gemini on TCP 443. *Why:* it protects the API key and chat text, and Google
    requires it. *How (planned, Special 1):* `urllib.request` POST from the server only.
- **AI prompt:** P3

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
- **How:** Every room id gets its own tab in a `ttk.Notebook`. DM tabs are titled `@bob` (room id `dm:alice|bob`) and group tabs `#netlab`
  (`group:netlab`). `open_room` creates a tab only the first time a room id is seen. Incoming messages are routed by their `room`
  field, so a DM from a new person opens a new tab instead of mixing into another one.
- **Code snapshot (mandatory):** `client.py` → `ChatApp.open_room` + `open_dm` + `open_group` (L327–345)
- **Code snapshot 2:** `client.py` → `ChatApp.on_msg` (L299–309: route an incoming `MSG` to its room's tab)
- **AI prompt:** P2

## R6: Chat box + chat window
- **How:** Each `RoomTab` has a read-only `ScrolledText` chat window (`state="disabled"`, unlocked for a moment to append) and
  an `Entry` + **Send** button (Enter also sends). Your own message appears when the server echoes it back, so what you
  see is what the server actually delivered.
- **Code snapshot (mandatory):** `client.py` → `RoomTab.__init__` (L109–128)
- **Code snapshot 2:** `client.py` → `RoomTab.send` / `add_message` / `append` (L130–150)
- **AI prompt:** P2

## Client threading (R2 Q&A, and why the GUI never freezes)
- One **receiver thread** blocks in `recv_msg()` and puts each message on a `queue.Queue`. The Tk main thread drains the
  queue every 50 ms with `root.after()`. Only the main thread touches widgets, because Tkinter is not thread-safe.
  The main thread sends directly. A TCP socket is full-duplex, so sending while another thread is in `recv()` is safe.
- **Code snapshot:** `client.py` → `ChatApp.receive_loop` + `poll_inbox` (L213–238), module docstring diagram (L1–14)
- **Login (R3 client side):** `LoginFrame.connect` (L55–96) opens the TCP connection, sends `HELLO`, and reads exactly one reply
  frame. On `NAME_TAKEN` it shows an error and lets the user pick another name.
- **AI prompt:** P2

## R7: Private message
- **Server side:** For `DM {to, text}`, the server builds one `MSG` with room id `dm:<a>|<b>` (names sorted) and
  writes it to exactly two sockets, the sender's and the recipient's. It never touches any other socket, so privacy does not depend on the
  client behaving.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.handle_dm` (L212–226)
- **Code snapshot 2:** `protocol.py` → `dm_room` (L138–141)
- **Proof:** `tests/test_server.py` → `test_dm_reaches_only_sender_and_recipient` (a third client, carol, receives nothing)
- **AI prompt:** P1
- **Client side:** Double-click a name in the user list → `open_dm` opens the `@name` tab, and Send sends `DM {to, text}`.
  The client never decides who sees a message; it only shows the `MSG` frames the server sends it.
  Snapshot: `client.py` → `on_user_double_click` (L357–360), `open_dm` (L337–340), `on_msg` (L299–309). AI prompt: P2

## R8: Create group (creator is only member)
- **Server side:** `GROUP_CREATE` stores `{"creator": name, "members": [name]}`, so the only member is the creator. The server then
  broadcasts the new `GROUPS` list to everyone. Duplicate names get `GROUP_EXISTS`.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.handle_group_create` (L228–238)
- **Proof:** `tests/test_server.py` → `R8CreateGroupTests` (L190–209)
- **AI prompt:** P1
- **Client side:** **Create…** asks for a name and sends `GROUP_CREATE`. The `#group` tab opens when the server's `GROUPS`
  update shows us as a member. Snapshot: `client.py` → `create_group` (L380–389). AI prompt: P2

## R9: List all groups with members
- **Server side:** `groups_message` builds the list of every group with its creator and members. It is broadcast after
  each create, join or leave, and also sent to each new client right after login, so late joiners see the existing groups.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.groups_message` + `broadcast_groups` (L151–163)
- **Proof:** `tests/test_server.py` → `R9GroupListTests` (L212–230)
- **AI prompt:** P1
- **Client side:** A `ttk.Treeview` shows each group as a parent row (`netlab (2 members) ✓ joined`) with its members
  underneath, marked `(you)` and `(creator)`. It's rebuilt on every `GROUPS` message, so it updates live.
  Snapshot: `client.py` → `ChatApp.on_groups` (L274–297). AI prompt: P2

## R10: Join group by yourself
- **Server side:** `GROUP_JOIN` has only a `group` field. The server appends the name tied to **this connection**,
  so no message can add someone else. A test sends extra `name`/`user` fields naming carol, and only the sender is added.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.handle_group_join` (L240–252)
- **Code snapshot 2:** `server.py` → `handle_group_leave` (L254–265)
- **Proof:** `tests/test_server.py` → `test_join_adds_only_the_sender`
- **AI prompt:** P1
- **Client side:** Select a group, then **Join** (or double-click it and confirm). The `GROUP_JOIN` message has only a `group`
  field, so there is no way to name someone else. **Leave** sends `GROUP_LEAVE` and closes the tab.
  Snapshot: `client.py` → `join_group` / `leave_group` (L391–400), `on_group_double_click` (L371–378). AI prompt: P2

## R11: Group message (members only)
- **Server side:** For `GROUP_MSG`, the server first checks that the sender is in the member list. If not, it replies `ERROR NOT_MEMBER`
  and delivers nothing. Otherwise it writes the `MSG` (room `group:<name>`) only to the members' sockets.
- **Code snapshot (mandatory):** `server.py` → `ChatServer.handle_group_msg` (L267–283)
- **Proof:** `tests/test_server.py` → `R11GroupMessageTests` (L265–297): non-members get nothing, a non-member's message is rejected and
  reaches nobody, and a member who left stops receiving
- **AI prompt:** P1
- **Client side:** A group tab's Send sends `GROUP_MSG {group, text}`. Group tabs exist only for groups you're in, and
  even a modified client can't read a group it isn't in, because the server only sends to members.
  Snapshot: `client.py` → `open_group` (L342–345). AI prompt: P2

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
