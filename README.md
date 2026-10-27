# Socket Chat: Computer Network Term Project

A simple chat application built on raw TCP sockets (Python stdlib) with a Tkinter GUI.
Private chats, group chats, AI chat summarization, file sharing, chat history, and typing indicators.

- Requirements: [`docs/SPEC.md`](docs/SPEC.md)
- Wire protocol: [`docs/PROTOCOL.md`](docs/PROTOCOL.md)
- Slide material: [`docs/SLIDE_NOTES.md`](docs/SLIDE_NOTES.md)
- Presentation: **Mon 9 Nov 2026**

## Quick start

Requires Python 3.10+ (the python.org Windows installer includes Tkinter). No other dependencies.

```bash
# server machine
cp .env.example .env        # add GEMINI_API_KEY (optional; there's an offline fallback)
python server.py --host 0.0.0.0 --port 5050

# each client machine
python client.py            # enter the server's IP, port 5050, and a unique name
```

## Team workflow

1. `git pull` on `main`, then `git checkout -b feat/<thing>`
2. Commit, push, `gh pr create`
3. A teammate reviews → squash merge. **No direct pushes to `main`.**

Suggested split (adjust as needed):

| Member | Owns |
|---|---|
| 1 | `protocol.py`, `server.py` core (R2–R4, R7–R11), tests |
| 2 | `client.py` GUI (R5, R6, client side of R7–R11) |
| 3 | AI summarization + chat history/unread |
| 4 | File transfer + typing/presence + architecture doc (R1) + slides |

Everyone should be able to explain R1–R11 in the Q&A, not just their own part.

## Testing

```bash
python -m unittest discover -s tests -v     # headless: real server + raw-socket clients, no windows
python scripts/e2e_gui_check.py             # opens 2 real client windows for a few seconds and clicks through R3-R11
python scripts/e2e_gui_check.py --screenshot e2e.png   # same, plus a picture of both windows (Windows only)
```

`scripts/e2e_gui_check.py` starts a server on a free port, logs in `alice` and `bob` through the real login screen,
connects a raw-socket `carol`, and drives the widgets in code: DM, create/join/leave a group, group message, a duplicate
name, and a duplicate group. It checks what each window shows, and that `carol` (no GUI, not a member) gets no chat message at all.
Message boxes are recorded instead of shown, so it never waits for a click. **Rerun it after every PR that touches
`client.py` or the protocol**; it exits non-zero if any check fails. Don't move the mouse into the windows while it runs.

## Members

| Student ID | Name | GitHub |
|---|---|---|
| | | |
| | | |
| | | |
| | | |
