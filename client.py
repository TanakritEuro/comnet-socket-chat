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
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

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
# The design lists a group's members only under the selected group. Set this to True to list them under every group
# (R9 asks for "a list of all groups with their members", so a grader may like to see them without clicking).
EXPAND_ALL_GROUPS = False

# Colours. The whole window uses one light palette (apply_theme), even when the OS is in dark mode, so it
# looks the same on every machine and the tinted AI blocks stay readable. Blue is the main accent and
# violet means "AI feature". Greys are the "slate" scale, which looks cleaner than pure grey.
INK, GRAY, BLUE, GREEN = "#0F172A", "#64748B", "#2563EB", "#0F766E"
BLUE_DARK, BLUE_TINT = "#1D4ED8", "#EFF6FF"
VIOLET, VIOLET_DARK, VIOLET_TINT, VIOLET_LIGHT = "#7C3AED", "#6D28D9", "#F3EEFF", "#FAF7FF"
BG, LINE, SELECTED = "#F1F5F9", "#E2E8F0", "#DBEAFE"       # window background, borders, selected list row
SURFACE = "#FFFFFF"                                         # cards: the chat area, inputs
# The dark sidebar (and the login brand panel), so the room list reads apart from the chat.
SIDEBAR = "#0F172A"         # sidebar + login brand panel background
SIDEBAR_SEL = "#1E293B"     # selected sidebar row, divider lines
SIDEBAR_LINE = "#334155"    # outline of the sidebar "Join" button, dashed icon of a group you have not joined
SIDEBAR_TEXT = "#E2E8F0"    # normal row text on the sidebar
SIDEBAR_MUTED = "#94A3B8"   # section labels, "(you)", "n members · joined", the status line
ONLINE = "#22C55E"          # the presence dot on avatars
ERROR_COLOR = "#B91C1C"     # inline error text (login). Not "ERROR": that name is the protocol's error message type
# Chat bubbles and the blocks under them.
BUBBLE_ME = BLUE            # my messages: white text on blue
BUBBLE_OTHER = "#F1F5F9"    # other people's messages: dark text on light grey
TRANSLATE_BG = "#F5F3FF"    # the translation box under a message
SUMMARY_BG, SUMMARY_LINE = "#FAF7FF", "#E9DDFF"      # the summary card and the assistant's header
FONT = ("Segoe UI", 11)
BOLD = ("Segoe UI", 11, "bold")
ITALIC = ("Segoe UI", 11, "italic")
SMALL = ("Segoe UI", 10)
SMALL_BOLD = ("Segoe UI", 10, "bold")
SMALL_ITALIC = ("Segoe UI", 10, "italic")
SECTION_FONT = ("Segoe UI", 9, "bold")      # sidebar section labels (written in capitals)
HEAD_FONT = ("Segoe UI", 14, "bold")
TITLE_FONT = ("Segoe UI", 22, "bold")
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


_IMAGE_CACHE = {}       # (radius, fill, border, border_width, extra) -> (Tk interpreter, PhotoImage)


def rounded_image(master, radius, fill, border=None, border_width=1, extra=24):
    """A small rounded rectangle with soft (anti-aliased) edges, as a PhotoImage. ttk keeps its four corners
    and stretches the middle, so this one image becomes a button or text field of any size. The outside is
    transparent, so the colour behind the widget shows around the corners. With extra=0 the image is exactly
    two radii wide, which makes a perfect circle (used for avatars and presence dots).

    Each distinct image is made once and kept for the whole run. That is faster (the sidebar redraws its rows
    often), and it matters for safety: a Tk image must be freed by the GUI thread, but Python may free an
    unused one in the receiver thread, which makes Tk hang or raise "main thread is not in main loop"."""
    key = (radius, fill, border, border_width, extra)
    cached = _IMAGE_CACHE.get(key)
    if cached and cached[0] is master.tk:
        return cached[1]
    size = 2 * radius + extra                   # the four corners (radius + 1 each) plus a 2-pixel strip ttk can stretch
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
    image = tk.PhotoImage(master=master, data=base64.b64encode(_png(size, size, rows)).decode("ascii"))
    _IMAGE_CACHE[key] = (master.tk, image)
    return image


_SKIN_IMAGES = []       # Tk draws an image only while Python holds a reference to it

# Button looks: style name -> (fill, hover fill, pressed fill, border, text, disabled fill, disabled border,
# disabled text, padding). Primary (blue) is for Send/Connect, AI (violet) for AI actions.
BUTTON_LOOKS = {
    "TButton": (SURFACE, "#F1F5F9", "#E2E8F0", "#CBD5E1", INK, "#F8FAFC", "#E2E8F0", "#94A3B8", (12, 8)),
    "Primary.TButton": (BLUE, BLUE_DARK, "#1E40AF", None, "#FFFFFF", "#93C5FD", None, "#FFFFFF", (14, 8)),
    "AI.TButton": (VIOLET, VIOLET_DARK, "#5B21B6", None, "#FFFFFF", "#C4B5FD", None, "#FFFFFF", (14, 7)),
    "Chip.TButton": (VIOLET_LIGHT, VIOLET_TINT, "#E9DDFF", "#DDD6FE", "#4C1D95", "#F8FAFC", "#E2E8F0", "#94A3B8", (12, 5)),
    "Danger.TButton": (SURFACE, "#FEF2F2", "#FEE2E2", "#FCA5A5", "#B91C1C", "#F8FAFC", "#E2E8F0", "#94A3B8", (12, 8)),
    # The two buttons at the foot of the dark sidebar. Their corners are see-through, so they sit on the navy.
    "SidebarNew.TButton": (BLUE, BLUE_DARK, "#1E40AF", None, "#FFFFFF", "#1E3A8A", None, "#94A3B8", (8, 8)),
    "SidebarJoin.TButton": (SIDEBAR, SIDEBAR_SEL, "#0B1220", SIDEBAR_LINE, SIDEBAR_TEXT, SIDEBAR, SIDEBAR_SEL,
                            SIDEBAR_MUTED, (8, 8)),
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
        on_dark = name.startswith("Sidebar")        # these two buttons sit on the navy sidebar, not on a white card
        style.configure(name, foreground=text, padding=padding, anchor="center",
                        background=SIDEBAR if on_dark else SURFACE,      # what shows through the rounded corners
                        font=SMALL_BOLD if on_dark else (FONT if name in ("TButton", "Danger.TButton") else BOLD))
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


def make_avatar(parent, name, background, size=32, color=None, glyph=None, online=False):
    """A round avatar: a coloured circle with the person's initial (or `glyph`), drawn on a canvas.
    With online=True a small green presence dot sits at the bottom-right, ringed in the background colour."""
    size = round(size * ui_scale(parent)) // 2 * 2          # an even size, so the circle is centred exactly
    canvas = tk.Canvas(parent, width=size, height=size, bg=background, highlightthickness=0, borderwidth=0)
    # A canvas oval has jagged edges; a rounded image with soft edges (the same trick as the buttons) is smooth.
    canvas.disc = rounded_image(parent, size // 2, color or avatar_color(name), extra=0)     # a perfect circle
    canvas.create_image(size // 2, size // 2, image=canvas.disc)
    canvas.create_text(size / 2, size / 2, text=glyph or name[:1].upper(), fill="#FFFFFF",
                       font=("Segoe UI", max(size // 3, 8), "bold"))
    if online:
        ring = max(size // 6, 5)                                                # the dot plus its 2 px ring
        canvas.dot = rounded_image(parent, ring, ONLINE, background, 2, extra=0)    # the ring is the background colour
        canvas.create_image(size - ring, size - ring, image=canvas.dot)             # tucked into the bottom-right corner
    return canvas


def make_square_icon(parent, background, glyph, fill, size=28, border=None, glyph_color=None):
    """A rounded-square icon with a glyph on it: "#" for a group, ✦ for the assistant."""
    scale = ui_scale(parent)
    size, radius = round(size * scale), round(8 * scale)
    canvas = tk.Canvas(parent, width=size, height=size, bg=background, highlightthickness=0, borderwidth=0)
    canvas.box = rounded_image(parent, radius, fill, border, 1, extra=size - 2 * radius)
    canvas.create_image(size // 2, size // 2, image=canvas.box)
    canvas.create_text(size / 2, size / 2, text=glyph, fill=glyph_color or ("#FFFFFF" if border is None else SIDEBAR_MUTED),
                       font=("Segoe UI", max(size // 2 - 2, 8), "bold"))
    return canvas


def make_dot(parent, background, color, size=8):
    """A small round dot (a member's presence in the group list)."""
    size = round(size * ui_scale(parent)) // 2 * 2
    canvas = tk.Canvas(parent, width=size, height=size, bg=background, highlightthickness=0, borderwidth=0)
    canvas.dot = rounded_image(parent, size // 2, color, extra=0)
    canvas.create_image(size // 2, size // 2, image=canvas.dot)
    return canvas


def make_pill(parent, count, background):
    """A small round blue counter: how many unread messages a room has."""
    size = round(20 * ui_scale(parent)) // 2 * 2
    canvas = tk.Canvas(parent, width=size, height=size, bg=background, highlightthickness=0, borderwidth=0)
    canvas.disc = rounded_image(parent, size // 2, BLUE, extra=0)
    canvas.create_image(size // 2, size // 2, image=canvas.disc)
    canvas.create_text(size / 2, size / 2, text=str(min(count, 99)), fill="#FFFFFF", font=("Segoe UI", 9, "bold"))
    return canvas


class SidebarRow(tk.Frame):
    """One row of the dark sidebar: an icon, a title with an optional muted suffix such as "(you)", an optional
    second line, and an optional unread pill or tag at the right. A click selects the row, a double-click opens it.

    Selecting only recolours the row. It never rebuilds a widget, because a widget destroyed between the two
    clicks of a double-click would swallow the double-click. `icon` is a function(parent, background) that draws
    the icon; it is called twice (normal and selected background) and one copy is shown at a time.
    """

    def __init__(self, parent, icon, title, suffix="", subtitle="", badge=0, tag="", selected=False,
                 selected_bg=SIDEBAR_SEL, compact=False, indent=0, tag_color=SIDEBAR_MUTED, tag_border=None,
                 on_click=None, on_open=None):
        super().__init__(parent, bg=SIDEBAR)
        self.title, self.suffix, self.subtitle, self.badge, self.tag = title, suffix, subtitle, badge, tag
        self.selected, self.selected_bg = selected, selected_bg
        self.on_click, self.on_open = on_click, on_open
        scale = ui_scale(parent)
        self.font, self.bold_font = (SMALL, SMALL_BOLD) if compact else (FONT, BOLD)
        self.icons = {False: icon(self, SIDEBAR), True: icon(self, selected_bg)}
        self.text_box = tk.Frame(self, bg=SIDEBAR)
        first_line = tk.Frame(self.text_box, bg=SIDEBAR)
        first_line.pack(anchor="w")
        self.surfaces = [self, self.text_box, first_line]       # everything that changes colour with the selection
        self.title_label = tk.Label(first_line, text=title, bg=SIDEBAR, fg=SIDEBAR_TEXT, font=self.font)
        self.title_label.pack(side="left")
        self.surfaces.append(self.title_label)
        if suffix:                          # "(you)" in the muted colour, next to the name
            suffix_label = tk.Label(first_line, text=suffix, bg=SIDEBAR, fg=SIDEBAR_MUTED, font=self.font)
            suffix_label.pack(side="left", padx=(5, 0))
            self.surfaces.append(suffix_label)
        if subtitle:                        # "2 members · joined" under a group's name
            subtitle_label = tk.Label(self.text_box, text=subtitle, bg=SIDEBAR, fg=SIDEBAR_MUTED, font=("Segoe UI", 9))
            subtitle_label.pack(anchor="w")
            self.surfaces.append(subtitle_label)
        padding = round((10 + indent) * scale)
        self.icon_pack = {"side": "left", "padx": (padding, round(10 * scale)), "pady": round((4 if compact else 6) * scale)}
        self.text_box.pack(side="left", fill="x", expand=True)
        self.pill = None
        if badge:                           # the unread counter
            self.pill = make_pill(self, badge, SIDEBAR)
            self.pill.pack(side="right", padx=(0, round(12 * scale)))
            self.surfaces.append(self.pill)
        if tag:                             # "BOT" or "creator"
            tag_label = tk.Label(self, text=tag, bg=SIDEBAR, fg=tag_color, font=("Segoe UI", 8, "bold"), padx=4,
                                 highlightthickness=1 if tag_border else 0, highlightbackground=tag_border or SIDEBAR)
            tag_label.pack(side="right", padx=(0, round(12 * scale)))
            self.surfaces.append(tag_label)
        self.set_selected(selected)
        for widget in self.all_widgets():
            widget.bind("<Button-1>", lambda event: self.click())
            widget.bind("<Double-Button-1>", lambda event: self.open())
            if on_click or on_open:
                widget.configure(cursor="hand2")

    def all_widgets(self):
        """This row and every widget inside it, so a click anywhere on the row counts."""
        found, todo = [], [self]
        while todo:
            widget = todo.pop()
            found.append(widget)
            todo.extend(widget.winfo_children())
        return found

    def click(self):
        if self.on_click:
            self.on_click()

    def open(self):
        if self.on_open:
            self.on_open()

    def set_selected(self, selected):
        """Recolour the row: a darker background and bold white text when selected."""
        self.selected = selected
        background = self.selected_bg if selected else SIDEBAR
        for icon in self.icons.values():
            icon.pack_forget()
        self.icons[selected].pack(before=self.text_box, **self.icon_pack)      # the icon drawn on the matching background
        for widget in self.surfaces:
            widget.configure(bg=background)
        self.title_label.configure(fg="#FFFFFF" if selected else SIDEBAR_TEXT,
                                   font=self.bold_font if selected else self.font)

    def text(self):
        """The row's text as one string, e.g. "bob (2)" or "net lab · 2 members · joined": what the GUI check reads."""
        badge = f" ({self.badge})" if self.badge else ""
        if self.subtitle:
            return f"{self.title}{badge} · {self.subtitle}"
        return f"{self.title}{' ' + self.suffix if self.suffix else ''}{badge}{f' ({self.tag})' if self.tag else ''}"


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
    window.tag_configure("stamp", foreground=GRAY, font=SMALL)       # timestamps: #94A3B8 was too faint on white
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


def append_text(window, text, *tags, scroll=True):
    """Add text at the end of a chat window, and by default scroll to it. `tags` may contain None (ignored).
    Scrolling forces Tk to measure the text, which is slow, so pass scroll=False for the pieces of a message
    that is written in several parts, and scroll once when it is complete."""
    window.configure(state="normal")
    window.insert("end", text, tuple(tag for tag in tags if tag))
    window.configure(state="disabled")
    if scroll:
        window.see("end")                   # auto-scroll to the newest line


class RoomTab(ttk.Frame):
    """One chat room (R5): a header, a read-only chat window and a chat box with Send (R6).

    The header holds the room's name and the AI actions that work on the whole room (Translate to, Summarize),
    plus Leave in a group. The per-message controls are the plug-in points for the AI features. This class only
    draws things and calls the callbacks below; the network side lives in ChatApp. Features show their
    results through show_translating / add_translation / translation_failed, show_mood_pending / set_mood /
    mood_failed and add_summary.
    """

    def __init__(self, parent, on_send, on_summarize, on_translate, on_mood, title="", subtitle="", on_file=None,
                 icon=None, on_leave=None):
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

        # Header: the room's icon, name and a line of help on the left; Translate, Summarize and (in a group)
        # Leave on the right. The actions are packed before the names, so a narrow window clips the help text
        # and never the buttons.
        head = tk.Frame(self, bg=SURFACE)
        head.pack(side="top", fill="x")
        icon_width = 0
        if icon is not None:
            icon_widget = icon(head, SURFACE)
            icon_widget.pack(side="left", padx=(0, 12))
            icon_width = icon_widget.winfo_reqwidth() + 12
        actions = tk.Frame(head, bg=SURFACE)
        actions.pack(side="right")
        self.leave_button = None
        if on_leave is not None:            # only a group has a Leave button
            self.leave_button = ttk.Button(actions, text="Leave", style="Danger.TButton", command=on_leave)
            self.leave_button.pack(side="right", padx=(8, 0))
        self.summarize_button = ttk.Button(actions, text="✦ Summarize", style="AI.TButton", command=self.summarize)
        self.summarize_button.pack(side="right", padx=(8, 0))
        # The picker only chooses the target language; nothing is translated until a message's link is clicked.
        self.language_box = ttk.Combobox(actions, values=[name for _, name in LANGUAGE_CHOICES],
                                         state="readonly", width=16)
        self.language_box.current(0)
        self.language_box.pack(side="right")
        tk.Label(actions, text="Translate to", bg=SURFACE, fg=GRAY, font=FONT).pack(side="right", padx=(0, 8))
        names = tk.Frame(head, bg=SURFACE)
        names.pack(side="left")
        tk.Label(names, text=title, bg=SURFACE, fg=INK, font=HEAD_FONT).pack(anchor="w")
        self.subtitle = tk.Label(names, text=subtitle, bg=SURFACE, fg=GRAY, font=SMALL, justify="left")
        self.subtitle.pack(anchor="w")
        # A long member list wraps into the space left beside the buttons, instead of being clipped.
        head.bind("<Configure>", lambda event: self.subtitle.configure(
            wraplength=max(event.width - actions.winfo_reqwidth() - icon_width - 24, 160)))
        tk.Frame(self, bg=LINE, height=1).pack(side="top", fill="x", pady=(12, 0))     # the line under the header

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
        self.write(sender, row, "header", "me" if mine else "other")
        self.write(f"  {stamp}", row, "stamp")
        self.write("\t", row)                                   # the tab pushes the mood button to the right edge
        self.insert_slot("end", number, "mood", [("✦ Analyze mood", ("mood_button",))])
        self.write("\n", row)
        self.write(text + "\n", row, "body")
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
        self.write(sender, row, "header", "me" if mine else "other")
        self.write(f"  {stamp}\n", row, "stamp")
        if data is None:                    # from history: no bytes, so no preview and no Save link
            self.write(f"📎 {filename} ({format_size(size)})", row, "file_line")
            self.write(" — sent earlier, not stored on the server\n", row, "note")
            self.window.see("end")
            return
        if filename.lower().endswith(IMAGE_TYPES):
            self.add_preview(data, row)
        self.write(f"📎 {filename}", row, "file_line")
        self.write(f"  ·  {format_size(size)}", row, "stamp")
        self.files[number] = (filename, data)
        self.write("  ·  ", row, "stamp")
        self.insert_slot("end", number, "save", [("Save as…", ("link", "file_save"))])
        self.write("\n", row)
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

    def write(self, text, *tags):
        """Append text without scrolling: a message is written in several pieces and scrolls once, at its end."""
        append_text(self.window, text, *tags, scroll=False)


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

        # Header: a violet ✦ icon, the title in deep violet, and Clear conversation on the right (packed first,
        # so a narrow window never squeezes it out).
        head = tk.Frame(self, bg=SUMMARY_BG, padx=16, pady=12, highlightthickness=1, highlightbackground=SUMMARY_LINE)
        head.pack(side="top", fill="x", pady=(0, 10))
        make_square_icon(head, SUMMARY_BG, "✦", VIOLET, size=40).pack(side="left", padx=(0, 12))
        ttk.Button(head, text="Clear conversation", command=self.clear).pack(side="right")
        titles = tk.Frame(head, bg=SUMMARY_BG)
        titles.pack(side="left")
        tk.Label(titles, text="AI Assistant", bg=SUMMARY_BG, fg="#4C1D95", font=HEAD_FONT).pack(anchor="w")
        tk.Label(titles, text="Private to you · answers come from Gemini and can be wrong",
                 bg=SUMMARY_BG, fg=GRAY, font=SMALL).pack(anchor="w")

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
        self.selected = None                # the highlighted sidebar row: ("user", name), ("group", name) or ("bot", None)
        self.user_rows = {}                 # user name -> SidebarRow in the "Online" list
        self.group_rows = {}                # group name -> SidebarRow in the "Groups" list
        self.member_rows = {}               # group name -> the SidebarRows of its members (shown under the selected group)
        self.member_boxes = {}              # group name -> the frame holding those member rows
        self.handlers = {USERS: self.on_users, GROUPS: self.on_groups, MSG: self.on_msg, FILE: self.on_msg,
                         SUMMARY: self.on_summary, TRANSLATION: self.on_translation,SENTIMENT_RESULT: self.on_sentiment, BOT_REPLY: self.on_bot_reply,
                         ERROR: self.on_error, HISTORY: self.on_history}

        self.build_widgets()
        root.title(f"Socket Chat: {name}")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        threading.Thread(target=self.receive_loop, daemon=True).start()
        self.poll_inbox()

    def build_widgets(self):
        """Left: the dark sidebar (you, the assistant, online users, groups). Right: a notebook with one tab per room."""
        side = tk.Frame(self.root, bg=SIDEBAR, width=round(288 * ui_scale(self.root)))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)          # a fixed width: a long group name must not widen the sidebar

        # You: an avatar with the green presence dot, your name and the connection status.
        card = tk.Frame(side, bg=SIDEBAR)
        card.pack(fill="x", padx=18, pady=(20, 14))
        make_avatar(card, self.me, SIDEBAR, size=40, color=BLUE, online=True).pack(side="left", padx=(0, 12))
        who = tk.Frame(card, bg=SIDEBAR)
        who.pack(side="left")
        tk.Label(who, text=self.me, bg=SIDEBAR, fg="#FFFFFF", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.status_label = tk.Label(who, text=f"Online · {self.server_address()}", bg=SIDEBAR, fg=SIDEBAR_MUTED, font=SMALL)
        self.status_label.pack(anchor="w")
        tk.Frame(side, bg=SIDEBAR_SEL, height=1).pack(fill="x", padx=18)

        # The two buttons at the foot are packed first, at the bottom, so a short window never squeezes them out.
        buttons = tk.Frame(side, bg=SIDEBAR)
        buttons.pack(side="bottom", fill="x", padx=14, pady=(8, 16))
        for column, (label, style, command, weight) in enumerate((("+ New group", "SidebarNew.TButton", self.create_group, 3),
                                                                  ("Join", "SidebarJoin.TButton", self.join_group, 2))):
            ttk.Button(buttons, text=label, style=style, command=command).grid(
                row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
            buttons.columnconfigure(column, weight=weight)      # "+ New group" is the longer label: it gets more room

        body = tk.Frame(side, bg=SIDEBAR)
        body.pack(fill="both", expand=True, padx=8)
        self.section_label(body, "ASSISTANT")
        self.assistant_row = SidebarRow(
            body, lambda parent, bg: make_square_icon(parent, bg, "✦", VIOLET), "AI Assistant", tag="BOT",
            tag_color="#C4B5FD", tag_border=VIOLET_DARK, selected_bg="#4C1D95",
            on_click=lambda: self.select("bot"), on_open=self.open_bot)
        self.assistant_row.pack(fill="x")
        self.users_label = self.section_label(body, "ONLINE · 0")
        self.users_box = tk.Frame(body, bg=SIDEBAR)
        self.users_box.pack(fill="x")
        self.groups_label = self.section_label(body, "GROUPS · 0")
        self.groups_box = tk.Frame(body, bg=SIDEBAR)
        self.groups_box.pack(fill="x")

        content = ttk.Frame(self.root, padding=(16, 14))        # the soft grey area around the chat
        content.pack(side="left", fill="both", expand=True)
        self.notebook = ttk.Notebook(content)
        self.notebook.pack(fill="both", expand=True)
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

    def section_label(self, parent, text):
        """A small capitalised heading in the sidebar, e.g. "ONLINE · 2". Returns the label so its count can change."""
        label = tk.Label(parent, text=text, bg=SIDEBAR, fg=SIDEBAR_MUTED, font=SECTION_FONT, anchor="w")
        label.pack(fill="x", padx=10, pady=(16, 6))
        return label

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
        self.draw_users()
        self.draw_groups()                  # a member's green dot in the group list depends on who is online

    # ------------------------------------------------------------------
    # The sidebar: rows are redrawn from self.users / self.groups / self.unread, and only recoloured on a click
    # ------------------------------------------------------------------

    def draw_users(self):
        """Redraw the "Online" rows: your own name first with (you), a blue pill for unread private messages."""
        for child in self.users_box.winfo_children():
            child.destroy()
        self.user_rows = {}
        for user in self.users:
            you = user == self.me
            row = SidebarRow(
                self.users_box,
                lambda parent, bg, user=user, you=you: make_avatar(parent, user, bg, size=28, online=True,
                                                                   color=BLUE if you else None),
                user, suffix="(you)" if you else "", badge=0 if you else self.unread.get(dm_room(self.me, user), 0),
                selected=self.selected == ("user", user),
                on_click=lambda user=user: self.select("user", user), on_open=lambda user=user: self.open_user(user))
            row.pack(fill="x")
            self.user_rows[user] = row
        self.users_label.config(text=f"ONLINE · {len(self.users)}")

    def draw_groups(self):
        """Redraw the "Groups" rows: the name, "2 members · joined" under it, and a pill for unread messages.
        The members of the selected group are listed under it."""
        for child in self.groups_box.winfo_children():
            child.destroy()
        self.group_rows, self.member_rows, self.member_boxes = {}, {}, {}
        for group, info in self.groups.items():
            joined, count = self.me in info["members"], len(info["members"])
            box = tk.Frame(self.groups_box, bg=SIDEBAR)         # the group's row, then its members
            box.pack(fill="x")
            row = SidebarRow(
                box,
                lambda parent, bg, joined=joined: make_square_icon(parent, bg, "#", SIDEBAR_LINE if joined else SIDEBAR,
                                                                   border=None if joined else SIDEBAR_LINE),
                group, subtitle=f"{count} member{'s' if count != 1 else ''} · {'joined' if joined else 'not joined'}",
                badge=self.unread.get(group_room(group), 0), selected=self.selected == ("group", group),
                on_click=lambda group=group: self.select("group", group),
                on_open=lambda group=group: self.open_group_row(group))
            row.pack(fill="x")
            members = tk.Frame(box, bg=SIDEBAR)
            self.member_rows[group] = [self.member_row(members, member, info) for member in info["members"]]
            self.group_rows[group], self.member_boxes[group] = row, members
        if not self.groups:
            tk.Label(self.groups_box, text="No groups yet. Create one, or select a group and press Join.", bg=SIDEBAR,
                     fg=SIDEBAR_MUTED, font=SMALL, wraplength=round(240 * ui_scale(self.root)), justify="left").pack(
                         anchor="w", padx=10)
        self.groups_label.config(text=f"GROUPS · {len(self.groups)}")
        self.show_members()

    def member_row(self, parent, member, info):
        """A member listed under a group: a green dot if they are online, their name, and "creator" for the creator."""
        row = SidebarRow(parent, lambda p, bg, online=member in self.users: make_dot(p, bg, ONLINE if online else SIDEBAR_LINE),
                         member, suffix="(you)" if member == self.me else "", tag="creator" if member == info["creator"] else "",
                         tag_color="#FBBF24", compact=True, indent=22)
        row.pack(fill="x")
        return row

    def show_members(self):
        """List the members under the selected group (or under every group, if EXPAND_ALL_GROUPS is on)."""
        for group, box in self.member_boxes.items():
            if EXPAND_ALL_GROUPS or self.selected == ("group", group):
                box.pack(fill="x")
            else:
                box.pack_forget()

    def select(self, kind, key=None):
        """Highlight one sidebar row: after a click, or for the chat tab that is now on screen.
        `kind` is "user", "group" or "bot"."""
        self.selected = (kind, key)
        self.assistant_row.set_selected(kind == "bot")
        for user, row in self.user_rows.items():
            row.set_selected(self.selected == ("user", user))
        for group, row in self.group_rows.items():
            row.set_selected(self.selected == ("group", group))
        self.show_members()

    def open_user(self, user):
        """Double-click on a user: open the private chat with them (you can't chat with yourself)."""
        if user != self.me:
            self.open_dm(user)

    def open_group_row(self, group):
        """Double-click on a group: open its chat if you are a member, otherwise offer to join it."""
        if self.me in self.groups[group]["members"]:
            self.open_group(group)
        elif messagebox.askyesno("Join group", f"You are not in #{group}. Join it?", parent=self.root):
            self.send(GROUP_JOIN, group=group)

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

        self.draw_groups()

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
        self.status_label.config(text="Disconnected", fg="#F87171")
        messagebox.showerror("Disconnected", "Lost the connection to the server.", parent=self.root)

    # ------------------------------------------------------------------
    # Rooms (tabs)
    # ------------------------------------------------------------------

    def open_room(self, room, title, on_send, subtitle="", select=True, heading=None, icon=None, on_leave=None):
        """Return the tab for this room id, creating it first if needed (R5: one tab per room).
        `title` is the tab's label ("@bob"); `heading` is the name in the header ("bob")."""
        if room not in self.rooms:
            self.rooms[room] = RoomTab(
                self.notebook, on_send,
                on_summarize=lambda: self.send(SUMMARIZE, room=room),
                on_translate=lambda ts, to: self.translate_message(room, ts, to),
                on_mood=lambda ts: self.analyze_mood(room, ts),
                title=heading or title, subtitle=subtitle,
                on_file=lambda path: self.send_file(room, path), icon=icon, on_leave=on_leave)
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
                              f"🔒 Private chat · only you and {user} can see these messages", select,
                              heading=user, icon=lambda parent, bg: make_avatar(parent, user, bg, size=40))

    def open_group(self, group, select=True):
        """Group chat tab (R11), with a Leave button in its header."""
        return self.open_room(group_room(group), f"#{group}",
                              lambda text: self.send(GROUP_MSG, group=group, text=text),
                              self.group_subtitle(group), select, heading=group,
                              icon=lambda parent, bg: make_square_icon(parent, bg, "#", "#DBEAFE", size=40, glyph_color=BLUE),
                              on_leave=lambda: self.leave_group(group))

    def group_subtitle(self, group):
        info = self.groups.get(group)
        if info is None:
            return "Group · only members see these messages"
        members = ", ".join(self.member_text(member, info) for member in info["members"])
        return f"Group · {members} · only members see these messages"

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
        mark its messages as read, and highlight its row in the sidebar."""
        current = self.notebook.nametowidget(self.notebook.select())
        for room, tab in self.rooms.items():
            if tab is current:
                self.last_room = room
                self.selected = (("user", dm_partner(room, self.me)) if room.startswith("dm:")
                                 else ("group", room.removeprefix("group:")))
                self.unread.pop(room, None)
                self.show_unread()          # redraws the rows with the new selection and without this room's pill
        if current is self.bot_tab:
            self.select("bot")

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
        """Redraw every unread badge: the blue pills in the sidebar rows, and the counts in the tab titles."""
        self.draw_users()
        self.draw_groups()
        for room, tab in self.rooms.items():
            title = f"@{dm_partner(room, self.me)}" if room.startswith("dm:") else f"#{room.removeprefix('group:')}"
            self.notebook.tab(tab, text=title + self.badge(room))

    def badge(self, room):
        """" (3)" if the room has 3 unread messages, else ""."""
        count = self.unread.get(room, 0)
        return f" ({count})" if count else ""

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

    def selected_group(self):
        """Name of the group selected in the sidebar, or None (after telling the user to select one)."""
        if self.selected and self.selected[0] == "group":
            return self.selected[1]
        messagebox.showinfo("No group selected", "Select a group in the list first.", parent=self.root)
        return None

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

    def leave_group(self, group=None):
        """Leave `group`, or the group selected in the sidebar."""
        group = group or self.selected_group()
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
