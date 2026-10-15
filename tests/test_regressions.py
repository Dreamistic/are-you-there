"""Regressions for platform signals, phone delivery, and notification isolation."""
import base64
import copy
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from are_you_there import config, hooks, notify, presence, relay  # noqa: E402
from test_are_you_there import Env, QUESTION  # noqa: E402


def response(body):
    result = io.BytesIO(body)
    result.status = 200
    return result


class DeliveryRegressionTest(unittest.TestCase):
    def test_discord_preserves_a_long_decodable_answer_link(self):
        questions = [copy.deepcopy(QUESTION) for _ in range(2)]
        for index, question in enumerate(questions):
            question["question"] += str(index)
            question["options"] = [{"label": label, "description": "Explanation. " * 15}
                                   for label in ("Postgres", "SQLite", "MySQL", "MongoDB")]
        cfg = dict(config.DEFAULTS, webhook_format="discord", webhook_url="https://example.test/hook")
        session = relay.Session(server="https://example.test", topic="ayt-test", nonce="nonce")
        link = relay.answer_url(cfg, session, questions, "en", "demo")
        self.assertGreater(len(link), 2000)
        message = notify.Message(title="Claude", body=hooks._summary(questions), url=link)
        with mock.patch.object(notify, "_post_json", return_value=True) as post:
            self.assertTrue(notify.send_webhook(cfg, message))
        payload = post.call_args[0][1]
        self.assertLessEqual(len(payload["content"]), 2000)
        delivered = payload["embeds"][0]["description"]
        self.assertEqual(delivered, link)
        fragment = delivered.split("#", 1)[1]
        decoded = json.loads(base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4)))
        self.assertEqual(decoded["n"], session.nonce)
        self.assertEqual([q["q"] for q in decoded["q"]], [q["question"] for q in questions])

    def test_discord_rejects_oversize_links_without_sending_a_broken_one(self):
        cfg = dict(config.DEFAULTS, webhook_format="discord", webhook_url="https://example.test/hook")
        message = notify.Message(title="Claude", body="Question", url="https://example.test/#" + "a" * 4096)
        with mock.patch.object(notify, "_post_json") as post, redirect_stderr(io.StringIO()):
            self.assertEqual(notify.send_all(cfg, message), [])
        post.assert_not_called()

    def test_channels_distinguish_business_errors_from_http_success(self):
        cases = [("feishu", {"code": 0}, True),
                 ("feishu", {"StatusCode": 0}, True),
                 ("feishu", {"code": 19024, "msg": "Key Words Not Found"}, False),
                 ("dingtalk", {"errcode": 0}, True),
                 ("dingtalk", {"errcode": 310000}, False),
                 ("wecom", {"errcode": 0}, True),
                 ("wecom", {"errcode": 93000}, False),
                 ("serverchan", {"code": 0}, True),
                 ("serverchan", {"code": 40001}, False),
                 ("feishu", {}, False)]
        for fmt, result, succeeds in cases:
            with self.subTest(fmt=fmt, result=result):
                cfg = dict(config.DEFAULTS, webhook_format=fmt, webhook_url="https://example.test/hook")
                with mock.patch.object(notify, "http", return_value=response(json.dumps(result).encode())), \
                        redirect_stderr(io.StringIO()):
                    sent = notify.send_all(cfg, notify.Message(title="Claude", body="Question"))
                self.assertEqual(sent, ["webhook"] if succeeds else [])

    def test_invalid_business_response_is_not_success(self):
        cfg = dict(config.DEFAULTS, webhook_format="feishu", webhook_url="https://example.test/hook")
        for body in (b"not JSON", b"[]"):
            with self.subTest(body=body), mock.patch.object(notify, "http", return_value=response(body)), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(notify.send_all(cfg, notify.Message(title="Claude", body="Question")), [])

    def test_rejected_push_falls_back_without_waiting_for_a_phone_answer(self):
        cfg = dict(config.DEFAULTS, language="en", relay_server="https://example.test",
                   webhook_format="feishu", webhook_url="https://example.test/hook")
        with mock.patch.object(presence, "detect", return_value=presence.Presence(presence.AWAY, idle=600)), \
                mock.patch.object(notify, "http", return_value=response(b'{"code":19024}')), \
                mock.patch.object(relay, "wait_for_answer") as wait, redirect_stderr(io.StringIO()):
            out = hooks.pre_ask({"tool_input": {"questions": [QUESTION]}}, cfg)
        wait.assert_not_called()
        self.assertIn("failed", out["hookSpecificOutput"]["additionalContext"])


class NotificationScopeTest(Env):
    def test_matching_notifications_from_separate_sessions_are_both_sent(self):
        self.level("away")
        for session in ("one", "two", "one"):
            hooks.notification({"session_id": session, "cwd": "/same/project",
                                "message": "Claude needs permission"}, self.cfg())
        self.assertEqual(len(self.ntfy.published), 2)

    def test_matching_stop_messages_from_separate_sessions_are_both_sent(self):
        self.level("away")
        for session in ("one", "two", "one"):
            hooks.stop({"session_id": session, "last_assistant_message": "Done"}, self.cfg())
        self.assertEqual(len(self.ntfy.published), 2)

    def test_failed_notification_can_be_retried(self):
        self.level("away")
        payload = {"session_id": "one", "message": "Claude needs permission"}
        with mock.patch.object(notify, "send_all", side_effect=[[], ["ntfy"]]) as send:
            hooks.notification(payload, self.cfg())
            hooks.notification(payload, self.cfg())
            hooks.notification(payload, self.cfg())
        self.assertEqual(send.call_count, 2)


class LinuxSignalTest(unittest.TestCase):
    def detect_linux(self):
        with mock.patch.object(presence.sys, "platform", "linux"), \
                mock.patch.object(presence, "_unreachable_reason", return_value=""):
            return presence.detect(dict(config.DEFAULTS, terminal_apps=["custom-terminal-server"]))

    def test_wayland_parses_the_value_after_the_uint64_annotation(self):
        for raw, expected in (("(uint64 0,)\n", 0), ("(uint64 600000,)", 600),
                              ("(uint64 3600000,)", 3600)):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
                    mock.patch.object(presence, "_run", return_value=raw):
                detected = self.detect_linux()
            self.assertEqual(detected.idle, expected)
            self.assertEqual(detected.level, presence.AWAY if expected else presence.PRESENT)

    def test_invalid_wayland_output_does_not_invent_an_idle_time(self):
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
                mock.patch.object(presence, "_run", return_value="error 64: unavailable"):
            self.assertEqual(self.detect_linux().level, presence.UNKNOWN)

    def test_x11_tmux_matches_truncated_and_script_terminal_names(self):
        for name in ("gnome-terminal-", "custom-terminal", "terminator"):
            def run(args):
                return "240000" if args[0] == "xprintidle" else "900"

            def read_text(path, *args, **kwargs):
                if path.name == "comm":
                    return name + "\n"
                # The tmux server has detached from the foreground terminal's process tree.
                return "100 (tmux: server) S 1 0 0"

            with self.subTest(name=name), mock.patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True), \
                    mock.patch.object(presence, "_run", side_effect=run), \
                    mock.patch.object(presence.os, "getpid", return_value=100), \
                    mock.patch.object(Path, "glob", return_value=[Path("/proc/100/stat")]), \
                    mock.patch.object(Path, "read_text", new=read_text):
                detected = self.detect_linux()
            self.assertEqual(detected.app, name)
            self.assertEqual(detected.level, presence.TERMINAL)
            self.assertTrue(detected.on_terminal)


class DesktopNotificationTest(unittest.TestCase):
    def test_windows_passes_unicode_and_metacharacters_as_data_to_a_hidden_process(self):
        text = 'Claude: 中文 <question> "quote" $(must_not_run)'
        with mock.patch.object(notify.sys, "platform", "win32"), \
                mock.patch.object(notify.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(notify.desktop_output(text), {})
        args, kwargs = run.call_args
        self.assertEqual(base64.b64decode(kwargs["input"]).decode("utf-8"), text)
        self.assertEqual(args[0][args[0].index("-WindowStyle") + 1], "Hidden")
        script = base64.b64decode(args[0][-1]).decode("utf-16le")
        self.assertNotIn(text, script)
        self.assertIn(".InnerText = $text", script)

    def test_windows_failure_or_timeout_falls_back_to_a_bell(self):
        for outcome in (FileNotFoundError(), subprocess.TimeoutExpired("powershell.exe", 8),
                        subprocess.CompletedProcess([], 1)):
            kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
            with self.subTest(outcome=type(outcome).__name__), mock.patch.object(notify.sys, "platform", "win32"), \
                    mock.patch.object(notify.subprocess, "run", **kwargs):
                self.assertEqual(notify.desktop_output("Claude has a question"), {"terminalSequence": "\x07"})

    def test_other_platforms_keep_terminal_notifications(self):
        with mock.patch.object(notify.sys, "platform", "darwin"):
            self.assertEqual(notify.desktop_output("Claude"),
                             {"terminalSequence": notify.desktop_sequence("Claude")})


if __name__ == "__main__":
    unittest.main()
