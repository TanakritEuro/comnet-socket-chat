# Socket Chat: Code Summary (for the presentation)

A chat application built with **raw TCP sockets** in Python (Computer Network, CP ENG Chula, presentation Mon 9 Nov 2026).
Python 3.10+, **standard library only** (Pillow is optional, just for JPEG previews).

- [1. The big picture](#1-the-big-picture)
- [2. Files at a glance](#2-files-at-a-glance)
- [3. Core files, explained, with every function](#3-core-files-explained)
  - [protocol.py](#protocolpy) · [server.py](#serverpy) · [client.py](#clientpy)
- [4. AI files](#4-ai-files) · [ai.py](#aipy) · [ai_bot.py](#ai_botpy) · [ai_translate.py](#ai_translatepy) · [ai_sentiment.py](#ai_sentimentpy)
- [5. UI files](#5-ui-files) · [ui_theme.py](#ui_themepy) · [ui_widgets.py](#ui_widgetspy) · [chat_tabs.py](#chat_tabspy)
- [6. Scripts and tests](#6-scripts-and-tests)
- [7. Message cheat sheet](#7-message-cheat-sheet)
- [8. Likely questions and answers](#8-likely-questions-and-answers)

---

## 1. The big picture

```
 Laptop A: client.py ─┐                                    ┌── HTTPS (TLS, port 443) ──> Google Gemini API
 Laptop B: client.py ─┼── TCP, port 5050 ──> server.py ────┤        (only the SERVER calls it,
 Laptop C: client.py ─┘   (our own protocol)               └─        so the API key never leaves the server)
```

**One server, many clients.** Every client keeps one TCP connection open to the server. Clients never talk to each other: the
server receives every message and decides who gets it (that is how private chats and groups stay private).

**Our application protocol** (what travels inside the TCP connection):

```
+----------------------+-----------------------------------+
| length: 4 bytes      | body: <length> bytes              |
| unsigned, big-endian | UTF-8 JSON object, has "type"     |
+----------------------+-----------------------------------+
```

TCP is a *byte stream* with no message boundaries, so every message gets a 4-byte length in front. The receiver reads exactly 4
bytes, then exactly that many bytes of JSON. Example: `{"type": "DM", "to": "bob", "text": "hi"}`.

**Threads (the part people ask about):**

| Where | Threads | Why |
|---|---|---|
| Server | per client: a **reader thread** (`handle_client`) and a **writer thread** (`Conn.write_loop`) with its own send queue; one `threading.Lock` for shared state | A slow client only delays its own queue; handlers decide *who* gets a message under the lock but only *queue* the frame, and the real network write happens outside the lock |
| Server (AI) | a short-lived **worker thread** per AI request | Gemini takes seconds; the server keeps serving everyone else meanwhile |
| Client | a **receiver thread** blocks on the socket and puts messages in a `queue.Queue`; the Tk GUI thread polls it every 50 ms with `root.after()` | Tkinter is **not thread-safe**: only the main thread may touch widgets |

**What the app can do (requirements R3-R11 plus the "special points"):**

| Feature | Idea |
|---|---|
| R3 unique names | `HELLO` with a name; duplicate gets `ERROR NAME_TAKEN` and the login screen shows it in red |
| R4 online list | `USERS` is broadcast to everyone on every connect/disconnect |
| R7 private chat | `DM` goes to exactly two sockets: sender and recipient |
| R8-R11 groups | create (creator is the only member), join (only yourself), leave, group messages go to members only |
| Special 1 | AI **summary** of a room (Gemini; offline fallback) |
| Special 2 | **Files and images** (base64 inside JSON, max 5 MiB, preview in chat) |
| Special 3 | **History** (last 200 messages per room, server memory) and **unread badges** |
| Special 4 | **AI Assistant** chatbot tab (private, remembers the last 10 Q&A pairs) |
| Extra AI | per-message **Translate** and **Mood** (sentiment) |
| Deploy | runs on a Google Cloud VM with a systemd service (`deploy/`) |

---

## 2. Files at a glance

| File | Lines | What it does (one sentence) |
|---|---|---|
| `protocol.py` | 188 | Shared by client and server: message-type constants, framing (`send_msg` / `recv_msg`), name and room-id helpers |
| `server.py` | 799 | The TCP server: accepts clients, logs them in, routes every message, enforces privacy, keeps history, starts AI workers |
| `client.py` | 864 | The Tkinter app: login screen, main window (sidebar + tabs), the receiver thread, and the code that turns server messages into screen changes |
| `ai.py` | 196 | Gemini REST call for **summaries**, `.env` loading, the model fallback chain, offline summary |
| `ai_bot.py` | 104 | Gemini call for the **chatbot** (sends the conversation as alternating turns) |
| `ai_translate.py` | 108 | Gemini call to **translate** one message (asks for JSON: source language + translation) |
| `ai_sentiment.py` | 112 | Gemini call for the **mood** of one message (positive / neutral / negative) plus a word-list fallback |
| `chat_tabs.py` | 780 | The chat tabs: `RoomTab` (private chat or group) and `BotTab` (AI Assistant), bubbles, cards, composer |
| `ui_widgets.py` | 152 | Small drawn widgets: avatars, icons, unread pills, `SidebarRow` |
| `ui_theme.py` | 290 | Colours, fonts, and the rounded buttons / fields drawn as images (Tk can't round corners) |
| `scripts/e2e_gui_check.py` | 495 | A script that starts a real server and real client windows, clicks through the app and prints PASS/FAIL (83 checks) |
| `scripts/render_diagrams.py` | 72 | Renders the Mermaid diagrams in `docs/ARCHITECTURE.md` to PNG |
| `tests/test_*.py` | 9 files | 177 headless unit tests with raw-socket test clients; the AI is always mocked |

Other folders: `docs/` (SPEC, PROTOCOL, ARCHITECTURE, AI_FEATURES, SLIDE_NOTES), `deploy/` (cloud guide + systemd unit).

**Dependency map** (who imports whom): `client.py` → `chat_tabs.py`, `ui_widgets.py`, `ui_theme.py`, `protocol.py`.
`server.py` → `protocol.py`, `ai.py`, `ai_bot.py`, `ai_translate.py`, `ai_sentiment.py`. The client never imports the server.

---

## 3. Core files, explained

### protocol.py
**Purpose:** the contract both sides share. If a message type or the framing changes, it changes here (and in `docs/PROTOCOL.md`).

**What's inside**
- **Constants:** every message `type` (`HELLO`, `DM`, `MSG`, `FILE`, `HISTORY`, `SUMMARIZE`, `TRANSLATE`, ...), every error code
  (`NAME_TAKEN`, `NOT_MEMBER`, `BUSY`, `TOO_LARGE`, `AI_FAILED`, ...), and limits: `MAX_FRAME` = 10 MiB, `MAX_NAME_LEN` = 20,
  `MAX_FILE_BYTES` = 5 MiB, `MAX_FILENAME_LEN` = 100.
- **Framing code.** The important idea: `recv()` may return *less* than you asked for, so `recv_exact` loops until it has all `n` bytes.

| Function | What it does |
|---|---|
| `class ProtocolError` | Raised when the peer sent a bad frame; carries an error `code` |
| `encode_frame(msg)` | dict → JSON (UTF-8, `ensure_ascii=False` so Thai stays Thai) → 4-byte length + body; refuses frames over `MAX_FRAME` |
| `send_msg(sock, msg)` | `encode_frame` then `sock.sendall(...)` |
| `recv_exact(sock, n)` | Loops on `recv` until exactly `n` bytes are read; returns `None` if the peer closed (empty read = TCP FIN) |
| `recv_msg(sock)` | Reads the 4-byte length, checks it against `MAX_FRAME`, reads the body, parses JSON, checks it is an object with a string `type`; returns a dict or `None` on close |
| `clean_name(raw)` | Trims a user/group name; returns it if valid (1-20 chars, no `|`), else `None` |
| `clean_filename(raw)` | Keeps only the part after the last `/` or `\` (no folder tricks); `None` if invalid |
| `dm_room(a, b)` | Room id of a private chat: names sorted, so both sides get the same one, e.g. `dm:alice|bob` |
| `dm_partner(room, me)` | From `dm:alice|bob` returns the name that is not `me` |
| `group_room(group)` | Room id of a group: `group:<name>` |

---

### server.py
**Purpose:** the heart of the project. It owns *all* shared state (who is online, groups, message history) and decides who may see what.

**Shared state in `ChatServer`:** `clients` (name → `Conn`), `groups` (name → creator + members), join times (for the "only messages
after you joined" rule), the `MessageBuffer`, the chatbot conversations, caches, and one `lock`.

**Life of one client:** `serve_forever` accepts a TCP connection → starts a thread running `handle_client` → `login` waits for a valid,
unused `HELLO` name → sends `WELCOME`, then `USERS` to everyone and `GROUPS` to the newcomer → loops reading messages and calling the matching `handle_*` →
on disconnect `remove_client` tells everyone the new user list.

**Key design points**
- **Privacy is enforced on the server**, never trusted from the client: a DM goes to two sockets only, a group message to members only, `HISTORY`
  / `SUMMARIZE` / `TRANSLATE` / `SENTIMENT` / `BOT_ASK` use `visible_since` (a member only sees messages from after they joined).
- **Writer queue per client** (`Conn`): handlers only `put()` an encoded frame; one writer thread sends them in order, so frames never
  interleave and a slow client can't block anyone. A client more than 32 MiB behind is disconnected.
- **AI never holds the lock:** `handle_*` validates under the lock, then a worker thread calls Gemini and replies to the requester only.
- **Anti-abuse:** one summary / chatbot question at a time per user (`BUSY`), at most 3 translations and 3 mood analyses in progress,
  results cached (200 translations, 500 moods) so the same click costs no API call.

**Classes and functions**

| Name | What it does |
|---|---|
| `class MessageBuffer` | The last 200 messages per room, in memory (a `deque(maxlen=200)` per room); lost on restart |
| ↳ `add(msg)` | Store a message; a `FILE` is stored as metadata only (no bytes) to save RAM |
| ↳ `recent(room, since, limit)` | Messages sent at/after `since`, oldest first, newest `limit` (50 for the AI, all for history) |
| ↳ `find(room, ts)` | One message by id (room + timestamp); used by Translate and Mood |
| ↳ `next_ts(room)` | A strictly increasing server timestamp per room (fixes Windows' 15 ms clock tick) |
| `class Conn` | One client's TCP connection: socket + send queue + writer thread |
| ↳ `put(frame)` | Queue a frame; never blocks |
| ↳ `write_loop()` | Writer thread: `sendall` queued frames one by one, outside every lock |
| ↳ `close()` | Let the queue drain (e.g. a final ERROR), then close |
| ↳ `abort()` | Drop the client now and end both of its threads |
| `class ChatServer` | Shared state + all handlers (below) |
| ↳ `start()` | Create the listening socket (`AF_INET`, `SOCK_STREAM`, bind, listen); returns the port |
| ↳ `serve_forever()` | Accept loop: one `handle_client` thread per connection |
| ↳ `stop()` | Stop accepting |
| ↳ `handle_client(sock, addr)` | Per-client thread: login, then read and dispatch messages until disconnect |
| ↳ `login(conn)` | R3: waits for a valid `HELLO` with a free name (can retry on the same connection) |
| ↳ `remove_client(name)` | Forget a client and broadcast `USERS` |
| ↳ `send(conn, msg)` / `deliver(msg, conns)` | Queue one message for one client / for several (encoded once) |
| ↳ `send_error(...)` | `ERROR` with `code`, `message` and optional `request`/`room`/`ts` so the client resets the right control |
| ↳ `broadcast_users()` | R4: full online list to everyone |
| ↳ `groups_message()` / `broadcast_groups()` | R9: build and broadcast every group with its members |
| ↳ `handle_dm` | R7: private message to sender + recipient only |
| ↳ `handle_group_create` / `handle_group_join` / `handle_group_leave` | R8 / R10 / leave (join only ever adds *the sender*) |
| ↳ `handle_group_msg` | R11: only members may send; delivered to members |
| ↳ `handle_file` | Special 2: validate base64 and size, strip folders from the name, deliver like a text message |
| ↳ `handle_history` | Special 3: send a room's stored messages to the requester only |
| ↳ `handle_summarize` + `summarize_worker` | Special 1: check access, then a worker calls the AI and sends `SUMMARY` |
| ↳ `handle_translate` + `translate_worker` + `translation_message` | Translate one message (looked up by id on the server) |
| ↳ `handle_sentiment` + `sentiment_worker` + `sentiment_message` | Mood of one message |
| ↳ `handle_bot_ask` + `bot_worker` | Special 4: chatbot with the user's last 10 Q&A pairs as context |
| ↳ `handle_bot_clear` | Forget this user's chatbot conversation |
| ↳ `visible_since(conn, name, room_id)` | The privacy check for reading a room's past messages; sends the error itself if refused |
| ↳ `find_group(conn, group)` | Returns the group or sends `NO_SUCH_GROUP` |
| `is_text(value)` | A chat text must be a non-blank string |
| `local_ipv4_addresses()` | Prints this machine's IPs at startup so clients know what to type |
| `main()` | Parses `--host` / `--port`, starts the server, logs the AI setup |

---

### client.py
**Purpose:** the desktop app. It connects, shows the UI, sends what you do, and draws what the server sends.

**Flow:** `main()` → `LoginFrame` (host, port, name) → `connect()` opens the TCP connection, sends `HELLO`, waits for `WELCOME` → on success
`ChatApp` is built. `ChatApp.receive_loop` runs in a thread and queues every message; `poll_inbox` (GUI thread, every 50 ms) takes messages
from the queue and calls the matching `on_*` handler (a dict `self.handlers` maps message type → method).

**Main window layout:** dark navy sidebar (you, AI Assistant, Online users, Groups, New group / Join) + a notebook of tabs
(Welcome, one tab per chat, Assistant). Each chat tab is a `RoomTab` from `chat_tabs.py`.

**`LoginFrame`**

| Function | What it does |
|---|---|
| `__init__` | Builds the navy brand panel and the form (host + port on one row, name) |
| `connect()` | Validates input, `socket.create_connection`, sends `HELLO`, handles `WELCOME` / `ERROR` |
| `fail(text)` | Shows a message box for general login errors |
| `show_name_error(text)` | R3: red message under the name field, red outline, focus; no pop-up |
| `clear_name_error()` | Removes the red error when the user types |

**`ChatApp`**

| Group | Functions |
|---|---|
| Setup | `__init__`, `build_widgets` (sidebar + notebook), `section_label`, `server_address` |
| Networking | `receive_loop` (thread), `poll_inbox` (GUI polling), `send`, `quit` (sends `BYE`), `on_disconnected` |
| Server → screen | `on_users`, `on_groups`, `on_msg` (text and files), `on_summary`, `on_translation`, `on_sentiment`, `on_bot_reply`, `on_error`, `on_history` |
| Sidebar | `draw_users`, `draw_groups`, `member_row`, `show_members`, `select`, `member_text` |
| Opening chats | `open_user` / `open_group_row` (double-click), `open_room` (one tab per room), `open_dm`, `open_group`, `group_subtitle`, `open_bot` |
| Tabs | `tab_title`, `tab_at`, `on_tab_press`, `on_tab_release`, `on_tab_middle_click`, `close_tab`, `close_room`, `on_tab_changed` |
| History and unread | `request_history`, `show_message`, `is_viewing`, `show_unread`, `badge` |
| Features (client → server) | `send_file`, `translate_message`, `analyze_mood`, `ask_bot`, `clear_bot` |
| Groups | `selected_group`, `create_group`, `join_group`, `leave_group` |

Other: `fit_to_screen(root)` sizes the window for the screen and Windows display scaling; `main()` parses `--host/--port/--name`, enables DPI awareness, shows the login screen.

**Worth saying out loud:** history is requested whenever a tab opens; live messages that arrive before the history reply wait in
`self.waiting`, so history is always first and nothing shows twice. Unread counts are computed on the client from messages that arrive
while a room's tab is not on screen.

---

## 4. AI files

All four use the **Gemini REST API** with `urllib.request` (HTTPS POST + JSON, TLS on port 443) and share the same ideas:
the **API key lives only on the server** (`.env`), it is sent in a header and never logged or sent to clients; a **model chain** is tried in order
(HTTP 429/404/503 → next model; a timeout or other error stops); **tests mock the HTTP call**, never the real API.

### ai.py
Summaries, plus the shared configuration.

| Function | What it does |
|---|---|
| `class Config` | `api_key` and `models` (the chain) |
| `read_env_file(path)` | Parses `KEY=VALUE` lines by hand (no `python-dotenv`) |
| `load_config(env_file, environ)` | Builds a `Config`; a real environment variable beats the file |
| `describe(config)` | One safe log line: which models, whether a key exists (never the key) |
| `summarize(room, messages, config)` | Returns `(text, model)`; **never raises** on network problems: falls back to the offline summary |
| `message_text(m)` | Text of a buffered message; a file becomes `[sent a file: name]` (bytes never reach the AI) |
| `build_prompt(room, messages)` | Turns messages into a transcript `[HH:MM] name: text` |
| `call_gemini(model, api_key, prompt)` | One HTTPS POST to `generateContent`; returns the reply text |
| `extract_text(data)` | Pulls the answer out of `candidates[0].content.parts[]` |
| `offline_summary(messages, reason)` | No-AI summary: message count, participants, last few lines |
| `_ipv4_first(...)` | Prefers IPv4 addresses (some networks announce IPv6 but can't route it) |

### ai_bot.py
| Function | What it does |
|---|---|
| `class BotError` | Gemini could not answer; the server turns it into `ERROR AI_FAILED` |
| `ask(history, text, room, messages, config)` | Answers one question; returns `(answer, model)`; with no key returns an offline notice |
| `build_contents(history, text, room, messages)` | Builds the alternating user/model turns, optionally with the chat's messages in front |
| `call_gemini_chat(model, api_key, contents)` | One HTTPS POST with the whole conversation |

### ai_translate.py
| Function | What it does |
|---|---|
| `class TranslateError` | Could not translate (no key, quota, no internet, bad answer); becomes `AI_FAILED` |
| `translate(text, to, config)` | Returns `(translation, detected source language, model)`; the message text is treated as data, not instructions |
| `call_gemini_json(model, api_key, request_text)` | HTTPS POST asking for a JSON answer |
| `parse_reply(reply)` | Reads the JSON (also inside a markdown fence, or plain text) into `(translation, source)` |

### ai_sentiment.py
| Function | What it does |
|---|---|
| `analyze(text, config)` | Returns `(label, model)`; label is `positive` / `neutral` / `negative`; never raises |
| `call_gemini_json(model, api_key, text)` | HTTPS POST asking for a JSON answer |
| `parse_reply(reply)` | Turns the answer into one of the three labels, else `ValueError` |
| `offline_label(text)` | Word-list guess (English, Thai, emoji) when AI is unavailable; marked `model: "offline"` |

---

## 5. UI files

The look is **plain Tkinter/ttk, no extra library** (an earlier try with `ttkbootstrap` was dropped). Tk can't draw rounded corners, so the
code generates small rounded images and uses them as ttk image elements.

### ui_theme.py
| Function | What it does |
|---|---|
| (constants) | Colours (`INK`, `BLUE`, `VIOLET`, `SIDEBAR`, ...), fonts, `TAB_CHARS`, `BUTTON_LOOKS` |
| `_png(width, height, rows)` | Writes PNG bytes by hand with `struct` and `zlib` |
| `_rgb(color)` | `#RRGGBB` → numbers |
| `rounded_image(master, radius, fill, border, ...)` | A small rounded rectangle with anti-aliased edges as a `PhotoImage` (cached) |
| `ui_scale(widget)` | 1.0 at 100 % Windows scaling, 1.25 at 125 %, ... |
| `build_skin(style, master)` | Registers the rounded buttons, entry fields, chat bubbles, tab close "x" and red error field as ttk elements |
| `apply_theme(root)` | Applies the light theme on top of ttk's `clam` theme (tabs, lists, scrollbars, ...) |

### ui_widgets.py
| Function | What it does |
|---|---|
| `avatar_color(name)` | Same name → same colour |
| `make_avatar(...)` | Round avatar with the initial and an optional green online dot |
| `make_square_icon(...)` | Rounded-square icon (`#` for groups, ✦ for the assistant) |
| `make_dot(...)` | Small round dot (member online) |
| `make_pill(...)` | Round blue unread counter |
| `class SidebarRow` | One sidebar row: icon, title, suffix, subtitle, badge or tag; click selects, double-click opens |
| ↳ `all_widgets`, `click`, `open`, `set_selected`, `text` | Bind clicks on every child, select/open, recolour on selection, row text (used by tests) |

### chat_tabs.py
**Purpose:** everything inside a chat tab. The chat area is one read-only `Text` widget; each message is built from text with tags plus
embedded widgets (avatar canvas, bubble `ttk.Label`, cards). Later additions (mood chip, translation box) are inserted at saved *marks*.

| Function / class | What it does |
|---|---|
| `make_chat_window(parent)` | The read-only `Text` with all text styles (tags) |
| `class PlaceholderEntry` | Entry with a grey hint (`Message @bob`); `get()` returns `""` while the hint shows |
| `format_size`, `add_avatar`, `append_text` | Helpers: "12.3 KB", an avatar at the start of a header, append text and scroll |
| `class Bubbles` (shared mixin) | `init_bubbles`, `wrap_width`, `track`/`on_window_resized`/`rewrap` (wrap at 70 % of the width, re-wrap after a resize pause), `make_interactive`/`scroll_chat`/`show_copy_menu`/`copy_text` (right-click Copy, wheel scrolling), `make_bubble`, `embed`, `append`, `write`, `add_notice` |
| `class RoomTab` | A private chat or group |
| ↳ composer | `attach` (pick a file), `send` |
| ↳ header | `set_subtitle`, `language_code` (the "Translate to" picker), `summarize`, `enable_summarize` |
| ↳ messages | `start_message`, `maybe_day_divider`, `set_marks`, `add_message`, `add_file`, `file_card`, `add_preview` (image preview), `save_file`, `add_divider` (below history) |
| ↳ message parts | `insert_slot`, `replace_slot`, `remove_link`, `line_tags`, `slot_at`, `on_link_click`, `on_mood_click` |
| ↳ Translate | `translate`, `show_translating`, `clear_box`, `add_translation`, `translation_failed` |
| ↳ Mood | `analyze`, `show_mood_pending`, `set_mood`, `mood_failed` |
| ↳ Summary | `add_summary` |
| `class BotTab` | The AI Assistant tab: `ask`, `add_question`, `show_thinking`, `add_reply`, `failed`, `finish_reply`, `set_waiting` (disables the field, Ask and chips while waiting), `clear` |

---

## 6. Scripts and tests

**`scripts/e2e_gui_check.py`**: starts a real server and real client windows, drives the GUI like a user, prints PASS/FAIL (83 checks). Uses a fake
translator/analyzer so no API calls. Helpers: `record_popups`, `wait_until`, `check`, `log_in`, `raw_messages`, `text_of`, `user_texts`,
`member_texts`, `make_png`, `offline_summarizer`, `offline_bot`, `fake_translator`, `fake_analyzer`, `screenshot`, `main`.
Run: `python scripts/e2e_gui_check.py`.

**`scripts/render_diagrams.py`**: `main()` turns the Mermaid diagrams in `docs/ARCHITECTURE.md` into PNGs.

**`tests/`** (run all: `python -m unittest discover -s tests -v`, 177 tests, about 18 s): a *real* server on a free port plus raw-socket clients; AI is mocked.

| File | What it tests |
|---|---|
| `test_protocol.py` | Framing: Thai text round-trip, a frame in small pieces, two frames in one send, closed connection, oversized and non-JSON frames, helper functions |
| `test_server.py` | R3 names, R4 user list, R7 DM privacy, R8-R11 groups, summaries, chatbot, key never leaked, robustness (unknown type, garbage frame) |
| `test_history.py` | HISTORY: exact messages, survives reconnect, privacy, late joiner, cap of 200, unique ids |
| `test_file.py` | Files: delivery only to the right people, size limit, name cleaning, metadata-only history |
| `test_translate.py` | Translate function and server flow: privacy, caching, limits, `AI_FAILED`, key not leaked |
| `test_sentiment.py` | Mood function and server flow, offline guess, caching, limits |
| `test_ai.py`, `test_ai_bot.py` | Gemini request format, model fallback, offline fallback, `.env` parsing |
| `test_send.py` | Writer queues: frames never interleave, slow or stuck clients delay nobody, a too-slow client is disconnected |

---

## 7. Message cheat sheet

| Client → Server | Meaning | Server → Client |
|---|---|---|
| `HELLO {name}` | Log in | `WELCOME`, then `USERS` (all) and `GROUPS` |
| `DM {to, text}` | Private message | `MSG` to sender and recipient |
| `GROUP_CREATE / GROUP_JOIN / GROUP_LEAVE {group}` | Group management | `GROUPS` broadcast |
| `GROUP_MSG {group, text}` | Group message | `MSG` to members |
| `FILE {room, filename, size, data(base64)}` | Send a file/image | `FILE` to the room's people |
| `HISTORY {room}` | Past messages | `HISTORY {room, messages}` (requester only) |
| `SUMMARIZE {room}` | AI summary | `SUMMARY` (requester only) |
| `TRANSLATE {room, ts, to}` | Translate one message | `TRANSLATION` (requester only) |
| `SENTIMENT {room, ts}` | Mood of one message | `SENTIMENT_RESULT` (requester only) |
| `BOT_ASK {text, room?}` / `BOT_CLEAR` | Chatbot | `BOT_REPLY` (asker only) |
| `BYE` | Leave | (connection closes) |
| any bad request | | `ERROR {code, message}` |

Error codes: `NAME_TAKEN`, `BAD_NAME`, `BAD_REQUEST`, `NO_SUCH_USER`, `NO_SUCH_GROUP`, `NOT_MEMBER`, `GROUP_EXISTS`, `ALREADY_MEMBER`,
`NO_SUCH_MESSAGE`, `BUSY`, `TOO_LARGE`, `AI_FAILED`. (`TYPING` and `NOTICE` appear in the protocol doc but are **not implemented**.)

---

## 8. Likely questions and answers

- **Why TCP, not UDP?** A chat message must arrive complete, once, and in order; TCP guarantees that and also tells the server when a client
  disconnects. With UDP we would have to rebuild all of that.
- **Why the 4-byte length prefix?** TCP is a byte stream; one `recv` can return half a message or two messages. The length says where a message ends.
- **Why JSON?** Easy to read and debug, easy to extend with new fields, and UTF-8 carries Thai text.
- **How does the server know who may read a message?** It enforces it: DMs go to two sockets, group messages to members, and history/AI requests
  pass `visible_since`. Clients can't ask for someone else's messages because the AI requests name a message by id and the server looks up the text itself.
- **Why threads?** One reader and one writer thread per client keep a slow client from blocking others; a lock protects shared state;
  AI calls run in worker threads without the lock so the server stays responsive.
- **Why does the client use a queue?** Tkinter isn't thread-safe. The receiver thread only puts messages in a `queue.Queue`; the GUI thread polls it.
- **Where is the API key?** Only in `.env` on the server. Clients never see it, and tests prove it is never sent to a client.
- **What if the AI is down?** Summary, chatbot and mood fall back to offline answers marked `offline`; translation has no fallback and shows an error with *Try again*.
- **What are the weak points?** Chat traffic is plain TCP, not encrypted (TLS via `ssl.wrap_socket` would fix it); history and groups live in server memory and
  reset on restart; names are not passwords; files in history are metadata only.
- **How do you run it?** `python server.py --host 0.0.0.0 --port 5050`, then `python client.py` on each laptop (host = server IP, port 5050, a unique name).
- **How did you test it?** 177 unit tests with raw-socket clients (no GUI) and an end-to-end GUI script with 83 checks; the AI is always faked.
