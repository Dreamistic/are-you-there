"""End-to-end check against a real Claude Code (costs a few cents of model usage).

Starts a mock ntfy server, runs `claude -p` with this plugin loaded and the
presence forced to "away", taps the "Blue" button the way the phone would,
and checks that Claude saw the answer.

    python tests/e2e_claude.py [--model sonnet]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mock_ntfy import MockNtfy  # noqa: E402

PROMPT = ("Use the AskUserQuestion tool to ask me one question: 'Which color?' with header 'Color' and "
          "exactly two options, 'Red' and 'Blue'. After I answer, reply with only the color I picked.")


def main() -> int:
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "sonnet"
    ntfy = MockNtfy()
    tmp = tempfile.mkdtemp(prefix="ayt-e2e-")
    env = dict(os.environ,
               ARE_YOU_THERE_FORCE_LEVEL="away",
               ARE_YOU_THERE_NTFY_SERVER=ntfy.url,
               ARE_YOU_THERE_NTFY_TOPIC="phone",
               ARE_YOU_THERE_RELAY_SERVER=ntfy.url,
               ARE_YOU_THERE_ANSWER_TIMEOUT_MINUTES="3",
               ARE_YOU_THERE_LANGUAGE="en",
               ARE_YOU_THERE_CONFIG=str(Path(tmp) / "none.json"),
               CLAUDE_PLUGIN_DATA=tmp)

    def tap_blue():
        push = ntfy.wait_published(timeout=180)[0]
        print("phone got:", push["title"], "|", push["message"].splitlines()[0])
        button = next(b for b in push["actions"] if b["label"] == "Blue")
        ntfy.publish(button["url"].rsplit("/", 1)[1], button["body"])
        print("phone tapped: Blue")

    threading.Thread(target=tap_blue, daemon=True).start()
    # In -p mode Claude Code offers AskUserQuestion only when a permission host is attached, so act as
    # one over stdio (the Agent SDK's protocol). If the plugin works, the host is never asked about
    # AskUserQuestion: the hook answers it first.
    # The binary running this session if there is one; a stale `claude` earlier on PATH is common.
    claude = os.environ.get("AYT_CLAUDE") or os.environ.get("CLAUDE_CODE_EXECPATH") or shutil.which("claude") or "claude"
    print("using:", claude)
    proc = subprocess.Popen([claude, "-p", "--plugin-dir", str(ROOT), "--model", model, "--verbose",
                             "--input-format", "stream-json", "--output-format", "stream-json",
                             "--permission-prompt-tool", "stdio",
                             # skip user-level settings so your own hooks don't mix in
                             "--setting-sources", "project,local"] + (["--debug-file", os.environ["AYT_DEBUG_FILE"]] if os.environ.get("AYT_DEBUG_FILE") else []),
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8")
    send = lambda obj: (proc.stdin.write(json.dumps(obj) + "\n"), proc.stdin.flush())  # noqa: E731
    send({"type": "user", "message": {"role": "user", "content": PROMPT}})
    reply, host_asked = "", []
    timer = threading.Timer(300, proc.kill)
    timer.start()
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "control_request":
            request = event.get("request", {})
            if request.get("subtype") == "can_use_tool":
                host_asked.append(request.get("tool_name"))
                behavior = {"behavior": "deny", "message": "e2e: the hook should have answered"} \
                    if request.get("tool_name") == "AskUserQuestion" else {"behavior": "allow", "updatedInput": request.get("input", {})}
            else:
                behavior = {}
            send({"type": "control_response", "response": {"subtype": "success", "request_id": event.get("request_id"),
                                                           "response": behavior}})
        elif event.get("type") == "result":
            reply = event.get("result", "")
            break
    proc.stdin.close()
    timer.cancel()
    proc.wait(10)
    ntfy.close()
    print("claude replied:", reply.strip()[:200])
    if host_asked:
        print("permission host was asked about:", host_asked)
    ok = "blue" in reply.lower() and "red" not in reply.lower() and "AskUserQuestion" not in host_asked
    if not ok:
        print(proc.stderr.read()[-2000:])
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
