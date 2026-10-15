"""Tests run with the standard library only:  python -m unittest discover -s tests"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from are_you_there import config, hooks, notify, presence, relay  # noqa: E402
from mock_ntfy import MockNtfy  # noqa: E402

QUESTION = {"question": "Which database?", "header": "DB", "multiSelect": False,
            "options": [{"label": "Postgres", "description": "Relational"}, {"label": "SQLite", "description": "File"}]}
MULTI = {"question": "Which checks?", "header": "CI", "multiSelect": True,
         "options": [{"label": "lint"}, {"label": "test"}, {"label": "build"}, {"label": "deploy"}]}


class Env(unittest.TestCase):
    """Isolated config, data dir and a forced presence level for every test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.force = Path(self.tmp.name) / "level"
        self.force.write_text("terminal")
        patch = {"ARE_YOU_THERE_CONFIG": str(Path(self.tmp.name) / "none.json"),
                 "CLAUDE_PLUGIN_DATA": str(Path(self.tmp.name) / "data"),
                 "ARE_YOU_THERE_FORCE_FILE": str(self.force),
                 "ARE_YOU_THERE_LANGUAGE": "en"}
        self.env = mock.patch.dict(os.environ, patch)
        self.env.start()
        for key in list(os.environ):
            if key.startswith("CLAUDE_PLUGIN_OPTION_"):
                del os.environ[key]
        self.ntfy = MockNtfy()

    def tearDown(self):
        self.ntfy.close()
        self.env.stop()
        self.tmp.cleanup()

    def cfg(self, **overrides):
        cfg = config.load()
        cfg.update(ntfy_server=self.ntfy.url, relay_server=self.ntfy.url, ntfy_topic="phone",
                   answer_page="https://example.test/answer.html")
        cfg.update(overrides)
        return cfg

    def level(self, value):
        self.force.write_text(value)


class ClassifyTest(unittest.TestCase):
    cfg = {"away_minutes": 3, "reading_minutes": 5}

    def test_levels(self):
        c = presence.classify
        self.assertEqual(c(10, True, False, self.cfg), presence.TERMINAL)
        self.assertEqual(c(240, True, False, self.cfg), presence.TERMINAL)  # still reading
        self.assertEqual(c(400, True, False, self.cfg), presence.AWAY)
        self.assertEqual(c(10, False, False, self.cfg), presence.ELSEWHERE)
        self.assertEqual(c(200, False, False, self.cfg), presence.AWAY)
        self.assertEqual(c(10, None, False, self.cfg), presence.PRESENT)
        self.assertEqual(c(1, True, True, self.cfg), presence.AWAY)  # locked
        self.assertEqual(c(None, True, False, self.cfg), presence.UNKNOWN)

    def test_real_detection_does_not_crash(self):
        p = presence.detect(config.load())
        self.assertIn(p.level, {presence.TERMINAL, presence.PRESENT, presence.ELSEWHERE, presence.AWAY, presence.UNKNOWN})


class ValidateTest(unittest.TestCase):
    session = relay.Session(server="http://x", topic="t", nonce="abc")

    def check(self, answer, questions=(QUESTION,), free=False, nonce="abc"):
        return relay.validate(json.dumps({"n": nonce, "a": answer}), self.session, list(questions), free)

    def test_accepts_label(self):
        self.assertEqual(self.check({"Which database?": "SQLite"}), {"Which database?": "SQLite"})

    def test_rejects_wrong_nonce_and_unknown_label(self):
        self.assertIsNone(self.check({"Which database?": "SQLite"}, nonce="zzz"))
        self.assertIsNone(self.check({"Which database?": "rm -rf /"}))
        self.assertIsNone(self.check({}))
        self.assertIsNone(relay.validate("not json", self.session, [QUESTION], False))

    def test_free_text_only_when_allowed(self):
        self.assertIsNone(self.check({"Which database?": "MySQL"}))
        self.assertEqual(self.check({"Which database?": "MySQL"}, free=True), {"Which database?": "MySQL"})
        self.assertIsNone(self.check({"Which database?": "x\ny"}, free=True))

    def test_multi_select(self):
        both = [QUESTION, MULTI]
        answer = {"Which database?": "Postgres", "Which checks?": ["lint", "test"]}
        self.assertEqual(self.check(answer, both), {"Which database?": "Postgres", "Which checks?": "lint, test"})
        self.assertIsNone(self.check({"Which database?": ["Postgres", "SQLite"]}))  # single select, two answers


class RelayShapeTest(unittest.TestCase):
    cfg = dict(config.DEFAULTS, relay_server="https://ntfy.sh", relay_token="")
    session = relay.Session(server="https://ntfy.sh", topic="ayt-t", nonce="n1")

    def test_answer_url_round_trip(self):
        link = relay.answer_url(self.cfg, self.session, [QUESTION], "zh", "demo")
        page, fragment = link.split("#", 1)
        self.assertEqual(page, self.cfg["answer_page"])
        data = json.loads(base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4)))
        self.assertEqual((data["t"], data["n"], data["l"], data["p"]), ("ayt-t", "n1", "zh", "demo"))
        self.assertEqual(data["q"][0]["o"][1], ["SQLite", "File"])
        self.assertNotIn("k", data)

    def test_quick_buttons(self):
        buttons = relay.quick_buttons(self.session, [QUESTION], "https://page", "Details")
        self.assertEqual([b["label"] for b in buttons], ["Postgres", "SQLite", "Details"])
        body = json.loads(buttons[0]["body"])
        self.assertEqual(body, {"n": "n1", "a": {"Which database?": "Postgres"}})
        self.assertEqual(relay.quick_buttons(self.session, [MULTI], "https://page", "Details"), [])
        self.assertEqual(relay.quick_buttons(self.session, [QUESTION, QUESTION], "", "Details"), [])


class WebhookFormatTest(unittest.TestCase):
    def test_shapes(self):
        message = notify.Message(title="T", body="B", url="https://u")
        expected = {"slack": {"text": "T\nB\nhttps://u"}, "discord": {"content": "T\nB\nhttps://u"},
                    "feishu": {"msg_type": "text", "content": {"text": "T\nB\nhttps://u"}},
                    "dingtalk": {"msgtype": "text", "text": {"content": "T\nB\nhttps://u"}},
                    "wecom": {"msgtype": "text", "text": {"content": "T\nB\nhttps://u"}},
                    "json": {"title": "T", "body": "B", "url": "https://u"}}
        for fmt, payload in expected.items():
            with mock.patch.object(notify, "_post_json", return_value=True) as post:
                notify.send_webhook(dict(webhook_url="https://hook", webhook_format=fmt), message)
                self.assertEqual(post.call_args[0][1], payload, fmt)

    def test_desktop_sequence_is_safe(self):
        seq = notify.desktop_sequence("4;evil\x1b[31m\x07text")
        self.assertTrue(seq.startswith("\x1b]9;· 4;evil"))
        self.assertNotIn("\x1b[", seq)


class PreAskTest(Env):
    def run_pre_ask(self, cfg, questions=(QUESTION,)):
        result = {}
        payload = {"cwd": "/work/demo", "tool_input": {"questions": list(questions)}}
        thread = threading.Thread(target=lambda: result.setdefault("out", hooks.pre_ask(payload, cfg)), daemon=True)
        thread.start()
        return thread, result

    def test_terminal_just_adds_context(self):
        out = hooks.pre_ask({"tool_input": {"questions": [QUESTION]}}, self.cfg())
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["permissionDecision"], "allow")
        self.assertIn("at the terminal", spec["additionalContext"])
        self.assertNotIn("updatedInput", spec)
        self.assertEqual(self.ntfy.published, [])

    def test_elsewhere_rings_the_desktop(self):
        self.level("elsewhere")
        with mock.patch.dict(os.environ, {"ARE_YOU_THERE_FORCE_APP": "Slack.exe"}), \
                mock.patch.object(notify, "desktop_output", return_value={"terminalSequence": "bell"}) as desktop:
            out = hooks.pre_ask({"tool_input": {"questions": [QUESTION]}}, self.cfg())
        self.assertIn("Slack.exe", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(out["terminalSequence"], "bell")
        desktop.assert_called_once()
        self.assertIn(QUESTION["question"], desktop.call_args[0][0])
        self.assertEqual(self.ntfy.published, [])

    def test_away_answer_from_phone(self):
        self.level("away")
        thread, result = self.run_pre_ask(self.cfg())
        push = self.ntfy.wait_published()[0]
        self.assertEqual(push["topic"], "phone")
        self.assertIn("demo", push["title"])
        button = push["actions"][1]  # "SQLite"
        self.ntfy.publish(button["url"].rsplit("/", 1)[1], "garbage")  # ignored
        self.ntfy.publish(button["url"].rsplit("/", 1)[1], button["body"])
        thread.join(10)
        spec = result["out"]["hookSpecificOutput"]
        self.assertEqual(spec["permissionDecision"], "allow")
        self.assertEqual(spec["updatedInput"]["answers"], {"Which database?": "SQLite"})
        self.assertEqual(spec["updatedInput"]["questions"], [QUESTION])

    def test_away_answer_from_page_multi(self):
        self.level("away")
        thread, result = self.run_pre_ask(self.cfg(), (QUESTION, MULTI))
        push = self.ntfy.wait_published()[0]
        self.assertNotIn("actions", push)  # two questions: page only
        fragment = push["click"].split("#", 1)[1]
        data = json.loads(base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4)))
        answer = {"n": data["n"], "a": {"Which database?": "Postgres", "Which checks?": ["test", "build"]}}
        self.ntfy.publish(data["t"], json.dumps(answer))
        thread.join(10)
        answers = result["out"]["hookSpecificOutput"]["updatedInput"]["answers"]
        self.assertEqual(answers, {"Which database?": "Postgres", "Which checks?": "test, build"})

    def test_away_then_back_at_terminal(self):
        self.level("away")
        thread, result = self.run_pre_ask(self.cfg())
        self.ntfy.wait_published()
        self.level("terminal")
        thread.join(10)
        spec = result["out"]["hookSpecificOutput"]
        self.assertEqual(spec["permissionDecision"], "allow")
        self.assertNotIn("updatedInput", spec)
        self.assertIn("came back", spec["additionalContext"])

    def test_timeout_then_decide(self):
        self.level("away")
        thread, result = self.run_pre_ask(self.cfg(answer_timeout_minutes=0.05, when_no_answer="decide"))
        thread.join(15)
        spec = result["out"]["hookSpecificOutput"]
        self.assertEqual(spec["permissionDecision"], "deny")
        self.assertIn("reversible", spec["permissionDecisionReason"])
        titles = [p["title"] for p in self.ntfy.wait_published(2)]
        self.assertIn("went ahead", titles[1])

    def test_no_channel(self):
        self.level("away")
        out = hooks.pre_ask({"tool_input": {"questions": [QUESTION]}}, self.cfg(ntfy_topic=""))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")
        out = hooks.pre_ask({"tool_input": {"questions": [QUESTION]}}, self.cfg(ntfy_topic="", when_no_answer="decide"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_already_answered_input_is_left_alone(self):
        self.level("away")
        out = hooks.pre_ask({"tool_input": {"questions": [QUESTION], "answers": {"Which database?": "SQLite"}}}, self.cfg())
        self.assertIsNone(out)


class StopAndNotificationTest(Env):
    def test_stop_pushes_once_when_away(self):
        self.level("away")
        payload = {"cwd": "/work/demo", "last_assistant_message": "All tests pass."}
        hooks.stop(payload, self.cfg())
        hooks.stop(payload, self.cfg())  # same text within ten minutes: skipped
        time.sleep(0.2)
        self.assertEqual(len(self.ntfy.published), 1)
        self.assertEqual(self.ntfy.published[0]["message"], "All tests pass.")

    def test_stop_quiet_at_terminal(self):
        self.assertIsNone(hooks.stop({"last_assistant_message": "x"}, self.cfg()))
        self.assertEqual(self.ntfy.published, [])

    def test_notification_when_away(self):
        self.level("away")
        hooks.notification({"message": "Claude needs your permission to use Bash"}, self.cfg())
        self.assertIn("permission", self.ntfy.wait_published()[0]["message"])


class EntryPointTest(Env):
    def test_run_sh_prints_utf8_json(self):
        env = dict(os.environ, ARE_YOU_THERE_LANGUAGE="zh")
        payload = json.dumps({"tool_input": {"questions": [QUESTION]}}).encode("utf-8")
        out = subprocess.run(["sh", str(ROOT / "scripts" / "run.sh"), "pre-ask"], input=payload,
                             capture_output=True, env=env, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout.decode("utf-8"))
        self.assertIn("终端前", data["hookSpecificOutput"]["additionalContext"])

    def test_bad_stdin_never_fails(self):
        out = subprocess.run(["sh", str(ROOT / "scripts" / "run.sh"), "stop"], input=b"{not json",
                             capture_output=True, timeout=30)
        self.assertEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
