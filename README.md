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

## Members

| Student ID | Name | GitHub |
|---|---|---|
| | | |
| | | |
| | | |
| | | |
