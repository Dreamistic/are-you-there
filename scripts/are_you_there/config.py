"""Configuration.

Precedence, lowest to highest:
  built-in defaults
  < config file (~/.claude/are-you-there.json, or $ARE_YOU_THERE_CONFIG)
  < plugin options set in Claude Code (exported as CLAUDE_PLUGIN_OPTION_<KEY>)
  < ARE_YOU_THERE_<KEY> environment variables (handy for testing)

Empty strings never override a lower layer, so a blank plugin option falls
back to the config file.
"""
import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    "language": "auto",              # auto | en | zh
    "away_minutes": 3,               # idle this long with another app focused -> away
    "reading_minutes": 5,            # idle this long with the terminal focused -> away
    "answer_timeout_minutes": 30,    # how long a question waits for a phone answer
    "when_no_answer": "ask",         # ask: fall back to the terminal dialog / decide: let Claude pick
    "phone_answer": True,            # include an answer link / buttons and wait for the reply
    "allow_free_text": False,        # accept typed answers from the phone, not only option labels
    "share_app_name": True,          # tell Claude which app is focused when you're elsewhere
    "desktop_notify": True,          # terminal desktop notification when you're elsewhere
    "notify_on_stop": True,          # push when Claude finishes a turn while you're away
    "notify_on_permission": True,    # push when Claude waits on a permission prompt while you're away
    "terminal_apps": [],             # extra process names that count as "the terminal"
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",
    "ntfy_token": "",
    "bark_url": "",
    "webhook_url": "",
    "webhook_format": "json",        # json | slack | discord | feishu | dingtalk | wecom | serverchan
    "relay_server": "",              # where phone answers travel back; defaults to ntfy_server
    "relay_token": "",
    "answer_page": "https://dreamistic.github.io/are-you-there/answer.html",
}

SECRET_KEYS = {"ntfy_topic", "ntfy_token", "relay_token", "bark_url", "webhook_url"}
MAX_ANSWER_TIMEOUT_MINUTES = 110     # hooks.json gives the hook 120 minutes


def config_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def config_path() -> Path:
    return Path(os.environ.get("ARE_YOU_THERE_CONFIG") or config_home() / "are-you-there.json")


def data_dir() -> Path:
    path = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or config_home() / "are-you-there")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _coerce(raw, default):
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default
    if isinstance(default, list):
        if isinstance(raw, list):
            return [str(x) for x in raw]
        return [part.strip() for part in str(raw).split(",") if part.strip()]
    return str(raw).strip()


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        for key, value in data.items():
            if key in DEFAULTS and value not in ("", None):
                cfg[key] = _coerce(value, DEFAULTS[key])
    except (OSError, ValueError):
        pass
    for prefix in ("CLAUDE_PLUGIN_OPTION_", "ARE_YOU_THERE_"):
        for key, default in DEFAULTS.items():
            raw = os.environ.get(prefix + key.upper())
            if raw is not None and raw.strip() != "":
                cfg[key] = _coerce(raw, default)
    cfg["answer_timeout_minutes"] = max(0.1, min(cfg["answer_timeout_minutes"], MAX_ANSWER_TIMEOUT_MINUTES))
    cfg["ntfy_server"] = cfg["ntfy_server"].rstrip("/")
    cfg["relay_server"] = (cfg["relay_server"] or cfg["ntfy_server"]).rstrip("/")
    if cfg["phone_answer"] and cfg["ntfy_token"] and not cfg["relay_token"] and cfg["relay_server"] == cfg["ntfy_server"]:
        print("are-you-there: ntfy_token is no longer reused for phone answers; "
              "set relay_token separately if the relay requires authentication", file=sys.stderr)
    return cfg


def channels(cfg: dict) -> list:
    """Names of the push channels that are configured."""
    found = []
    if cfg["ntfy_topic"]:
        found.append("ntfy")
    if cfg["bark_url"]:
        found.append("bark")
    if cfg["webhook_url"]:
        found.append("webhook")
    return found


def masked(cfg: dict) -> dict:
    """Config safe to print: secrets shortened."""
    out = {}
    for key, value in cfg.items():
        if key in SECRET_KEYS and value:
            value = str(value)
            out[key] = value[:12] + "…" if len(value) > 12 else "…"
        else:
            out[key] = value
    return out
