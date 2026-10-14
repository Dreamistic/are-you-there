"""English and Chinese strings. Everything the user or Claude reads goes through t()."""
import json
import locale
import os

from .config import config_home

STRINGS = {
    "en": {
        "ctx_terminal": "The user is at the terminal.",
        "ctx_present": "The user is at the computer (which window has focus is unknown).",
        "ctx_elsewhere_app": "The user is at the computer, but {app} has focus, not the terminal.",
        "ctx_elsewhere": "The user is at the computer, but another window has focus, not the terminal.",
        "ctx_away": "The user has been away from the computer for about {minutes} min.",
        "ctx_locked": "The user's screen is locked.",
        "ctx_answered": "They answered from their phone; the answers are in the tool result.",
        "ctx_returned": "They just came back to the terminal, so the question is shown there as usual.",
        "ctx_no_channel": "No phone channel is set up, so the question waits in the terminal.",
        "ctx_push_failed": "Sending to their phone failed, so the question waits in the terminal.",
        "ctx_pushed": "The question was sent to their phone and also waits in the terminal.",
        "ctx_timeout": "No answer from the phone within {timeout} min; the question now waits in the terminal.",
        "why_no_channel": "No phone channel is set up to reach them.",
        "why_push_failed": "Sending the question to their phone failed.",
        "why_timeout": "They didn't answer on the phone within {timeout} min.",
        "reason_decide": (
            "Don't wait for them. Continue with the safest, most reversible option, say which option you "
            "chose and why in your final message, and leave anything irreversible (deleting data, pushing, "
            "publishing, spending money) until they are back."
        ),
        "push_question": "[{project}] Claude has a question",
        "push_tap_to_answer": "Tap to answer",
        "push_button_more": "Details",
        "push_decided": "[{project}] Claude went ahead without you",
        "push_decided_body": "No answer in {timeout} min. Claude picked the safest reversible option and will explain.",
        "push_stop": "[{project}] Claude finished",
        "push_stop_empty": "Done. Check the terminal.",
        "push_attention": "[{project}] Claude needs you",
        "desktop_question": "Claude has a question",
        "desktop_stop": "Claude finished",
        "test_title": "are-you-there: test",
        "test_body": "If you can read this, notifications work.",
        "test_question": "Did the answer link work?",
        "test_header": "Test",
        "test_yes": "Yes",
        "test_yes_desc": "The answer reached the computer",
        "test_no": "Not really",
        "test_no_desc": "Something looked off",
        "level_terminal": "at the terminal",
        "level_present": "at the computer (focus unknown)",
        "level_elsewhere": "at the computer, focus elsewhere",
        "level_away": "away",
        "level_unknown": "unknown",
    },
    "zh": {
        "ctx_terminal": "用户在终端前。",
        "ctx_present": "用户在电脑前（不知道焦点在哪个窗口）。",
        "ctx_elsewhere_app": "用户在电脑前，但焦点在 {app}，不在终端。",
        "ctx_elsewhere": "用户在电脑前，但焦点在别的窗口，不在终端。",
        "ctx_away": "用户离开电脑约 {minutes} 分钟了。",
        "ctx_locked": "用户锁屏了。",
        "ctx_answered": "他在手机上答了，答案在工具结果里。",
        "ctx_returned": "他刚回到终端前，问题照常在终端里弹出。",
        "ctx_no_channel": "没有配置手机推送，问题在终端里等他。",
        "ctx_push_failed": "推到手机失败了，问题在终端里等他。",
        "ctx_pushed": "问题已推到他手机，终端里也在等他。",
        "ctx_timeout": "手机上 {timeout} 分钟没回，问题改在终端里等他。",
        "why_no_channel": "没有配置手机推送，联系不上他。",
        "why_push_failed": "问题推到他手机时失败了。",
        "why_timeout": "手机上 {timeout} 分钟没回。",
        "reason_decide": (
            "别等他了。选最稳、最容易撤回的那个选项继续做，在最后的回复里说清楚选了哪个、为什么；"
            "不可逆的事（删数据、push、发布、花钱）留到他回来再做。"
        ),
        "push_question": "[{project}] Claude 有个问题要问你",
        "push_tap_to_answer": "点开作答",
        "push_button_more": "详情",
        "push_decided": "[{project}] 没等到你，Claude 先往下做了",
        "push_decided_body": "{timeout} 分钟没回。Claude 选了最稳、能撤回的那条路，回来会跟你说明。",
        "push_stop": "[{project}] Claude 做完了",
        "push_stop_empty": "做完了，回终端看看。",
        "push_attention": "[{project}] Claude 需要你",
        "desktop_question": "Claude 有个问题要问你",
        "desktop_stop": "Claude 做完了",
        "test_title": "在吗：测试",
        "test_body": "看到这条，说明推送通了。",
        "test_question": "答题链接能用吗？",
        "test_header": "测试",
        "test_yes": "能用",
        "test_yes_desc": "答案传回电脑了",
        "test_no": "不太对",
        "test_no_desc": "哪里看着不对劲",
        "level_terminal": "在终端前",
        "level_present": "在电脑前（不知道焦点）",
        "level_elsewhere": "在电脑前，焦点在别处",
        "level_away": "离开了",
        "level_unknown": "不知道",
    },
}


def _claude_language() -> str:
    """The `language` setting from Claude Code's user settings, if any."""
    try:
        settings = json.loads((config_home() / "settings.json").read_text(encoding="utf-8"))
        return str(settings.get("language", ""))
    except (OSError, ValueError):
        return ""


def _looks_chinese(value: str) -> bool:
    value = value.strip().lower()
    return value.startswith("zh") or "chinese" in value or "中文" in value or "汉语" in value


def resolve(setting: str) -> str:
    if setting in STRINGS:
        return setting
    if _looks_chinese(_claude_language()):
        return "zh"
    candidates = [os.environ.get(k, "") for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
    try:
        candidates.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    return "zh" if any(_looks_chinese(c) for c in candidates) else "en"


def t(lang: str, key: str, **kwargs) -> str:
    text = STRINGS.get(lang, STRINGS["en"]).get(key) or STRINGS["en"][key]
    return text.format(**kwargs) if kwargs else text
