"""Tests for sentiment analysis: ai_sentiment.py (HTTP mocked) and the SENTIMENT -> SENTIMENT_RESULT server flow.

Every AI call is faked or mocked: these tests never touch the real API or the .env key.
"""

import base64
import io
import json
import logging
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "import server" works from any folder
sys.path.insert(0, str(Path(__file__).resolve().parent))          # so "import test_server" works too

import ai
import ai_sentiment
from protocol import (
    AI_FAILED, BAD_REQUEST, BUSY, DM, ERROR, FILE, GROUP_JOIN, GROUP_MSG, GROUPS, MSG, NO_SUCH_GROUP,
    NO_SUCH_MESSAGE, NOT_MEMBER, SENTIMENT, SENTIMENT_RESULT,
)
from test_server import ServerTestCase

logging.getLogger("ai_sentiment").addHandler(logging.NullHandler())   # keep expected warnings off the console

KEY = "TEST-SECRET-KEY-123"
MODELS = ["model-a", "model-b", "model-c"]


def gemini_reply(text):
    """A fake urlopen() result shaped like a real generateContent response."""
    body = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}
    response = mock.MagicMock()
    response.__enter__.return_value = io.BytesIO(json.dumps(body).encode())
    return response


def label(value):
    """The text of a model's answer in the format ai_sentiment asks for."""
    return json.dumps({"label": value})


def http_error(code):
    return urllib.error.HTTPError("https://example.invalid", code, f"error mentioning {KEY}", {}, io.BytesIO(b"{}"))


class AnalyzeFunctionTests(unittest.TestCase):
    def setUp(self):
        self.config = ai.Config(api_key=KEY, models=list(MODELS))

    @mock.patch("ai_sentiment.urllib.request.urlopen")
    def test_request_format_and_key_only_in_header(self, urlopen):
        urlopen.return_value = gemini_reply(label("positive"))
        self.assertEqual(ai_sentiment.analyze("วันนี้สนุกมาก", self.config), ("positive", "model-a"))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url,
                         "https://generativelanguage.googleapis.com/v1beta/models/model-a:generateContent")
        self.assertNotIn(KEY, request.full_url)                 # never in the URL
        self.assertEqual(request.get_header("X-goog-api-key"), KEY)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], ai.TIMEOUT)
        body = json.loads(request.data)
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "วันนี้สนุกมาก")   # the message travels as data
        self.assertIn("never instructions", body["systemInstruction"]["parts"][0]["text"])

    @mock.patch("ai_sentiment.urllib.request.urlopen")
    def test_no_key_uses_the_offline_guess_and_makes_no_request(self, urlopen):
        result = ai_sentiment.analyze("I love it", ai.Config(api_key="", models=list(MODELS)))
        self.assertEqual(result, ("positive", ai.OFFLINE))
        urlopen.assert_not_called()

    @mock.patch("ai_sentiment.urllib.request.urlopen")
    def test_rate_limit_falls_back_to_the_next_model(self, urlopen):
        urlopen.side_effect = [http_error(429), http_error(503), gemini_reply(label("negative"))]
        self.assertEqual(ai_sentiment.analyze("ugh", self.config), ("negative", "model-c"))
        self.assertEqual(urlopen.call_count, 3)

    @mock.patch("ai_sentiment.urllib.request.urlopen")
    def test_bad_key_stops_at_once_and_falls_back_offline(self, urlopen):
        urlopen.side_effect = [http_error(403), gemini_reply(label("positive"))]
        self.assertEqual(ai_sentiment.analyze("what time is it?", self.config), ("neutral", ai.OFFLINE))
        self.assertEqual(urlopen.call_count, 1)                 # another model won't fix a bad key

    @mock.patch("ai_sentiment.urllib.request.urlopen")
    def test_every_model_rate_limited_falls_back_offline(self, urlopen):
        urlopen.side_effect = [http_error(429) for _ in MODELS]
        self.assertEqual(ai_sentiment.analyze("this is terrible", self.config), ("negative", ai.OFFLINE))
        self.assertEqual(urlopen.call_count, len(MODELS))

    @mock.patch("ai_sentiment.urllib.request.urlopen")
    def test_no_internet_falls_back_without_retrying(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("no route to host")
        self.assertEqual(ai_sentiment.analyze("hello", self.config), ("neutral", ai.OFFLINE))
        self.assertEqual(urlopen.call_count, 1)                 # don't make the user wait for 3 timeouts

    @mock.patch("ai_sentiment.urllib.request.urlopen")
    def test_garbled_reply_falls_back_offline(self, urlopen):
        garbled = mock.MagicMock(**{"__enter__.return_value": io.BytesIO(b"not json")})
        for bad in (gemini_reply("   "), garbled, gemini_reply('{"label": "happy"}'), gemini_reply("{broken json")):
            urlopen.side_effect = [bad]
            self.assertEqual(ai_sentiment.analyze("hello", self.config), ("neutral", ai.OFFLINE))


class ParseReplyTests(unittest.TestCase):
    def test_json_plain_word_and_fence(self):
        self.assertEqual(ai_sentiment.parse_reply(label("positive")), "positive")
        self.assertEqual(ai_sentiment.parse_reply("  Negative. "), "negative")
        self.assertEqual(ai_sentiment.parse_reply('```json\n{"label": "neutral"}\n```'), "neutral")

    def test_anything_else_raises(self):
        for bad in ('{"label": "angry"}', '{"mood": "positive"}', '["positive"]', "very positive", '{"label": 1}'):
            with self.assertRaises(ValueError, msg=bad):
                ai_sentiment.parse_reply(bad)


class OfflineLabelTests(unittest.TestCase):
    def test_english(self):
        self.assertEqual(ai_sentiment.offline_label("I love this, thanks!"), "positive")
        self.assertEqual(ai_sentiment.offline_label("This is so BAD, I hate it"), "negative")
        self.assertEqual(ai_sentiment.offline_label("See you at 3 pm"), "neutral")
        self.assertEqual(ai_sentiment.offline_label("I know"), "neutral")          # "no" only counts as a whole word

    def test_thai_and_emoji(self):
        self.assertEqual(ai_sentiment.offline_label("ขอบคุณมากครับ"), "positive")
        self.assertEqual(ai_sentiment.offline_label("เหนื่อยมาก"), "negative")
        self.assertEqual(ai_sentiment.offline_label("ไม่ชอบเลย"), "negative")       # not also counted as "ชอบ" (like)
        self.assertEqual(ai_sentiment.offline_label("ok 👍"), "positive")


class SentimentServerTests(ServerTestCase):
    """The SENTIMENT request through a real server, with a fake analyzer in place of Gemini."""

    def setUp(self):
        self.calls = []                     # texts the fake analyzer was asked about
        self.release = threading.Event()
        self.release.set()
        super().setUp()
        self.alice = self.connect("alice")
        self.bob = self.connect("bob")
        self.carol = self.connect("carol")

    def analyzer(self, text):
        self.calls.append(text)
        self.release.wait(5)
        if text == "boom":
            raise RuntimeError("a bug in the analyzer")
        if text == "offline":
            return "neutral", ai.OFFLINE
        return "positive", "fake-model"

    def dm(self, sender, to, receiver, text):
        """Send a DM and read it on both ends. Returns the delivered message (with its ts)."""
        sender.send(DM, to=to, text=text)
        sender.wait_for(MSG, text=text)
        return receiver.wait_for(MSG, text=text)

    def analyze(self, client, room, ts, **extra):
        client.send(SENTIMENT, room=room, ts=ts, **extra)

    def join(self, client, name, group):
        """Join a group and wait until the server's GROUPS broadcast lists us as a member."""
        client.send(GROUP_JOIN, group=group)
        while not any(g["name"] == group and name in g["members"] for g in client.wait_for(GROUPS)["groups"]):
            pass

    def test_reply_goes_to_the_requester_only(self):
        sent = self.dm(self.alice, "bob", self.bob, "good morning")
        self.analyze(self.bob, "dm:alice|bob", sent["ts"])
        reply = self.bob.wait_for(SENTIMENT_RESULT)
        self.assertEqual((reply["room"], reply["ts"], reply["label"], reply["model"]),
                         ("dm:alice|bob", sent["ts"], "positive", "fake-model"))
        self.alice.assert_no(SENTIMENT_RESULT)  # the sender sees nothing: a mood is private to who clicked
        self.carol.assert_no(SENTIMENT_RESULT)

    def test_own_message_can_be_analyzed_too(self):
        sent = self.dm(self.alice, "bob", self.bob, "my own words")
        self.analyze(self.alice, "dm:alice|bob", sent["ts"])
        self.assertEqual(self.alice.wait_for(SENTIMENT_RESULT)["ts"], sent["ts"])

    def test_the_text_comes_from_the_server_not_from_the_request(self):
        sent = self.dm(self.alice, "bob", self.bob, "the real message")
        self.analyze(self.bob, "dm:alice|bob", sent["ts"], text="something the client made up")
        self.bob.wait_for(SENTIMENT_RESULT)
        self.assertEqual(self.calls, ["the real message"])

    def test_someone_elses_dm_cannot_be_analyzed(self):
        sent = self.dm(self.alice, "bob", self.bob, "just us")
        self.analyze(self.carol, "dm:alice|bob", sent["ts"])
        error = self.carol.wait_for(ERROR)
        self.assertEqual((error["code"], error["request"]), (NOT_MEMBER, SENTIMENT))
        self.carol.assert_no(SENTIMENT_RESULT)
        self.assertEqual(self.calls, [])

    def test_group_members_can_analyze_and_outsiders_cannot(self):
        self.create_group(self.alice, "netlab")
        self.join(self.bob, "bob", "netlab")
        self.alice.send(GROUP_MSG, group="netlab", text="members only")
        sent = self.bob.wait_for(MSG, text="members only")
        self.analyze(self.bob, "group:netlab", sent["ts"])
        self.assertEqual(self.bob.wait_for(SENTIMENT_RESULT)["room"], "group:netlab")
        self.analyze(self.carol, "group:netlab", sent["ts"])
        self.assertEqual(self.carol.wait_for(ERROR)["code"], NOT_MEMBER)
        self.carol.assert_no(SENTIMENT_RESULT)

    def test_a_message_from_before_you_joined_is_not_available(self):
        self.create_group(self.alice, "netlab")
        self.alice.send(GROUP_MSG, group="netlab", text="before bob joined")
        old = self.alice.wait_for(MSG, text="before bob joined")
        self.join(self.bob, "bob", "netlab")
        self.analyze(self.bob, "group:netlab", old["ts"])
        self.assertEqual(self.bob.wait_for(ERROR)["code"], NO_SUCH_MESSAGE)
        self.bob.assert_no(SENTIMENT_RESULT)
        self.assertEqual(self.calls, [])

    def test_bad_requests_are_refused_and_never_reach_the_ai(self):
        sent = self.dm(self.alice, "bob", self.bob, "hello")
        room, ts = "dm:alice|bob", sent["ts"]
        cases = [
            ((room, "not a number"), BAD_REQUEST),
            ((room, True), BAD_REQUEST),
            ((room, [1]), BAD_REQUEST),
            ((room, None), BAD_REQUEST),
            ((room, 123.5), NO_SUCH_MESSAGE),                   # a ts nobody has
            (("not a room", ts), BAD_REQUEST),
            (("group:nothing", ts), NO_SUCH_GROUP),
            ((None, ts), BAD_REQUEST),
        ]
        for (r, t), code in cases:
            self.bob.send(SENTIMENT, room=r, ts=t)
            error = self.bob.wait_for(ERROR)
            self.assertEqual((error["code"], error["request"], error["ts"]), (code, SENTIMENT, t), (r, t))
        self.assertEqual(self.calls, [])

    def test_a_message_without_text_or_too_long_is_refused(self):
        sent = self.dm(self.alice, "bob", self.bob, "x" * (ai_sentiment.MAX_CHARS + 1))
        self.analyze(self.bob, "dm:alice|bob", sent["ts"])
        self.assertEqual(self.bob.wait_for(ERROR)["code"], BAD_REQUEST)
        # A file has no text: refused with BAD_REQUEST instead of crashing, and the AI is never asked.
        self.alice.send(FILE, room="dm:alice|bob", filename="a.png", size=3, data=base64.b64encode(b"abc").decode())
        sent_file = self.bob.wait_for(FILE)
        self.analyze(self.bob, "dm:alice|bob", sent_file["ts"])
        self.assertEqual(self.bob.wait_for(ERROR)["code"], BAD_REQUEST)
        self.assertEqual(self.calls, [])

    def test_the_same_message_is_analyzed_only_once(self):
        self.create_group(self.alice, "netlab")
        self.join(self.bob, "bob", "netlab")
        self.alice.send(GROUP_MSG, group="netlab", text="cache me")
        sent = self.bob.wait_for(MSG, text="cache me")
        self.analyze(self.bob, "group:netlab", sent["ts"])
        first = self.bob.wait_for(SENTIMENT_RESULT)
        self.analyze(self.bob, "group:netlab", sent["ts"])              # again: no AI call
        self.analyze(self.alice, "group:netlab", sent["ts"])            # another member: no AI call either
        self.assertEqual(self.bob.wait_for(SENTIMENT_RESULT), first)
        self.assertEqual(self.alice.wait_for(SENTIMENT_RESULT)["label"], first["label"])
        self.assertEqual(len(self.calls), 1)

    def test_an_offline_guess_is_not_cached(self):
        sent = self.dm(self.alice, "bob", self.bob, "offline")
        for _ in range(2):                                              # each click asks again: the AI may be back
            self.analyze(self.bob, "dm:alice|bob", sent["ts"])
            self.assertEqual(self.bob.wait_for(SENTIMENT_RESULT)["model"], ai.OFFLINE)
        self.assertEqual(len(self.calls), 2)

    def test_a_crashing_analyzer_gives_ai_failed_and_the_server_keeps_working(self):
        with self.assertLogs("server", "ERROR"):                        # the traceback goes to the log, not the console
            sent = self.dm(self.alice, "bob", self.bob, "boom")
            self.analyze(self.bob, "dm:alice|bob", sent["ts"])
            error = self.bob.wait_for(ERROR)
        self.assertEqual((error["code"], error["request"], error["room"], error["ts"]),
                         (AI_FAILED, SENTIMENT, "dm:alice|bob", sent["ts"]))
        self.dm(self.alice, "bob", self.bob, "still alive")

    def test_one_message_at_a_time_and_a_cap_per_user(self):
        self.release.clear()                    # the fake AI blocks until released
        sent = [self.dm(self.alice, "bob", self.bob, t) for t in ("one", "two", "three", "four")]
        room = "dm:alice|bob"
        self.analyze(self.bob, room, sent[0]["ts"])
        self.analyze(self.bob, room, sent[0]["ts"])                     # same message again, still in progress
        self.assertEqual(self.bob.wait_for(ERROR)["code"], BUSY)
        self.analyze(self.bob, room, sent[1]["ts"])
        self.analyze(self.bob, room, sent[2]["ts"])
        self.analyze(self.bob, room, sent[3]["ts"])                     # the 4th at once is over the cap
        self.assertEqual(self.bob.wait_for(ERROR, ts=sent[3]["ts"])["code"], BUSY)
        self.release.set()
        arrived = {self.bob.wait_for(SENTIMENT_RESULT)["ts"] for _ in range(3)}
        self.assertEqual(arrived, {item["ts"] for item in sent[:3]})
        self.analyze(self.bob, room, sent[3]["ts"])                     # and it works once the others are done
        self.bob.wait_for(SENTIMENT_RESULT, ts=sent[3]["ts"])

    def test_server_keeps_serving_while_the_ai_is_slow(self):
        self.release.clear()
        sent = self.dm(self.alice, "bob", self.bob, "slow one")
        self.analyze(self.bob, "dm:alice|bob", sent["ts"])
        self.alice.send(DM, to="bob", text="still chatting")            # handled while the AI "thinks"
        self.bob.wait_for(MSG, text="still chatting")
        self.release.set()
        self.bob.wait_for(SENTIMENT_RESULT)

    def test_a_requester_who_left_gets_nothing_and_the_server_survives(self):
        self.release.clear()
        sent = self.dm(self.alice, "bob", self.bob, "bye")
        self.analyze(self.bob, "dm:alice|bob", sent["ts"])
        self.clients.remove(self.bob)
        self.bob.close()
        self.alice.wait_for("USERS", users=["alice", "carol"])
        self.release.set()
        self.dm(self.alice, "carol", self.carol, "anyone there?")       # the server is fine


class NoKeyTests(ServerTestCase):
    """With no API key (the default test analyzer) the user still gets a mood, marked "offline"."""

    def test_no_key_gives_the_offline_guess(self):
        alice, bob = self.connect("alice"), self.connect("bob")
        alice.send(DM, to="bob", text="thanks, I love it")
        sent = bob.wait_for(MSG, text="thanks, I love it")
        bob.send(SENTIMENT, room="dm:alice|bob", ts=sent["ts"])
        reply = bob.wait_for(SENTIMENT_RESULT)
        self.assertEqual((reply["label"], reply["model"]), ("positive", ai.OFFLINE))


class KeyLeakTests(ServerTestCase):
    """Real ai_sentiment.analyze with a fake key and a mocked HTTP layer: the key must never reach a client."""

    def analyzer(self, text):
        return ai_sentiment.analyze(text, ai.Config(api_key=KEY, models=["model-a", "model-b"]))

    def run_with(self, urlopen_result):
        alice, bob = self.connect("alice"), self.connect("bob")
        alice.send(DM, to="bob", text="hello")
        sent = bob.wait_for(MSG, text="hello")
        with mock.patch("ai_sentiment.urllib.request.urlopen", **urlopen_result):
            bob.send(SENTIMENT, room="dm:alice|bob", ts=sent["ts"])
            bob.wait_for(SENTIMENT_RESULT)
        return alice.received + bob.received

    def test_key_not_leaked_on_success(self):
        self.assertNotIn(KEY, json.dumps(self.run_with({"return_value": gemini_reply(label("neutral"))})))

    def test_key_not_leaked_on_failure(self):
        self.assertNotIn(KEY, json.dumps(self.run_with({"side_effect": [http_error(429), http_error(403)]})))


if __name__ == "__main__":
    unittest.main()