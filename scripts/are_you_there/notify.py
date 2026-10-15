"""Push channels: ntfy, Bark, and webhooks (Slack, Discord, Feishu, DingTalk, WeCom, ServerChan, plain JSON).

Every sender takes the same Message and returns True on success. Failures are
swallowed: a broken channel must never break Claude Code.
"""
import base64
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

from .config import channels

USER_AGENT = "are-you-there/0.1"


@dataclass
class Message:
    title: str
    body: str
    url: str = ""                  # answer page or anything worth opening
    actions: List[dict] = field(default_factory=list)  # ntfy buttons
    urgent: bool = False


def http(url: str, data: Optional[bytes] = None, headers: Optional[dict] = None,
         method: Optional[str] = None, timeout: float = 8):
    """urlopen that honours HTTP(S)_PROXY for the internet but never proxies localhost."""
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"User-Agent": USER_AGENT, **(headers or {})})
    host = urllib.parse.urlparse(url).hostname or ""
    handlers = [urllib.request.ProxyHandler({})] if host in ("localhost", "127.0.0.1", "::1") else []
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)


def _response_ok(response, success_fields: tuple = ()) -> bool:
    if not 200 <= response.status < 300:
        return False
    if not success_fields:
        return True
    try:
        result = json.load(response)
    except (ValueError, UnicodeError):
        print("are-you-there: channel returned invalid JSON", file=sys.stderr)
        return False
    if isinstance(result, dict):
        for key in success_fields:
            if key in result:
                if result[key] == 0:
                    return True
                break
    print("are-you-there: channel rejected the notification", file=sys.stderr)
    return False


def _post_json(url: str, payload: dict, headers: Optional[dict] = None,
               success_fields: tuple = ()) -> bool:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with http(url, data, {"Content-Type": "application/json", **(headers or {})}, "POST") as response:
        return _response_ok(response, success_fields)


def _plain(message: Message, link_label: str = "") -> str:
    text = f"{message.title}\n{message.body}".strip()
    if message.url:
        text += f"\n{link_label + ': ' if link_label else ''}{message.url}"
    return text


def send_ntfy(cfg: dict, message: Message) -> bool:
    payload = {"topic": cfg["ntfy_topic"], "title": message.title, "message": message.body,
               "tags": ["raising_hand" if message.urgent else "white_check_mark"]}
    if message.urgent:
        payload["priority"] = 4
    if message.url:
        payload["click"] = message.url
    if message.actions:
        payload["actions"] = message.actions[:3]  # ntfy shows at most three buttons
    headers = {"Authorization": f"Bearer {cfg['ntfy_token']}"} if cfg["ntfy_token"] else {}
    return _post_json(cfg["ntfy_server"] + "/", payload, headers)


def send_bark(cfg: dict, message: Message) -> bool:
    payload = {"title": message.title, "body": message.body, "group": "are-you-there"}
    if message.url:
        payload["url"] = message.url
    if message.urgent:
        payload["level"] = "timeSensitive"
    return _post_json(cfg["bark_url"].rstrip("/"), payload)


def send_webhook(cfg: dict, message: Message) -> bool:
    url, fmt = cfg["webhook_url"], cfg["webhook_format"].lower()
    text = _plain(message)
    if fmt == "slack":
        return _post_json(url, {"text": text})
    if fmt == "discord":
        if message.url and len(text) > 2000:
            # Embeds allow 4096 characters in their description; never cut the fragment.
            if len(message.url) > 4096:
                raise ValueError("answer link exceeds Discord's 4096-character embed limit")
            summary = f"{message.title}\n{message.body}".strip()
            return _post_json(url, {"content": summary[:2000],
                                    "embeds": [{"description": message.url}]})
        return _post_json(url, {"content": text[:2000]})
    if fmt == "feishu":
        return _post_json(url, {"msg_type": "text", "content": {"text": text}},
                          success_fields=("code", "StatusCode"))
    if fmt in ("dingtalk", "wecom"):
        return _post_json(url, {"msgtype": "text", "text": {"content": text}},
                          success_fields=("errcode",))
    if fmt == "serverchan":
        desp = message.body + (f"\n\n[{message.url}]({message.url})" if message.url else "")
        data = urllib.parse.urlencode({"title": message.title, "desp": desp}).encode("utf-8")
        with http(url, data, {"Content-Type": "application/x-www-form-urlencoded"}, "POST") as response:
            return _response_ok(response, ("code",))
    return _post_json(url, {"title": message.title, "body": message.body, "url": message.url})


SENDERS = {"ntfy": send_ntfy, "bark": send_bark, "webhook": send_webhook}


def send_all(cfg: dict, message: Message) -> List[str]:
    """Send to every configured channel; return the names that worked."""
    sent = []
    for name in channels(cfg):
        try:
            if SENDERS[name](cfg, message):
                sent.append(name)
        except Exception as error:
            print(f"are-you-there: {name} failed: {error}", file=sys.stderr)
    return sent


def desktop_sequence(text: str) -> str:
    """OSC 9 for terminals such as iTerm2 and WezTerm, plus a bell."""
    clean = "".join(ch for ch in text if ch >= " " and ch not in "\x7f\x9c")[:180]
    # OSC 9;4 means "progress" to Windows Terminal; never start with a digit.
    if clean[:1].isdigit():
        clean = "· " + clean
    return f"\x1b]9;{clean}\x07\x07"


def desktop_output(text: str) -> dict:
    """Use Windows notifications directly; other platforms notify through the terminal."""
    if sys.platform == "win32":
        return {} if _windows_desktop(text) else {"terminalSequence": "\x07"}
    return {"terminalSequence": desktop_sequence(text)}


def _windows_desktop(text: str) -> bool:
    # Windows PowerShell and WinRT ship with Windows 10/11. Keep text on stdin so
    # question content is never interpreted as PowerShell or XML markup.
    script = r"""
$ErrorActionPreference = 'Stop'
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
    $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd()))
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>Claude Code</text><text></text></binding></visual></toast>')
    $xml.GetElementsByTagName('text').Item(1).InnerText = $text
    $app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app)
    $notifier.Show([Windows.UI.Notifications.ToastNotification]::new($xml))
    exit 0
} catch { exit 1 }
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-EncodedCommand", base64.b64encode(script.encode("utf-16le")).decode("ascii")],
            input=base64.b64encode(text[:180].encode("utf-8")), capture_output=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
