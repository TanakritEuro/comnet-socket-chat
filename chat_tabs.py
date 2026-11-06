"""The chat tabs: RoomTab (a private chat or a group) and BotTab (the AI Assistant), and the read-only
chat window with all its text styles. They only draw things and call the callbacks they are given;
the networking is in client.py (ChatApp)."""

import base64
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ui_theme import (
    BLUE, BOLD, FONT, GRAY, HEAD_FONT, INK, ITALIC, LINE, SMALL, SMALL_BOLD, SMALL_ITALIC,
    SUMMARY_BG, SUMMARY_LINE, SURFACE, ui_scale, VIOLET, VIOLET_DARK, VIOLET_LIGHT, VIOLET_TINT,
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
