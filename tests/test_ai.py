"""Tests for ai.py (special 1). Every HTTP call is mocked: these tests never touch the real API or .env."""

import io
import json
import logging
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "import ai" works from any folder

import ai

logging.getLogger("ai").addHandler(logging.NullHandler())   # keep expected warnings off the console

KEY = "TEST-SECRET-KEY-123"
MODELS = ["model-a", "model-b", "model-c"]
MESSAGES = [
    {"type": "MSG", "room": "group:netlab", "from": "alice", "text": "lab report due Friday?", "ts": 1793066669.0},
    {"type": "MSG", "room": "group:netlab", "from": "bob", "text": "yes, I'll do the TCP part", "ts": 1793066700.0},
    {"type": "MSG", "room": "group:netlab", "from": "alice", "text": "great, I'll take UDP", "ts": 1793066760.0},
]


def gemini_reply(text):
    """A fake urlopen() result shaped like a real generateContent response."""
    body = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}
    response = mock.MagicMock()
    response.__enter__.return_value = io.BytesIO(json.dumps(body).encode())
    return response


def http_error(code):
    return urllib.error.HTTPError("https://example.invalid", code, "error", {}, io.BytesIO(b"{}"))


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.config = ai.Config(api_key=KEY, models=list(MODELS))

    @mock.patch("ai.urllib.request.urlopen")
    def test_request_format_and_key_only_in_header(self, urlopen):
        urlopen.return_value = gemini_reply("- alice and bob split the lab report")
        text, model = ai.summarize("group:netlab", MESSAGES, self.config)

        self.assertEqual((text, model), ("- alice and bob split the lab report", "model-a"))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url,
                         "https://generativelanguage.googleapis.com/v1beta/models/model-a:generateContent")
        self.assertNotIn(KEY, request.full_url)                 # never in the URL
        self.assertEqual(request.get_header("X-goog-api-key"), KEY)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], ai.TIMEOUT)
        body = json.loads(request.data)
        prompt = body["contents"][0]["parts"][0]["text"]
        self.assertIn("bob: yes, I'll do the TCP part", prompt)
        self.assertIn("systemInstruction", body)

    @mock.patch("ai.urllib.request.urlopen")
    def test_falls_back_to_next_model_on_429_404_503(self, urlopen):
        for code in (429, 404, 503):
            with self.subTest(code=code):
                urlopen.reset_mock()
                urlopen.side_effect = [http_error(code), gemini_reply("summary from b")]
                self.assertEqual(ai.summarize("group:netlab", MESSAGES, self.config), ("summary from b", "model-b"))
                tried = [c.args[0].full_url.split("/models/")[1] for c in urlopen.call_args_list]
                self.assertEqual(tried, ["model-a:generateContent", "model-b:generateContent"])

    @mock.patch("ai.urllib.request.urlopen")
    def test_offline_when_every_model_fails(self, urlopen):
        urlopen.side_effect = [http_error(429), http_error(503), http_error(429)]
        text, model = ai.summarize("group:netlab", MESSAGES, self.config)
        self.assertEqual(model, ai.OFFLINE)
        self.assertEqual(urlopen.call_count, 3)
        self.assertIn("3 messages from 2 people: alice, bob", text)
        self.assertIn("- alice: great, I'll take UDP", text)

    @mock.patch("ai.urllib.request.urlopen")
    def test_other_errors_go_offline_without_trying_more_models(self, urlopen):
        for error in (http_error(400), http_error(403), TimeoutError(), urllib.error.URLError("no network")):
            with self.subTest(error=error):
                urlopen.reset_mock()
                urlopen.side_effect = error
                self.assertEqual(ai.summarize("group:netlab", MESSAGES, self.config)[1], ai.OFFLINE)
                self.assertEqual(urlopen.call_count, 1)

    @mock.patch("ai.urllib.request.urlopen")
    def test_offline_without_key_or_messages_makes_no_request(self, urlopen):
        text, model = ai.summarize("group:netlab", MESSAGES, ai.Config(api_key="", models=MODELS))
        self.assertEqual(model, ai.OFFLINE)
        self.assertIn("no API key", text)
        text, model = ai.summarize("group:netlab", [], self.config)
        self.assertEqual(model, ai.OFFLINE)
        self.assertIn("No messages", text)
        urlopen.assert_not_called()

    @mock.patch("ai.urllib.request.urlopen")
    def test_empty_reply_counts_as_failure(self, urlopen):
        urlopen.return_value = gemini_reply("   ")
        self.assertEqual(ai.summarize("group:netlab", MESSAGES, self.config)[1], ai.OFFLINE)

    @mock.patch("ai.urllib.request.urlopen")
    def test_key_never_logged(self, urlopen):
        urlopen.side_effect = [http_error(429), http_error(503), TimeoutError()]
        with self.assertLogs("ai", level="WARNING") as logs:
            ai.summarize("group:netlab", MESSAGES, self.config)
        self.assertTrue(logs.output)
        self.assertFalse(any(KEY in line for line in logs.output))
        self.assertNotIn(KEY, ai.describe(self.config))


class ConfigTests(unittest.TestCase):
    def test_env_file_is_parsed_by_hand(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text('# comment\n\nGEMINI_API_KEY="abc"\nGEMINI_MODEL=m1\n'
                           'GEMINI_FALLBACK_MODELS= m2 , m3,m1\nOTHER=x\n', encoding="utf-8")
            config = ai.load_config(env, environ={})
        self.assertEqual(config.api_key, "abc")
        self.assertEqual(config.models, ["m1", "m2", "m3"])     # trimmed, duplicates removed

    def test_environment_variable_wins_and_defaults_apply(self):
        config = ai.load_config("does-not-exist.env", environ={"GEMINI_API_KEY": "from-env"})
        self.assertEqual(config.api_key, "from-env")
        self.assertEqual(config.models, [ai.DEFAULT_MODEL] + ai.DEFAULT_FALLBACKS.split(","))

    def test_describe_hides_the_key(self):
        self.assertEqual(ai.describe(ai.Config("", ["m"])), "offline summaries only (no GEMINI_API_KEY)")
        self.assertEqual(ai.describe(ai.Config("secret", ["m1", "m2"])), "Gemini models m1 -> m2")

    def test_env_example_matches_the_team_chain(self):
        """.env.example must list the same chain as ai.py, so nobody copies a stale list into .env."""
        example = Path(__file__).resolve().parent.parent / ".env.example"
        defaults = ai.load_config("does-not-exist.env", environ={}).models
        self.assertEqual(ai.load_config(example, environ={}).models, defaults)
        self.assertEqual(defaults[0], "gemini-3.5-flash-lite")
        self.assertNotIn("gemini-3.5-flash", defaults)          # not in the free tier
        self.assertFalse(any(m.startswith("gemma") for m in defaults))   # too slow on the free tier


if __name__ == "__main__":
    unittest.main()
