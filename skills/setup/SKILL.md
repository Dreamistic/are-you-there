---
name: setup
description: Set up are-you-there (在吗) - pick where phone notifications go (ntfy, Bark, or a Slack/Discord/Feishu/DingTalk/WeCom/ServerChan webhook), generate a private ntfy topic, save the config and send a test question to the phone. Use when the user asks to set up, configure, reconfigure or test are-you-there, or asks why phone notifications don't arrive.
---

# Set up are-you-there

Help the user get questions onto their phone. Talk in the user's language. Keep it short: they want it working, not a lecture.

The helper script is `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh"`. Run it with the Bash tool.

## 1. See where things stand

Run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" status`. It prints the detected presence level, which channels are configured, and the config file path.

- If presence is `unknown`, explain the note: over SSH, in containers and in WSL the plugin can't see the keyboard of the person, so it stays quiet. Phone answers still need presence to trigger, so setup can stop here unless they want to keep the config for later.
- If a channel is already configured, offer to run the test (step 4) instead of starting over.

## 2. Pick a channel

Ask with AskUserQuestion which channel they want:

- **ntfy** (recommended): free, no account, Android/iOS app, one-tap answer buttons. Works with the public ntfy.sh or a self-hosted server.
- **Bark**: iOS only, popular in China. One-way push; tapping it opens the answer page.
- **Webhook**: a bot in Slack, Discord, Feishu (飞书), DingTalk (钉钉), WeCom (企业微信), or ServerChan (Server 酱). The message carries the answer link.

Any channel can answer questions: answers travel back through a one-time ntfy topic regardless of which channel delivered the question.

For an authenticated relay, configure `relay_token` explicitly; it no longer inherits `ntfy_token`. Recommend a dedicated account restricted to the reply topics. Relay credentials are never included in phone links or action buttons: the phone page asks for a read/write relay credential and does not save it. Tell the user that authenticated relays use the answer page instead of native one-tap HTTP buttons.

## 3. Configure

For **ntfy**: run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" new-topic` to get a random topic. Tell the user to install the ntfy app and subscribe to exactly that topic on `https://ntfy.sh` (or their own server). The topic name is the only thing keeping strangers out, so never suggest a guessable name.

For **Bark**: ask for their Bark URL (`https://api.day.app/<key>`).

For **webhooks**: ask for the bot URL and which platform it is. DingTalk and Feishu bots usually require a security keyword; tell them to add `Claude` as the keyword.

Save the values. Two places work:

- If the plugin was installed from a marketplace, the same keys appear in `/config` under the plugin's options; the user can set them there.
- Otherwise, or if they prefer a file, write `~/.claude/are-you-there.json` (merge with any existing keys, don't drop them). Example:

```json
{
  "ntfy_topic": "ayt-1f3c9e0d2b7a48c6a5e1f0d9",
  "language": "auto"
}
```

Keys: `ntfy_topic`, `ntfy_server`, `ntfy_token`, `relay_server`, `relay_token`, `bark_url`, `webhook_url`, `webhook_format` (`json|slack|discord|feishu|dingtalk|wecom|serverchan`), `away_minutes`, `answer_timeout_minutes`, `when_no_answer` (`ask|decide`), `language` (`auto|en|zh`). `decide` applies only after successful delivery and a healthy timeout; channel/relay failures always return to the terminal. The README lists the advanced ones.

## 4. Test

Run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" test --ask`. It sends a test question to every channel and waits up to three minutes for the answer. Tell the user to answer it on the phone. The command prints `answered: {...}` on success.

If nothing arrives, check in this order: the topic in the app matches exactly; the phone allows notifications for the app; a proxy or firewall is not blocking ntfy.sh from the computer (the script honours HTTP(S)_PROXY).

If the page says the link is outdated, publish the updated `docs/answer.html` together with the plugin (or update the configured self-hosted copy). For an authenticated relay, check the explicit computer-side `relay_token` and the credential entered on the phone. A sent-but-unconfirmed answer is not proof that the computer received it; check the terminal.

Finish by telling them what happens from now on, in one or two sentences: when they're away for a few minutes, Claude's questions go to their phone; when they come back to the terminal, the question shows up there as usual.
