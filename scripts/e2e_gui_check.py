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
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "import client" works from any folder

import tkinter as tk

import ai
import ai_bot
import ai_translate
import client
from protocol import DM, FILE, HELLO, MSG, SUMMARY, TRANSLATION, recv_msg, send_msg
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


def make_png(width, height):
    """The bytes of a plain grey PNG, built by hand so the check needs no image library."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    row = b"\x00" + b"\x80\x80\x80" * width             # filter byte 0, then width RGB pixels
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height))
            + chunk(b"IEND", b""))


def offline_summarizer(room, messages):
    """Force the offline summary: the e2e check must never use the real API quota or read the .env key."""
    return ai.summarize(room, messages, ai.Config(api_key="", models=[]))


def offline_bot(history, text, room, messages):
    """Same for the chatbot: the no-API-key answer, so the real server path runs without the real API."""
    return ai_bot.ask(history, text, room, messages, ai.Config(api_key="", models=[]))


def fake_translator(text, to):
    """Stands in for Gemini, so the check never uses the real API quota or reads the .env key.
    It "translates" by tagging the text, reports Thai as the source, and fails when asked for Japanese."""
    if to == "ja":
        raise ai_translate.TranslateError("fake failure")
    return f"[{to}] {text}", "th", "fake-model"


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

    # port 0: the OS picks a free port
    server = ChatServer("127.0.0.1", 0, summarizer=offline_summarizer, bot=offline_bot, translator=fake_translator)
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
    check("R7  bob's tab shows the DM", wait_until(root, lambda: "hi bob, private" in text_of(bob.rooms["dm:alice|bob"])))
    check("R6  alice's window shows her own message", "hi bob, private" in text_of(dm))
    check("R6  chat box is cleared after Send", dm.entry.get() == "")
    check("R6  chat window is read-only", str(dm.window.cget("state")) == "disabled")

    # R8: bob creates a group (a name with a space, to exercise the tree ids)
    client.simpledialog.askstring = lambda *args, **kwargs: "net lab"
    bob.create_group()
    check("R8  bob's #net lab tab opens", wait_until(root, lambda: "group:net lab" in bob.rooms))
    check("R9  alice sees the group with 1 member",
          wait_until(root, lambda: alice.group_tree.exists("group:net lab")
                     and alice.group_tree.item("group:net lab", "text") == "net lab · 1 member"))
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
    check("R11 bob sees the group message", wait_until(root, lambda: "hello group" in text_of(bob.rooms["group:net lab"])))
    check("R7/R11 carol received no chat message", not any(m["type"] == MSG for m in raw_messages(carol)))

    # AI shell: the buttons exist, their stubs are harmless, and the result methods the features will call work.
    # (No real AI feature is connected yet, so these call the tab's result methods directly.)
    bob_dm = bob.rooms["dm:alice|bob"]
    alice_ts = next(iter(bob_dm.by_ts))
    check("AI  sidebar shows the Assistant row and the user count",
          "AI Assistant" in alice.assistant_list.get(0) and alice.users_label.cget("text").startswith("ONLINE USERS · 3"))
    check("AI  group tab header lists the members", "bob (creator)" in group_tab.subtitle.cget("text"))
    check("AI  own message has an Analyze mood button but no Translate link",
          bool(dm.window.tag_ranges("mood_button")) and not dm.window.tag_ranges("link"))
    check("AI  alice's message has both controls in bob's tab",
          bool(bob_dm.window.tag_ranges("mood_button")) and bool(bob_dm.window.tag_ranges("link")))
    check("AI  'Translate to' picker starts on English", bob_dm.language_box.get() == "English" and bob_dm.language_code() == "en")
    bob.notebook.select(bob_dm)
    wait_until(root, lambda: False, timeout=0.3)

    def click_link():
        """A real click on the (first) link in bob's tab: the Translate link, or the Try again link."""
        x, y, *_ = bob_dm.window.bbox(bob_dm.window.tag_ranges("link")[0])
        bob_dm.window.event_generate("<Motion>", x=x + 3, y=y + 3)     # Tk decides which tag is under the mouse on Motion
        bob_dm.window.event_generate("<Button-1>", x=x + 3, y=y + 3)

    # Translation: a real click, a real TRANSLATE to the server, and a fake translator in place of Gemini.
    click_link()
    check("AI  clicking Translate shows the translation under the message",
          wait_until(root, lambda: "Translated · Thai → English" in text_of(bob_dm) and "[en] " in text_of(bob_dm)))
    check("AI  the Translate link is replaced by the result", not bob_dm.window.tag_ranges("link"))
    check("AI  the translation is private to bob",
          "Translated" not in text_of(dm) and not any(m["type"] == TRANSLATION for m in raw_messages(carol)))
    bob_dm.language_box.current(2)                                     # Japanese: the fake translator fails for it
    bob_dm.translate(alice_ts)
    check("AI  a failed translation shows an error with Try again, not a pop-up",
          wait_until(root, lambda: "Couldn't translate this message." in text_of(bob_dm) and bool(bob_dm.window.tag_ranges("link")))
          and not any(p[1] == "AI_FAILED" for p in popups))
    bob_dm.language_box.current(0)                                     # back to English
    click_link()                                                       # click Try again
    check("AI  Try again works", wait_until(root, lambda: "Translated · Thai → English" in text_of(bob_dm))
          and "Couldn't translate" not in text_of(bob_dm))

    bob_dm.analyze(alice_ts)
    check("AI  Analyze mood without a feature shows a notice", "Mood analysis isn't available yet." in text_of(bob_dm))
    bob_dm.show_mood_pending(alice_ts)
    check("AI  'Analyzing…' replaces the button", "Analyzing…" in text_of(bob_dm) and not bob_dm.window.tag_ranges("mood_button"))
    bob_dm.set_mood(alice_ts, "positive")
    check("AI  mood pill replaces the button", "● Positive" in text_of(bob_dm) and bool(bob_dm.window.tag_ranges("mood_positive")))
    bot = alice.open_bot()
    check("AI  AI Assistant tab opens", alice.bot_tab is bot and alice.notebook.select() == str(bot))
    bot.entry.insert(0, "what is TCP?")
    bot.ask()
    check("AI  asking sends the question and shows 'Thinking…'",
          bot.entry.get() == "" and "what is TCP?" in text_of(bot) and "Thinking…" in text_of(bot))
    check("AI  the server answers (offline, no API key) and Ask is enabled again",
          wait_until(root, lambda: "Offline reply · no AI was used" in text_of(bot)) and
          "Thinking…" not in text_of(bot) and "GEMINI_API_KEY" in text_of(bot) and bot.ask_button.instate(["!disabled"]))
    bot.entry.delete(0, "end")
    bot.add_question("what is TCP?")
    bot.show_thinking()
    check("AI  'Thinking…' disables Ask", "Thinking…" in text_of(bot) and bot.ask_button.instate(["disabled"]))
    bot.add_reply("TCP is a byte stream.", "offline")
    check("AI  reply replaces 'Thinking…' and Ask is enabled again",
          "TCP is a byte stream." in text_of(bot) and "Thinking…" not in text_of(bot) and bot.ask_button.instate(["!disabled"]))
    check("AI  offline reply is labelled", "Offline reply · no AI was used" in text_of(bot))
    bot.clear()
    check("AI  Clear conversation wipes the tab", "TCP is a byte stream." not in text_of(bot) and "Conversation cleared." in text_of(bot))
    alice.notebook.select(group_tab)

    # Special 1: alice clicks Summarize in the group tab (offline summarizer, so no API quota is used)
    group_tab.summarize_button.invoke()
    check("S1  Summarize button disables while waiting", group_tab.summarize_button.instate(["disabled"]))
    check("S1  summary appears in alice's group tab",
          wait_until(root, lambda: "Offline summary · no AI was used" in text_of(group_tab)))
    check("S1  summary is a marked system block", bool(group_tab.window.tag_ranges("summary_title")))
    check("S1  summary describes the room", "1 message from 1 person: alice" in text_of(group_tab))
    check("S1  Summarize button is enabled again", group_tab.summarize_button.instate(["!disabled"]))
    wait_until(root, lambda: False, timeout=0.3)
    check("S1  bob's window shows no summary", "Summary of this room" not in text_of(bob.rooms["group:net lab"]))
    check("S1  carol received no SUMMARY", not any(m["type"] == SUMMARY for m in raw_messages(carol)))

    # Server errors reach the user
    bob.create_group()                                      # "net lab" again
    check("ERR duplicate group shows GROUP_EXISTS", wait_until(root, lambda: any(p[1] == "GROUP_EXISTS" for p in popups)))

    # Special 3: unread badges. alice looks at the group tab while bob writes to her privately.
    def users_of(app):
        return app.user_list.get(0, "end")

    def tab_title(app, tab):
        return app.notebook.tab(tab, "text")

    alice.notebook.select(group_tab)
    wait_until(root, lambda: False, timeout=0.1)
    bob_dm.entry.insert(0, "are you there?")
    bob_dm.send()
    check("S3  unread DM shows 'bob (1)' in alice's user list", wait_until(root, lambda: "bob (1)" in users_of(alice)))
    check("S3  ... and on alice's @bob tab title", tab_title(alice, dm) == "@bob (1)")
    bob_dm.entry.insert(0, "hello?")
    bob_dm.send()
    check("S3  the count goes up with each message", wait_until(root, lambda: "bob (2)" in users_of(alice)))
    alice.notebook.select(dm)
    check("S3  viewing the tab clears the badge",
          wait_until(root, lambda: "bob" in users_of(alice) and tab_title(alice, dm) == "@bob"))

    # A group message while alice looks at the DM tab; her own messages never count.
    bob_group = bob.rooms["group:net lab"]
    bob_group.entry.insert(0, "group ping")
    bob_group.send()
    check("S3  unread group shows 'net lab (1)' in the groups tree",
          wait_until(root, lambda: alice.group_tree.item("group:net lab", "text").startswith("net lab (1) · ")))
    check("S3  ... and on the #net lab tab title", tab_title(alice, group_tab) == "#net lab (1)")
    group_tab.entry.insert(0, "my own message")
    group_tab.send()
    wait_until(root, lambda: "my own message" in text_of(group_tab))
    check("S3  own messages never count", tab_title(alice, group_tab) == "#net lab (1)")
    alice.notebook.select(group_tab)
    check("S3  viewing the group clears tree and tab",
          wait_until(root, lambda: tab_title(alice, group_tab) == "#net lab"
                     and alice.group_tree.item("group:net lab", "text") == "net lab · 2 members · ✓ joined"))

    # A message for a room whose tab isn't open yet still counts, and is shown exactly once.
    send_msg(carol, {"type": DM, "to": "alice", "text": "hi from carol"})
    check("S3  a room with no open tab gets a badge too", wait_until(root, lambda: "carol (1)" in users_of(alice)))
    check("S3  that message is shown once, after its history loads",
          wait_until(root, lambda: text_of(alice.rooms["dm:alice|carol"]).count("hi from carol") == 1
                     and "dm:alice|carol" not in alice.waiting))
    alice.notebook.select(alice.rooms["dm:alice|carol"])
    check("S3  opening that tab clears its badge", wait_until(root, lambda: "carol" in users_of(alice)))

    # Special 3: history. alice closes the @bob tab and opens it again.
    alice.close_room("dm:alice|bob")
    alice.user_list.selection_clear(0, "end")
    alice.user_list.selection_set(alice.users.index("bob"))
    alice.on_user_double_click(None)
    reopened = alice.rooms["dm:alice|bob"]
    check("S3  history appears after reopening a tab",
          wait_until(root, lambda: "hi bob, private" in text_of(reopened) and "hello?" in text_of(reopened)))
    check("S3  history sits above the 'earlier messages' divider",
          text_of(reopened).index("hello?") < text_of(reopened).index("earlier messages"))
    check("S3  every old message is shown once", text_of(reopened).count("hi bob, private") == 1)
    bob_dm.entry.insert(0, "new after reopen")
    bob_dm.send()
    check("S3  new messages go below the divider",
          wait_until(root, lambda: "new after reopen" in text_of(reopened))
          and text_of(reopened).index("earlier messages") < text_of(reopened).index("new after reopen"))

    # Special 2: files and images. send_file is what the 📎 button calls after the file dialog.
    with tempfile.TemporaryDirectory() as folder:
        notes = Path(folder) / "notes.txt"
        notes.write_bytes(b"line one\nline two\n")
        check("S2  alice sends a text file", alice.send_file("dm:alice|bob", str(notes)))
        check("S2  it appears in alice's own tab (echo)",
              wait_until(root, lambda: "notes.txt" in text_of(reopened) and "Save as…" in text_of(reopened)))
        check("S2  it appears in bob's tab with its size",
              wait_until(root, lambda: "notes.txt" in text_of(bob_dm) and "18 B" in text_of(bob_dm)))
        check("S2  carol received no file", not any(m["type"] == FILE for m in raw_messages(carol)))

        saved = Path(folder) / "saved.txt"
        client.filedialog.asksaveasfilename = lambda **kwargs: str(saved)     # answer the Save dialog in code
        bob_dm.save_file(*bob_dm.files[max(bob_dm.files)])
        check("S2  Save as… writes the same bytes", saved.read_bytes() == b"line one\nline two\n")

        picture = Path(folder) / "picture.png"
        picture.write_bytes(make_png(400, 400))
        check("S2  bob sends a PNG image", bob.send_file("dm:alice|bob", str(picture)))
        check("S2  alice's tab shows a preview image",
              wait_until(root, lambda: len(reopened.images) == 1 and reopened.window.image_names()))
        check("S2  the preview is shrunk to fit", reopened.images[0].width() <= client.PREVIEW_PX)

        before = len(popups)
        big = Path(folder) / "big.bin"
        big.write_bytes(b"\0" * (client.MAX_FILE_BYTES + 1))
        check("S2  a file over 5 MiB is refused on the client", not alice.send_file("dm:alice|bob", str(big)))
        check("S2  ... with a 'File too large' message", any(p[1] == "File too large" for p in popups[before:]))
        check("S2  ... and nothing was sent", "big.bin" not in text_of(reopened) and "big.bin" not in text_of(bob_dm))

        # History: the file comes back when alice reopens the tab, still with its Save link.
        alice.close_room("dm:alice|bob")
        alice.user_list.selection_clear(0, "end")
        alice.user_list.selection_set(alice.users.index("bob"))
        alice.on_user_double_click(None)
        again = alice.rooms["dm:alice|bob"]
        check("S2  files come back from history with a Save link",
              wait_until(root, lambda: "notes.txt" in text_of(again) and "picture.png" in text_of(again)
                         and text_of(again).count("Save as…") == 2))
        check("S2  an image from history shows its preview again", wait_until(root, lambda: len(again.images) == 1))

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

    # Special 3: bob logs in again with the same name. His group tab reopens with its history,
    # and so does his private chat with alice when he opens it.
    log_in(tk.Toplevel(root), port, "bob", apps)
    bob = apps["bob"]
    check("S3  group tab reopens with its history after a reconnect",
          wait_until(root, lambda: "group:net lab" in bob.rooms and "hello group" in text_of(bob.rooms["group:net lab"])))
    wait_until(root, lambda: "alice" in bob.users)
    bob.user_list.selection_set(bob.users.index("alice"))
    bob.on_user_double_click(None)
    check("S3  DM history survives a reconnect",
          wait_until(root, lambda: "new after reopen" in text_of(bob.rooms["dm:alice|bob"])))
    bob.quit()

    carol.close()
    root.destroy()
    server.stop()

    failed = [label for label, passed in results if not passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
