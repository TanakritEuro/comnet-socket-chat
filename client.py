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
import base64
import math
import os
import queue
import socket
import struct
import threading
import time
import tkinter as tk
import zlib
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, simpledialog, ttk

from protocol import (
    AI_FAILED, BOT_ASK, BOT_CLEAR, BOT_REPLY, BYE, DM, ERROR, FILE, GROUP_CREATE, GROUP_JOIN, GROUP_LEAVE,
    GROUP_MSG, GROUPS, HELLO, HISTORY, MAX_FILE_BYTES, MAX_NAME_LEN, MSG, NAME_TAKEN, SUMMARIZE, SUMMARY,
    TRANSLATE, TRANSLATION, USERS, WELCOME,SENTIMENT, SENTIMENT_RESULT,
    ProtocolError, clean_name, dm_partner, dm_room, group_room, recv_msg, send_msg,
)

POLL_MS = 50            # how often the GUI thread checks the inbox queue
CONNECT_TIMEOUT = 5     # seconds to wait for the server while logging in
IMAGE_TYPES = (".png", ".gif")   # Tk can draw these without extra libraries (no JPEG: that needs Pillow)
PREVIEW_PX = 320        # an image preview is shrunk until it is at most this wide

# Colours. The whole window uses one light palette (apply_theme), even when the OS is in dark mode, so it
# looks the same on every machine and the tinted AI blocks stay readable. Blue is the main accent and
# violet means "AI feature". Greys are the "slate" scale, which looks cleaner than pure grey.
INK, GRAY, BLUE, GREEN = "#0F172A", "#64748B", "#2563EB", "#0F766E"
BLUE_DARK, BLUE_TINT = "#1D4ED8", "#EFF6FF"
VIOLET, VIOLET_DARK, VIOLET_TINT, VIOLET_LIGHT = "#7C3AED", "#6D28D9", "#F3EEFF", "#FAF7FF"
BG, LINE, SELECTED = "#F1F5F9", "#E2E8F0", "#DBEAFE"       # window background, borders, selected list row
SURFACE = "#FFFFFF"                                         # cards: the sidebar, chat area, inputs
FONT = ("Segoe UI", 11)
BOLD = ("Segoe UI", 11, "bold")
ITALIC = ("Segoe UI", 11, "italic")
SMALL = ("Segoe UI", 10)
SMALL_BOLD = ("Segoe UI", 10, "bold")
SMALL_ITALIC = ("Segoe UI", 10, "italic")
HEAD_FONT = ("Segoe UI", 15, "bold")
TITLE_FONT = ("Segoe UI", 26, "bold")
AVATAR_COLORS = ["#0EA5E9", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#14B8A6", "#6366F1"]

# The tip cards on the Welcome tab: (icon, icon colour, title, help text).
WELCOME_TIPS = [("💬", "#2563EB", "Private chat", "Double-click a name under Online users."),
                ("👥", "#0EA5E9", "Group chat", "Create a group, or select one and press Join. Double-click a group you are in to open it."),
                ("✦", "#7C3AED", "AI Assistant", "Double-click AI Assistant in the sidebar to ask the chatbot."),
                ("⚡", "#F59E0B", "AI tools", "In any chat: summarize the room, translate a message, or analyze its mood."),
                ("📎", "#10B981", "Files and images", "Use Attach to send a file or an image up to 5 MiB.")]

# Targets offered by the "Translate to" picker: (code sent to the server, name shown in the picker).
LANGUAGE_CHOICES = [("en", "English"), ("th", "ไทย (Thai)"), ("ja", "日本語 (Japanese)"),
                    ("zh", "中文 (Chinese)"), ("es", "Español")]
LANGUAGE_NAMES = {"en": "English", "th": "Thai", "ja": "Japanese", "zh": "Chinese", "es": "Spanish"}

# Mood pills: label from the server -> (text shown, text colour, background colour).
MOODS = {"positive": ("Positive", "#17663F", "#E1F3E8"),
         "neutral": ("Neutral", "#3F4A5C", "#E8ECF1"),
         "negative": ("Negative", "#A5331F", "#FBE6E1")}


# ---------------------------------------------------------------------------
# Look: rounded buttons and fields, drawn in code (standard library only)
# ---------------------------------------------------------------------------

def _png(width, height, rows):
    """The bytes of a PNG with transparency, built by hand so no image library is needed.
    `rows` is a list of rows, each a list of (red, green, blue, alpha) pixels."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    raw = b"".join(b"\x00" + bytes(value for pixel in row for value in pixel) for row in rows)   # filter byte 0 per row
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def rounded_image(master, radius, fill, border=None, border_width=1):
    """A small rounded rectangle with soft (anti-aliased) edges, as a PhotoImage. ttk keeps its four corners
    and stretches the middle, so this one image becomes a button or text field of any size. The outside is
    transparent, so the colour behind the widget shows around the corners."""
    size = 2 * radius + 4                       # the four corners (radius + 1 each) plus a 2-pixel strip ttk can stretch
    half = size / 2
    fill_rgb, border_rgb = _rgb(fill), _rgb(border or fill)

    def distance(x, y, inset):
        """How far (x, y) is outside the rounded square shrunk by `inset` pixels (negative = inside it)."""
        r = max(radius - inset, 0.0)
        dx, dy = abs(x - half) - (half - inset - r), abs(y - half) - (half - inset - r)
        return math.hypot(max(dx, 0.0), max(dy, 0.0)) + min(max(dx, dy), 0.0) - r

    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            x, y = px + 0.5, py + 0.5
            outer = min(max(0.5 - distance(x, y, 0), 0.0), 1.0)      # 1 inside the shape, 0 outside, soft in between
            inner = min(max(0.5 - distance(x, y, border_width), 0.0), 1.0) if border else 1.0
            rgb = tuple(round(b * (1 - inner) + f * inner) for b, f in zip(border_rgb, fill_rgb))
            row.append((*rgb, round(255 * outer)))
        rows.append(row)
    return tk.PhotoImage(master=master, data=base64.b64encode(_png(size, size, rows)).decode("ascii"))


_SKIN_IMAGES = []       # Tk draws an image only while Python holds a reference to it

# Button looks: style name -> (fill, hover fill, pressed fill, border, text, disabled fill, disabled border,
# disabled text, padding). Primary (blue) is for Send/Connect, AI (violet) for AI actions.
BUTTON_LOOKS = {
    "TButton": (SURFACE, "#F1F5F9", "#E2E8F0", "#CBD5E1", INK, "#F8FAFC", "#E2E8F0", "#94A3B8", (12, 8)),
    "Primary.TButton": (BLUE, BLUE_DARK, "#1E40AF", None, "#FFFFFF", "#93C5FD", None, "#FFFFFF", (14, 8)),
    "AI.TButton": (VIOLET, VIOLET_DARK, "#5B21B6", None, "#FFFFFF", "#C4B5FD", None, "#FFFFFF", (14, 7)),
    "Chip.TButton": (VIOLET_LIGHT, VIOLET_TINT, "#E9DDFF", "#DDD6FE", "#4C1D95", "#F8FAFC", "#E2E8F0", "#94A3B8", (12, 5)),
    "Danger.TButton": (SURFACE, "#FEF2F2", "#FEE2E2", "#FCA5A5", "#B91C1C", "#F8FAFC", "#E2E8F0", "#94A3B8", (12, 8)),
}


def ui_scale(widget):
    """1.0 at 100 % Windows display scaling, 1.25 at 125 %, and so on."""
    return max(widget.winfo_fpixels("1i") / 96, 1.0)


def build_skin(style, master):
    """Draw the rounded buttons and text fields once, as ttk image elements. The look and the layout of
    each ttk widget stay the same, so buttons still have .state(), .instate() and .invoke() as before."""
    if "Skin.TButton" in style.element_names():
        return                                  # already built for this window system
    radius = round(9 * ui_scale(master))
    for name, (fill, hover, pressed, border, text, off_fill, off_border, off_text, padding) in BUTTON_LOOKS.items():
        normal, active, down, disabled = (rounded_image(master, radius, f, b) for f, b in (
            (fill, border), (hover, border), (pressed, border), (off_fill, off_border)))
        _SKIN_IMAGES.extend((normal, active, down, disabled))
        style.element_create(f"Skin.{name}", "image", normal, ("disabled", disabled), ("pressed", down),
                             ("active", active), border=radius + 1, sticky="nsew")
        style.layout(name, [(f"Skin.{name}", {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
        style.configure(name, foreground=text, padding=padding, anchor="center",
                        font=FONT if name in ("TButton", "Danger.TButton") else BOLD)
        style.map(name, foreground=[("disabled", off_text)])

    field, focused, off = (rounded_image(master, radius, f, b, w) for f, b, w in (
        (SURFACE, "#CBD5E1", 1), (SURFACE, BLUE, 2), ("#F8FAFC", "#E2E8F0", 1)))
    _SKIN_IMAGES.extend((field, focused, off))
    style.element_create("Skin.entry", "image", field, ("disabled", off), ("focus", focused),
                         border=radius + 1, sticky="nsew")
    style.layout("TEntry", [("Skin.entry", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    # The language drop-down gets the same rounded field, with its arrow on the right.
    style.layout("TCombobox", [("Skin.entry", {"sticky": "nsew", "children": [
        ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"expand": "1", "sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]})]})])


def apply_theme(root):
    """Give every ttk widget the modern light look. The "clam" theme is the base because the native
    macOS/Windows themes ignore most colour settings; on top of it the buttons and fields get rounded corners."""
    root.configure(bg=BG)
    root.option_add("*TEntry.font", FONT)        # text fields use the same font as everything else
    root.option_add("*Entry.font", FONT)
    root.option_add("*TCombobox*Font", FONT)     # the language picker and its drop-down list
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=INK, font=FONT, bordercolor=LINE,
                    lightcolor=BG, darkcolor=BG, troughcolor=BG, focuscolor=BG)
    style.configure("TFrame", background=BG)
    style.configure("White.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG)
    style.configure("White.TLabel", background=SURFACE)
    style.configure("Section.TLabel", background=SURFACE, foreground=GRAY, font=SMALL_BOLD)
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=8, gripcount=0, background=BG)
    build_skin(style, root)
    style.configure("TEntry", fieldbackground=SURFACE, padding=(10, 7), foreground=INK, insertcolor=INK, font=FONT)
    style.map("TEntry", fieldbackground=[("disabled", "#F8FAFC")])
    style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE, foreground=INK, arrowcolor=VIOLET_DARK,
                    bordercolor="#CBD5E1", padding=5, lightcolor=SURFACE, darkcolor=SURFACE)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)], selectbackground=[("readonly", SURFACE)],
              selectforeground=[("readonly", INK)], bordercolor=[("focus", BLUE)])
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=GRAY, padding=(20, 11), borderwidth=0, font=BOLD)
    style.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", "#E7ECF3")],
              foreground=[("selected", INK), ("active", INK)])
    # Lists: no frame, taller rows, a soft blue selection.
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=INK, borderwidth=0)
    style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", INK)])
    # Scroll bars: a thin rounded-looking thumb with no arrow buttons.
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Vertical.TScrollbar", troughcolor=SURFACE, background="#CBD5E1", bordercolor=SURFACE,
                    lightcolor="#CBD5E1", darkcolor="#CBD5E1", gripcount=0, width=10)
    style.map("Vertical.TScrollbar", background=[("active", "#94A3B8")], lightcolor=[("active", "#94A3B8")],
              darkcolor=[("active", "#94A3B8")])


def avatar_color(name):
    """The same name always gets the same colour."""
    return AVATAR_COLORS[sum(map(ord, name)) % len(AVATAR_COLORS)]


def make_avatar(parent, name, background, size=32, color=None, glyph=None):
    """A round avatar: a coloured circle with the person's initial (or `glyph`), drawn on a canvas."""
    size = round(size * ui_scale(parent)) // 2 * 2          # an even size, so the circle is centred exactly
    canvas = tk.Canvas(parent, width=size, height=size, bg=background, highlightthickness=0, borderwidth=0)
    # A canvas oval has jagged edges; a rounded image with soft edges (the same trick as the buttons) is smooth.
    canvas.disc = rounded_image(parent, size // 2 - 2, color or avatar_color(name))
    canvas.create_image(size // 2, size // 2, image=canvas.disc)
    canvas.create_text(size / 2, size / 2, text=glyph or name[:1].upper(), fill="#FFFFFF",
                       font=("Segoe UI", max(size // 3, 8), "bold"))
    return canvas


def make_list(parent, **options):
    """A plain list box in the light palette (a tk.Listbox ignores the ttk theme)."""
    return tk.Listbox(parent, bg=SURFACE, fg=INK, font=FONT, selectbackground=SELECTED, selectforeground=INK,
                      activestyle="none", relief="flat", highlightthickness=0, borderwidth=0,
                      exportselection=False, **options)


class LoginFrame(ttk.Frame):
    """Login screen: server host, port and a name. Re-prompts until the server accepts the name (R3)."""

    def __init__(self, root, host, port, name, on_success):
        apply_theme(root)
        super().__init__(root, style="White.TFrame")
        self.on_success = on_success        # called with (socket, accepted name)
        self.host = tk.StringVar(value=host)
        self.port = tk.StringVar(value=str(port))
        self.name = tk.StringVar(value=name)

        # Left: a blue brand panel. Right: the form.
        brand = tk.Frame(self, bg=BLUE, padx=36, pady=40)
        brand.pack(side="left", fill="y")
        make_avatar(brand, "Socket Chat", BLUE, size=64, color=BLUE_DARK, glyph="💬").pack(anchor="w")
        tk.Label(brand, text="Socket Chat", bg=BLUE, fg="#FFFFFF", font=TITLE_FONT).pack(anchor="w", pady=(14, 0))
        tk.Label(brand, text="Private chats, groups and AI tools,\nover plain TCP sockets.", bg=BLUE, fg="#DBEAFE",
                 font=FONT, justify="left").pack(anchor="w", pady=(6, 30))
        for line in ("✦   Summaries, translation and mood", "📎   Share files and images", "💬   History and unread badges"):
            tk.Label(brand, text=line, bg=BLUE, fg="#FFFFFF", font=FONT, anchor="w").pack(fill="x", pady=5)

        form = ttk.Frame(self, style="White.TFrame", padding=(44, 40))
        form.pack(side="left", fill="both", expand=True)
        ttk.Label(form, text="Welcome", style="White.TLabel", font=HEAD_FONT).pack(anchor="w")
        ttk.Label(form, text="Connect to a chat server", style="White.TLabel", foreground=GRAY).pack(anchor="w")
        for label, var in (("Server host / IP", self.host), ("Port", self.port), ("Your name", self.name)):
            ttk.Label(form, text=label, style="White.TLabel", foreground=GRAY, font=SMALL_BOLD).pack(anchor="w", pady=(16, 5))
            entry = ttk.Entry(form, textvariable=var, width=30)
            entry.pack(fill="x")
            entry.bind("<Return>", lambda event: self.connect())
        self.name_entry = entry             # the last entry created is the name box
        self.status = ttk.Label(form, text="", style="White.TLabel", foreground=GRAY)
        self.status.pack(anchor="w", pady=(14, 0))
        ttk.Button(form, text="Connect", style="Primary.TButton", command=self.connect).pack(fill="x", pady=(8, 0))
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
                                       bg=SURFACE, fg=INK, padx=6, pady=8, relief="flat", highlightthickness=0,
                                       borderwidth=0, spacing2=3)
    # A thin modern scroll bar instead of the classic one (ScrolledText created its own: swap it).
    window.vbar.pack_forget()
    bar = ttk.Scrollbar(window.frame, orient="vertical", command=window.yview)
    bar.pack(side="right", fill="y")
    window.configure(yscrollcommand=bar.set)
    # Messages are laid out like Slack or Discord: an avatar, then the name and time, then the text indented
    # to line up under the name. `indent` is that avatar column's width (it grows with the display scaling).
    indent = round(40 * ui_scale(parent))        # = the avatar (30) plus its padding (2 x 5), see add_avatar
    window.indent = indent
    # Order matters: a tag created later wins when two tags set the same option (e.g. background).
    window.tag_configure("row_me", background="#F6F9FF")                # your own messages get a faint blue wash
    window.tag_configure("row_bot", background=VIOLET_LIGHT)            # assistant answers get a faint violet one
    window.tag_configure("header", spacing1=16)                         # air above each message
    window.tag_configure("body", lmargin1=indent, lmargin2=indent)      # the message text lines up under the name
    window.tag_configure("me", foreground=BLUE, font=BOLD)
    window.tag_configure("other", foreground=INK, font=BOLD)
    window.tag_configure("bot", foreground=VIOLET_DARK, font=BOLD)
    window.tag_configure("stamp", foreground="#94A3B8", font=SMALL)
    window.tag_configure("notice", foreground=GRAY, font=ITALIC, spacing1=8)
    window.tag_configure("note", foreground=GRAY, font=SMALL_ITALIC)
    window.tag_configure("file_line", foreground=INK, font=BOLD, lmargin1=indent, lmargin2=indent)
    window.tag_configure("divider", foreground="#94A3B8", font=SMALL, justify="center", spacing1=10, spacing3=4)
    # AI summaries get a tinted block so nobody mistakes them for a chat message.
    window.tag_configure("summary_title", foreground=VIOLET_DARK, background=VIOLET_TINT,
                         font=BOLD, lmargin1=16, lmargin2=16, rmargin=16, spacing1=14)
    window.tag_configure("summary", foreground=INK, background=VIOLET_TINT, lmargin1=16, lmargin2=16, rmargin=16)
    window.tag_configure("summary_model", foreground=GRAY, background=VIOLET_TINT,
                         font=SMALL_ITALIC, lmargin1=16, lmargin2=16, rmargin=16, spacing3=10)
    # Per-message AI controls: clickable text ("link"), the mood button, mood pills and result blocks.
    block = {"lmargin1": indent, "lmargin2": indent, "rmargin": 12}
    window.tag_configure("link", foreground=VIOLET, underline=False, font=SMALL_BOLD)
    window.tag_configure("mood_button", foreground=VIOLET, font=SMALL_BOLD)
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


def format_size(size):
    """A file size for people: "532 B", "12.3 KB" or "4.8 MB"."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def add_avatar(window, name, kind, *tags):
    """Put a round avatar at the start of a message header. It becomes one character of the chat text, so it
    carries the same tags as the header line (spacing, background). `kind` is "me", "other" or "bot"."""
    background = {"me": "#F6F9FF", "bot": VIOLET_LIGHT}.get(kind, SURFACE)
    avatar = make_avatar(window, name, background, size=30, color={"me": BLUE, "bot": VIOLET}.get(kind),
                         glyph="✦" if kind == "bot" else None)
    window.configure(state="normal")
    start = window.index("end-1c")
    window.window_create("end", window=avatar, padx=round(5 * ui_scale(window)), align="center")
    for tag in tags:
        if tag:
            window.tag_add(tag, start)
    window.configure(state="disabled")


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

    def __init__(self, parent, on_send, on_summarize, on_translate, on_mood, title="", subtitle="", on_file=None):
        super().__init__(parent, padding=(20, 16), style="White.TFrame")
        self.on_send = on_send              # function(text) -> True if the message was sent
        self.on_summarize = on_summarize    # function() -> True if SUMMARIZE was sent
        self.on_translate = on_translate    # function(ts, language code) -> True if the request was sent
        self.on_mood = on_mood              # function(ts) -> True if the request was sent
        self.on_file = on_file              # function(path) -> True if the file was sent (special 2)
        self.entries = {}                   # message number -> {"ts": ..., "row": tag of its background}
        self.by_ts = {}                     # message ts -> message number
        self.files = {}                     # message number -> (file name, base64 data) of a file we can save
        self.images = []                    # previews on screen: Tk draws an image only while Python holds it

        # Chat box: Enter or the Send button sends. Packed first (at the bottom) so it
        # never gets squeezed out when the window is small.
        bar = ttk.Frame(self, style="White.TFrame")
        bar.pack(side="bottom", fill="x", pady=(14, 0))
        if on_file is not None:
            ttk.Button(bar, text="📎 Attach", command=self.attach).pack(side="left", padx=(0, 10))
        self.entry = ttk.Entry(bar)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda event: self.send())
        ttk.Button(bar, text="Send  ➤", style="Primary.TButton", command=self.send).pack(side="left", padx=(10, 0))

        head = ttk.Frame(self, style="White.TFrame")
        head.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(head, text=title, style="White.TLabel", font=HEAD_FONT).pack(anchor="w")
        self.subtitle = ttk.Label(head, text=subtitle, style="White.TLabel", foreground=GRAY, font=SMALL)
        self.subtitle.pack(anchor="w", pady=(2, 0))

        # AI strip: everything AI that works on the whole room, in one soft violet bar.
        strip = tk.Frame(self, bg=VIOLET_LIGHT, padx=14, pady=9, highlightthickness=1, highlightbackground="#E9DDFF")
        strip.pack(side="top", fill="x", pady=(0, 10))
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
        self.window.tag_bind("file_save", "<Button-1>", self.on_save_click)

    def attach(self):
        """The 📎 button: pick a file or image and send it (special 2)."""
        path = filedialog.askopenfilename(parent=self, title="Send a file or image")
        if path:
            self.on_file(path)

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
        add_avatar(self.window, sender, "me" if mine else "other", "header", row)
        self.append(sender, row, "header", "me" if mine else "other")
        self.append(f"  {stamp}", row, "stamp")
        self.append("\t", row)                                  # the tab pushes the mood button to the right edge
        self.insert_slot("end", number, "mood", [("✦ Analyze mood", ("mood_button",))])
        self.append("\n", row)
        self.append(text + "\n", row, "body")
        if not mine:
            self.insert_slot("end", number, "tr", [("Translate", ("link", "body")), ("\n", ())])
        self.window.see("end")

    def add_file(self, sender, filename, size, data, ts, mine):
        """Special 2: append a file message: a header, a preview for PNG/GIF images, then the file's name,
        size and a "Save as…" link. `data` is the base64 text, or None for a file shown from history: the
        server keeps files there as metadata only (name, size, sender, time), never their bytes."""
        number = len(self.entries)
        row = "row_me" if mine else None
        self.entries[number] = {"ts": ts, "row": row}
        self.by_ts[ts] = number
        stamp = time.strftime("%H:%M", time.localtime(ts))
        add_avatar(self.window, sender, "me" if mine else "other", "header", row)
        self.append(sender, row, "header", "me" if mine else "other")
        self.append(f"  {stamp}\n", row, "stamp")
        if data is None:                    # from history: no bytes, so no preview and no Save link
            self.append(f"📎 {filename} ({format_size(size)})", row, "file_line")
            self.append(" — sent earlier, not stored on the server\n", row, "note")
            self.window.see("end")
            return
        if filename.lower().endswith(IMAGE_TYPES):
            self.add_preview(data, row)
        self.append(f"📎 {filename}", row, "file_line")
        self.append(f"  ·  {format_size(size)}", row, "stamp")
        self.files[number] = (filename, data)
        self.append("  ·  ", row, "stamp")
        self.insert_slot("end", number, "save", [("Save as…", ("link", "file_save"))])
        self.append("\n", row)
        self.window.see("end")

    def add_preview(self, data, row):
        """Draw an image inside the chat window, shrunk to fit. If Tk can't read it, show nothing here
        (the file still appears below as a normal file with a Save link)."""
        try:
            image = tk.PhotoImage(data=data)            # Tk decodes the base64 PNG/GIF itself
            shrink = -(-max(image.width(), image.height()) // PREVIEW_PX)    # whole-number divisor, rounded up
            if shrink > 1:
                image = image.subsample(shrink)
        except tk.TclError:
            return
        self.images.append(image)                       # keep a reference, or Tk would erase the picture
        self.window.configure(state="normal")
        start = self.window.index("end-1c")
        self.window.image_create("end", image=image, padx=4, pady=6)
        self.window.tag_add("body", start)              # line the picture up under the name, like the text
        if row:
            self.window.tag_add(row, start)
        self.window.insert("end", "\n", row)
        self.window.configure(state="disabled")

    def on_save_click(self, event):
        number = self.by_ts.get(self.slot_at(event, "save"))
        if number in self.files:
            self.save_file(*self.files[number])

    def save_file(self, filename, data):
        """Ask where to save, then write the decoded bytes there."""
        path = filedialog.asksaveasfilename(parent=self, title="Save file", initialfile=filename)
        if not path:
            return                                      # cancelled
        try:
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
        except OSError as e:
            messagebox.showerror("Cannot save", str(e), parent=self)

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

    def translation_failed(self, ts, reason=None):
        """Show an error block with a Try again link (the original text is unchanged).
        `reason` is the server's explanation when retrying won't help (e.g. the message is gone)."""
        why = f": {reason}" if reason else "."
        self.replace_slot(ts, "tr", [(f"Couldn't translate this message{why}  ", ("ai_error",)),
                                     ("Try again", ("ai_error", "link")), ("\n", ("ai_error",))])

    # Sentiment (magga) ------------------------------------------------

    def analyze(self, ts):
        """The Analyze mood button was clicked: ask for it, and show "Analyzing…" if the request went out."""
        if self.on_mood(ts):
            self.show_mood_pending(ts)

    def show_mood_pending(self, ts):
        self.replace_slot(ts, "mood", [("Analyzing…", ("mood_pending",))])

    def set_mood(self, ts, label, model=None):
        """Replace the button with a pill. model "offline" = guessed from a word list, so the pill says so."""
        if label in MOODS:
            offline = " · offline" if model == "offline" else ""
            self.replace_slot(ts, "mood", [(f" ● {MOODS[label][0]}{offline} ", (f"mood_{label}",))])

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

    def add_divider(self):
        """Special 3: a thin line under the history, so new messages are easy to tell apart."""
        self.append("──────────  earlier messages  ──────────\n", "divider")

    def append(self, text, *tags):
        append_text(self.window, text, *tags)


class BotTab(ttk.Frame):
    """The AI Assistant tab (special 4): a private conversation with the chatbot.

    It is not a room on the server. Like RoomTab it only draws things; ChatApp does the networking and
    calls add_reply / failed when the answer arrives.
    """

    SUGGESTIONS = [("Explain TCP framing", False), ("Explain my last chat", True), ("Help me write a reply", False)]

    def __init__(self, parent, on_ask, on_clear):
        super().__init__(parent, padding=(20, 16), style="White.TFrame")
        self.on_ask = on_ask                # function(text, use_last_chat) -> True if the question was sent
        self.on_clear = on_clear            # function(): tell the server to forget the conversation
        self.waiting = False                # True from "Thinking…" until the answer arrives

        bar = ttk.Frame(self, style="White.TFrame")
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        self.entry = ttk.Entry(bar)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda event: self.ask())
        self.ask_button = ttk.Button(bar, text="Ask  ✦", style="AI.TButton", command=self.ask)
        self.ask_button.pack(side="left", padx=(10, 0))

        chips = ttk.Frame(self, style="White.TFrame")
        chips.pack(side="bottom", fill="x", pady=(12, 0))
        self.chips = []
        for text, use_last_chat in self.SUGGESTIONS:
            chip = ttk.Button(chips, text=text, style="Chip.TButton", command=lambda t=text, c=use_last_chat: self.ask(t, c))
            chip.pack(side="left", padx=(0, 8))
            self.chips.append(chip)

        head = tk.Frame(self, bg=VIOLET_LIGHT, padx=16, pady=12, highlightthickness=1, highlightbackground="#E9DDFF")
        head.pack(side="top", fill="x", pady=(0, 10))
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
        add_avatar(self.window, "you", "me", "header", "row_me")
        self.append("you", "header", "row_me", "me")
        self.append(f"  {time.strftime('%H:%M')}\n", "row_me", "stamp")
        self.append(text + "\n", "row_me", "body")

    def show_thinking(self):
        """Show "Thinking…" under the assistant's name and block new questions until the answer arrives."""
        self.set_waiting(True)
        add_avatar(self.window, "AI", "bot", "header", "row_bot")
        self.append("AI Assistant", "header", "row_bot", "bot")
        self.append(f"  {time.strftime('%H:%M')}\n", "row_bot", "stamp")
        self.append("Thinking…\n", "row_bot", "thinking", "body")

    def add_reply(self, text, model):
        """Replace "Thinking…" with the assistant's answer."""
        self.replace_thinking([(text.strip() + "\n", ("row_bot", "body"))] +
                              ([("Offline reply · no AI was used\n", ("row_bot", "note", "body"))] if model == "offline" else []))

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


def fit_to_screen(root):
    """Size the main window: 1100x700 at 100 % Windows scaling, grown with the scaling (1.25x at 125 %),
    but never bigger than the screen minus the taskbar and title bar, so on a small laptop screen
    (e.g. 1366x768 at 125 %) nothing ends up hidden below the bottom edge."""
    scale = float(root.tk.call("tk", "scaling")) / (96 / 72)    # Tk's points-to-pixels factor; 1.0 at 100 %
    max_width = root.winfo_screenwidth() - round(40 * scale)
    max_height = root.winfo_screenheight() - round(110 * scale)  # taskbar + title bar + a small margin
    width, height = min(round(1100 * scale), max_width), min(round(700 * scale), max_height)
    root.geometry(f"{width}x{height}")
    # The minimum keeps the AI tools strip (Translate to + Summarize chat) fully visible next to the sidebar.
    root.minsize(min(round(980 * scale), width), min(round(480 * scale), height))


class ChatApp:
    """Main window after login: online users, groups with members, and one tab per chat room."""

    def __init__(self, root, sock, name):
        apply_theme(root)
        fit_to_screen(root)
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
        self.waiting = {}                   # room id -> live messages that arrived before its HISTORY (special 3)
        self.unread = {}                    # room id -> number of unread messages from other people (special 3)
        self.handlers = {USERS: self.on_users, GROUPS: self.on_groups, MSG: self.on_msg, FILE: self.on_msg,
                         SUMMARY: self.on_summary, TRANSLATION: self.on_translation,SENTIMENT_RESULT: self.on_sentiment, BOT_REPLY: self.on_bot_reply,
                         ERROR: self.on_error, HISTORY: self.on_history}

        self.build_widgets()
        root.title(f"Socket Chat: {name}")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        threading.Thread(target=self.receive_loop, daemon=True).start()
        self.poll_inbox()

    def build_widgets(self):
        """Left: users and groups. Right: a notebook with one tab per room."""
        # Treeview rows don't grow with the font on high-DPI screens, which clips letters like "y".
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        ttk.Style().configure("Treeview", rowheight=line + 10)
        panes = ttk.PanedWindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=14, pady=14)

        side = ttk.Frame(panes, style="White.TFrame", padding=(18, 18), width=round(364 * ui_scale(self.root)))
        side.pack_propagate(False)          # a fixed width: the long section titles must not widen the sidebar
        # Profile header: a round avatar with your initial, your name and the connection status.
        header = ttk.Frame(side, style="White.TFrame")
        header.pack(fill="x", pady=(0, 18))
        make_avatar(header, self.me, SURFACE, size=46, color=BLUE).pack(side="left", padx=(0, 12))
        who = ttk.Frame(header, style="White.TFrame")
        who.pack(side="left")
        ttk.Label(who, text=self.me, style="White.TLabel", font=HEAD_FONT).pack(anchor="w")
        self.status_label = ttk.Label(who, text=f"● Connected · {self.server_address()}", style="White.TLabel",
                                      foreground="#16A34A", font=SMALL)
        self.status_label.pack(anchor="w")

        ttk.Label(side, text="ASSISTANT (DOUBLE-CLICK TO OPEN)", style="Section.TLabel").pack(anchor="w")
        self.assistant_list = make_list(side, height=1)
        self.assistant_list.insert("end", "✦ AI Assistant  [BOT]")
        self.assistant_list.pack(fill="x", pady=(4, 16))
        self.assistant_list.bind("<Double-Button-1>", lambda event: self.open_bot())

        self.users_label = ttk.Label(side, text="ONLINE USERS (DOUBLE-CLICK TO CHAT)", style="Section.TLabel")
        self.users_label.pack(anchor="w")
        self.user_list = make_list(side, height=6)
        self.user_list.pack(fill="x", pady=(4, 16))
        self.user_list.bind("<Double-Button-1>", self.on_user_double_click)

        # Group buttons: packed at the bottom BEFORE the tree, like the chat box in RoomTab. When the window is
        # short, pack takes the missing height from the widgets packed last, so the tree shrinks, not the buttons.
        buttons = ttk.Frame(side, style="White.TFrame")
        buttons.pack(side="bottom", fill="x", pady=(10, 0))
        # Three equal columns (uniform), so no button is squeezed out however long its label is.
        for column, (label, style, command) in enumerate((("+ Create", "Primary.TButton", self.create_group),
                                                          ("Join", "TButton", self.join_group),
                                                          ("Leave", "Danger.TButton", self.leave_group))):
            ttk.Button(buttons, text=label, style=style, command=command).grid(
                row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0))
            buttons.columnconfigure(column, weight=1, uniform="group_buttons")
        ttk.Label(side, text="GROUPS AND MEMBERS", style="Section.TLabel").pack(anchor="w")
        self.group_tree = ttk.Treeview(side, show="tree", height=6, selectmode="browse")   # grows to fill the rest
        self.group_tree.pack(fill="both", expand=True, pady=(4, 4))
        self.group_tree.bind("<Double-Button-1>", self.on_group_double_click)
        panes.add(side, weight=0)

        self.notebook = ttk.Notebook(panes)
        welcome = ttk.Frame(self.notebook, style="White.TFrame", padding=36)
        ttk.Label(welcome, text=f"Welcome, {self.me}", style="White.TLabel", font=TITLE_FONT).pack(anchor="w")
        ttk.Label(welcome, text="Here is how to get started", style="White.TLabel", foreground=GRAY).pack(
            anchor="w", pady=(2, 24))
        for glyph, color, title, detail in WELCOME_TIPS:       # one card per tip: a coloured icon, a title, a line of help
            card = tk.Frame(welcome, bg=BG, padx=18, pady=11)
            card.pack(fill="x", pady=5)
            make_avatar(card, title, BG, size=40, color=color, glyph=glyph).pack(side="left", padx=(0, 16))
            words = tk.Frame(card, bg=BG)
            words.pack(side="left", fill="x", expand=True)
            tk.Label(words, text=title, bg=BG, fg=INK, font=BOLD, anchor="w").pack(fill="x")
            text_label = tk.Label(words, text=detail, bg=BG, fg=GRAY, font=FONT, anchor="w", justify="left")
            text_label.pack(fill="x")
            # Wrap the help text to the card's width, so it is never cut off however wide the window is.
            card.bind("<Configure>", lambda event, label=text_label: label.configure(wraplength=max(event.width - 130, 200)))
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
            self.user_list.insert("end", self.user_label(user))
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
            # The item id is the room id, e.g. "group:netlab", so it can't clash with Tk's own ids.
            parent = self.group_tree.insert("", "end", iid=group_room(group), text=self.group_label(group), open=True)
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
        if room in self.waiting:            # the tab's history isn't here yet: show this after it
            self.waiting[room].append(msg)
        else:
            self.show_message(tab, msg)
        if msg["from"] != self.me and not self.is_viewing(room):
            self.unread[room] = self.unread.get(room, 0) + 1
            self.show_unread()

    def on_summary(self, msg):
        """Special 1: the server's summary, sent only to us. Shown in that room's tab."""
        tab = self.rooms.get(msg["room"])
        if tab is not None:                 # the tab may have closed (e.g. we left the group)
            tab.add_summary(msg["text"], msg["model"])

    def on_translation(self, msg):
        """Translation: the server's answer, sent only to us. Replaces "Translating…" under that message."""
        tab = self.rooms.get(msg["room"])
        if tab is not None:                 # the tab may have closed meanwhile
            tab.add_translation(msg["ts"], msg["text"], msg["source"], msg["to"])
    def on_sentiment(self, msg):
        """Sentiment: the server's answer, sent only to us. Replaces "Analyzing…" with a mood pill."""
        tab = self.rooms.get(msg["room"])
        if tab is not None:
            tab.set_mood(msg["ts"], msg["label"], msg.get("model"))
    def on_bot_reply(self, msg):
        """Special 4: the chatbot's answer, sent only to us. Replaces "Thinking…" in the assistant tab."""
        if self.bot_tab is not None:
            self.bot_tab.add_reply(msg["text"], msg["model"])

    def on_error(self, msg):
        """The server refused a request (e.g. GROUP_EXISTS, NO_SUCH_USER)."""
        if msg.get("request") == TRANSLATE:     # a translation failed: show it under that message, no pop-up
            tab = self.rooms.get(msg.get("room"))
            if tab is not None:
                # A temporary failure just offers "Try again"; for any other refusal say why.
                tab.translation_failed(msg.get("ts"), None if msg.get("code") == AI_FAILED else msg.get("message"))
            return
        if msg.get("request") == SENTIMENT:     # a mood analysis failed: the button comes back
            tab = self.rooms.get(msg.get("room"))
            if tab is not None:
                tab.mood_failed(msg.get("ts"))
            return
        if msg.get("request") == HISTORY:   # a history couldn't load: just show the live messages we held back
            for room in list(self.waiting):
                self.on_history({"room": room, "messages": []})
            return
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
                title=title, subtitle=subtitle,
                on_file=lambda path: self.send_file(room, path))
            self.notebook.add(self.rooms[room], text=title)
            self.request_history(room)
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
        self.waiting.pop(room, None)
        if self.unread.pop(room, None):
            self.show_unread()

    def on_tab_changed(self, event):
        """Remember the last chat room the user looked at (the chatbot's "Explain my last chat" uses it),
        and mark its messages as read."""
        current = self.notebook.nametowidget(self.notebook.select())
        for room, tab in self.rooms.items():
            if tab is current:
                self.last_room = room
                if self.unread.pop(room, None):
                    self.show_unread()

    # ------------------------------------------------------------------
    # Special 3: chat history and unread badges
    # ------------------------------------------------------------------

    def request_history(self, room):
        """A room tab just opened: ask the server for its past messages. Live messages that arrive
        before the answer wait in self.waiting, so history always comes first and nothing is shown twice."""
        self.waiting[room] = []
        if not self.send(HISTORY, room=room):
            del self.waiting[room]

    def on_history(self, msg):
        """The server's HISTORY: past messages first, then a divider, then the live messages that waited."""
        room = msg["room"]
        tab, waiting = self.rooms.get(room), self.waiting.pop(room, [])
        if tab is None:
            return                          # the tab was closed meanwhile
        # Live messages that waited are also in the history (the server saved them before sending them
        # to us). They stay below the divider, as new messages; every message is shown once (keyed by ts).
        waiting_ts = {m["ts"] for m in waiting}
        past = [m for m in msg["messages"] if m["ts"] not in waiting_ts and m["ts"] not in tab.by_ts]
        for m in past:
            self.show_message(tab, m)
        if past:
            tab.add_divider()
        for m in waiting:
            if m["ts"] not in tab.by_ts:
                self.show_message(tab, m)

    def show_message(self, tab, msg):
        """Draw one stored or live message, which is either a chat message (MSG) or a file (FILE)."""
        mine = msg["from"] == self.me
        if msg.get("type") == FILE:
            tab.add_file(msg["from"], msg["filename"], msg["size"], msg.get("data"), msg["ts"], mine)   # no "data" in history
        else:
            tab.add_message(msg["from"], msg["text"], msg["ts"], mine)

    def send_file(self, room, path):
        """Special 2: read a file from disk and send it to a room. Returns True if it was sent.

        The server only accepts text in JSON, so the bytes go as base64. The size limit is checked here
        too, so a file that is too big never leaves this computer (the server checks it again)."""
        try:
            size = os.path.getsize(path)
            if size == 0:
                messagebox.showinfo("Empty file", "That file is empty, so there is nothing to send.", parent=self.root)
                return False
            if size > MAX_FILE_BYTES:
                messagebox.showerror("File too large", f"{os.path.basename(path)} is {format_size(size)}. "
                                     f"The limit is {format_size(MAX_FILE_BYTES)}.", parent=self.root)
                return False
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            messagebox.showerror("Cannot read file", str(e), parent=self.root)
            return False
        data = base64.b64encode(raw).decode("ascii")
        return self.send(FILE, room=room, filename=os.path.basename(path), size=size, data=data)

    def is_viewing(self, room):
        """True if this room's tab is the one on screen right now."""
        tab = self.rooms.get(room)
        return tab is not None and self.notebook.select() == str(tab)

    def show_unread(self):
        """Redraw every unread badge: "bob (3)" in the user list, "netlab (2)" in the groups tree, and tab titles."""
        selection = self.user_list.curselection()
        self.user_list.delete(0, "end")
        for user in self.users:
            self.user_list.insert("end", self.user_label(user))
        for index in selection:
            self.user_list.selection_set(index)
        for group in self.groups:
            if self.group_tree.exists(group_room(group)):
                self.group_tree.item(group_room(group), text=self.group_label(group))
        for room, tab in self.rooms.items():
            title = f"@{dm_partner(room, self.me)}" if room.startswith("dm:") else f"#{room.removeprefix('group:')}"
            self.notebook.tab(tab, text=title + self.badge(room))

    def badge(self, room):
        """" (3)" if the room has 3 unread messages, else ""."""
        count = self.unread.get(room, 0)
        return f" ({count})" if count else ""

    def user_label(self, user):
        """A row of the online list: "alice (you)", or "bob (3)" when our private chat has 3 unread."""
        return f"{user} (you)" if user == self.me else user + self.badge(dm_room(self.me, user))

    def group_label(self, group):
        """A group row of the tree, e.g. "netlab (2) · 3 members · ✓ joined" (2 = unread messages)."""
        info = self.groups[group]
        count = len(info["members"])
        label = f"{group}{self.badge(group_room(group))} · {count} member{'s' if count != 1 else ''}"
        return label + (" · ✓ joined" if self.me in info["members"] else "")

    # ------------------------------------------------------------------
    # AI feature plug-in points. Each returns True if the request was sent, and the tab then shows
    # its "working" state. The feature's owner replaces the stub body with self.send(...) and adds an
    # on_<reply> handler that calls the tab's result methods (see RoomTab and BotTab).
    # ------------------------------------------------------------------

    def translate_message(self, room, ts, to):
        """Translation: the Translate link under the message `ts` of `room` was clicked. The server
        looks the message up by room + ts, so we send no text. The answer arrives as TRANSLATION."""
        return self.send(TRANSLATE, room=room, ts=ts, to=to)

    def analyze_mood(self, room, ts):
        """Sentiment: send only the message's id; the server reads the text itself."""
        return self.send(SENTIMENT, room=room, ts=ts)

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
    login.pack(padx=32, pady=32)            # the white card floats on the window's soft grey
    root.mainloop()


if __name__ == "__main__":
    main()
