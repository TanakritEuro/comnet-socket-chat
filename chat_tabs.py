"""The chat tabs: RoomTab (a private chat or a group) and BotTab (the AI Assistant), and the read-only
chat window with all its text styles. They only draw things and call the callbacks they are given;
the networking is in client.py (ChatApp)."""

import base64
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ui_theme import (
    BG, BLUE, BLUE_TINT, BOLD, ERROR_COLOR, FONT, GRAY, HEAD_FONT, INK, ITALIC, LINE, SMALL, SMALL_BOLD,
    SMALL_ITALIC, SUMMARY_BG, SUMMARY_LINE, SURFACE, TRANSLATE_BG, ui_scale, VIOLET, VIOLET_DARK,
)
from ui_widgets import make_avatar, make_square_icon


IMAGE_TYPES = (".png", ".gif")   # Tk can draw these without extra libraries (no JPEG: that needs Pillow)
PREVIEW_PX = 320        # an image preview is shrunk until it is at most this wide

# Targets offered by the "Translate to" picker: (code sent to the server, name shown in the picker).
LANGUAGE_CHOICES = [("en", "English"), ("th", "ไทย (Thai)"), ("ja", "日本語 (Japanese)"),
                    ("zh", "中文 (Chinese)"), ("es", "Español")]
LANGUAGE_NAMES = {"en": "English", "th": "Thai", "ja": "Japanese", "zh": "Chinese", "es": "Spanish"}

# Mood pills: label from the server -> (text shown, text colour, background colour).
MOODS = {"positive": ("Positive", "#17663F", "#E1F3E8"),
         "neutral": ("Neutral", "#3F4A5C", "#E8ECF1"),
         "negative": ("Negative", "#A5331F", "#FBE6E1")}

_KEEP_ALIVE = []        # image previews live here too: a Tk image must not be freed by the receiver thread


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

    scale = ui_scale(parent)
    indent = round(44 * scale)           # = the avatar (34) plus its padding (2 x 5), see add_avatar
    gap = round(12 * scale)              # the space between my bubbles and the right edge
    window.indent = indent
    # Layout. Tk takes the margins and the alignment of a line from the tags on its FIRST character, so each
    # message line starts with one of these: others' lines hang under the name; mine are right-aligned.
    window.tag_configure("header", spacing1=16)                                     # others: avatar, name, time, links
    window.tag_configure("body", lmargin1=indent, lmargin2=indent, spacing1=2)      # others: bubble, chip, boxes
    window.tag_configure("mine_head", justify="right", rmargin=gap, spacing1=16)    # mine: the time above my bubble
    window.tag_configure("mine_line", justify="right", rmargin=gap, spacing1=2)     # mine: bubble, chip, image
    window.tag_configure("card", lmargin1=gap, lmargin2=gap, rmargin=gap, spacing1=10, spacing3=4)   # summary card
    # Text styles.
    window.tag_configure("other", foreground=INK, font=BOLD)
    window.tag_configure("bot", foreground=VIOLET_DARK, font=BOLD)
    window.tag_configure("stamp", foreground=GRAY, font=SMALL)       # timestamps: #94A3B8 was too faint on white
    window.tag_configure("note", foreground=GRAY, font=SMALL_ITALIC)
    window.tag_configure("notice", justify="center", spacing1=10, spacing3=4)        # the line a notice sits on
    window.tag_configure("notice_pill", foreground="#475569", background=BG, font=SMALL, justify="center")
    window.tag_configure("divider", foreground=GRAY, font=SMALL, justify="center", spacing1=10, spacing3=4)
    # Per-message AI controls: the clickable links in a message's header, and the chip with the mood.
    window.tag_configure("link", foreground=VIOLET, underline=False, font=SMALL_BOLD)
    window.tag_configure("mood_button", foreground=VIOLET, font=SMALL_BOLD)
    window.tag_configure("mood_error", foreground="#A5331F")
    window.tag_configure("mood_pending", foreground=VIOLET, font=SMALL_ITALIC)
    window.tag_configure("trerror", foreground=ERROR_COLOR, font=SMALL)
    for label, (name, color, tint) in MOODS.items():
        window.tag_configure(f"mood_{label}", foreground=color, background=tint, font=SMALL_BOLD)
    # The mouse turns into a hand over anything clickable.
    for tag in ("link", "mood_button"):
        window.tag_bind(tag, "<Enter>", lambda event: window.configure(cursor="hand2"))
        window.tag_bind(tag, "<Leave>", lambda event: window.configure(cursor=""))
    return window


class PlaceholderEntry(ttk.Entry):
    """A text field that shows a grey hint while it is empty and not focused. get() returns "" while the hint
    shows, and insert() / delete() work as if it were not there, so callers never see the hint."""

    def __init__(self, parent, placeholder):
        super().__init__(parent)
        self.placeholder = placeholder
        self.showing = False
        self.bind("<FocusIn>", lambda event: self.hide(), add="+")
        self.bind("<FocusOut>", lambda event: self.show(), add="+")
        self.show()

    def show(self):
        if not self.showing and not super().get():
            super().insert(0, self.placeholder)
            self.configure(foreground=GRAY)
            self.showing = True

    def hide(self):
        if self.showing:
            super().delete(0, "end")
            self.configure(foreground=INK)
            self.showing = False

    def get(self):
        return "" if self.showing else super().get()

    def insert(self, index, text):
        self.hide()
        super().insert(index, text)

    def delete(self, first, last=None):
        if self.showing:
            return
        super().delete(first, last)
        if self.focus_get() is not self:
            self.show()


def format_size(size):
    """A file size for people: "532 B", "12.3 KB" or "4.8 MB"."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def add_avatar(window, name, kind, *tags):
    """Put a round avatar at the start of a message header. It becomes one character of the chat text, so it
    carries the same tags as the header line (spacing). `kind` is "other" or "bot"."""
    avatar = make_avatar(window, name, SURFACE, size=34, color=VIOLET if kind == "bot" else None,
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


class Bubbles:
    """What RoomTab and BotTab share: rounded bubbles and cards embedded in the chat window (self.window).

    A bubble is a ttk.Label drawn on a rounded image, so it hugs its text and wraps at 70 % of the window's width.
    The text of a label can't be selected, so a right-click on a bubble offers Copy. Scrolling with the mouse
    wheel works over a bubble too.
    """

    def init_bubbles(self):
        scale = ui_scale(self)
        self.wrapped = []                           # (label, share of the window's width, space taken by padding)
        self.window_width = round(760 * scale)      # until the window reports its real width
        self.resize_job = None
        self.copy_menu = tk.Menu(self.window, tearoff=0)
        self.copy_menu.add_command(label="Copy", command=self.copy_text)
        self.copied = ""
        self.window.bind("<Configure>", self.on_window_resized)

    # --- wrapping: bubbles follow the window's width, but only once it stops changing -------------------------

    def wrap_width(self, share, padding):
        return max(int(self.window_width * share) - padding, 160)

    def track(self, label, share, padding=0):
        """Wrap this label at `share` of the window's width (minus its padding) now and after every resize."""
        label.configure(wraplength=self.wrap_width(share, padding))
        self.wrapped.append((label, share, padding))

    def on_window_resized(self, event):
        """Re-wrapping every bubble on every step of a window drag would be slow, so wait until it pauses."""
        self.window_width = event.width
        if self.resize_job is not None:
            self.after_cancel(self.resize_job)
        self.resize_job = self.after(120, self.rewrap)

    def rewrap(self):
        self.resize_job = None
        alive = []
        for label, share, padding in self.wrapped:
            try:
                label.configure(wraplength=self.wrap_width(share, padding))
                alive.append((label, share, padding))
            except tk.TclError:
                pass                                # the label was deleted together with its line
        self.wrapped = alive

    # --- mouse: wheel scrolling and Copy ---------------------------------------------------------------------

    def make_interactive(self, widget, get_text):
        """Let the wheel scroll the chat and a right-click copy `get_text()`, anywhere on this widget."""
        todo = [widget]
        while todo:
            part = todo.pop()
            part.bind("<MouseWheel>", self.scroll_chat)
            part.bind("<Button-3>", lambda event, get_text=get_text: self.show_copy_menu(event, get_text()))
            todo.extend(part.winfo_children())

    def scroll_chat(self, event):
        self.window.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def show_copy_menu(self, event, text):
        self.copied = text
        self.copy_menu.tk_popup(event.x_root, event.y_root)

    def copy_text(self):
        self.clipboard_clear()
        self.clipboard_append(self.copied)

    # --- putting widgets and text into the chat window -------------------------------------------------------

    def make_bubble(self, text, mine):
        """A rounded bubble with this text: blue for me, light grey for everybody else."""
        label = ttk.Label(self.window, text=text, justify="left",
                          style="BubbleMe.TLabel" if mine else "BubbleOther.TLabel")
        self.track(label, 0.7, padding=round(28 * ui_scale(self)))      # 14 px of padding on each side
        self.make_interactive(label, lambda: text)
        return label

    def embed(self, widget, *tags, at="end"):
        """Put a widget on a line of its own in the chat window, and tag the line (to place it under the name,
        or on the right). `at` is "end" or a mark, the place for a chip or box under an earlier message."""
        window = self.window
        window.configure(state="normal")
        start = window.index("end-1c" if at == "end" else at)
        window.window_create(at, window=widget, pady=2)
        window.insert(f"{start}+1c", "\n")
        for tag in tags:
            window.tag_add(tag, start, f"{start}+2c")
        window.configure(state="disabled")

    def append(self, text, *tags):
        append_text(self.window, text, *tags)

    def write(self, text, *tags):
        """Append text without scrolling: a message is written in several pieces and scrolls once, at its end."""
        append_text(self.window, text, *tags, scroll=False)

    def add_notice(self, text):
        """A small centred grey chip, e.g. "You are a member of #netlab". The line starts with a plain centred
        space, so the chip's colour covers only the chip (see set_mood for why)."""
        self.write(" ", "notice")
        self.write(f"  {text}  ", "notice_pill")
        self.append("\n", "notice")


class RoomTab(Bubbles, ttk.Frame):
    """One chat room (R5): a header, a read-only chat window and a chat box with Send (R6).

    The header holds the room's name and the AI actions that work on the whole room (Translate to, Summarize),
    plus Leave in a group. The per-message controls are the plug-in points for the AI features. This class only
    draws things and calls the callbacks below; the network side lives in ChatApp. Features show their
    results through show_translating / add_translation / translation_failed, show_mood_pending / set_mood /
    mood_failed and add_summary.
    """

    def __init__(self, parent, on_send, on_summarize, on_translate, on_mood, title="", subtitle="", on_file=None,
                 icon=None, on_leave=None, placeholder=""):
        super().__init__(parent, padding=(20, 16), style="White.TFrame")
        self.on_send = on_send             # function(text) -> True if the message was sent
        self.on_summarize = on_summarize    # function() -> True if SUMMARIZE was sent
        self.on_translate = on_translate    # function(ts, language code) -> True if the request was sent
        self.on_mood = on_mood              # function(ts) -> True if the request was sent
        self.on_file = on_file              # function(path) -> True if the file was sent (special 2)
        self.entries = {}                   # message number -> {"ts": ..., "mine": True if I wrote it}
        self.by_ts = {}                     # message ts -> message number
        self.files = {}                     # message number -> (file name, base64 data) of a file we can save
        self.images = []                    # previews on screen: Tk draws an image only while Python holds it
        self.last_day = None                # the day of the last message shown, to put a "Today" divider between days

        # Chat box: Enter or the Send button sends. Packed first (at the bottom) so it
        # never gets squeezed out when the window is small.
        bar = ttk.Frame(self, style="White.TFrame")
        bar.pack(side="bottom", fill="x", pady=(14, 0))
        tk.Frame(self, bg=LINE, height=1).pack(side="bottom", fill="x")      # the line above the composer (packed after the bar, so it sits over it)
        if on_file is not None:
            ttk.Button(bar, text="📎", style="Square.TButton", command=self.attach).pack(side="left", padx=(0, 10))
        self.entry = PlaceholderEntry(bar, placeholder or f"Message {title}")
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
        self.init_bubbles()
        self.window.tag_bind("link", "<Button-1>", self.on_link_click)
        self.window.tag_bind("mood_button", "<Button-1>", self.on_mood_click)

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

    def start_message(self, ts, mine):
        """Register a message (its number, its time) and put a day divider before it if the day changed."""
        number = len(self.entries)
        self.entries[number] = {"ts": ts, "mine": mine}
        self.by_ts[ts] = number
        self.maybe_day_divider(ts)
        return number

    def maybe_day_divider(self, ts):
        """A centred "Today" (or "Yesterday", or the date) between rules, whenever the day changes."""
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        if day == self.last_day:
            return
        self.last_day = day
        today = time.time()
        if day == time.strftime("%Y-%m-%d", time.localtime(today)):
            label = "Today"
        elif day == time.strftime("%Y-%m-%d", time.localtime(today - 86400)):
            label = "Yesterday"
        else:
            label = time.strftime("%a %d %b", time.localtime(ts))
        self.write(f"──────────  {label}  ──────────\n", "divider")

    def set_marks(self, number):
        """Two places under a message's bubble, for what the AI adds later: the mood chip and the translation box.
        Both marks stay on the left of anything inserted at them, so later messages never push them away."""
        for name in ("chip", "tbox"):
            self.window.mark_set(f"{name}-{number}", "end-1c")
            self.window.mark_gravity(f"{name}-{number}", "left")

    def add_message(self, sender, text, ts, mine):
        """Append one chat message. Others: avatar, name, time and the Translate · Mood links, then a grey bubble.
        Mine: the time (and the Mood link) above a blue bubble on the right, with no avatar and no name."""
        number = self.start_message(ts, mine)
        stamp = time.strftime("%H:%M", time.localtime(ts))
        if mine:
            self.insert_slot("end", number, "mood", [("Mood", ("mood_button", "mine_head"))])
            self.write(f"   {stamp}\n", "mine_head", "stamp")
        else:
            add_avatar(self.window, sender, "other", "header")
            self.write(sender, "header", "other")
            self.write(f"  {stamp}   ", "stamp")
            self.insert_slot("end", number, "tr", [("Translate", ("link",))])
            self.insert_slot("end", number, "sep", [("  ·  ", ("stamp",))])
            self.insert_slot("end", number, "mood", [("Mood", ("mood_button",))])
            self.write("\n")
        self.embed(self.make_bubble(text, mine), "mine_line" if mine else "body")
        self.set_marks(number)
        self.window.see("end")

    def add_file(self, sender, filename, size, data, ts, mine):
        """Special 2: append a file message: the header, a card with the file's name, size and a Save button,
        and a preview under it for PNG/GIF images. `data` is the base64 text, or None for a file shown from
        history: the server keeps files there as metadata only (name, size, sender, time), never their bytes."""
        number = self.start_message(ts, mine)
        stamp = time.strftime("%H:%M", time.localtime(ts))
        if mine:
            self.write(f"{stamp}\n", "mine_head", "stamp")
        else:
            add_avatar(self.window, sender, "other", "header")
            self.write(sender, "header", "other")
            self.write(f"  {stamp}\n", "stamp")
        self.embed(self.file_card(number, filename, size, data), "mine_line" if mine else "body")
        if data is not None and filename.lower().endswith(IMAGE_TYPES):
            self.add_preview(data, mine)
        self.window.see("end")

    def file_card(self, number, filename, size, data):
        """A bordered card: a file icon, the name in bold, the size, and a Save button (or, for a file from
        history, a note that the server no longer has it)."""
        card = tk.Frame(self.window, bg=SURFACE, highlightthickness=1, highlightbackground=LINE, padx=12, pady=10)
        make_square_icon(card, SURFACE, "📄", BLUE_TINT, size=36, glyph_color=BLUE).pack(side="left", padx=(0, 12))
        words = tk.Frame(card, bg=SURFACE)
        words.pack(side="left")
        tk.Label(words, text=filename, bg=SURFACE, fg=INK, font=BOLD).pack(anchor="w")
        tk.Label(words, text=format_size(size), bg=SURFACE, fg=GRAY, font=SMALL).pack(anchor="w")
        if data is None:
            tk.Label(words, text="Sent earlier · not stored on the server", bg=SURFACE, fg=GRAY,
                     font=SMALL_ITALIC).pack(anchor="w")
        else:
            self.files[number] = (filename, data)
            ttk.Button(card, text="Save", command=lambda: self.save_file(filename, data)).pack(side="right", padx=(24, 0))
        self.make_interactive(card, lambda: filename)
        return card

    def add_preview(self, data, mine):
        """Draw an image inside the chat window, shrunk to fit. If Tk can't read it, show nothing here
        (the file still appears above as a normal file card with a Save button)."""
        try:
            image = tk.PhotoImage(data=data)            # Tk decodes the base64 PNG/GIF itself
            shrink = -(-max(image.width(), image.height()) // PREVIEW_PX)    # whole-number divisor, rounded up
            if shrink > 1:
                image = image.subsample(shrink)
        except tk.TclError:
            return
        self.images.append(image)                       # keep a reference, or Tk would erase the picture
        _KEEP_ALIVE.append(image)
        self.window.configure(state="normal")
        start = self.window.index("end-1c")
        self.window.image_create("end", image=image, padx=4, pady=6)
        self.window.tag_add("mine_line" if mine else "body", start)    # on the right for me, under the name for others
        self.window.insert("end", "\n")
        self.window.configure(state="disabled")

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

    # A slot is a stretch of text, tagged "<kind>-<message number>", that can later be swapped out as a whole
    # (the Translate link, the Mood link, ...). insert_slot makes one, replace_slot changes it.

    def insert_slot(self, index, number, kind, parts):
        """Insert `parts` (pairs of text and tag tuples) tagged as slot `kind` of message `number`."""
        slot = f"{kind}-{number}"
        args = []
        for text, tags in parts:
            args += [text, (*tags, slot)]
        if not args:
            return
        self.window.configure(state="normal")
        self.window.insert(index, *args)
        self.window.configure(state="disabled")

    def replace_slot(self, ts, kind, parts):
        """Swap the content of one message's slot (e.g. the Translate link for "Translating…").
        With no parts the slot is removed."""
        number = self.by_ts.get(ts)
        ranges = self.window.tag_ranges(f"{kind}-{number}") if number is not None else ()
        if not ranges:
            return                          # unknown message, or the slot is gone
        start = str(ranges[0])
        self.window.configure(state="normal")
        self.window.delete(start, str(ranges[-1]))
        self.window.configure(state="disabled")
        self.insert_slot(start, number, kind, parts)

    def remove_link(self, ts, kind):
        """Remove a link from the header for good, and the "·" between the links with it."""
        self.replace_slot(ts, kind, [])
        self.replace_slot(ts, "sep", [])

    def line_tags(self, number, *tags):
        """My header line is right-aligned by a tag on its first character, so whatever starts it keeps that tag."""
        return (*tags, "mine_head") if self.entries[number]["mine"] else tags

    def slot_at(self, event, kind):
        """Message ts of the slot under the mouse, or None."""
        for tag in self.window.tag_names(f"@{event.x},{event.y}"):
            if tag.startswith(f"{kind}-"):
                return self.entries[int(tag.split("-")[1])]["ts"]
        return None

    def on_link_click(self, event):
        ts = self.slot_at(event, "tr")
        if ts is None:
            ts = self.slot_at(event, "retry")           # the "Try again" next to a failed translation
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
        number = self.by_ts.get(ts)
        if number is not None:
            self.clear_box(number)                      # an old translation or error goes while the new one is made
        self.replace_slot(ts, "tr", [("Translating…", ("mood_pending",))])

    def clear_box(self, number):
        """Remove whatever is under the bubble from an earlier translation (the box or the error)."""
        ranges = self.window.tag_ranges(f"trbox-{number}")
        self.window.configure(state="normal")
        for start, end in reversed(list(zip(ranges[0::2], ranges[1::2]))):
            self.window.delete(start, end)
        self.window.configure(state="disabled")

    def add_translation(self, ts, text, source, target):
        """Show a finished translation in a lilac box under its message. `source` and `target` are language codes."""
        number = self.by_ts.get(ts)
        if number is None:
            return
        route = (f"{LANGUAGE_NAMES.get(source, source)} → {LANGUAGE_NAMES.get(target, target)}" if source
                 else f"to {LANGUAGE_NAMES.get(target, target)}")
        self.clear_box(number)
        self.remove_link(ts, "tr")
        box = tk.Frame(self.window, bg=TRANSLATE_BG, padx=14, pady=10)
        tk.Label(box, text=route.upper(), bg=TRANSLATE_BG, fg=VIOLET, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        body = tk.Label(box, text=text.strip(), bg=TRANSLATE_BG, fg=INK, font=FONT, justify="left", anchor="w")
        body.pack(fill="x")
        self.track(body, 0.7, padding=round(28 * ui_scale(self)))
        self.make_interactive(box, lambda: text.strip())
        self.embed(box, "body", f"trbox-{number}", at=f"tbox-{number}")

    def translation_failed(self, ts, reason=None):
        """Show an error line under the bubble with a Try again link (the original text is unchanged).
        `reason` is the server's explanation when retrying won't help (e.g. the message is gone)."""
        number = self.by_ts.get(ts)
        if number is None:
            return
        self.clear_box(number)
        self.replace_slot(ts, "tr", [("Translate", ("link",))])        # the header link comes back
        why = f": {reason}" if reason else "."
        self.insert_slot(f"tbox-{number}", number, "trbox", [
            (f"Couldn't translate this message{why}  ", ("trerror", "body")),
            ("Try again", ("link", "trerror", "body", f"retry-{number}")),
            ("\n", ("body",))])

    # Sentiment (magga) ------------------------------------------------

    def analyze(self, ts):
        """The Mood link was clicked: ask for it, and show "Reading mood…" if the request went out."""
        if self.on_mood(ts):
            self.show_mood_pending(ts)

    def show_mood_pending(self, ts):
        number = self.by_ts.get(ts)
        if number is not None:
            self.replace_slot(ts, "mood", [("Reading mood…", self.line_tags(number, "mood_pending"))])

    def set_mood(self, ts, label, model=None):
        """Replace the Mood link with a chip under the bubble. model "offline" = guessed from a word list."""
        number = self.by_ts.get(ts)
        if label not in MOODS or number is None:
            return
        offline = " · offline" if model == "offline" else ""
        self.remove_link(ts, "mood")
        layout = "mine_line" if self.entries[number]["mine"] else "body"
        # The line starts with a plain space: Tk paints the empty margin of a line with the background of its first
        # character, which would stretch the chip's colour across the whole margin.
        self.insert_slot(f"chip-{number}", number, "moodchip", [
            (" ", (layout,)), (f" ● {MOODS[label][0]}{offline} ", (f"mood_{label}",)), ("\n", (layout,))])
        # A translation box must stay below the chip, so the box's place moves to the end of the chip.
        self.window.mark_set(f"tbox-{number}", self.window.tag_ranges(f"moodchip-{number}")[-1])

    def mood_failed(self, ts):
        number = self.by_ts.get(ts)
        if number is not None:
            self.replace_slot(ts, "mood", [("Couldn't analyze · Try again",
                                            self.line_tags(number, "mood_button", "mood_error"))])

    # Summarization (euro) ---------------------------------------------

    def summarize(self):
        """Ask for a summary. The button stays disabled until the answer (or an error) arrives,
        so a double-click can't use up the tiny API quota."""
        if self.on_summarize():
            self.summarize_button.state(["disabled"])
            self.add_notice("Asking the server for a summary…")

    def add_summary(self, text, model):
        """Show a SUMMARY as a lilac card in the message flow, with the model and the time on the right."""
        card = tk.Frame(self.window, bg=SUMMARY_BG, highlightthickness=1, highlightbackground=SUMMARY_LINE,
                        padx=16, pady=12)
        top = tk.Frame(card, bg=SUMMARY_BG)
        top.pack(fill="x")
        tk.Label(top, text="✦ Summary of this chat", bg=SUMMARY_BG, fg="#5B21B6", font=BOLD).pack(side="left")
        tk.Label(top, text=f"{'offline' if model == 'offline' else model} · {time.strftime('%H:%M')}",
                 bg=SUMMARY_BG, fg=VIOLET, font=SMALL).pack(side="right", padx=(16, 0))
        body = tk.Label(card, text=text.strip(), bg=SUMMARY_BG, fg=INK, font=FONT, justify="left", anchor="w")
        body.pack(fill="x", pady=(8, 0))
        self.track(body, 0.85, padding=round(40 * ui_scale(self)))
        if model == "offline":
            tk.Label(card, text="Offline summary · no AI was used", bg=SUMMARY_BG, fg=GRAY,
                     font=SMALL_ITALIC).pack(anchor="w", pady=(6, 0))
        self.make_interactive(card, lambda: text.strip())
        self.embed(card, "card")
        self.window.see("end")
        self.enable_summarize()

    def enable_summarize(self):
        self.summarize_button.state(["!disabled"])

    def add_divider(self):
        """Special 3: a thin line under the history, so new messages are easy to tell apart."""
        self.append("──────────  earlier messages  ──────────\n", "divider")


class BotTab(Bubbles, ttk.Frame):
    """The AI Assistant tab (special 4): a private conversation with the chatbot.

    It is not a room on the server. Like RoomTab it only draws things; ChatApp does the networking and
    calls add_reply / failed when the answer arrives. My questions are blue bubbles on the right; the
    assistant's answers sit on the left in a lilac card under its name.
    """

    SUGGESTIONS = [("Explain TCP framing", False), ("Explain my last chat", True), ("Help me write a reply", False)]

    def __init__(self, parent, on_ask, on_clear):
        super().__init__(parent, padding=(20, 16), style="White.TFrame")
        self.on_ask = on_ask                # function(text, use_last_chat) -> True if the question was sent
        self.on_clear = on_clear            # function(): tell the server to forget the conversation
        self.waiting = False                # True from "Thinking…" until the answer arrives
        self.reply_label = None             # the label of the answer being waited for ("Thinking…")
        self.reply_card = None

        bar = ttk.Frame(self, style="White.TFrame")
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        tk.Frame(self, bg=LINE, height=1).pack(side="bottom", fill="x")      # the line above the composer
        self.entry = PlaceholderEntry(bar, "Ask the assistant…")
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
        self.init_bubbles()
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
        """My question: the time above a blue bubble on the right."""
        self.write(f"{time.strftime('%H:%M')}\n", "mine_head", "stamp")
        self.embed(self.make_bubble(text, True), "mine_line")
        self.window.see("end")

    def show_thinking(self):
        """Show the assistant's name and a card saying "Thinking…", and block new questions until the answer arrives."""
        self.set_waiting(True)
        add_avatar(self.window, "AI", "bot", "header")
        self.write("AI Assistant", "header", "bot")
        self.write(f"  {time.strftime('%H:%M')}\n", "stamp")
        card = tk.Frame(self.window, bg=SUMMARY_BG, highlightthickness=1, highlightbackground=SUMMARY_LINE,
                        padx=14, pady=10)
        label = tk.Label(card, text="Thinking…", bg=SUMMARY_BG, fg=VIOLET_DARK, font=ITALIC, justify="left", anchor="w")
        label.pack(fill="x")
        self.track(label, 0.75, padding=round(30 * ui_scale(self)))
        self.make_interactive(card, lambda: label.cget("text"))
        self.reply_label, self.reply_card = label, card
        self.embed(card, "body")
        self.window.see("end")

    def add_reply(self, text, model):
        """Replace "Thinking…" with the assistant's answer."""
        if self.reply_label is not None:
            self.reply_label.configure(text=text.strip(), fg=INK, font=FONT)
            if model == "offline":
                tk.Label(self.reply_card, text="Offline reply · no AI was used", bg=SUMMARY_BG, fg=GRAY,
                         font=SMALL_ITALIC).pack(anchor="w", pady=(6, 0))
        self.finish_reply()

    def failed(self, text="Couldn't get an answer. Try again."):
        """Replace "Thinking…" with a red error."""
        if self.reply_label is not None:
            self.reply_label.configure(text=text, fg=ERROR_COLOR, font=FONT)
        self.finish_reply()

    def finish_reply(self):
        self.reply_label = self.reply_card = None
        self.set_waiting(False)
        self.window.see("end")

    def set_waiting(self, waiting):
        self.waiting = waiting
        for widget in (self.entry, self.ask_button, *self.chips):
            widget.state(["disabled" if waiting else "!disabled"])

    def clear(self):
        """Wipe the conversation on screen and tell the server to forget it too."""
        self.on_clear()
        self.window.configure(state="normal")
        self.window.delete("1.0", "end")
        self.window.configure(state="disabled")
        self.reply_label = self.reply_card = None
        self.set_waiting(False)
        self.add_notice("Conversation cleared.")
