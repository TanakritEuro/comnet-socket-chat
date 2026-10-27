# Prompts for Claude Code

Open a terminal in this folder (`D:\CU Engineering\year3\Computer Network\project\comnet-socket-chat`), run `claude`, and paste the prompt below.

---

## 1. First session: repo setup + foundation (paste this)

```
Read CLAUDE.md, docs/SPEC.md and docs/PROTOCOL.md first. They describe our socket-programming
chat app term project and the decisions we've already made. Then do the following, one stage at a time.

STAGE 0: Repo setup (the only time committing straight to main is OK)
1. Check `git --version`, `gh auth status` and `python --version` (try `py` if `python` is missing).
   Stop and tell me if any of them fail.
2. `git init -b main`, check that .gitignore covers .env, then commit everything currently in the folder
   (the scaffold docs only) as "chore: initial project scaffold".
3. Create a PUBLIC GitHub repo with gh (suggested name: comnet-socket-chat) from this folder, and push main.
   Before pushing, double-check that no .env file or API key is staged, since the repo is public.
   Don't invite collaborators; I'll add my teammates myself.
4. Set the repo to squash-merge only and to delete branches after merge
   (`gh repo edit --enable-squash-merge --enable-merge-commit=false --enable-rebase-merge=false --delete-branch-on-merge`).

From here on, follow the Workflow section of CLAUDE.md: every change goes on its own branch and
becomes a PR. Never commit to main.

STAGE 1: Part 1 + server core  (branch: feat/protocol-and-server)
- protocol.py: framing (send_msg/recv_msg with a looped exact recv), message type constants.
- server.py: threaded TCP server implementing HELLO/WELCOME/ERROR (R3), USERS broadcast (R4),
  DM (R7), GROUP_CREATE / GROUP_JOIN / GROUP_LEAVE / GROUP_MSG / GROUPS broadcast (R8–R11),
  with every privacy rule enforced server-side. CLI args --host/--port.
- tests/: unittest tests that start the server on a free port and connect raw-socket clients, covering
  each of R3, R4, R7–R11, including the negative cases (duplicate name, a third client must NOT
  receive a DM, a non-member's group message is rejected and not delivered).
- Update docs/SLIDE_NOTES.md for R2, R3, R4, R7–R11 (server side), and quote this prompt as the AI prompt.
- Open the PR and give me the link. If the PR isn't merged when you're ready for the next stage, ask me
  whether to wait or to stack the next branch on top of it.

STAGE 2: GUI client  (branch: feat/tkinter-client)
- client.py: login dialog (host, port, name, re-prompt on NAME_TAKEN), main window with an online
  users list ("(you)" marker) and a groups list showing members with Create/Join buttons, plus a
  ttk.Notebook with one tab per room (R5), each with a read-only chat window + entry box + Send (R6).
  Double-click a user to open a DM. Network receive thread → queue.Queue → root.after() polling.
- Update SLIDE_NOTES.md for R5, R6 and the client side of R7–R11. Open the PR.

STAGE 3: R1 docs  (branch: docs/architecture)
- Architecture diagram (Mermaid in docs/ARCHITECTURE.md, also rendered to PNG if mmdc/npx is available)
  showing the server and ≥2 client PCs on the LAN, plus the HTTPS call to Gemini.
- Explanation of each protocol (TCP, IP, our app protocol, JSON, HTTPS/TLS): what, why, and how it is used here.
- README "How to demo on 2+ computers" section (find the server IP with ipconfig, Windows firewall inbound rule for the port).
- Open the PR.

After each stage, summarize what changed, then stop and wait for me before starting the next stage.
Keep the code simple and commented: all 4 of us have to explain it live.
```

---

## 2. Later sessions: special features (one PR each)

Paste one at a time, in a fresh session or after `/clear`. Any teammate can use these on their own machine
after `git clone` + `git pull`.

```
Read CLAUDE.md and docs/PROTOCOL.md. Make sure main is up to date and create the branch feat/ai-summary.
Implement special point 1, AI chat summarization: a "Summarize" button in each room tab sends
SUMMARIZE; the server (ai.py) sends the room's last ~50 messages the requester is allowed to see to
the Gemini REST API using urllib.request (key from .env: GEMINI_API_KEY, model from GEMINI_MODEL).
Look up the current endpoint and model name in Google's docs. If there's no key or the call fails,
use an offline fallback summary so the demo never breaks. Reply with SUMMARY to the requester only.
Add tests (mock the HTTP call), update PROTOCOL.md and SLIDE_NOTES.md (with the model name and a docs
link as the reference), then open a PR.
```

```
… branch feat/file-transfer. Special point 2: send files/images in a room (FILE message, base64,
5 MiB limit, same privacy routing as text). The receiver saves to downloads/<myname>/ and PNG/GIF
are shown inline in the chat window. Tests + PROTOCOL.md + SLIDE_NOTES.md, then open a PR.
```

```
… branch feat/history-unread. Special point 3: the server keeps per-room history (group history only
from the requester's join time onward). Opening a room loads it via HISTORY. Unread counts show
on tabs and in the user/group lists, and are cleared when the tab is viewed. Tests + docs, then open a PR.
```

```
… branch feat/typing-presence. Special point 4: "X is typing…" under the chat window (debounced,
auto-clears after 3 s), online/offline markers in group member lists, join/leave NOTICEs.
Tests + docs, then open a PR.
```

```
… branch feat/cloud-deploy. Optional special point: write deploy/README.md with step-by-step instructions
to run server.py on a free cloud VM (e.g. Google Cloud e2-micro or Oracle Cloud Always Free):
open the TCP port in the cloud firewall, a systemd unit file in deploy/, and how clients connect
using the public IP. Then open a PR.
```

---

## 3. Reviewing a teammate's PR

```
Review PR #<n> with `gh pr view <n>` and `gh pr diff <n>`. Check it against CLAUDE.md's
"Rules that must always hold", run the tests on that branch (`gh pr checkout <n>`), and tell me
what's wrong before posting anything. Only post a review comment with `gh pr review` if I say so.
```

## 4. Before the presentation

```
Go through docs/SPEC.md requirement by requirement and check that each one is implemented,
tested, and documented in docs/SLIDE_NOTES.md. List gaps. Then write a 2-computer demo script
(who clicks what, in what order) that shows R3–R11 and all special points in under 8 minutes.
```
