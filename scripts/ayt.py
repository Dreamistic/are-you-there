"""are-you-there command line.

Hook entry points (read the hook payload on stdin, print JSON on stdout):
  pre-ask        PreToolUse on AskUserQuestion
  stop           Stop
  notification   Notification (permission_prompt, elicitation_dialog)

For people:
  status         where you are right now, and the active config
  watch          print presence every second (Ctrl+C to stop)
  test           send a test notification to every channel
  test --ask     send a test question and wait for the answer from your phone
  new-topic      print a random ntfy topic name

Whatever happens, hooks exit 0: a broken plugin must never block Claude Code.
"""
import io
import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from are_you_there import __version__, config, hooks, i18n, notify, presence, relay  # noqa: E402


def _utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def _read_payload() -> dict:
    try:
        return json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (OSError, ValueError):
        return {}


def run_hook(name: str) -> None:
    handler = {"pre-ask": hooks.pre_ask, "stop": hooks.stop, "notification": hooks.notification}[name]
    try:
        out = handler(_read_payload(), config.load())
    except Exception as error:
        print(f"are-you-there: {name} hook failed: {error}", file=sys.stderr)
        return
    if out:
        sys.stdout.write(json.dumps(out, ensure_ascii=False))


def status() -> None:
    cfg = config.load()
    lang = i18n.resolve(cfg["language"])
    p = presence.detect(cfg)
    idle = "?" if p.idle is None else f"{p.idle:.0f}s"
    print(f"are-you-there {__version__}  ({sys.platform}, Python {sys.version.split()[0]})")
    print(f"presence : {p.level} ({i18n.t(lang, 'level_' + p.level)})")
    print(f"idle     : {idle}")
    print(f"focused  : {p.app or '?'}  on_terminal={p.on_terminal}  locked={p.locked}")
    if p.reason:
        print(f"note     : {p.reason}")
    print(f"language : {lang}")
    print(f"channels : {', '.join(config.channels(cfg)) or 'none'}")
    print(f"config   : {config.config_path()}")
    print(json.dumps(config.masked(cfg), ensure_ascii=False, indent=2))


def watch() -> None:
    cfg = config.load()
    try:
        while True:
            p = presence.detect(cfg)
            idle = "?" if p.idle is None else f"{p.idle:5.0f}s"
            print(f"\r{p.level:<9} idle {idle}  {p.app[:40]:<40}", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print()


def test(ask: bool) -> int:
    cfg = config.load()
    lang = i18n.resolve(cfg["language"])
    if not config.channels(cfg):
        print("No channel configured. Set ntfy_topic, bark_url or webhook_url first.")
        return 1
    if not ask:
        sent = notify.send_all(cfg, notify.Message(title=i18n.t(lang, "test_title"), body=i18n.t(lang, "test_body")))
        print(f"sent via: {', '.join(sent) or 'nothing (see errors above)'}")
        return 0 if sent else 1

    questions = [{"question": i18n.t(lang, "test_question"), "header": i18n.t(lang, "test_header"), "multiSelect": False,
                  "options": [{"label": i18n.t(lang, "test_yes"), "description": i18n.t(lang, "test_yes_desc")},
                              {"label": i18n.t(lang, "test_no"), "description": i18n.t(lang, "test_no_desc")}]}]
    session = relay.new_session(cfg, timeout=180)
    link = relay.answer_url(cfg, session, questions, lang)
    buttons = relay.quick_buttons(session, questions, link, i18n.t(lang, "push_button_more"))
    sent = notify.send_all(cfg, notify.Message(title=i18n.t(lang, "test_title"), body=questions[0]["question"],
                                               url=link, actions=buttons, urgent=True))
    print(f"sent via: {', '.join(sent) or 'nothing'}")
    if link:
        print(f"answer page: {link}")
    if not sent:
        return 1
    print("waiting up to 3 minutes for your answer…")
    outcome, answers = relay.wait_for_answer(session, questions, cfg, 180, lambda: False)
    print(f"{outcome}: {json.dumps(answers, ensure_ascii=False) if answers else ''}")
    return 0 if outcome == "answered" else 1


def main(argv: list) -> int:
    _utf8_stdio()
    command = argv[1] if len(argv) > 1 else "status"
    if command in ("pre-ask", "stop", "notification"):
        run_hook(command)
        return 0
    if command == "status":
        status()
    elif command == "watch":
        watch()
    elif command == "test":
        return test("--ask" in argv)
    elif command == "new-topic":
        print("ayt-" + secrets.token_hex(12))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
