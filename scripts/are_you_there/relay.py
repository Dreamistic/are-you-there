"""Answers from the phone.

Each question gets a fresh random ntfy topic and a nonce. The phone sends
{"n": nonce, "a": {question: answer}} to that topic, either from an ntfy
button or from the static answer page. The hook listens on the topic and
accepts only answers carrying the right nonce whose values are among the
offered option labels (free text is off unless allow_free_text is set).

The question itself travels to the answer page in the URL fragment, which
browsers never send to the server hosting the page.
"""
import base64
import json
import queue
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from .notify import http

MAX_FREE_TEXT = 500


@dataclass
class Session:
    server: str
    topic: str
    nonce: str
    token: str = ""

    @property
    def url(self) -> str:
        return f"{self.server}/{self.topic}"


def new_session(cfg: dict) -> Session:
    return Session(server=cfg["relay_server"], topic="ayt-" + secrets.token_hex(16),
                   nonce=secrets.token_hex(6), token=cfg["relay_token"])


def compact_questions(questions: list) -> list:
    """Shrink AskUserQuestion's questions for the answer page URL."""
    out = []
    for q in questions:
        out.append({
            "h": str(q.get("header", ""))[:40],
            "q": str(q.get("question", "")),
            "m": bool(q.get("multiSelect")),
            "o": [[str(o.get("label", "")), str(o.get("description", ""))[:200]] for o in q.get("options") or []],
        })
    return out


def answer_url(cfg: dict, session: Session, questions: list, lang: str, project: str = "") -> str:
    if not cfg["answer_page"]:
        return ""
    data = {"v": 1, "r": session.server, "t": session.topic, "n": session.nonce, "l": lang,
            "x": bool(cfg["allow_free_text"]), "p": project[:60], "q": compact_questions(questions)}
    if session.token:
        data["k"] = session.token
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return cfg["answer_page"] + "#" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def quick_buttons(session: Session, questions: list, answer_link: str, more_label: str) -> list:
    """One-tap ntfy buttons when there is a single question with few enough options."""
    if len(questions) != 1 or questions[0].get("multiSelect"):
        return []
    q = questions[0]
    labels = [str(o.get("label", "")) for o in q.get("options") or []]
    if not labels or len(labels) > 3:  # ntfy shows at most three buttons
        return []
    headers = {"Authorization": f"Bearer {session.token}"} if session.token else {}
    buttons = [{"action": "http", "label": label[:40], "url": session.url, "method": "POST",
                "headers": headers, "clear": True,
                "body": json.dumps({"n": session.nonce, "a": {q.get("question", ""): label}}, ensure_ascii=False)}
               for label in labels]
    # A spare slot opens the page with the option descriptions; tapping the notification does too.
    if answer_link and len(buttons) < 3:
        buttons.append({"action": "view", "label": more_label, "url": answer_link, "clear": True})
    return buttons


def validate(message: str, session: Session, questions: list, allow_free_text: bool) -> Optional[dict]:
    """Return {question: answer} if the message is a complete, legitimate answer; else None."""
    try:
        data = json.loads(message)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("n") != session.nonce or not isinstance(data.get("a"), dict):
        return None
    given, answers = data["a"], {}
    for q in questions:
        text = str(q.get("question", ""))
        labels = [str(o.get("label", "")) for o in q.get("options") or []]
        raw = given.get(text)
        parts = raw if isinstance(raw, list) else [raw] if raw is not None else []
        parts = [str(p).strip() for p in parts if str(p).strip()]
        if not parts:
            return None
        if not q.get("multiSelect") and len(parts) != 1:
            return None
        for part in parts:
            if part in labels:
                continue
            if not allow_free_text or len(part) > MAX_FREE_TEXT or any(ord(c) < 32 for c in part):
                return None
        answers[text] = ", ".join(parts)
    return answers


class Listener(threading.Thread):
    """Streams the session topic and queues every message body."""

    def __init__(self, session: Session):
        super().__init__(daemon=True)
        self.session = session
        self.messages: "queue.Queue[str]" = queue.Queue()
        self.since = "all"

    def run(self):
        headers = {"Authorization": f"Bearer {self.session.token}"} if self.session.token else {}
        delay = 1.0
        while True:
            url = f"{self.session.url}/json?since={urllib.parse.quote(self.since)}"
            try:
                # ntfy sends a keepalive every ~45 s, so a 90 s read timeout only trips on a dead link.
                with http(url, headers=headers, timeout=90) as response:
                    delay = 1.0
                    for line in response:
                        event = json.loads(line.decode("utf-8") or "{}")
                        if event.get("event") == "message":
                            self.since = event.get("id", self.since)
                            self.messages.put(event.get("message", ""))
            except Exception:
                time.sleep(delay)
                delay = min(delay * 2, 30)


def wait_for_answer(session: Session, questions: list, cfg: dict, timeout: float, came_back) -> tuple:
    """Block until a valid answer arrives, the user returns, or time runs out.

    Returns ("answered", answers), ("returned", None) or ("timeout", None).
    `came_back` is a zero-argument callable checked every couple of seconds.
    """
    listener = Listener(session)
    listener.start()
    deadline = time.monotonic() + timeout
    next_check = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            message = listener.messages.get(timeout=0.5)
            answers = validate(message, session, questions, cfg["allow_free_text"])
            if answers:
                return "answered", answers
        except queue.Empty:
            pass
        if time.monotonic() >= next_check:
            next_check = time.monotonic() + 2
            if came_back():
                return "returned", None
    return "timeout", None
