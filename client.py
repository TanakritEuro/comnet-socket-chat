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
    BOT_ASK, BOT_CLEAR, BOT_REPLY, BYE, DM, ERROR, GROUP_CREATE, GROUP_JOIN, GROUP_LEAVE, GROUP_MSG,
    GROUPS, HELLO, MAX_NAME_LEN, MSG, NAME_TAKEN, SUMMARIZE, SUMMARY, USERS, WELCOME,
    ProtocolError, clean_name, dm_partner, dm_room, group_room, recv_msg, send_msg,
)

POLL_MS = 50            # how often the GUI thread checks the inbox queue
CONNECT_TIMEOUT = 5     # seconds to wait for the server while logging in

# Colours. The whole window uses one light palette (apply_theme), even when the OS is in dark mode, so it
# looks the same on every machine and the tinted AI blocks stay readable. Violet means "AI feature".
INK, GRAY, BLUE, GREEN = "#1B2430", "#5B6678", "#1F5FD1", "#17663F"
VIOLET, VIOLET_DARK, VIOLET_TINT, VIOLET_LIGHT = "#5B3FC4", "#4B30AB", "#EFEAFB", "#F5F2FD"
BG, LINE, SELECTED = "#EEF1F5", "#D5DBE3", "#E6EEFC"       # window background, borders, selected list row
FONT = ("Segoe UI", 10)
BOLD = ("Segoe UI", 10, "bold")
ITALIC = ("Segoe UI", 10, "italic")
SMALL = ("Segoe UI", 9)
SMALL_BOLD = ("Segoe UI", 9, "bold")
SMALL_ITALIC = ("Segoe UI", 9, "italic")
HEAD_FONT = ("Segoe UI", 12, "bold")

# Targets offered by the "Translate to" picker: (code sent to the server, name shown in the picker).
LANGUAGE_CHOICES = [("en", "English"), ("th", "ไทย (Thai)"), ("ja", "日本語 (Japanese)"),
                    ("zh", "中文 (Chinese)"), ("es", "Español")]
LANGUAGE_NAMES = {"en": "English", "th": "Thai", "ja": "Japanese", "zh": "Chinese", "es": "Spanish"}

# Mood pills: label from the server -> (text shown, text colour, background colour).
MOODS = {"positive": ("Positive", "#17663F", "#E1F3E8"),
         "neutral": ("Neutral", "#3F4A5C", "#E8ECF1"),
         "negative": ("Negative", "#A5331F", "#FBE6E1")}


def apply_theme(root):
    """Give every ttk widget the light look of the design. The "clam" theme is used because the native
    macOS/Windows themes ignore most colour settings (buttons would stay grey)."""
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=INK, font=FONT, bordercolor="#C3CBD6",
                    lightcolor=BG, darkcolor=BG, troughcolor=BG, focuscolor=BG)
    style.configure("TFrame", background=BG)
    style.configure("White.TFrame", background="#FFFFFF")
    style.configure("TLabel", background=BG)
    style.configure("White.TLabel", background="#FFFFFF")
    style.configure("Section.TLabel", background="#FFFFFF", foreground=GRAY, font=SMALL_BOLD)
    style.configure("TPanedwindow", background=BG)
    # Buttons: white with a grey border; Primary (blue) is for Send/Connect, AI (violet) for AI actions.
    # relief="solid" is what draws the visible border (a flat clam button has none, so white-on-white vanishes).
    style.configure("TButton", background="#FFFFFF", foreground=INK, padding=(12, 5), relief="solid", borderwidth=1,
                    bordercolor="#9AA5B5", lightcolor="#FFFFFF", darkcolor="#FFFFFF")
    style.map("TButton", background=[("disabled", BG), ("active", "#E6EAF0")], foreground=[("disabled", "#9AA3B2")],
              bordercolor=[("disabled", "#C3CBD6"), ("active", "#6B778A")])
    for name, color, hover, off in (("Primary", BLUE, "#1A52B8", "#9DB8E8"), ("AI", VIOLET, VIOLET_DARK, "#BBAEE6")):
        style.configure(f"{name}.TButton", background=color, foreground="#FFFFFF", bordercolor=color,
                        lightcolor=color, darkcolor=color)
        style.map(f"{name}.TButton", background=[("disabled", off), ("active", hover)],
                  foreground=[("disabled", "#FFFFFF")], bordercolor=[("disabled", off), ("active", hover)])
    style.configure("Chip.TButton", background=VIOLET_LIGHT, foreground="#2F2160", bordercolor=VIOLET, padding=(10, 3),
                    lightcolor=VIOLET_LIGHT, darkcolor=VIOLET_LIGHT)
    style.map("Chip.TButton", background=[("disabled", BG), ("active", VIOLET_TINT)], bordercolor=[("disabled", "#C3CBD6")])
    style.configure("TEntry", fieldbackground="#FFFFFF", padding=6, insertcolor=INK, lightcolor="#FFFFFF", darkcolor="#FFFFFF")
    style.configure("TCombobox", fieldbackground="#FFFFFF", background="#FFFFFF", foreground=INK, arrowcolor=VIOLET_DARK,
                    padding=4, lightcolor="#FFFFFF", darkcolor="#FFFFFF")
    style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")], selectbackground=[("readonly", "#FFFFFF")],
              selectforeground=[("readonly", INK)])
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=BG, foreground="#4A5568", padding=(16, 7), borderwidth=1)
    style.map("TNotebook.Tab", background=[("selected", "#FFFFFF")], foreground=[("selected", INK)])
    style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=INK, borderwidth=1)
    style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", INK)])


def make_list(parent, **options):
    """A plain list box in the light palette (a tk.Listbox ignores the ttk theme)."""
    return tk.Listbox(parent, bg="#FFFFFF", fg=INK, font=FONT, selectbackground=SELECTED, selectforeground=INK,
                      activestyle="none", relief="flat", highlightthickness=1, highlightbackground=LINE,
                      highlightcolor=LINE, exportselection=False, **options)


class LoginFrame(ttk.Frame):
    """Login screen: server host, port and a name. Re-prompts until the server accepts the name (R3)."""

    def __init__(self, root, host, port, name, on_success):
        apply_theme(root)
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
        ttk.Button(self, text="Connect", style="Primary.TButton", command=self.connect).grid(row=5, column=0, columnspan=2, pady=(8, 0))
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


def make_chat_window(parent):
    """A read-only chat window with every text style the chat rooms and the assistant use.

    The window is always light (even when the OS is in dark mode) so the tinted AI blocks
    stay readable. state="disabled" makes it read-only; callers unlock it briefly to append.
    """
    window = scrolledtext.ScrolledText(parent, state="disabled", wrap="word", width=60, height=20, font=FONT,
                                       bg="#FFFFFF", fg=INK, padx=8, pady=4, relief="flat", highlightthickness=1,
                                       highlightbackground="#D5DBE3")
    # Order matters: a tag created later wins when two tags set the same option (e.g. background).
    window.tag_configure("row_me", background="#EAF1FD")                # your own messages are tinted blue
    window.tag_configure("row_bot", background=VIOLET_LIGHT)            # assistant answers are tinted violet
    window.tag_configure("header", spacing1=8)                          # a little air above each message
    window.tag_configure("me", foreground=BLUE, font=BOLD)
    window.tag_configure("other", foreground=GREEN, font=BOLD)
    window.tag_configure("bot", foreground=VIOLET_DARK, font=BOLD)
    window.tag_configure("stamp", foreground=GRAY, font=SMALL)
    window.tag_configure("notice", foreground="gray", font=ITALIC)
    window.tag_configure("note", foreground=GRAY, font=SMALL_ITALIC)
    # AI summaries get a tinted, indented block so nobody mistakes them for a chat message.
    window.tag_configure("summary_title", foreground="#613583", background="#f3eefa",
                         font=BOLD, lmargin1=8, lmargin2=8, spacing1=6)
    window.tag_configure("summary", foreground=INK, background="#f3eefa", lmargin1=8, lmargin2=8)
    window.tag_configure("summary_model", foreground="gray", background="#f3eefa",
                         font=SMALL_ITALIC, lmargin1=8, lmargin2=8, spacing3=6)
    # Per-message AI controls: clickable text ("link"), the mood button, mood pills and result blocks.
    block = {"lmargin1": 8, "lmargin2": 8, "rmargin": 8}
    window.tag_configure("link", foreground=VIOLET_DARK, underline=True, font=SMALL_BOLD)
    window.tag_configure("mood_button", foreground=VIOLET_DARK, background=VIOLET_LIGHT, font=SMALL_BOLD)
    window.tag_configure("mood_error", foreground="#A5331F")
    window.tag_configure("mood_pending", foreground=VIOLET_DARK, font=SMALL_ITALIC)
    for label, (name, color, tint) in MOODS.items():
        window.tag_configure(f"mood_{label}", foreground=color, background=tint, font=SMALL_BOLD)
    window.tag_configure("ai_pending", foreground=VIOLET_DARK, background=VIOLET_TINT, font=ITALIC, **block)
    window.tag_configure("ai_title", foreground=VIOLET_DARK, background=VIOLET_TINT, font=SMALL_BOLD, **block)
    window.tag_configure("ai_text", foreground="#2F2160", background=VIOLET_TINT, **block)
    window.tag_configure("ai_error", foreground="#7A2414", background="#FBE6E1", **block)
    window.tag_configure("thinking", foreground=VIOLET_DARK, font=ITALIC)
    # The mood button/pill sits at the right edge of the message header, using a right-aligned tab stop.
    window.bind("<Configure>", lambda event: window.tag_configure("header", tabs=(max(event.width - 40, 100), "right")))
    # The mouse turns into a hand over anything clickable.
    for tag in ("link", "mood_button"):
        window.tag_bind(tag, "<Enter>", lambda event: window.configure(cursor="hand2"))
        window.tag_bind(tag, "<Leave>", lambda event: window.configure(cursor=""))
    return window


def append_text(window, text, *tags):
    """Add text at the end of a chat window and scroll to it. `tags` may contain None (ignored)."""
    window.configure(state="normal")
    window.insert("end", text, tuple(tag for tag in tags if tag))
    window.configure(state="disabled")
    window.see("end")                       # auto-scroll to the newest line


class RoomTab(ttk.Frame):
    """One chat room (R5): a header, an AI strip, a read-only chat window and a chat box with Send (R6).

    The AI strip and the per-message controls are the plug-in points for the AI features. This class only
    draws things and calls the callbacks below; the network side lives in ChatApp. Features show their
    results through show_translating / add_translation / translation_failed, show_mood_pending / set_mood /
    mood_failed and add_summary.
    """

    def __init__(self, parent, on_send, on_summarize, on_translate, on_mood, title="", subtitle=""):
        super().__init__(parent, padding=10, style="White.TFrame")
        self.on_send = on_send              # function(text) -> True if the message was sent
        self.on_summarize = on_summarize    # function() -> True if SUMMARIZE was sent
        self.on_translate = on_translate    # function(ts, language code) -> True if the request was sent
        self.on_mood = on_mood              # function(ts) -> True if the request was sent
        self.entries = {}                   # message number -> {"ts": ..., "row": tag of its background}
        self.by_ts = {}                     # message ts -> message number

        # Chat box: Enter or the Send button sends. Packed first (at the bottom) so it
        # never gets squeezed out when the window is small.
        bar = ttk.Frame(self, style="White.TFrame")
        bar.pack(side="bottom", fill="x", pady=(8, 0))
        self.entry = ttk.Entry(bar)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda event: self.send())
        ttk.Button(bar, text="Send", style="Primary.TButton", command=self.send).pack(side="left", padx=(8, 0))

        head = ttk.Frame(self, style="White.TFrame")
        head.pack(side="top", fill="x", pady=(0, 8))
        ttk.Label(head, text=title, style="White.TLabel", font=HEAD_FONT).pack(anchor="w")
        self.subtitle = ttk.Label(head, text=subtitle, style="White.TLabel", foreground=GRAY)
        self.subtitle.pack(anchor="w")

        # AI strip: everything AI that works on the whole room, in one violet bar.
        strip = tk.Frame(self, bg=VIOLET_LIGHT, padx=10, pady=6)
        strip.pack(side="top", fill="x", pady=(0, 8))
        tk.Label(strip, text="✦ AI tools", bg=VIOLET_LIGHT, fg=VIOLET_DARK, font=BOLD).pack(side="left")
        tk.Label(strip, text="Translate to:", bg=VIOLET_LIGHT, fg=INK, font=FONT).pack(side="left", padx=(14, 4))
        # The picker only chooses the target language; nothing is translated until a message's link is clicked.
        self.language_box = ttk.Combobox(strip, values=[name for _, name in LANGUAGE_CHOICES],
                                         state="readonly", width=18)
        self.language_box.current(0)
        self.language_box.pack(side="left")
        self.summarize_button = ttk.Button(strip, text="✦ Summarize chat", style="AI.TButton", command=self.summarize)
        self.summarize_button.pack(side="right")

        self.window = make_chat_window(self)
        self.window.pack(fill="both", expand=True)
        self.window.tag_bind("link", "<Button-1>", self.on_link_click)
        self.window.tag_bind("mood_button", "<Button-1>", self.on_mood_click)

    def send(self):
        """Hand the typed text to on_send. It appears in the window when the server echoes it back."""
        text = self.entry.get()
        if text.strip() and self.on_send(text):
            self.entry.delete(0, "end")

    def set_subtitle(self, text):
        self.subtitle.config(text=text)

    def language_code(self):
        """Code of the language chosen in the "Translate to" picker, e.g. "en"."""
        return LANGUAGE_CHOICES[self.language_box.current()][0]

    # ------------------------------------------------------------------
    # Messages and their per-message AI controls
    # ------------------------------------------------------------------

    def add_message(self, sender, text, ts, mine):
        """Append one chat message: a header line (name, time, Analyze mood button) and the text.
        Messages from other people also get a Translate link underneath."""
        number = len(self.entries)
        row = "row_me" if mine else None
        self.entries[number] = {"ts": ts, "row": row}
        self.by_ts[ts] = number
        stamp = time.strftime("%H:%M", time.localtime(ts))
        self.append(sender, row, "header", "me" if mine else "other")
        self.append(f"  {stamp}", row, "stamp")
        self.append("\t", row)                                  # the tab pushes the mood button to the right edge
        self.insert_slot("end", number, "mood", [("✦ Analyze mood", ("mood_button",))])
        self.append("\n", row)
        self.append(text + "\n", row)
        if not mine:
            self.insert_slot("end", number, "tr", [("Translate", ("link",)), ("\n", ())])
        self.window.see("end")

    def insert_slot(self, index, number, kind, parts):
        """Insert `parts` (pairs of text and tag tuples) tagged as slot `kind` of message `number`.
        A slot is a stretch of text that can later be swapped out as a whole (see replace_slot)."""
        slot, row = f"{kind}-{number}", self.entries[number]["row"]
        args = []
        for text, tags in parts:
            args += [text, tuple(tag for tag in (*tags, slot, row) if tag)]
        self.window.configure(state="normal")
        self.window.insert(index, *args)
        self.window.configure(state="disabled")

    def replace_slot(self, ts, kind, parts):
        """Swap the content of one message's slot (e.g. the Translate link for a translated block)."""
        number = self.by_ts.get(ts)
        ranges = self.window.tag_ranges(f"{kind}-{number}") if number is not None else ()
        if not ranges:
            return                          # unknown message, or the slot is gone
        start = str(ranges[0])
        self.window.configure(state="normal")
        self.window.delete(start, str(ranges[-1]))
        self.window.configure(state="disabled")
        self.insert_slot(start, number, kind, parts)

    def slot_at(self, event, kind):
        """Message ts of the slot under the mouse, or None."""
        for tag in self.window.tag_names(f"@{event.x},{event.y}"):
            if tag.startswith(f"{kind}-"):
                return self.entries[int(tag.split("-")[1])]["ts"]
        return None

    def on_link_click(self, event):
        ts = self.slot_at(event, "tr")
        if ts is not None:
            self.translate(ts)

    def on_mood_click(self, event):
        ts = self.slot_at(event, "mood")
        if ts is not None:
            self.analyze(ts)

    # Translation (mee) ------------------------------------------------

    def translate(self, ts):
        """The Translate link was clicked: ask for it, and show "Translating…" if the request went out."""
        if self.on_translate(ts, self.language_code()):
            self.show_translating(ts)

    def show_translating(self, ts):
        self.replace_slot(ts, "tr", [("Translating…\n", ("ai_pending",))])

    def add_translation(self, ts, text, source, target):
        """Show a finished translation under its message. `source` and `target` are language codes."""
        names = dict(LANGUAGE_NAMES)
        route = f"{names.get(source, source)} → {names.get(target, target)}" if source else f"to {names.get(target, target)}"
        self.replace_slot(ts, "tr", [(f"Translated · {route}\n", ("ai_title",)), (text.strip() + "\n", ("ai_text",))])

    def translation_failed(self, ts):
        """Show an error block with a Try again link (the original text is unchanged)."""
        self.replace_slot(ts, "tr", [("Couldn't translate this message.  ", ("ai_error",)),
                                     ("Try again", ("ai_error", "link")), ("\n", ("ai_error",))])

    # Sentiment (magga) ------------------------------------------------

    def analyze(self, ts):
        """The Analyze mood button was clicked: ask for it, and show "Analyzing…" if the request went out."""
        if self.on_mood(ts):
            self.show_mood_pending(ts)

    def show_mood_pending(self, ts):
        self.replace_slot(ts, "mood", [("Analyzing…", ("mood_pending",))])

    def set_mood(self, ts, label):
        """Replace the button with a pill. `label` is "positive", "neutral" or "negative"."""
        if label in MOODS:
            self.replace_slot(ts, "mood", [(f" ● {MOODS[label][0]} ", (f"mood_{label}",))])

    def mood_failed(self, ts):
        self.replace_slot(ts, "mood", [("✦ Couldn't analyze · Try again", ("mood_button", "mood_error"))])

    # Summarization (euro) ---------------------------------------------

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

    def append(self, text, *tags):
        append_text(self.window, text, *tags)


class BotTab(ttk.Frame):
    """The AI Assistant tab (special 4): a private conversation with the chatbot.

    It is not a room on the server. Like RoomTab it only draws things; ChatApp does the networking and
    calls add_reply / failed when the answer arrives.
    """

    SUGGESTIONS = [("Explain TCP framing", False), ("Explain my last chat", True), ("Help me write a reply", False)]

    def __init__(self, parent, on_ask, on_clear):
        super().__init__(parent, padding=10, style="White.TFrame")
        self.on_ask = on_ask                # function(text, use_last_chat) -> True if the question was sent
        self.on_clear = on_clear            # function(): tell the server to forget the conversation
        self.waiting = False                # True from "Thinking…" until the answer arrives

        bar = ttk.Frame(self, style="White.TFrame")
        bar.pack(side="bottom", fill="x", pady=(6, 0))
        self.entry = ttk.Entry(bar)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda event: self.ask())
        self.ask_button = ttk.Button(bar, text="Ask", style="AI.TButton", command=self.ask)
        self.ask_button.pack(side="left", padx=(8, 0))

        chips = ttk.Frame(self, style="White.TFrame")
        chips.pack(side="bottom", fill="x", pady=(8, 0))
        self.chips = []
        for text, use_last_chat in self.SUGGESTIONS:
            chip = ttk.Button(chips, text=text, style="Chip.TButton", command=lambda t=text, c=use_last_chat: self.ask(t, c))
            chip.pack(side="left", padx=(0, 6))
            self.chips.append(chip)

        head = tk.Frame(self, bg=VIOLET_LIGHT, padx=10, pady=8)
        head.pack(side="top", fill="x", pady=(0, 8))
        titles = tk.Frame(head, bg=VIOLET_LIGHT)
        titles.pack(side="left")
        tk.Label(titles, text="✦ AI Assistant", bg=VIOLET_LIGHT, fg=VIOLET_DARK, font=HEAD_FONT).pack(anchor="w")
        tk.Label(titles, text="Private to you · answers come from Gemini and can be wrong",
                 bg=VIOLET_LIGHT, fg=GRAY, font=SMALL).pack(anchor="w")
        ttk.Button(head, text="Clear conversation", command=self.clear).pack(side="right")

        self.window = make_chat_window(self)
        self.window.pack(fill="both", expand=True)
        self.add_notice("Ask me about this app, networking, or help with a reply.")

    def ask(self, text=None, use_last_chat=False):
        """Send the typed question (or a suggestion chip's text). One question at a time."""
        text = (self.entry.get() if text is None else text).strip()
        if not text or self.waiting:
            return
        if self.on_ask(text, use_last_chat):
            self.entry.delete(0, "end")
            self.add_question(text)
            self.show_thinking()

    def add_question(self, text):
        self.append("you", "header", "row_me", "me")
        self.append(f"  {time.strftime('%H:%M')}\n", "row_me", "stamp")
        self.append(text + "\n", "row_me")

    def show_thinking(self):
        """Show "Thinking…" under the assistant's name and block new questions until the answer arrives."""
        self.set_waiting(True)
        self.append("AI Assistant", "header", "row_bot", "bot")
        self.append(f"  {time.strftime('%H:%M')}\n", "row_bot", "stamp")
        self.append("Thinking…\n", "row_bot", "thinking")

    def add_reply(self, text, model):
        """Replace "Thinking…" with the assistant's answer."""
        self.replace_thinking([(text.strip() + "\n", ("row_bot",))] +
                              ([("Offline reply · no AI was used\n", ("row_bot", "note"))] if model == "offline" else []))

    def failed(self, text="Couldn't get an answer. Try again."):
        """Replace "Thinking…" with a red error block."""
        self.replace_thinking([(text + "\n", ("ai_error",))])

    def replace_thinking(self, parts):
        ranges = self.window.tag_ranges("thinking")
        if ranges:
            start = str(ranges[0])
            args = []
            for text, tags in parts:
                args += [text, tags]
            self.window.configure(state="normal")
            self.window.delete(start, str(ranges[-1]))
            self.window.insert(start, *args)
            self.window.configure(state="disabled")
        self.set_waiting(False)

    def set_waiting(self, waiting):
        self.waiting = waiting
        for button in (self.ask_button, *self.chips):
            button.state(["disabled" if waiting else "!disabled"])

    def clear(self):
        """Wipe the conversation on screen and tell the server to forget it too."""
        self.on_clear()
        self.window.configure(state="normal")
        self.window.delete("1.0", "end")
        self.window.configure(state="disabled")
        self.set_waiting(False)
        self.add_notice("Conversation cleared.")

    def add_notice(self, text):
        self.append(text + "\n", "notice")

    def append(self, text, *tags):
        append_text(self.window, text, *tags)


class ChatApp:
    """Main window after login: online users, groups with members, and one tab per chat room."""

    def __init__(self, root, sock, name):
        apply_theme(root)
        root.geometry("1100x700")
        root.minsize(800, 500)
        self.root = root
        self.sock = sock
        self.me = name
        self.connected = True
        self.inbox = queue.Queue()          # receiver thread -> GUI thread
        self.rooms = {}                     # room id -> RoomTab
        self.bot_tab = None                 # the AI Assistant tab, once opened (it is not a room)
        self.last_room = None               # room id of the chat tab the user looked at last
        self.users = []                   # online names, in the order shown in the user list
        self.groups = {}                    # group name -> {"creator": ..., "members": [...]}
        self.handlers = {USERS: self.on_users, GROUPS: self.on_groups, MSG: self.on_msg,
                         SUMMARY: self.on_summary, BOT_REPLY: self.on_bot_reply, ERROR: self.on_error}

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

        side = ttk.Frame(panes, style="White.TFrame", padding=14, width=300)
        ttk.Label(side, text=self.me, style="White.TLabel", font=HEAD_FONT).pack(anchor="w")
        self.status_label = ttk.Label(side, text=f"● Connected · {self.server_address()}", style="White.TLabel",
                                      foreground="#2E9E5B")
        self.status_label.pack(anchor="w", pady=(0, 14))

        ttk.Label(side, text="ASSISTANT (DOUBLE-CLICK TO OPEN)", style="Section.TLabel").pack(anchor="w")
        self.assistant_list = make_list(side, height=1)
        self.assistant_list.insert("end", "✦ AI Assistant  [BOT]")
        self.assistant_list.pack(fill="x", pady=(2, 10))
        self.assistant_list.bind("<Double-Button-1>", lambda event: self.open_bot())

        self.users_label = ttk.Label(side, text="ONLINE USERS (DOUBLE-CLICK TO CHAT)", style="Section.TLabel")
        self.users_label.pack(anchor="w")
        self.user_list = make_list(side, height=8)
        self.user_list.pack(fill="x", pady=(2, 10))
        self.user_list.bind("<Double-Button-1>", self.on_user_double_click)

        ttk.Label(side, text="GROUPS AND MEMBERS", style="Section.TLabel").pack(anchor="w")
        self.group_tree = ttk.Treeview(side, show="tree", height=10, selectmode="browse")
        self.group_tree.pack(fill="both", expand=True, pady=(2, 4))
        self.group_tree.bind("<Double-Button-1>", self.on_group_double_click)
        buttons = ttk.Frame(side, style="White.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Create…", command=self.create_group).pack(side="left", expand=True, fill="x")
        ttk.Button(buttons, text="Join", command=self.join_group).pack(side="left", expand=True, fill="x", padx=3)
        ttk.Button(buttons, text="Leave", command=self.leave_group).pack(side="left", expand=True, fill="x")
        panes.add(side, weight=0)

        self.notebook = ttk.Notebook(panes)
        welcome = ttk.Frame(self.notebook, style="White.TFrame", padding=20)
        ttk.Label(welcome, style="White.TLabel", justify="left", text=(
            f"Logged in as {self.me}.\n\n"
            "• Double-click a user to open a private chat.\n"
            "• Create a group, or select one and press Join.\n"
            "• Double-click a group you are in to open its chat.\n"
            "• Double-click AI Assistant to ask the chatbot.\n"
            "• In a chat, use the AI tools bar and the buttons on each message."
        )).pack(anchor="nw")
        self.notebook.add(welcome, text="Welcome")
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        panes.add(self.notebook, weight=1)

    def server_address(self):
        """"host:port" of the server, for the status line."""
        try:
            host, port = self.sock.getpeername()[:2]
            return f"{host}:{port}"
        except OSError:
            return "server"

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
        self.users_label.config(text=f"Online users · {len(self.users)} (double-click to chat)".upper())

    def member_text(self, member, info):
        """A member's name as listed in the groups tree and the group tab's header."""
        text = member
        if member == self.me:
            text += " (you)"
        if member == info["creator"]:
            text += " (creator)"
        return text

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
                self.group_tree.insert(parent, "end", text=self.member_text(member, info))

        for group in sorted(is_member - was_member):
            self.open_group(group).add_notice(f"You are a member of #{group}.")
        for group in was_member - is_member:
            self.close_room(group_room(group))
        for group in is_member:             # keep each open group tab's member list up to date
            tab = self.rooms.get(group_room(group))
            if tab is not None:
                tab.set_subtitle(self.group_subtitle(group))

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

    def on_bot_reply(self, msg):
        """Special 4: the chatbot's answer, sent only to us. Replaces "Thinking…" in the assistant tab."""
        if self.bot_tab is not None:
            self.bot_tab.add_reply(msg["text"], msg["model"])

    def on_error(self, msg):
        """The server refused a request (e.g. GROUP_EXISTS, NO_SUCH_USER)."""
        if msg.get("request") == BOT_ASK:   # the chatbot failed: show it in its tab instead of a pop-up
            if self.bot_tab is not None:
                self.bot_tab.failed(msg.get("message") or "Couldn't get an answer. Try again.")
            return
        for tab in self.rooms.values():     # if it was a SUMMARIZE that failed, allow a retry
            tab.enable_summarize()
        messagebox.showwarning(msg.get("code", "Error"), msg.get("message", ""), parent=self.root)

    def on_disconnected(self):
        """The receiver thread saw the connection close."""
        if not self.connected:
            return                          # we are quitting ourselves
        self.connected = False
        self.root.title(f"Socket Chat: {self.me} (disconnected)")
        self.status_label.config(text="● Disconnected", foreground="#A5331F")
        messagebox.showerror("Disconnected", "Lost the connection to the server.", parent=self.root)

    # ------------------------------------------------------------------
    # Rooms (tabs)
    # ------------------------------------------------------------------

    def open_room(self, room, title, on_send, subtitle="", select=True):
        """Return the tab for this room id, creating it first if needed (R5: one tab per room)."""
        if room not in self.rooms:
            self.rooms[room] = RoomTab(
                self.notebook, on_send,
                on_summarize=lambda: self.send(SUMMARIZE, room=room),
                on_translate=lambda ts, to: self.translate_message(room, ts, to),
                on_mood=lambda ts: self.analyze_mood(room, ts),
                title=title, subtitle=subtitle)
            self.notebook.add(self.rooms[room], text=title)
        if select:
            self.notebook.select(self.rooms[room])
            self.rooms[room].entry.focus_set()
        return self.rooms[room]

    def open_dm(self, user, select=True):
        """Private chat tab with one user (R7)."""
        return self.open_room(dm_room(self.me, user), f"@{user}",
                              lambda text: self.send(DM, to=user, text=text),
                              f"Private chat · only you and {user} can see these messages", select)

    def open_group(self, group, select=True):
        """Group chat tab (R11)."""
        return self.open_room(group_room(group), f"#{group}",
                              lambda text: self.send(GROUP_MSG, group=group, text=text),
                              self.group_subtitle(group), select)

    def group_subtitle(self, group):
        info = self.groups.get(group)
        if info is None:
            return "Group chat · only members can see these messages"
        members = ", ".join(self.member_text(member, info) for member in info["members"])
        return f"Group · {len(info['members'])} members: {members} · only members can see these messages"

    def open_bot(self, select=True):
        """The AI Assistant tab (special 4). Not a room: the conversation is private to us."""
        if self.bot_tab is None:
            self.bot_tab = BotTab(self.notebook, self.ask_bot, self.clear_bot)
            self.notebook.add(self.bot_tab, text="AI Assistant")
        if select:
            self.notebook.select(self.bot_tab)
            self.bot_tab.entry.focus_set()
        return self.bot_tab

    def close_room(self, room):
        tab = self.rooms.pop(room, None)
        if tab is not None:
            self.notebook.forget(tab)
            tab.destroy()

    def on_tab_changed(self, event):
        """Remember the last chat room the user looked at (the chatbot's "Explain my last chat" uses it)."""
        current = self.notebook.nametowidget(self.notebook.select())
        for room, tab in self.rooms.items():
            if tab is current:
                self.last_room = room

    # ------------------------------------------------------------------
    # AI feature plug-in points. Each returns True if the request was sent, and the tab then shows
    # its "working" state. The feature's owner replaces the stub body with self.send(...) and adds an
    # on_<reply> handler that calls the tab's result methods (see RoomTab and BotTab).
    # ------------------------------------------------------------------

    def translate_message(self, room, ts, to):
        """Translation: the Translate link under the message `ts` of `room` was clicked."""
        self.rooms[room].add_notice("Translation isn't available yet.")
        return False

    def analyze_mood(self, room, ts):
        """Sentiment: the Analyze mood button on the message `ts` of `room` was clicked."""
        self.rooms[room].add_notice("Mood analysis isn't available yet.")
        return False

    def ask_bot(self, text, use_last_chat):
        """Chatbot: a question was typed. With use_last_chat, self.last_room is the room it is about."""
        fields = {"text": text}
        if use_last_chat:
            if self.last_room not in self.rooms:        # no chat tab was opened yet, or it has been closed
                self.bot_tab.add_notice("Open a chat tab first, then I can explain it.")
                return False
            fields["room"] = self.last_room             # the server checks we may read it, then adds its messages
        return self.send(BOT_ASK, **fields)

    def clear_bot(self):
        """Chatbot: "Clear conversation" was clicked; the server should forget it too."""
        self.send(BOT_CLEAR)

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
