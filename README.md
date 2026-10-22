# AI Dashboard

A local web dashboard that shows every AI coding-agent session on your machine in one place —
**Claude Code**, **OpenAI Codex CLI**, **GitHub Copilot CLI** and **OpenClaw** — with token usage,
context-window fill and rate limits, and one-click resume or cleanup.

![Python](https://img.shields.io/badge/python-3.9+-blue) ![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen) ![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)

## Why

Running several agent CLIs side by side leaves sessions scattered across `~/.claude`, `~/.codex`,
`~/.copilot` and `~/.openclaw`, each with its own format and its own idea of how close you are to a
limit. This dashboard reads them all directly from disk and puts them in one table per tool.

## Features

- **One view for four CLIs** — a panel per tool; tools that are not installed show an install hint
  instead of an error.
- **Context-window fill** — tokens per session against the model's context window, colour-coded
  green / amber / red.
- **Rate limits** — Claude Code (status and reset time) and Codex (5-hour and weekly remaining, plan).
- **Resume in a terminal** — reopens a session in its original working directory
  (`claude --resume`, `codex resume`, `openclaw tui --session`).
- **Cleanup** — deletes Claude Code and Copilot sessions, archives Codex threads, removes OpenClaw entries.
- **Search and sort** — a global filter across all panels; click a column header to sort.
- **Auto-refresh** every 30 s from cache; the refresh button fetches fresh rate limits.

## Quick start

```bash
python3 dashboard.py
# Dashboard: http://localhost:7788
```

Change the port with `PORT=8080 python3 dashboard.py`. Requires Python 3.9+ and at least one of
`claude`, `codex`, `gh` (with the Copilot extension) or `openclaw` on the `PATH`.

The UI is in Bulgarian.

## How it works

A single file, standard library only — `http.server` serves the page, `sqlite3` reads Codex state,
and a minimal hand-written WebSocket client talks to the Codex app server. No build step, no `pip install`.

| Tool | Sessions come from | Rate limits come from |
|---|---|---|
| Claude Code | `~/.claude/projects/*/*.jsonl` | `claude -p` → `rate_limit_event` in stream-json |
| Codex CLI | `~/.codex/state_5.sqlite` (`threads`) | `codex app-server` → JSON-RPC over WebSocket |
| Copilot CLI | `~/.copilot/session-state/*/` | — |
| OpenClaw | `~/.openclaw/agents/main/sessions/sessions.json` | — |

Rate-limit lookups take a few seconds, so normal page loads read a cache
(`~/.claude/.rate_limits_cache.json`, `~/.codex/.rate_limits_cache.json`) and only the refresh
button queries the CLIs. The Claude probe sends a one-word prompt and removes the session file it
creates.

## Notes

- The server binds to `localhost` only and has no authentication — it is meant for your own machine.
- Deleting a session removes its files from disk; there is no undo.
- Terminal resume uses Terminal.app on macOS and the first of gnome-terminal, xterm, kitty,
  alacritty or xfce4-terminal on Linux.
