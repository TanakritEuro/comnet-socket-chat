# AI Features: UI and Who Builds What

Part 3 needs at least one AI feature. We build four, one per person, and they share one screen layout.
This page is the contract for the **UI**; the **wire messages** are in [`PROTOCOL.md`](PROTOCOL.md).
Change either one here first, then in the code, in the same PR.

| Feature | Owner | What the user does | Wire messages |
|---|---|---|---|
| Summarization | euro | Click **Summarize** in the AI strip | `SUMMARIZE` → `SUMMARY` (done) |
| Translation | mee | Pick a language once, click **Translate** under a message | `TRANSLATE` → `TRANSLATION` |
| Sentiment | magga | Click **Analyze mood** on a message | `SENTIMENT` → `SENTIMENT_RESULT` |
| Chatbot | timsum | Open **AI Assistant** in the sidebar and ask | `BOT_ASK` → `BOT_REPLY` |

Rules every AI feature follows:

- **The server calls Gemini**, never the client, so `GEMINI_API_KEY` stays on the server. The client only sends a request and shows the answer.
- **Nothing runs automatically.** Every AI action starts with a click, so the free quota (5–10 requests/min) is spent only on purpose.
- **Results are private to the person who clicked.** Nobody else in the room sees a translation, a mood or a summary you asked for.
- **Privacy is checked on the server.** A request about a room you cannot read is rejected with the same rules as `SUMMARIZE` (`visible_since` in `server.py`).
- **The window never freezes.** The client shows the "working" state at once, the server answers from a worker thread, and the reply arrives through the existing receiver thread → `queue.Queue` → `root.after()` path.
- **Violet (`#5B3FC4`) and the sparkle icon mean "AI"**, so it is clear which controls are AI.

## Screen layout

```
┌─────────────┬──────────────────────────────────────────────────┐
│ alice       │ [Welcome] [@bob] [#netlab] [AI Assistant]        │  room tabs
│ Connected   ├──────────────────────────────────────────────────┤
│             │ @bob                                              │  room header
│ ASSISTANT   │ ✦ AI tools  [Translate to: English ▾]  [Summarize]│  AI strip
│  AI Assistant [BOT]       ├────────────────────────────────────┤
│             │ bob  14:02                  [Analyze mood]        │  message header
│ ONLINE USERS│ สวัสดี alice ...                                  │
│  alice (you)│ Translate                                         │  link under message
│  bob        │ ┌ Translated · Thai → English ┐                   │  result block
│ GROUPS      │ └─────────────────────────────┘                   │
│  netlab ... ├──────────────────────────────────────────────────┤
│ [Create][Join][Leave]     │ [message box ...............] [Send] │
└─────────────┴──────────────────────────────────────────────────┘
```

The full mock-ups (login, dialogs, private chat, group chat with summary drawer, AI Assistant tab) are in the
"Socket Chat UI Design" artifact on claude.ai; it is private to timsum, so this page is the copy the team can read.

## Per feature: where it is and what states it has

### Translation (mee)
- **AI strip:** one *Translate to: &lt;language&gt;* dropdown per room (English, Thai, Japanese, Chinese, Spanish). It only sets the target language; it translates nothing by itself. There is **no auto-translate**.
- **Under each message from someone else:** a *Translate* link. Click → `TRANSLATE`.
- **States:** idle (link) → translating ("Translating…" block) → translated (violet block "Translated · Thai → English" + text) → error (red block "Couldn't translate this message", link comes back).

### Sentiment (magga)
- **Right end of each message header:** an *Analyze mood* button. Click → `SENTIMENT`.
- **States:** idle (button) → analyzing ("Analyzing…") → result (the button is replaced by a Positive / Neutral / Negative pill in the same spot) → error (button comes back with a short red note).
- Colours: Positive `#17663F`, Neutral `#3F4A5C`, Negative `#A5331F`. There is no room-level mood: only messages somebody clicked have a pill.

### Summarization (euro)
- **AI strip:** *Summarize* button at the right end (today it sits next to the message box in `RoomTab`; it moves to the strip).
- **Result:** a drawer on the right of the chat window (group chat mock-up), private to whoever asked, with the model name and an offline-fallback banner.
- **States:** idle → loading ("Summarizing the last 50 messages…") → ready → offline fallback (amber banner) → error.

### Chatbot (timsum)
- **Sidebar:** a row *AI Assistant* with a violet BOT badge under the heading "Assistant". Double-click opens its own tab (same gesture as the user list).
- **Tab:** the bot is **not** a room on the server and not in the message buffer. It is a private conversation between you and the server. Your questions and the bot's answers are listed like a normal chat; answers have a violet tint. Suggestion chips (*Explain TCP framing*, *Explain my last chat*, *Help me write a reply*) fill the question with one click. *Clear conversation* in the header forgets the conversation on both sides.
- **States:** idle → thinking ("Thinking…" under the assistant name) → reply → error. One question at a time: **Ask** is disabled while the bot is thinking.
- *Explain my last chat* sends `BOT_ASK` with the `room` field set to the room tab that was open before, so the bot can read that room's recent messages (same privacy check as `SUMMARIZE`).

## Where it goes in the code

| Piece | File | Change |
|---|---|---|
| AI strip, per-message controls, result blocks | `client.py`, `RoomTab` | New strip frame above the chat window; `add_message` returns/stores a handle per message `ts` |
| Bot tab, sidebar row | `client.py`, `ChatApp.build_widgets`, `open_room` | One extra room key (`"bot"`) whose `on_send` sends `BOT_ASK` |
| Handling the replies | `client.py`, `ChatApp` | One `on_<type>` method per reply, registered like `on_summary` |
| Message constants | `protocol.py` | New constants (see PROTOCOL.md) |
| Request handlers | `server.py` | One `handle_<type>` + `<type>_worker` pair per feature, copied from `handle_summarize` |
| Gemini prompts and parsing | `ai.py` shared helpers; one new module per feature (`ai_translate.py`, `ai_sentiment.py`, `ai_bot.py`) | Keeps four people out of the same file |

Tkinter notes:

- Tinted blocks, mood pills and "links" are `Text`-widget tags (`tag_configure`, `tag_bind("<Button-1>", …)`), like the colours `add_message` already uses. Buttons inside a message can be `window_create` widgets.
- A reply arrives later than newer messages. To put a result **under the right message**, give each message a `Text` mark (`mark_set(f"msg-{ts}", …)`, gravity `left`) when it is added, and insert the block at that mark.
- The language dropdown is a `ttk.Combobox`.

## Testing rules

- **Never call Gemini in tests.** `ChatServer` takes the AI functions as constructor arguments (`summarizer=` today); the new features add `translator=`, `analyzer=` and `bot=` the same way, and tests pass fakes. `scripts/e2e_gui_check.py` must use fakes or offline mode too.
- Each feature needs a test that a user who cannot read the room gets `NOT_MEMBER` and no result.

## Order of work

1. **This docs PR** (protocol + UI spec): merge first so everyone codes against the same messages.
2. **UI shell PR** (timsum, `feat/ui-ai-strip`): AI strip, per-message *Translate* / *Analyze mood* controls and the Assistant row, wired to stub callbacks.
3. **Feature PRs**, in parallel: `feat/translate` (mee), `feat/sentiment` (magga), move *Summarize* into the strip (euro), `feat/chatbot` (timsum). Each adds its own constants, server handler, `ai_*.py` module, client `on_<type>` method, tests, and a `docs/SLIDE_NOTES.md` entry with the AI prompt used.
