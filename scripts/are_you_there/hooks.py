"""Hook handlers. Each takes the hook's stdin payload and returns the JSON to print (or None)."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from . import config, i18n, notify, presence, relay
from .notify import Message


def _project(payload: dict) -> str:
    return Path(payload.get("cwd") or os.getcwd()).name or "Claude Code"


def _summary(questions: list) -> str:
    parts = []
    for q in questions:
        labels = " / ".join(str(o.get("label", "")) for o in q.get("options") or [])
        parts.append(str(q.get("question", "")).strip() + (f"  ({labels})" if labels else ""))
    return "\n".join(parts)[:900]


def describe(p: presence.Presence, cfg: dict, lang: str) -> str:
    """One sentence for Claude about where the user is."""
    if p.level == presence.TERMINAL:
        return i18n.t(lang, "ctx_terminal")
    if p.level == presence.PRESENT:
        return i18n.t(lang, "ctx_present")
    if p.level == presence.ELSEWHERE:
        if cfg["share_app_name"] and p.app:
            return i18n.t(lang, "ctx_elsewhere_app", app=p.app)
        return i18n.t(lang, "ctx_elsewhere")
    if p.level == presence.AWAY:
        text = i18n.t(lang, "ctx_away", minutes=max(1, p.away_minutes))
        return (i18n.t(lang, "ctx_locked") + " " + text) if p.locked else text
    return ""


def _context(*sentences: str) -> str:
    text = " ".join(s for s in sentences if s)
    return f'<presence source="are-you-there">{text}</presence>' if text else ""


def _allow(context: str = "", **extra) -> dict:
    out = {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
    if context:
        out["additionalContext"] = context
    out.update(extra)
    return {"hookSpecificOutput": out}


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


# ------------------------------------------------------------------ PreToolUse: AskUserQuestion

def pre_ask(payload: dict, cfg: dict) -> dict | None:
    lang = i18n.resolve(cfg["language"])
    tool_input = payload.get("tool_input") or {}
    questions = tool_input.get("questions") or []
    if not questions or tool_input.get("answers"):
        return None
    p = presence.detect(cfg)
    where = describe(p, cfg, lang)

    if p.level in (presence.UNKNOWN, presence.TERMINAL, presence.PRESENT):
        return _allow(_context(where)) if where else None

    if p.level == presence.ELSEWHERE:
        out = _allow(_context(where))
        if cfg["desktop_notify"]:
            first = str(questions[0].get("question", ""))
            out.update(notify.desktop_output(f"{i18n.t(lang, 'desktop_question')}: {first}"))
        return out

    # Away: try the phone.
    if not config.channels(cfg):
        return _no_answer(cfg, lang, where, "no_channel", payload)

    title = i18n.t(lang, "push_question", project=_project(payload))
    session = relay.new_session(cfg) if cfg["phone_answer"] else None
    link = relay.answer_url(cfg, session, questions, lang, _project(payload)) if session else ""
    buttons = relay.quick_buttons(session, questions, link, i18n.t(lang, "push_button_more")) if session else []
    body = _summary(questions) + (f"\n\n{i18n.t(lang, 'push_tap_to_answer')}" if link else "")
    sent = notify.send_all(cfg, Message(title=title, body=body, url=link, actions=buttons, urgent=True))
    if not sent:
        if session:
            relay.publish_state(session, "cancelled")
        return _no_answer(cfg, lang, where, "push_failed", payload)
    if not session:
        return _allow(_context(where, i18n.t(lang, "ctx_pushed")))

    def came_back() -> bool:
        return presence.detect(cfg).level == presence.TERMINAL

    timeout = cfg["answer_timeout_minutes"] * 60
    outcome, answers = relay.wait_for_answer(session, questions, cfg, timeout, came_back)
    if outcome == "answered":
        updated = dict(tool_input)
        updated["answers"] = answers
        return _allow(_context(where, i18n.t(lang, "ctx_answered")), updatedInput=updated)
    if outcome == "returned":
        return _allow(_context(i18n.t(lang, "ctx_returned")))
    if outcome == "unavailable":
        return _no_answer(cfg, lang, where, "reply_failed", payload)
    return _no_answer(cfg, lang, where, "timeout", payload)


def _minutes(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _no_answer(cfg, lang, where, cause, payload) -> dict:
    """Nobody can answer right now: wait at the terminal, or let Claude decide (when_no_answer).

    Only a delivered question that timed out can use the "decide" policy.
    """
    timeout = _minutes(cfg["answer_timeout_minutes"])
    if cfg["when_no_answer"] != "decide" or cause != "timeout":
        return _allow(_context(where, i18n.t(lang, f"ctx_{cause}", timeout=timeout)))
    if cause == "timeout":
        notify.send_all(cfg, Message(title=i18n.t(lang, "push_decided", project=_project(payload)),
                                     body=i18n.t(lang, "push_decided_body", timeout=timeout)))
    return _deny(_context(where, i18n.t(lang, f"why_{cause}", timeout=timeout)) + " " + i18n.t(lang, "reason_decide"))


# ------------------------------------------------------------------ Stop

def stop(payload: dict, cfg: dict) -> dict | None:
    if payload.get("stop_hook_active"):
        return None
    lang = i18n.resolve(cfg["language"])
    p = presence.detect(cfg)
    text = " ".join(str(payload.get("last_assistant_message") or "").split())

    if p.level == presence.ELSEWHERE and cfg["desktop_notify"]:
        return notify.desktop_output(f"{i18n.t(lang, 'desktop_stop')}: {text[:120]}") or None
    if p.level != presence.AWAY or not cfg["notify_on_stop"] or not config.channels(cfg):
        return None
    body = (text[:300] + ("…" if len(text) > 300 else "")) if text else i18n.t(lang, "push_stop_empty")
    _send_once("stop", text, payload, cfg,
               Message(title=i18n.t(lang, "push_stop", project=_project(payload)), body=body))
    return None


# ------------------------------------------------------------------ Notification

def notification(payload: dict, cfg: dict) -> dict | None:
    """Permission prompts and MCP elicitations block Claude; tell the phone if nobody is around."""
    if not cfg["notify_on_permission"] or not config.channels(cfg):
        return None
    if presence.detect(cfg).level != presence.AWAY:
        return None
    lang = i18n.resolve(cfg["language"])
    message = str(payload.get("message") or payload.get("title") or "")
    _send_once("notification", message, payload, cfg,
               Message(title=i18n.t(lang, "push_attention", project=_project(payload)),
                       body=message[:300], urgent=True))
    return None


def _send_once(kind: str, text: str, payload: dict, cfg: dict, message: Message,
               window: float = 600) -> None:
    """Drop repeated successful pushes within the same session for ten minutes."""
    path = config.data_dir() / "recent.json"
    scope = payload.get("session_id") or payload.get("transcript_path") or payload.get("cwd") or os.getcwd()
    identity = json.dumps([scope, text], ensure_ascii=False).encode("utf-8")
    key = kind + ":" + hashlib.sha1(identity).hexdigest()
    now = time.time()
    try:
        recent = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recent = {}
    recent = {k: v for k, v in recent.items() if now - v < window}
    if key in recent:
        return
    if not notify.send_all(cfg, message):
        return
    recent[key] = now
    try:
        path.write_text(json.dumps(recent), encoding="utf-8")
    except OSError:
        pass
