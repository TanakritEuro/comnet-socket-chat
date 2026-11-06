"""Small drawn widgets for the sidebar: round avatars with a presence dot, icons, unread pills, and the
SidebarRow that the online-user, group and assistant rows are built from."""

import tkinter as tk

from ui_theme import (
    AVATAR_COLORS, BLUE, BOLD, FONT, ONLINE, rounded_image, SIDEBAR, SIDEBAR_MUTED, SIDEBAR_SEL,
    SIDEBAR_TEXT, SMALL, SMALL_BOLD, ui_scale,
)

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
