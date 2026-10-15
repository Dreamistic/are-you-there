"""Question lifetime, delivery policy, and credential-boundary regressions."""
import base64
import io
import json
import os
import queue
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from test_are_you_there import Env, QUESTION
from are_you_there import config, hooks, notify, relay


class CredentialBoundaryTest(unittest.TestCase):
    def test_notification_credentials_are_not_implicitly_reused_for_the_relay(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
                "ARE_YOU_THERE_CONFIG": str(Path(tmp) / "none.json"),
                "ARE_YOU_THERE_NTFY_TOKEN": "primary-notification-secret"}, clear=True), \
                redirect_stderr(io.StringIO()) as errors:
            cfg = config.load()
            self.assertEqual(cfg["relay_token"], "")
            self.assertIn("relay_token", errors.getvalue())
            with mock.patch.dict(os.environ, {"ARE_YOU_THERE_RELAY_TOKEN": "explicit-relay-secret"}):
                self.assertEqual(config.load()["relay_token"], "explicit-relay-secret")

    def test_links_and_notification_buttons_never_contain_the_relay_credential(self):
        cfg = dict(config.DEFAULTS, relay_server="https://example.test", relay_token="private-relay-secret")
        session = relay.new_session(cfg, timeout=180)
        link = relay.answer_url(cfg, session, [QUESTION], "en")
        fragment = link.split("#", 1)[1]
        decoded = json.loads(base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4)))
        self.assertEqual(decoded["v"], 2)
        self.assertTrue(decoded["auth"])
        self.assertEqual(decoded["e"], session.expires_at)
        self.assertNotIn("k", decoded)
        self.assertNotIn(session.token, json.dumps(decoded))
        buttons = relay.quick_buttons(session, [QUESTION], link, "Details")
        self.assertEqual([b["action"] for b in buttons], ["view"])
        self.assertNotIn(session.token, json.dumps(buttons))

    def test_expiry_is_enforced_by_the_computer(self):
        session = relay.Session(server="https://example.test", topic="t", nonce="n", expires_at=1000)
        answer = json.dumps({"n": "n", "a": {QUESTION["question"]: "SQLite"}})
        with mock.patch.object(relay.time, "time", return_value=999):
            self.assertEqual(relay.validate(answer, session, [QUESTION], False), {QUESTION["question"]: "SQLite"})
        with mock.patch.object(relay.time, "time", return_value=1000):
            self.assertIsNone(relay.validate(answer, session, [QUESTION], False))


class AnswerLifetimeTest(Env):
    def session(self, timeout=10):
        return relay.new_session(self.cfg(), timeout=timeout)

    def cached_answer(self, session):
        self.ntfy.publish(session.topic, json.dumps({"n": session.nonce, "a": {QUESTION["question"]: "SQLite"}}))

    def receipts(self, session):
        return [json.loads(body) for _, body in self.ntfy.topics.get(session.topic, []) if '"s"' in body]

    def test_accepted_answer_publishes_a_receipt(self):
        session = self.session()
        self.cached_answer(session)
        result = relay.wait_for_answer(session, [QUESTION], self.cfg(), 5, lambda: False)
        self.assertEqual(result, ("answered", {QUESTION["question"]: "SQLite"}))
        self.assertEqual(self.receipts(session), [{"n": session.nonce, "s": "accepted"}])

    def test_missing_receipt_does_not_discard_the_selected_answer(self):
        session = self.session()
        self.cached_answer(session)
        with mock.patch.object(relay, "publish_state", return_value=False) as publish:
            result = relay.wait_for_answer(session, [QUESTION], self.cfg(), 5, lambda: False)
        self.assertEqual(result, ("answered", {QUESTION["question"]: "SQLite"}))
        publish.assert_called_once_with(session, "accepted")

    def test_a_queued_answer_wins_over_a_subsequent_reconnect_failure(self):
        session = self.session()
        listener = mock.Mock(messages=queue.Queue(), connected=threading.Event(), unavailable=threading.Event())
        listener.messages.put(json.dumps({"n": session.nonce, "a": {QUESTION["question"]: "SQLite"}}))
        listener.unavailable.set()
        with mock.patch.object(relay, "Listener", return_value=listener), \
                mock.patch.object(relay, "publish_state") as publish:
            result = relay.wait_for_answer(session, [QUESTION], self.cfg(), 5, lambda: False)
        self.assertEqual(result, ("answered", {QUESTION["question"]: "SQLite"}))
        publish.assert_called_once_with(session, "accepted")

    def test_return_to_terminal_cancels_the_phone_question(self):
        session = self.session()
        result = relay.wait_for_answer(session, [QUESTION], self.cfg(), 5, lambda: True)
        self.assertEqual(result, ("returned", None))
        self.assertEqual(self.receipts(session), [{"n": session.nonce, "s": "cancelled"}])

    def test_healthy_timeout_publishes_expiry(self):
        session = self.session(timeout=0.2)
        self.assertEqual(relay.wait_for_answer(session, [QUESTION], self.cfg(), 1, lambda: False), ("timeout", None))
        self.assertEqual(self.receipts(session), [{"n": session.nonce, "s": "expired"}])

    def test_question_already_expired_before_waiting_cannot_trigger_decide(self):
        session = self.session(timeout=-1)
        with mock.patch.object(relay, "Listener") as listener:
            result = relay.wait_for_answer(session, [QUESTION], self.cfg(), 5, lambda: False)
        self.assertEqual(result, ("unavailable", None))
        listener.assert_not_called()

    def test_authentication_failure_returns_without_waiting_for_the_question_timeout(self):
        session = self.session()
        failure = urllib.error.HTTPError(session.url, 401, "Unauthorized", {}, None)
        with mock.patch.object(relay, "http", side_effect=failure), redirect_stderr(io.StringIO()):
            started = time.monotonic()
            result = relay.wait_for_answer(session, [QUESTION], self.cfg(), 30, lambda: False)
        self.assertEqual(result, ("unavailable", None))
        self.assertLess(time.monotonic() - started, 2)

    def test_deadline_probe_failure_is_a_transport_failure(self):
        listener = mock.Mock(messages=queue.Queue(), connected=threading.Event(), unavailable=threading.Event())
        listener.connected.set()
        session = self.session()
        with mock.patch.object(relay, "Listener", return_value=listener), \
                mock.patch.object(relay, "_relay_available", return_value=False), \
                mock.patch.object(relay, "publish_state") as publish:
            result = relay.wait_for_answer(session, [QUESTION], self.cfg(), 0.01, lambda: False)
        self.assertEqual(result, ("unavailable", None))
        listener.stop.assert_called_once()
        publish.assert_called_once_with(session, "unavailable")


class NoAnswerPolicyTest(Env):
    def test_failed_push_always_returns_to_terminal_even_in_decide_mode(self):
        self.level("away")
        with mock.patch.object(notify, "send_all", return_value=[]), \
                mock.patch.object(relay, "publish_state"), mock.patch.object(relay, "wait_for_answer") as wait:
            out = hooks.pre_ask({"tool_input": {"questions": [QUESTION]}}, self.cfg(when_no_answer="decide"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")
        wait.assert_not_called()

    def test_unavailable_reply_relay_never_triggers_decide(self):
        self.level("away")
        with mock.patch.object(notify, "send_all", return_value=["ntfy"]), \
                mock.patch.object(relay, "wait_for_answer", return_value=("unavailable", None)):
            out = hooks.pre_ask({"tool_input": {"questions": [QUESTION]}}, self.cfg(when_no_answer="decide"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertIn("relay is unavailable", out["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
