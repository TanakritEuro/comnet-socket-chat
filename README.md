# Socket Chat: Computer Network Term Project

A simple chat application built on raw TCP sockets (Python stdlib) with a Tkinter GUI.
Private chats, group chats, AI chat summarization, file sharing, chat history, and typing indicators.

- Requirements: [`docs/SPEC.md`](docs/SPEC.md)
- Architecture + protocols (R1): [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Wire protocol: [`docs/PROTOCOL.md`](docs/PROTOCOL.md)
- AI features (UI + who builds what): [`docs/AI_FEATURES.md`](docs/AI_FEATURES.md)
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

## AI summaries (Gemini)

Each chat tab has a **Summarize** button. The **server** asks Google Gemini to summarize that room and shows the result only to
you. Without a key (or if Gemini is unreachable) you get an **offline summary** instead: message count, who spoke, and the last few lines.

Set up on the **server machine only**:

1. Get a free API key at https://aistudio.google.com/apikey.
2. `cp .env.example .env` (PowerShell: `Copy-Item .env.example .env`) and paste the key after `GEMINI_API_KEY=`.
   `.env` is git-ignored. Never commit it, and never put the key on a client PC.
3. Start the server. It logs `AI summaries: Gemini models gemini-3.5-flash-lite -> gemini-3.1-flash-lite -> gemini-3.8-flash -> gemini-3-flash-preview -> gemini-2.5-flash-lite -> gemini-2.5-flash`
   (or `offline summaries only` when no key is set). The key itself is never printed.
   Without `GEMINI_MODEL` / `GEMINI_FALLBACK_MODELS` in `.env`, the server uses the team chain below.

**Model chain and free-tier limits.** Every AI request (Summarize, an AI Assistant question, and later Translate and Analyze
mood) tries these models in order. HTTP 429 (rate limit), 404 or 503 moves to the next one; if all fail you get the offline
answer. Limits are **per Google Cloud project** (not per key or per teammate), and daily quotas reset at **midnight Pacific
time** (3 pm in Thailand). Check your own numbers on the [AI Studio rate-limit page](https://aistudio.google.com/rate-limit).

| Order | Model ID | Free-tier requests/min | Notes |
|---|---|---|---|
| 1 | `gemini-3.5-flash-lite` | 15 | fast; good enough for summary, translate, mood and chatbot |
| 2 | `gemini-3.1-flash-lite` | 15 | about 1 s per answer in our tests |
| 3 | `gemini-3.8-flash` | 5 | stronger, slower |
| 4 | `gemini-3-flash-preview` | 5 | preview; has been overloaded (503) at times |
| 5 | `gemini-2.5-flash-lite` | 10 | only for keys that used 2.5 before; others get a quick 404 and skip it |
| 6 | `gemini-2.5-flash` | 5 | same as above |

Not in the chain: `gemma-4-31b-it` / `gemma-4-26b-a4b-it` (30 requests/min, but `gemma-4-31b-it` didn't answer within 60 s in our
test), `gemini-3.5-flash` (not in the free tier), and `gemini-3.6-flash` (could be added later).

**How many AI clicks per day?** Each click is one request to the first model that still has quota (rate-limited 429 answers are
not counted as successful requests). So a day's total is roughly the **sum of the daily limits (RPD) of the six models**: for example
6 × 20 = about 120 clicks if each allowed 20 a day. Bursts are limited by requests/min: about 15 a minute on the first model, about
55 a minute across the whole chain. No AI feature calls the API by itself: only a click (or a sent AI Assistant question) does.
The tests and `scripts/e2e_gui_check.py` never call the real API.

## AI Assistant (chatbot)

Double-click **AI Assistant** in the sidebar to open a private chat with the bot. Ask anything about the app or networking, click a
suggestion chip, or use **Explain my last chat** to let the bot read the chat tab you had open before (the server first checks
that you may read that room). It remembers your last 10 questions, **Clear conversation** makes it forget, and only you see the
conversation. It uses the same `.env` key and model list as the summaries. Without a key it answers with a notice saying so.

## How to demo on 2+ computers

R1 requires **at least two clients on different physical computers**. The server can run on a third PC or on one of them,
or on a cloud VM so the laptops can be on any network (see [Running the server on Google Cloud](#running-the-server-on-google-cloud)).
Architecture and protocol explanation: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

**0. Same network.** Put every PC on the same Wi-Fi or LAN. Some campus or public Wi-Fi networks use *client isolation*,
which blocks connections between laptops. If step 4 fails with no firewall problem, switch everyone to a **phone hotspot**.

**1. Find the server's IP address** (on the server PC):

```powershell
ipconfig
```

Use the `IPv4 Address` under your **Wi-Fi** (or Ethernet) adapter, e.g. `192.168.1.20`. Ignore `vEthernet (WSL)`,
`VirtualBox`, `VMware` and `169.254.x.x` addresses. `server.py` also prints all of this PC's addresses when it starts.

**2. Allow the port through Windows Firewall** (on the server PC, in PowerShell **as Administrator**):

```powershell
Get-NetConnectionProfile    # NetworkCategory = Private or Public?
New-NetFirewallRule -DisplayName "Socket Chat 5050" -Direction Inbound -Protocol TCP -LocalPort 5050 -Action Allow -Profile Private,Public
```

- When Windows asks "Allow Python to communicate on these networks?", click **Allow**.
- If someone once clicked **Cancel** on that prompt, Windows created a **block** rule for `python.exe`, and block rules beat
  allow rules. Remove it under *Windows Defender Firewall → Allow an app through firewall* (or *Advanced settings → Inbound
  Rules*, look for `python` with a red icon).
- After the demo, remove the rule: `Remove-NetFirewallRule -DisplayName "Socket Chat 5050"`.

**3. Start the server** (on the server PC):

```powershell
python server.py --host 0.0.0.0 --port 5050
```

**4. Check that a client PC can reach it** (on each client PC):

```powershell
Test-NetConnection 192.168.1.20 -Port 5050    # use the server's IP from step 1
```

`TcpTestSucceeded : True` means the TCP handshake worked. `False` means a firewall, the wrong IP, or client isolation (see step 0).

**5. Start the clients** (on each client PC):

```powershell
python client.py --host 192.168.1.20 --name alice     # or just: python client.py, and type them in
```

**Suggested demo order** (one requirement per step):

| Step | Shows |
|---|---|
| Log in alice (PC 1) and bob (PC 2); point at the user lists | R1, R2, R4 (own name marked "(you)") |
| Try to log in a second "alice" | R3 (error, re-prompt) |
| alice double-clicks bob and sends a DM; carol (3rd client) sees nothing | R5, R6, R7 |
| bob clicks **Create…** → `netlab`; everyone sees it with only bob in it | R8, R9 |
| alice selects `netlab` → **Join**; member lists update everywhere | R9, R10 |
| alice and bob chat in `#netlab`; carol (not a member) sees nothing | R11 |
| alice presses **Summarize** in `#netlab`; only alice sees the AI summary | Special 1 |
| Optional: Wireshark on the server PC with filter `tcp.port == 5050` | TCP handshake, 4-byte length + JSON on the wire |

**Troubleshooting**

| Symptom | Likely cause |
|---|---|
| "timed out" on Connect | Firewall on the server PC (step 2), wrong IP, or Wi-Fi client isolation (step 0) |
| "actively refused" / "connection refused" | Server not running, or a different port |
| Works on the server PC itself (`localhost`) but not from another PC | Firewall, or you used a virtual adapter's IP |
| `ModuleNotFoundError: No module named 'tkinter'` | Python was installed without Tcl/Tk. Reinstall from python.org with "tcl/tk and IDLE" ticked |
| AI features fail, server log says `Gemini ... failed: URLError` (macOS) | Python from python.org has no root certificates yet. Run `open "/Applications/Python 3.12/Install Certificates.command"` (use your version), then restart the server |
| Server log says `Gemini <model> answered HTTP 404` | Google retired that model for new keys. List the models your key can use (`GET https://generativelanguage.googleapis.com/v1beta/models`, key in the `x-goog-api-key` header) and set `GEMINI_MODEL` / `GEMINI_FALLBACK_MODELS` in `.env` |
| `HTTP 503` or `TimeoutError` in the log | The model is overloaded (preview models often are). Try a non-preview model such as a `-flash-lite` one |
| The assistant says "couldn't answer right now" | The server could not get an answer from Gemini. The reason is in the server log (see the rows above) |

### Running the server on Google Cloud

Instead of one of the laptops, the server can run on a free **Google Cloud e2-micro VM** (us-west1). Then every client
connects to the VM's external IP on port 5050 from **any** network (home Wi-Fi, a phone hotspot), with no Windows Firewall
or LAN setup on the laptops. Clients only make outgoing connections, so NAT and client isolation don't matter.

- Full step-by-step guide (one person, about 30–45 min): [`deploy/README.md`](deploy/README.md)
- The server runs as a systemd service: [`deploy/chat-server.service`](deploy/chat-server.service)
- Teammates only need: `python client.py`, Host = the VM's external IP (shared in the group chat, never committed), Port = `5050`
- Diagram: the cloud variant in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#cloud-variant-google-cloud-vm)

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
name, a duplicate group, and **Summarize** (forced offline, so it never uses API quota). It checks what each window shows, and that `carol` (no GUI, not a member) gets no chat message at all.
Message boxes are recorded instead of shown, so it never waits for a click. **Rerun it after every PR that touches
`client.py` or the protocol**; it exits non-zero if any check fails. Don't move the mouse into the windows while it runs.

## Members

| Student ID | Name | GitHub |
|---|---|---|
| | | |
| | | |
| | | |
| | | |
