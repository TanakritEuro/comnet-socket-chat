"""The look of the chat client: colours, fonts, and the rounded buttons and fields.

Standard library only. Tk cannot draw rounded corners, so small rounded images are generated in code
(rounded_image) and used as ttk image elements (build_skin). apply_theme() gives every ttk widget the
light modern style; the chat window and sidebar colours live here too.
"""

import base64
import math
import struct
import tkinter as tk
import zlib
from tkinter import ttk


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


# ---------------------------------------------------------------------------
# Look: rounded buttons and fields, drawn in code (standard library only)
# ---------------------------------------------------------------------------

def _png(width, height, rows):
    """The bytes of a PNG with transparency, built by hand so no image library is needed.
    `rows` is a list of rows, each a list of (red, green, blue, alpha) pixels."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    encoded = {}                            # many rows are the very same list: convert each one only once

    def row_bytes(row):
        if id(row) not in encoded:
            encoded[id(row)] = bytes(value for pixel in row for value in pixel)
        return encoded[id(row)]

    raw = b"".join(b"\x00" + row_bytes(row) for row in rows)        # filter byte 0 per row
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


_IMAGE_CACHE = {}       # (radius, fill, border, border_width, extra) -> (Tk interpreter, PhotoImage)
# Width of the stretchy middle of a button or field image. ttk fills it by TILING, one tile per copy of the middle,
# so a narrow middle means hundreds of tiles per redraw and a laggy window resize. A wide one means a few tiles.
STRETCH = 120


def rounded_image(master, radius, fill, border=None, border_width=1, extra=None):
    """A small rounded rectangle with soft (anti-aliased) edges, as a PhotoImage. ttk keeps its four corners
    and stretches the middle, so this one image becomes a button or text field of any size. The outside is
    transparent, so the colour behind the widget shows around the corners. With extra=0 the image is exactly
    two radii wide, which makes a perfect circle (used for avatars and presence dots).

    Each distinct image is made once and kept for the whole run. That is faster (the sidebar redraws its rows
    often), and it matters for safety: a Tk image must be freed by the GUI thread, but Python may free an
    unused one in the receiver thread, which makes Tk hang or raise "main thread is not in main loop"."""
    extra = STRETCH if extra is None else extra
    key = (radius, fill, border, border_width, extra)
    cached = _IMAGE_CACHE.get(key)
    if cached and cached[0] is master.tk:
        return cached[1]
    size = 2 * radius + extra                   # the four corners (radius + 1 each) plus a wide strip ttk can stretch
    half = size / 2
    fill_rgb, border_rgb = _rgb(fill), _rgb(border or fill)

    def distance(x, y, inset):
        """How far (x, y) is outside the rounded square shrunk by `inset` pixels (negative = inside it)."""
        r = max(radius - inset, 0.0)
        dx, dy = abs(x - half) - (half - inset - r), abs(y - half) - (half - inset - r)
        return math.hypot(max(dx, 0.0), max(dy, 0.0)) + min(max(dx, dy), 0.0) - r

    def pixel(px, py):
        x, y = px + 0.5, py + 0.5
        outer = min(max(0.5 - distance(x, y, 0), 0.0), 1.0)          # 1 inside the shape, 0 outside, soft in between
        inner = min(max(0.5 - distance(x, y, border_width), 0.0), 1.0) if border else 1.0
        rgb = tuple(round(b * (1 - inner) + f * inner) for b, f in zip(border_rgb, fill_rgb))
        return (*rgb, round(255 * outer))

    # Only the corners differ from pixel to pixel. The flat middle looks the same everywhere, so one column
    # (and one row) of it is computed and reused, which keeps a wide image as cheap to draw as a small one.
    corner = radius + 2

    def flat(n):
        return n if n < corner or n >= size - corner else corner

    rows, made = [], {}
    for py in range(size):
        y = flat(py)
        if y not in made:
            cells = {}
            row = []
            for px in range(size):
                x = flat(px)
                if x not in cells:
                    cells[x] = pixel(x, y)
                row.append(cells[x])
            made[y] = row
        rows.append(made[y])
    image = tk.PhotoImage(master=master, data=base64.b64encode(_png(size, size, rows)).decode("ascii"))
    _IMAGE_CACHE[key] = (master.tk, image)
    return image


TAB_CHARS = 14            # every tab label is this many characters wide, like the tabs of a web browser
_SKIN_IMAGES = []       # Tk draws an image only while Python holds a reference to it

# Button looks: style name -> (fill, hover fill, pressed fill, border, text, disabled fill, disabled border,
# disabled text, padding). Primary (blue) is for Send/Connect, AI (violet) for AI actions.
BUTTON_LOOKS = {
    "TButton": (SURFACE, "#F1F5F9", "#E2E8F0", "#CBD5E1", INK, "#F8FAFC", "#E2E8F0", "#94A3B8", (12, 8)),
    "Square.TButton": (SURFACE, "#F1F5F9", "#E2E8F0", "#CBD5E1", INK, "#F8FAFC", "#E2E8F0", "#94A3B8", (0, 8)),
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
    # ttk uses an image's own size as the control's minimum size, and our images are wide (STRETCH), so the
    # minimum is set here: just the two corners across, and a control height of 44 px down (34 for the small
    # suggestion chips). ttk also pads a control by the image's corner size; we keep that sideways but drop it
    # vertically, so the height is the text plus the style's padding, never taller than the minimum asks for.
    scale = ui_scale(master)
    min_width, corner_padding = 2 * (radius + 1), (radius + 1, 0, radius + 1, 0)
    for name, (fill, hover, pressed, border, text, off_fill, off_border, off_text, padding) in BUTTON_LOOKS.items():
        min_height = round((34 if name == "Chip.TButton" else 44) * scale)
        control_width = round(44 * scale) if name == "Square.TButton" else min_width      # the attach button is a 44 px square
        normal, active, down, disabled = (rounded_image(master, radius, f, b) for f, b in (
            (fill, border), (hover, border), (pressed, border), (off_fill, off_border)))
        _SKIN_IMAGES.extend((normal, active, down, disabled))
        style.element_create(f"Skin.{name}", "image", normal, ("disabled", disabled), ("pressed", down),
                             ("active", active), border=radius + 1, padding=corner_padding, width=control_width,
                             height=min_height, sticky="nsew")
        style.layout(name, [(f"Skin.{name}", {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
        on_dark = name.startswith("Sidebar")        # these two buttons sit on the navy sidebar, not on a white card
        style.configure(name, foreground=text, padding=padding, anchor="center",
                        background=SIDEBAR if on_dark else SURFACE,      # what shows through the rounded corners
                        font=SMALL_BOLD if on_dark else (FONT if name in ("TButton", "Danger.TButton") else BOLD))
        if name == "Square.TButton":
            style.configure(name, width=0)         # ttk buttons are at least 11 characters wide unless told otherwise
        # clam recolours a button's background on hover and press, and that colour shows in the see-through
        # corners as light squares; keep it the same as the surface the button sits on.
        behind = SIDEBAR if on_dark else SURFACE
        style.map(name, foreground=[("disabled", off_text)],
                  background=[(state, behind) for state in ("disabled", "pressed", "active", "!disabled")])

    field, focused, off = (rounded_image(master, radius, f, b, w) for f, b, w in (
        (SURFACE, "#CBD5E1", 1), (SURFACE, BLUE, 2), ("#F8FAFC", "#E2E8F0", 1)))
    _SKIN_IMAGES.extend((field, focused, off))
    style.element_create("Skin.entry", "image", field, ("disabled", off), ("focus", focused),
                         border=radius + 1, padding=corner_padding, width=min_width,
                         height=round(44 * scale), sticky="nsew")
    style.layout("TEntry", [("Skin.entry", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    # Browser-style tabs: every tab has a small x after its label (grey, darker while the pointer is over the tab).
    size = round(10 * scale)
    crosses = []
    for colour in ("#94A3B8", INK):
        cross = tk.PhotoImage(master=master, width=size, height=size)
        for i in range(1, size - 3):                       # two diagonal strokes, 2 px thick
            cross.put(colour, to=(i, i, i + 2, i + 2))
            cross.put(colour, to=(size - 3 - i, i, size - 1 - i, i + 2))
        crosses.append(cross)
    _SKIN_IMAGES.extend(crosses)
    style.element_create("Tab.close", "image", crosses[0], ("active", crosses[1]), ("selected", crosses[1]),
                         padding=(round(8 * scale), 0, 0, 0), sticky="")
    style.layout("TNotebook.Tab", [("Notebook.tab", {"sticky": "nswe", "children": [
        ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
            ("Notebook.focus", {"side": "top", "sticky": "nswe", "children": [
                ("Notebook.label", {"side": "left", "sticky": ""}),
                ("Tab.close", {"side": "left", "sticky": ""})]})]})]})])
    # The same field with a red 2 px outline, for a login error (the name is taken).
    wrong = rounded_image(master, radius, SURFACE, ERROR_COLOR, 2)
    _SKIN_IMAGES.append(wrong)
    style.element_create("Skin.entryError", "image", wrong, border=radius + 1, padding=corner_padding,
                         width=min_width, height=round(44 * scale), sticky="nsew")
    style.layout("Error.TEntry", [("Skin.entryError", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    # The language drop-down gets the same rounded field, with its arrow on the right.
    style.layout("TCombobox", [("Skin.entry", {"sticky": "nsew", "children": [
        ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"expand": "1", "sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]})]})])

    # Chat bubbles: labels drawn on a rounded image. The minimum size is just the two corners, so a bubble
    # hugs its text however short it is; mine are blue with white text, other people's light grey.
    bubble_radius = round(14 * scale)
    for style_name, fill, text_color in (("BubbleMe.TLabel", BUBBLE_ME, "#FFFFFF"), ("BubbleOther.TLabel", BUBBLE_OTHER, INK)):
        image = rounded_image(master, bubble_radius, fill)
        _SKIN_IMAGES.append(image)
        style.element_create(f"Skin.{style_name}", "image", image, border=bubble_radius + 1, padding=(0, 0, 0, 0),
                             width=2 * (bubble_radius + 1), height=2 * (bubble_radius + 1), sticky="nsew")
        style.layout(style_name, [(f"Skin.{style_name}", {"sticky": "nsew", "children": [
            ("Label.padding", {"sticky": "nsew", "children": [("Label.label", {"sticky": "nsew"})]})]})])
        style.configure(style_name, foreground=text_color, background=SURFACE, font=FONT,
                        padding=(round(14 * scale), round(9 * scale)))


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
    style.configure("Error.TEntry", fieldbackground=SURFACE, padding=(10, 7), foreground=INK, insertcolor=INK, font=FONT)
    style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE, foreground=INK, arrowcolor=VIOLET_DARK,
                    bordercolor="#CBD5E1", padding=5, lightcolor=SURFACE, darkcolor=SURFACE)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)], selectbackground=[("readonly", SURFACE)],
              selectforeground=[("readonly", INK)], bordercolor=[("focus", BLUE)])
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    # Tabs: the idle ones have no fill and grey bold text; the open one is white with ink text and a 1 px border.
    style.configure("TNotebook.Tab", background=BG, foreground=GRAY, padding=(14, 11), borderwidth=1, font=BOLD,
                    width=TAB_CHARS, anchor="center", expand=(0, 0, 0, 0), bordercolor=BG, lightcolor=BG, darkcolor=BG)
    style.map("TNotebook.Tab", expand=[("selected", (0, 0, 0, 0))], padding=[("selected", (14, 11))], background=[("selected", SURFACE), ("active", "#E7ECF3")],
              foreground=[("selected", INK), ("active", INK)],
              bordercolor=[("selected", LINE)], lightcolor=[("selected", SURFACE)], darkcolor=[("selected", LINE)])
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
