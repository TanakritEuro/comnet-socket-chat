"""Tkinter chat client.

Run:  python client.py                      (the login screen asks for host, port and name)
      python client.py --host 192.168.1.20 --name alice     (pre-fills the login screen)

Threads: Tkinter is not thread-safe, so only the main (GUI) thread touches widgets.

    receiver thread:  recv_msg() in a loop  --->  queue.Queue  --->  GUI thread: poll_inbox()
                      (blocks on the socket)                          every 50 ms via root.after()

The GUI thread sends directly with send_msg(). A TCP socket is full-duplex, so one
thread sending while another is blocked in recv() on the same socket is safe.
"""

import argparse
import queue
import socket
import threading
import time
import tkinter as tk
from tkinter import font as tkfont, messagebox, scrolledtext, simpledialog, ttk

from protocol import (
    BYE, DM, ERROR, GROUP_CREATE, GROUP_JOIN, GROUP_LEAVE, GROUP_MSG, GROUPS, HELLO,
    MAX_NAME_LEN, MSG, NAME_TAKEN, SUMMARIZE, SUMMARY, USERS, WELCOME,
    ProtocolError, clean_name, dm_partner, dm_room, group_room, recv_msg, send_msg,
)

POLL_MS = 50            # how often the GUI thread checks the inbox queue
CONNECT_TIMEOUT = 5     # seconds to wait for the server while logging in


class LoginFrame(ttk.Frame):
    """Login screen: server host, port and a name. Re-prompts until the server accepts the name (R3)."""

    def __init__(self, root, host, port, name, on_success):
        super().__init__(root, padding=20)
        self.on_success = on_success        # called with (socket, accepted name)
        self.host = tk.StringVar(value=host)
        self.port = tk.StringVar(value=str(port))
        self.name = tk.StringVar(value=name)

        ttk.Label(self, text="Socket Chat", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 12))
        for row, (label, var) in enumerate([("Server host / IP", self.host), ("Port", self.port), ("Your name", self.name)], start=1):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            entry = ttk.Entry(self, textvariable=var, width=28)
            entry.grid(row=row, column=1, pady=3)
            entry.bind("<Return>", lambda event: self.connect())
        self.name_entry = entry             # the last entry created is the name box
        self.status = ttk.Label(self, text="", foreground="gray")
        self.status.grid(row=4, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(self, text="Connect", command=self.connect).grid(row=5, column=0, columnspan=2, pady=(8, 0))
        self.name_entry.focus_set()

    def connect(self):
        """Open a TCP connection, send HELLO, and wait for WELCOME or ERROR."""
        host = self.host.get().strip()
        name = clean_name(self.name.get())
        try:
            port = int(self.port.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            return self.fail("Port must be a number from 1 to 65535.")
        if not host:
            return self.fail("Enter the server's host name or IP address.")
        if name is None:
            return self.fail(f"Name must be 1-{MAX_NAME_LEN} characters and must not contain '|'.")

        self.status.config(text=f"Connecting to {host}:{port}…")
        self.update_idletasks()             # repaint now; the calls below block the GUI briefly
        try:
            # create_connection does the DNS lookup and the TCP 3-way handshake.
            sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError as e:
            return self.fail(f"Could not connect to {host}:{port}\n{e}")
        try:
            send_msg(sock, {"type": HELLO, "name": name})
            reply = recv_msg(sock)          # reads exactly one frame; USERS/GROUPS stay queued in the socket
        except (OSError, ProtocolError) as e:
            sock.close()
            return self.fail(f"Connection failed during login\n{e}")

        if reply is not None and reply["type"] == WELCOME:
            # From now on the receiver thread blocks in recv() for as long as it takes,
            # so remove the login timeout (otherwise an idle chat would "time out").
            sock.settimeout(None)
            self.on_success(sock, reply["name"])
            return
        sock.close()
        if reply is None:
            self.fail("The server closed the connection.")
        elif reply.get("code") == NAME_TAKEN:
            self.fail(f"The name {name!r} is already taken. Please choose another one.")
        else:
            self.fail(reply.get("message", "Login failed."))

    def fail(self, text):
        """Show why login failed and let the user try again."""
        self.status.config(text="")
        messagebox.showerror("Cannot log in", text, parent=self)
        self.name_entry.focus_set()
        self.name_entry.select_range(0, "end")


class RoomTab(ttk.Frame):
    """One chat room (R5): a read-only chat window plus a chat box and Send button (R6),
    and a Summarize button that asks the server for an AI summary (special 1)."""

    def __init__(self, parent, on_send, on_summarize):
        super().__init__(parent, padding=6)
        self.on_send = on_send              # function(text) -> True if the message was sent
        self.on_summarize = on_summarize    # function() -> True if SUMMARIZE was sent

        # Chat box: Enter or the Send button sends. Packed first (at the bottom) so it
        # never gets squeezed out when the window is small.
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x", pady=(6, 0))
        self.entry = ttk.Entry(bar)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda event: self.send())
        ttk.Button(bar, text="Send", command=self.send).pack(side="left", padx=(6, 0))
        self.summarize_button = ttk.Button(bar, text="Summarize", command=self.summarize)
        self.summarize_button.pack(side="left", padx=(6, 0))

        # Chat window. state="disabled" makes it read-only; we unlock it briefly to append.
        self.window = scrolledtext.ScrolledText(self, state="disabled", wrap="word", width=60, height=20,
                                                font=("Segoe UI", 10))
        self.window.pack(fill="both", expand=True)
        self.window.tag_configure("me", foreground="#1a5fb4", font=("Segoe UI", 10, "bold"))
        self.window.tag_configure("other", foreground="#26a269", font=("Segoe UI", 10, "bold"))
        self.window.tag_configure("notice", foreground="gray", font=("Segoe UI", 10, "italic"))
        # AI summaries get a tinted, indented block so nobody mistakes them for a chat message.
        self.window.tag_configure("summary_title", foreground="#613583", background="#f3eefa",
                                  font=("Segoe UI", 10, "bold"), lmargin1=8, lmargin2=8, spacing1=6)
        self.window.tag_configure("summary", background="#f3eefa", lmargin1=8, lmargin2=8)
        self.window.tag_configure("summary_model", foreground="gray", background="#f3eefa",
                                  font=("Segoe UI", 9, "italic"), lmargin1=8, lmargin2=8, spacing3=6)

    def send(self):
        """Hand the typed text to on_send. It appears in the window when the server echoes it back."""
        text = self.entry.get()
        if text.strip() and self.on_send(text):
            self.entry.delete(0, "end")

    def add_message(self, sender, text, ts, mine):
        """Append one chat message, e.g. "[14:05] alice: hi"."""
        stamp = time.strftime("%H:%M", time.localtime(ts))
        self.append(f"[{stamp}] {sender}: ", "me" if mine else "other")
        self.append(text + "\n")

    def summarize(self):
        """Ask for a summary. The button stays disabled until the answer (or an error) arrives,
        so a double-click can't use up the tiny API quota."""
        if self.on_summarize():
            self.summarize_button.state(["disabled"])
            self.add_notice("Asking the server for a summary…")

    def add_summary(self, text, model):
        """Show a SUMMARY as a marked system block, with the model name underneath."""
        self.append("Summary of this room\n", "summary_title")
        self.append(text.strip() + "\n", "summary")
        if model == "offline":
            self.append("Offline summary · no AI was used\n", "summary_model")
        else:
            self.append(f"AI summary · {model}\n", "summary_model")
        self.enable_summarize()

    def enable_summarize(self):
        self.summarize_button.state(["!disabled"])

    def add_notice(self, text):
        """Append a grey status line, e.g. "You joined #netlab"."""
        self.append(text + "\n", "notice")

    def append(self, text, tag=None):
        self.window.configure(state="normal")
        self.window.insert("end", text, tag)
        self.window.configure(state="disabled")
        self.window.see("end")              # auto-scroll to the newest line


class ChatApp:
    """Main window after login: online users, groups with members, and one tab per chat room."""

    def __init__(self, root, sock, name):
        self.root = root
        self.sock = sock
        self.me = name
        self.connected = True
        self.inbox = queue.Queue()          # receiver thread -> GUI thread
        self.rooms = {}                     # room id -> RoomTab
        self.users = []                     # online names, in the order shown in the user list
        self.groups = {}                    # group name -> {"creator": ..., "members": [...]}
        self.handlers = {USERS: self.on_users, GROUPS: self.on_groups, MSG: self.on_msg,
                         SUMMARY: self.on_summary, ERROR: self.on_error}

        self.build_widgets()
        root.title(f"Socket Chat: {name}")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        threading.Thread(target=self.receive_loop, daemon=True).start()
        self.poll_inbox()

    def build_widgets(self):
        """Left: users and groups. Right: a notebook with one tab per room."""
        # Treeview rows don't grow with the font on high-DPI screens, which clips letters like "y".
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        ttk.Style().configure("Treeview", rowheight=line + 4)
        panes = ttk.PanedWindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=6, pady=6)

        side = ttk.Frame(panes, padding=(0, 0, 6, 0))
        ttk.Label(side, text="Online users (double-click to chat)").pack(anchor="w")
        self.user_list = tk.Listbox(side, height=8, exportselection=False)
        self.user_list.pack(fill="x", pady=(2, 10))
        self.user_list.bind("<Double-Button-1>", self.on_user_double_click)

        ttk.Label(side, text="Groups and members").pack(anchor="w")
        self.group_tree = ttk.Treeview(side, show="tree", height=10, selectmode="browse")
        self.group_tree.pack(fill="both", expand=True, pady=(2, 4))
        self.group_tree.bind("<Double-Button-1>", self.on_group_double_click)
        buttons = ttk.Frame(side)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Create…", command=self.create_group).pack(side="left", expand=True, fill="x")
        ttk.Button(buttons, text="Join", command=self.join_group).pack(side="left", expand=True, fill="x", padx=3)
        ttk.Button(buttons, text="Leave", command=self.leave_group).pack(side="left", expand=True, fill="x")
        panes.add(side, weight=0)

        self.notebook = ttk.Notebook(panes)
        welcome = ttk.Frame(self.notebook, padding=20)
        ttk.Label(welcome, justify="left", text=(
            f"Logged in as {self.me}.\n\n"
            "• Double-click a user to open a private chat.\n"
            "• Create a group, or select one and press Join.\n"
            "• Double-click a group you are in to open its chat."
        )).pack(anchor="nw")
        self.notebook.add(welcome, text="Welcome")
        panes.add(self.notebook, weight=1)

    # ------------------------------------------------------------------
    # Networking: receiver thread + queue polling + sending
    # ------------------------------------------------------------------

    def receive_loop(self):
        """Background thread: block on the socket and queue every message for the GUI thread.
        Never touches widgets (Tkinter is not thread-safe)."""
        try:
            while True:
                msg = recv_msg(self.sock)
                if msg is None:
                    break                   # server closed the connection
                self.inbox.put(msg)
        except (OSError, ProtocolError):
            pass                            # connection reset, or we closed the socket ourselves
        self.inbox.put(None)                # None = "connection lost"

    def poll_inbox(self):
        """GUI thread, every POLL_MS: handle everything the receiver thread has queued."""
        while True:
            try:
                msg = self.inbox.get_nowait()
            except queue.Empty:
                break
            if msg is None:
                return self.on_disconnected()   # stop polling
            handler = self.handlers.get(msg["type"])
            if handler is not None:
                handler(msg)
        self.root.after(POLL_MS, self.poll_inbox)

    def send(self, type_, **fields):
        """Send one message to the server. Returns True on success."""
        if not self.connected:
            messagebox.showerror("Not connected", "The connection to the server was lost.", parent=self.root)
            return False
        try:
            send_msg(self.sock, {"type": type_, **fields})
            return True
        except (OSError, ProtocolError) as e:
            messagebox.showerror("Send failed", str(e), parent=self.root)
            return False

    def quit(self):
        """Window closed: say BYE, close the socket, and exit."""
        if self.connected:
            try:
                send_msg(self.sock, {"type": BYE})
            except OSError:
                pass
        self.connected = False
        self.sock.close()                   # also wakes the receiver thread out of recv()
        self.root.destroy()

    # ------------------------------------------------------------------
    # Handlers for messages from the server (run on the GUI thread)
    # ------------------------------------------------------------------

    def on_users(self, msg):
        """R4: redraw the online list. Our own name goes first, marked "(you)"."""
        self.users = [self.me] + [u for u in msg["users"] if u != self.me]
        self.user_list.delete(0, "end")
        for user in self.users:
            self.user_list.insert("end", f"{user} (you)" if user == self.me else user)

    def on_groups(self, msg):
        """R9: redraw every group with its members. Opens a tab for each group we just
        joined or created, and closes the tab of a group we just left."""
        was_member = {g for g, info in self.groups.items() if self.me in info["members"]}
        self.groups = {g["name"]: g for g in msg["groups"]}
        is_member = {g for g, info in self.groups.items() if self.me in info["members"]}

        self.group_tree.delete(*self.group_tree.get_children())
        for group, info in self.groups.items():
            label = f"{group}  ({len(info['members'])} members)" + ("  ✓ joined" if group in is_member else "")
            # The item id is the room id, e.g. "group:netlab", so it can't clash with Tk's own ids.
            parent = self.group_tree.insert("", "end", iid=group_room(group), text=label, open=True)
            for member in info["members"]:
                text = member
                if member == self.me:
                    text += " (you)"
                if member == info["creator"]:
                    text += " (creator)"
                self.group_tree.insert(parent, "end", text=text)

        for group in sorted(is_member - was_member):
            self.open_group(group).add_notice(f"You are a member of #{group}.")
        for group in was_member - is_member:
            self.close_room(group_room(group))

    def on_msg(self, msg):
        """R7/R11: show a chat message in its room's tab, opening the tab if needed.
        The server only sends us messages we are allowed to see."""
        room = msg["room"]
        if room.startswith("dm:"):
            tab = self.open_dm(dm_partner(room, self.me), select=False)
        elif room.startswith("group:"):
            tab = self.open_group(room.removeprefix("group:"), select=False)
        else:
            return
        tab.add_message(msg["from"], msg["text"], msg["ts"], mine=msg["from"] == self.me)

    def on_summary(self, msg):
        """Special 1: the server's summary, sent only to us. Shown in that room's tab."""
        tab = self.rooms.get(msg["room"])
        if tab is not None:                 # the tab may have closed (e.g. we left the group)
            tab.add_summary(msg["text"], msg["model"])

    def on_error(self, msg):
        """The server refused a request (e.g. GROUP_EXISTS, NO_SUCH_USER)."""
        for tab in self.rooms.values():     # if it was a SUMMARIZE that failed, allow a retry
            tab.enable_summarize()
        messagebox.showwarning(msg.get("code", "Error"), msg.get("message", ""), parent=self.root)

    def on_disconnected(self):
        """The receiver thread saw the connection close."""
        if not self.connected:
            return                          # we are quitting ourselves
        self.connected = False
        self.root.title(f"Socket Chat: {self.me} (disconnected)")
        messagebox.showerror("Disconnected", "Lost the connection to the server.", parent=self.root)

    # ------------------------------------------------------------------
    # Rooms (tabs)
    # ------------------------------------------------------------------

    def open_room(self, room, title, on_send, select=True):
        """Return the tab for this room id, creating it first if needed (R5: one tab per room)."""
        if room not in self.rooms:
            self.rooms[room] = RoomTab(self.notebook, on_send, lambda: self.send(SUMMARIZE, room=room))
            self.notebook.add(self.rooms[room], text=title)
        if select:
            self.notebook.select(self.rooms[room])
            self.rooms[room].entry.focus_set()
        return self.rooms[room]

    def open_dm(self, user, select=True):
        """Private chat tab with one user (R7)."""
        return self.open_room(dm_room(self.me, user), f"@{user}",
                              lambda text: self.send(DM, to=user, text=text), select)

    def open_group(self, group, select=True):
        """Group chat tab (R11)."""
        return self.open_room(group_room(group), f"#{group}",
                              lambda text: self.send(GROUP_MSG, group=group, text=text), select)

    def close_room(self, room):
        tab = self.rooms.pop(room, None)
        if tab is not None:
            self.notebook.forget(tab)
            tab.destroy()

    # ------------------------------------------------------------------
    # Button and double-click actions
    # ------------------------------------------------------------------

    def on_user_double_click(self, event):
        selection = self.user_list.curselection()
        if selection and self.users[selection[0]] != self.me:
            self.open_dm(self.users[selection[0]])

    def selected_group(self):
        """Name of the group selected in the tree (clicking a member row counts as its group)."""
        selection = self.group_tree.selection()
        if not selection:
            messagebox.showinfo("No group selected", "Select a group in the list first.", parent=self.root)
            return None
        item = self.group_tree.parent(selection[0]) or selection[0]
        return item.removeprefix("group:")

    def on_group_double_click(self, event):
        group = self.selected_group()
        if group is None:
            return
        if self.me in self.groups[group]["members"]:
            self.open_group(group)
        elif messagebox.askyesno("Join group", f"You are not in #{group}. Join it?", parent=self.root):
            self.send(GROUP_JOIN, group=group)

    def create_group(self):
        """R8: ask for a name and send GROUP_CREATE. The tab opens when the server's GROUPS update arrives."""
        raw = simpledialog.askstring("Create group", "Group name:", parent=self.root)
        if raw is None:
            return                          # cancelled
        group = clean_name(raw)
        if group is None:
            messagebox.showerror("Bad group name", f"Use 1-{MAX_NAME_LEN} characters, without '|'.", parent=self.root)
            return
        self.send(GROUP_CREATE, group=group)

    def join_group(self):
        """R10: we can only ever join *ourselves*; the message has no field for anyone else."""
        group = self.selected_group()
        if group is not None:
            self.send(GROUP_JOIN, group=group)

    def leave_group(self):
        group = self.selected_group()
        if group is not None:
            self.send(GROUP_LEAVE, group=group)


def main():
    parser = argparse.ArgumentParser(description="Socket chat client")
    parser.add_argument("--host", default="localhost", help="pre-fill the server host / IP")
    parser.add_argument("--port", type=int, default=5050, help="pre-fill the server port")
    parser.add_argument("--name", default="", help="pre-fill your name")
    args = parser.parse_args()

    try:
        # Sharp text on high-DPI Windows screens (harmless elsewhere).
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (ImportError, AttributeError, OSError):
        pass

    root = tk.Tk()
    root.title("Socket Chat: login")

    def start_chat(sock, name):
        login.destroy()                     # swap the login screen for the chat window
        ChatApp(root, sock, name)

    login = LoginFrame(root, args.host, args.port, args.name, on_success=start_chat)
    login.pack()
    root.mainloop()


if __name__ == "__main__":
    main()
