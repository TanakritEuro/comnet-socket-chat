# Deploying the Chat Server on Google Cloud (e2-micro)

How to run `server.py` on a free Google Cloud VM, so every client can connect over the internet. Only the **server** goes to the cloud; everyone still runs `client.py` on their own laptop.

- **Setting up the server takes:** about 30–45 minutes, done once by one person
- **Cost:** free. It uses the $300 / 90-day free trial credit, and e2-micro is in the "Always Free" tier
- **Presentation:** Mon 9 Nov 2026. **Delete the VM afterwards** (Part H)
- Architecture of this setup: the cloud diagram in [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md#cloud-variant-google-cloud-vm)

In the commands below, `<EXTERNAL_IP>` means the VM's external IP from step B3. It's never written in the repo: share it only in the group chat.

---

## Just want to connect? (for teammates)

You don't need to do any of the setup below. Once the server is running:

1. Pull the latest code: `git checkout main && git pull`
2. Run the client: `python client.py` (or `python client.py --host <EXTERNAL_IP> --name yourname` to pre-fill the dialog)
3. In the login dialog, enter:
   - **Host:** the server's external IP, shared in our group chat
   - **Port:** `5050`
   - **Name:** something unique. We all share one server, so two people can't use the same name.

That's it. The AI features use the key stored on the server, so you don't need your own `.env` just to connect.

If you can't connect, first check that your network allows it. In PowerShell, run `Test-NetConnection <EXTERNAL_IP> -Port 5050` and look for `TcpTestSucceeded : True`. If it fails on campus Wi-Fi but works on a phone hotspot, the campus network is blocking the port. Tell the group.

---

## Part A: Account and project (about 10 min)

### A1. Start the free trial
1. Open **console.cloud.google.com** and sign in with a personal Gmail.
2. Click **Start free** or **Activate free trial**, the banner at the top.
3. Fill in the form:
   - **Country:** Thailand
   - **Account type:** **Individual**
   - **Payment:** add a card. It's only used to check your identity: a trial account is **not charged unless you click "Upgrade" yourself**. Your bank may show a small temporary hold that's released afterwards.
4. You land on the console with **$300 credit for 90 days**.

### A2. Create a separate project

> ⚠️ Do **not** use the AI Studio project that holds the Gemini API key. If billing gets linked to that project, the Gemini key may move from the free tier to paid. Always use a separate project for the VM.

1. Click the **project dropdown** in the top bar, to the right of "Google Cloud", then **New project**.
2. Set **Project name** to `comnet-chat-server`, leave Location as is, and click **Create**.
3. Open the dropdown again and **select `comnet-chat-server`**. Check that the top bar shows this name from now on.

### A3. Set a $1 budget alert
1. Search for **"Budgets & alerts"** in the top search bar and open it.
2. Click **Create budget** and name it `chat-alert`.
3. In **Scope**, keep the defaults and click **Next**.
4. In **Amount**, choose Budget type **Specified amount**, set Target amount **1** (USD) and click **Next**.
5. In **Actions**, keep the threshold rules and make sure **"Email alerts to billing admins"** is ticked. Click **Finish**.

---

## Part B: Create the VM (about 10 min)

### B1. Enable Compute Engine
Search for **"Compute Engine"** and open **VM instances**. If it asks you to **Enable** the Compute Engine API, click it and wait 1–2 minutes.

### B2. Create the instance
Click **Create instance**, then work through each section on the left.

**Machine configuration**

| Field | Value |
|---|---|
| Name | `chat-server` |
| Region | **us-west1 (Oregon)**, the closest of the free regions to Thailand |
| Zone | Any, e.g. `us-west1-b` |
| Machine family | **General purpose** → series **E2** |
| Machine type | Preset → **e2-micro (2 vCPU, 1 GB memory)** |

The estimate on the right may show a monthly price, or a note like "744 hours of e2-micro free per month". The free allowance is applied on the bill, so the price can be ignored.

**OS and storage:** click **Change**:

| Field | Value |
|---|---|
| Operating system | **Ubuntu** |
| Version | **Ubuntu 24.04 LTS** (x86/64, amd64). It comes with Python 3.12, which meets our 3.10+ requirement |
| Boot disk type | **Standard persistent disk**. ⚠️ The default is "Balanced"; change it, because Balanced isn't free |
| Size (GB) | **10** |

Click **Select**.

**Data protection:** choose **No backups**. Backups and snapshots cost extra.

**Networking**
- **Firewall:** leave "Allow HTTP traffic" and "Allow HTTPS traffic" **unticked**.
- **Network tags:** type `chat-server` and press Enter.
- Leave everything else as is. The default gives the VM an ephemeral external IP.

**Observability:** if there's an option to install the **Ops Agent**, untick it. It uses RAM you'd rather keep on a 1 GB machine.

Leave **Security** and **Advanced** as they are. Click **Create**.

### B3. Note the external IP
After about a minute, `chat-server` shows a green check. Copy its **External IP**. That's the address everyone will connect to (`<EXTERNAL_IP>` below).

---

## Part C: Open port 5050 (about 3 min)

1. Search for **"Firewall"** and open **VPC network → Firewall**.
2. Click **Create firewall rule**:

| Field | Value |
|---|---|
| Name | `allow-chat-5050` |
| Network | `default` |
| Priority | `1000` |
| Direction of traffic | **Ingress** |
| Action on match | **Allow** |
| Targets | **Specified target tags** |
| Target tags | `chat-server` (it must match the VM's network tag exactly) |
| Source filter | IPv4 ranges |
| Source IPv4 ranges | `0.0.0.0/0` (from anywhere on the internet) |
| Protocols and ports | **Specified protocols and ports** → tick **TCP**, enter `5050` |

3. Click **Create**.

Only this one inbound port is opened. The laptops don't need any open port: each one *starts* its TCP connection to the VM, so home routers and campus NAT let it through.

---

## Part D: Install the server (about 10 min)

### D1. Open SSH in the browser
On **Compute Engine → VM instances**, click **SSH** in the `chat-server` row. A terminal window opens; click **Authorize** if it asks. To paste into it, use **Ctrl+V** or right-click → Paste.

### D2. Install the tools and the code
The server and the tests use only the Python standard library, so git and Python are all the VM needs. Tkinter isn't needed, because nobody runs the GUI on the VM.
```bash
sudo apt update && sudo apt install -y git python3
git clone https://github.com/TanakritEuro/comnet-socket-chat.git
cd comnet-socket-chat
```

### D3. Create `.env` with the Gemini API key
Use the **same key** as in your laptop's `comnet-socket-chat/.env`. The free-tier limits apply to the AI Studio project, so a second key wouldn't add any quota.

```bash
nano .env
```
Type one line, with no spaces or quotes:
```
GEMINI_API_KEY=your-key-here
```
That's all `.env` needs: the server uses the team model chain from `ai.py` unless you add `GEMINI_MODEL` / `GEMINI_FALLBACK_MODELS` lines (see `.env.example`).

Save with **Ctrl+O** then **Enter**, and exit with **Ctrl+X**. Then make the file readable only by you:
```bash
chmod 600 .env
```

You can upload your laptop's `.env` instead: use the SSH window's **Upload file** button (a gear or upload icon at the top right), then run `mv ~/.env ~/comnet-socket-chat/.env`.

> 🔒 Never paste the key into chats, screenshots, commits or this guide.

### D4. Run the tests
```bash
python3 -m unittest discover -s tests
```
It should end with **OK**. The tests never call the real Gemini API, so they don't use any quota.

### D5. Start the server once to check it
```bash
python3 server.py --host 0.0.0.0 --port 5050
```
You should see three startup lines: `listening on 0.0.0.0:5050`, **`AI summaries:`** with the model chain (or `offline summaries only` if `.env` is missing), and `this machine's IPv4 addresses`. The address in that last line is the VM's **internal** one (a `10.x` address); ignore it and use the external IP from B3. **Keep this window open** for Part E.

---

## Part E: Test from a laptop (about 5 min)

### E1. Check the port
In PowerShell on your laptop, with the real external IP:
```powershell
Test-NetConnection <EXTERNAL_IP> -Port 5050
```
You want **`TcpTestSucceeded : True`**.

### E2. Chat through the cloud
In the repo folder:
```powershell
python client.py --host <EXTERNAL_IP> --name alice
```
Check **Port** is `5050` and connect. Then connect `bob`: on a second laptop if possible, ideally on a different network such as a phone hotspot. Send a DM and try **Summarize** once. The SSH window should show the logins.

---

## Part F: Keep it running after you close SSH (about 3 min)

### F1. Stop the test server
In the SSH window, press **Ctrl+C**.

### F2. Install it as a systemd service
The unit file is in the repo: [`deploy/chat-server.service`](chat-server.service). It runs `python3 server.py --host 0.0.0.0 --port 5050` from the clone, restarts it if it crashes, and starts it when the VM boots. In the repo folder on the VM, paste this whole block (`sed` fills in your Linux user name):
```bash
sed "s/YOUR_LINUX_USER/$USER/g" deploy/chat-server.service | sudo tee /etc/systemd/system/chat-server.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now chat-server
sudo systemctl status chat-server
```
It should show **`active (running)`** in green. Press **q** to leave the status view.

### F3. Confirm it runs on its own
Close the SSH window. On your laptop, connect with `python client.py` again; it should still work. The server now restarts by itself if it crashes or the VM reboots.

---

## Part G: Everyday use

| Task | How |
|---|---|
| Connect | `python client.py` with Host = the external IP, Port = `5050` |
| Update after a PR merges | Click SSH, then `cd ~/comnet-socket-chat && git pull && python3 -m unittest discover -s tests && sudo systemctl restart chat-server` |
| Update after `deploy/chat-server.service` changed | Repeat the commands from F2, then `sudo systemctl restart chat-server` |
| Watch the live log | `journalctl -u chat-server -f` (Ctrl+C to quit) |
| Restart the server | `sudo systemctl restart chat-server` |
| Stop the server | `sudo systemctl stop chat-server` |

**Things to know:**
- **Updates kick everyone off.** Restarting the server logs all clients out, and history and groups live in memory, so they reset too. Don't update in the middle of a session or the demo.
- **One shared server:** names must be unique across the whole group.
- **One shared AI quota:** every Summarize and Assistant click from everyone uses the same daily free-tier allowance (it resets at midnight Pacific, 3 pm in Thailand). Don't use it up the day before the demo.
- **Don't stop the VM in the console.** An ephemeral IP can change when the VM restarts. If it does, check the new one on the VM list and tell the group.
- **Keep the IP private:** share it in our group chat only. The port is open to the internet with no password, and chat traffic is plain TCP (see the security note in `docs/PROTOCOL.md`).

---

## Troubleshooting

| Problem | Likely cause and fix |
|---|---|
| `TcpTestSucceeded : False` | Make sure the firewall rule's target tag and the VM's network tag are both exactly `chat-server`. Check the server is running (`sudo systemctl status chat-server`) and was started with `--host 0.0.0.0` |
| `Address already in use` when starting | The service or an old test server is already running. Run `sudo systemctl stop chat-server`, or press Ctrl+C in the other window |
| Summarize or the Assistant says "offline" | `.env` is missing, in the wrong folder or mistyped. Check it's at `~/comnet-socket-chat/.env` (next to `ai.py`), then run `sudo systemctl restart chat-server` |
| Service fails right after F2 (`status` shows `failed`) | Check `journalctl -u chat-server -n 30`. Usually `User=` or `WorkingDirectory=` doesn't match: the clone must be at `/home/<your user>/comnet-socket-chat`. Re-run F2 from inside the clone |
| Works on a hotspot but not on Chula Wi-Fi | The campus network blocks outgoing port 5050. Moving to port 443 would need three changes: `--port 443` in the unit's `ExecStart` plus `AmbientCapabilities=CAP_NET_BIND_SERVICE` under `[Service]` (a normal user may not open a port below 1024), a firewall rule for TCP 443, and Port `443` in every client |
| Suddenly can't connect | Check whether the external IP changed, and whether the VM is still running |
| Tests fail on the VM | Make sure you're on the latest `main` (`git pull`), then run them again |

---

## Part H: After the presentation (Nov 9)

1. **Compute Engine → VM instances:** tick `chat-server` → **Delete**.
2. **IAM & Admin → Settings → Shut down** the `comnet-chat-server` project.
3. The free trial ends by itself. **Don't click "Upgrade"**, and the card won't be charged.
