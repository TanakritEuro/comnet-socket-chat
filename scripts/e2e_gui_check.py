"""End-to-end GUI check: a real server, two real client windows, and a raw-socket third client.

Run from the repo root:

    python scripts/e2e_gui_check.py
    python scripts/e2e_gui_check.py --screenshot e2e.png     # also save a picture of both windows (Windows only)

It opens real Tk windows on your screen for a few seconds and clicks through them in code,
so it lives outside tests/ to keep `python -m unittest discover -s tests` headless.
Rerun it after every feature PR. Exit code 0 means every check passed.
"""

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "import client" works from any folder

import tkinter as tk

import client
from protocol import HELLO, MSG, recv_msg, send_msg
from server import ChatServer

TIMEOUT = 3.0           # seconds to wait for the GUI to show something
popups = []             # (kind, title, text) of every message box the client tried to open
results = []            # (label, passed)


def record_popups():
    """Replace message boxes with recorders, so the script never waits for a human click."""
    for kind in ("showerror", "showwarning", "showinfo"):
        setattr(client.messagebox, kind,
                lambda title, text, kind=kind, **kwargs: popups.append((kind, title, text)))


def wait_until(root, condition, timeout=TIMEOUT):
    """Keep the Tk event loop running until condition() is true. Returns False on timeout."""
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if condition():
            return True
        time.sleep(0.01)
    return False


def check(label, passed):
    results.append((label, bool(passed)))
    print(("PASS  " if passed else "FAIL  ") + label)


def log_in(window, port, name, apps):
    """Fill the real login screen and press Connect. On success the ChatApp lands in apps[name]."""
    def on_success(sock, accepted_name):
        frame.destroy()
        apps[accepted_name] = client.ChatApp(window, sock, accepted_name)

    frame = client.LoginFrame(window, "127.0.0.1", port, name, on_success=on_success)
    frame.pack()
    frame.connect()
    return frame


def raw_messages(sock, quiet_time=0.3):
    """Everything a raw socket receives until it has been quiet for quiet_time seconds."""
    sock.settimeout(quiet_time)
    received = []
    try:
        while (msg := recv_msg(sock)) is not None:
            received.append(msg)
    except socket.timeout:
        pass
    return received


def text_of(tab):
    return tab.window.get("1.0", "end")


def screenshot(windows, path):
    """Save a PNG covering the given windows (Windows only: PowerShell + .NET do the capture)."""
    left = min(w.winfo_rootx() for w in windows) - 10
    top = min(w.winfo_rooty() for w in windows) - 40          # include the title bars
    right = max(w.winfo_rootx() + w.winfo_width() for w in windows) + 10
    bottom = max(w.winfo_rooty() + w.winfo_height() for w in windows) + 10
    script = f"""
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class Dpi {{ [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }}'
[Dpi]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap {right - left}, {bottom - top}
[System.Drawing.Graphics]::FromImage($bmp).CopyFromScreen({left}, {top}, 0, 0, $bmp.Size)
$bmp.Save('{Path(path).resolve()}')
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False)
    print(f"screenshot saved to {path}")


def main():
    parser = argparse.ArgumentParser(description="Click through two real client windows against a real server.")
    parser.add_argument("--screenshot", metavar="PNG", help="save a screenshot of both windows (Windows only)")
    args = parser.parse_args()

    if os.name == "nt":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)    # same as client.main(), so sizes match a real run
    record_popups()

    server = ChatServer("127.0.0.1", 0)                     # port 0: the OS picks a free port
    port = server.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    root = tk.Tk()                                          # alice's window
    bob_window = tk.Toplevel(root)
    apps = {}
    log_in(root, port, "alice", apps)
    log_in(bob_window, port, "bob", apps)
    carol = socket.create_connection(("127.0.0.1", port), timeout=2)   # a client with no GUI
    send_msg(carol, {"type": HELLO, "name": "carol"})
    alice, bob = apps["alice"], apps["bob"]

    # R4: everyone online, own name first and marked (you)
    check("R4  alice's user list", wait_until(root, lambda: alice.user_list.get(0, "end") == ("alice (you)", "bob", "carol")))
    check("R4  bob's user list", wait_until(root, lambda: bob.user_list.get(0, "end") == ("bob (you)", "alice", "carol")))

    # R3: a second "alice" is refused and the login screen stays up for another try
    extra_window = tk.Toplevel(root)
    login = log_in(extra_window, port, "alice", apps)
    check("R3  duplicate name shows 'already taken'", any("already taken" in p[2] for p in popups))
    check("R3  login screen stays open to retry", login.winfo_exists())
    extra_window.destroy()

    # R5-R7: double-click bob, type, Send
    alice.user_list.selection_clear(0, "end")
    alice.user_list.selection_set(1)
    alice.on_user_double_click(None)
    dm = alice.rooms["dm:alice|bob"]
    dm.entry.insert(0, "hi bob, private")
    dm.send()
    check("R5  bob gets his own @alice tab", wait_until(root, lambda: "dm:alice|bob" in bob.rooms))
    check("R7  bob's tab shows the DM", wait_until(root, lambda: "alice: hi bob, private" in text_of(bob.rooms["dm:alice|bob"])))
    check("R6  alice's window shows her own message", "alice: hi bob, private" in text_of(dm))
    check("R6  chat box is cleared after Send", dm.entry.get() == "")
    check("R6  chat window is read-only", str(dm.window.cget("state")) == "disabled")

    # R8: bob creates a group (a name with a space, to exercise the tree ids)
    client.simpledialog.askstring = lambda *args, **kwargs: "net lab"
    bob.create_group()
    check("R8  bob's #net lab tab opens", wait_until(root, lambda: "group:net lab" in bob.rooms))
    check("R9  alice sees the group with 1 member",
          wait_until(root, lambda: alice.group_tree.exists("group:net lab")
                     and alice.group_tree.item("group:net lab", "text") == "net lab  (1 members)"))
    members = [alice.group_tree.item(i, "text") for i in alice.group_tree.get_children("group:net lab")]
    check("R8  creator is the only member", members == ["bob (creator)"])
    check("R10 alice was not added automatically", "group:net lab" not in alice.rooms)

    # R10: alice selects a member row of the group and presses Join
    alice.group_tree.selection_set(alice.group_tree.get_children("group:net lab")[0])
    alice.join_group()
    check("R10 alice's tab opens after Join", wait_until(root, lambda: "group:net lab" in alice.rooms))
    check("R9  bob's tree lists both members",
          wait_until(root, lambda: [bob.group_tree.item(i, "text") for i in bob.group_tree.get_children("group:net lab")]
                     == ["bob (you) (creator)", "alice"]))

    # R11: group message reaches members; carol (no GUI, not a member) gets no MSG at all
    group_tab = alice.rooms["group:net lab"]
    group_tab.entry.insert(0, "hello group")
    group_tab.send()
    check("R11 bob sees the group message", wait_until(root, lambda: "alice: hello group" in text_of(bob.rooms["group:net lab"])))
    check("R7/R11 carol received no chat message", not any(m["type"] == MSG for m in raw_messages(carol)))

    # Server errors reach the user
    bob.create_group()                                      # "net lab" again
    check("ERR duplicate group shows GROUP_EXISTS", wait_until(root, lambda: any(p[1] == "GROUP_EXISTS" for p in popups)))

    if args.screenshot:
        alice.notebook.select(group_tab)
        bob.notebook.select(bob.rooms["group:net lab"])
        root.geometry("+10+10")
        wait_until(root, lambda: False, timeout=0.3)
        bob_window.geometry(f"+{root.winfo_rootx() + root.winfo_width() + 20}+10")
        wait_until(root, lambda: False, timeout=0.5)
        screenshot([root, bob_window], args.screenshot)

    # Leave closes the tab
    alice.group_tree.selection_set("group:net lab")
    alice.leave_group()
    check("R10 Leave closes alice's group tab", wait_until(root, lambda: "group:net lab" not in alice.rooms))

    # Closing a window updates everyone's user list
    bob.quit()
    check("R4  list updates when bob closes his window",
          wait_until(root, lambda: alice.user_list.get(0, "end") == ("alice (you)", "carol")))

    carol.close()
    root.destroy()
    server.stop()

    failed = [label for label, passed in results if not passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
