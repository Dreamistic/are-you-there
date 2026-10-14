---
name: status
description: Show what are-you-there (在吗) currently detects - whether the user counts as at the terminal, elsewhere on the computer, or away - and which phone channels are configured. Use when the user asks whether are-you-there is working, what it sees, or why it did or didn't notify them.
---

# are-you-there status

Run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" status` with the Bash tool and explain the result briefly in the user's language:

- `presence`: `terminal` (focused on the terminal, recent input), `present` (recent input, focus unknown), `elsewhere` (another app has focus), `away` (idle past the threshold or screen locked), `unknown` (SSH, container, WSL or a desktop the plugin can't read; it stays quiet).
- `idle` is seconds since the last keyboard or mouse input anywhere on the system.
- `focused` is the app that has focus; `on_terminal` says whether it is the terminal running this session.
- `channels` lists where questions would be pushed. `none` means questions always wait in the terminal; suggest `/are-you-there:setup`.

Note that the user is necessarily at the terminal while running this, so `presence` will usually read `terminal`. To watch it change, they can run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" watch` in another terminal and switch windows or step away.
