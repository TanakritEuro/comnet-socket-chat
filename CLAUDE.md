# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

Term project for **Computer Network (CP ENG, Chulalongkorn University), 1st semester 2026**:
a **simple chat application built with socket programming**. Group of 4 students.

- **Presentation / demo: Monday 9 Nov 2026.** Each student must be able to explain the code
  live, so keep the code small, readable, and well commented. Clarity beats cleverness.
- Full requirements: `docs/SPEC.md` (converted from the assignment PDF, which lives one folder up in `../` and is NOT part of the repo; the PDF is the source of truth).
- Wire protocol: `docs/PROTOCOL.md`. **Keep it in sync with the code**. Any change to a message type goes in the same PR as the code change.
- Grading is 12.5 points: Part 1 (R1–R2) must be complete before Part 2 is graded, and Part 2
  (R3–R11) must be complete before Part 3 (special points) is graded. **Build in that order.**

## Tech decisions (already made: don't change without asking)

| Area | Decision | Why |
|---|---|---|
| Language | Python 3.10+ | Everyone in the group can read it |
| Transport | Raw TCP with the stdlib `socket` module | R2 demands "socket programming only": no Socket.IO, no WebSocket libs, no HTTP for chat |
| Concurrency | Server: one `threading.Thread` per client + one `threading.Lock` for shared state. Client: one receiver thread → `queue.Queue` → Tkinter polls with `root.after()` | Simple to explain; Tkinter is not thread-safe, so never touch widgets from the network thread |
| Framing | 4-byte big-endian length prefix + UTF-8 JSON body | TCP is a byte stream, so we need message boundaries |
| GUI | Tkinter (stdlib) | Ships with the python.org installer on Windows; no installs during the demo |
| AI feature | Chat summarization through the Google Gemini REST API, called **from the server** with `urllib.request` over HTTPS | Keeps the API key on the server only. If no key is set, fall back to a simple offline summary so the demo never breaks |
| Dependencies | **Standard library only** (optionally `python-dotenv`, but parsing `.env` by hand is fine) | Zero setup on lab machines |

Team model chain (`ai.py` defaults, mirrored in `.env.example`): `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite` →
`gemini-3.8-flash` → `gemini-3-flash-preview` → `gemini-2.5-flash-lite` → `gemini-2.5-flash`. HTTP 429/404/503 moves to the next
model; a timeout or any other error stops the chain. Free-tier limits are per Google Cloud project, 5–15 requests/min per model,
and the daily quota resets at midnight Pacific time. So tests and `scripts/e2e_gui_check.py` must mock the HTTP call or force
offline mode. Never make real calls in tests.
The 2.5 models are not retired, but Google serves them only to keys that used them before; other keys get HTTP 404 "no longer
available to new users". Gemma 4 (`gemma-4-31b-it`) is left out: it didn't answer within 60 s on the free tier. To change the chain,
list the models your key can use (`GET /v1beta/models`) and update `ai.py` and `.env.example` together.

## Planned layout

```
server.py            # TCP server: accepts clients, routes messages, enforces privacy
client.py            # Tkinter GUI client
protocol.py          # shared: send_msg / recv_msg framing, message type constants
ai.py                # summarize(room, messages) -> (text, model)  (Gemini + offline fallback)
tests/               # scripted socket clients that test the server headlessly
docs/SPEC.md         # assignment requirements
docs/PROTOCOL.md     # message format: source of truth for client <-> server
docs/SLIDE_NOTES.md  # per-requirement: where it's implemented + AI prompts used (feeds the slides)
deploy/              # cloud deployment notes / systemd unit (special point)
```

The server and client **must be separate files** (spec requirement).

## Run

```bash
python server.py --host 0.0.0.0 --port 5050          # on the server machine
python client.py                                     # on each client machine; login dialog asks for host/port/name
python -m unittest discover -s tests -v              # headless tests (start no GUI)
python scripts/e2e_gui_check.py                      # GUI end-to-end check: 2 real windows; rerun after client/protocol changes
```

Copy `.env.example` to `.env` and set `GEMINI_API_KEY` on the server machine only. Never commit `.env`.
The Windows firewall on the server machine must allow inbound TCP on the chosen port, because the demo needs ≥2 clients on different physical computers.

## Rules that must always hold (graders will test these)

- **Unique names (R3):** the server rejects a `HELLO` whose name is already connected and sends an `ERROR`; the client re-prompts.
- **Privacy is enforced on the server, not the client (R7, R11):** a DM is sent only to sender and recipient; a group message only to current members, and a non-member's group message is rejected. History requests are checked the same way.
- **Groups (R8–R10):** a new group's only member is its creator. Nobody is ever added by someone else. A client joins only by sending `GROUP_JOIN` themselves.
- **Every room is its own window or tab (R5, R6)** with a message display plus an input box. DM room id = `dm:` + the two names sorted and joined with `|`; group room id = `group:<name>`.
- The user list includes the client's own name, marked "(you)" (R4). The user and group lists update live on every join, leave, create, or member change.

## Workflow (team of 4 sharing one GitHub repo)

- **Never commit directly to `main`.** The initial scaffold commit is the only exception.
- For every piece of work:
  1. `git checkout main && git pull`
  2. `git checkout -b <type>/<short-name>`, where type is one of `feat`, `fix`, `docs`, `test`, `chore` (e.g. `feat/r7-private-message`)
  3. Make small, focused commits with clear messages. Reference requirement IDs when relevant (`R7: route DMs only to sender and recipient`)
  4. Run the tests before pushing
  5. `git push -u origin <branch>` then `gh pr create --fill` (or a written title/body). Use the PR template checklist.
  6. Do **not** merge your own PR unless the user says so; a teammate reviews it.
- One requirement (or one coherent feature) per PR, to keep reviews short.
- Don't force-push to `main`. Don't rewrite history on branches other people have pulled.
- Before starting work, check `gh pr list` so you don't duplicate a teammate's open PR.

## Slide support (part of the grade)

The Implementation Explanation slides need, **for every requirement**, a code snapshot and the
generative-AI prompt used (if any). After implementing a requirement, update `docs/SLIDE_NOTES.md`:

- requirement ID, with a 1–2 sentence explanation of how it works
- file + function names (and line ranges) that make the best code snapshot
- the prompt the user gave Claude Code for it, quoted

R1 also needs an architecture diagram and an explanation of the protocols used: TCP, IP, the custom
application-layer protocol, JSON, and HTTPS/TLS for the Gemini call. For each protocol, give what it is, why it was chosen, and how it is used here.

## Code style

- Clear names, short functions, a docstring on each handler, comments that explain *networking* reasons
  (e.g. why `recv` is looped until N bytes arrive).
- Handle disconnects cleanly: catch `ConnectionResetError` / empty `recv`, remove the client, broadcast the updated user list.
- No hard-coded IPs; the host and port come from CLI args or the login dialog.
