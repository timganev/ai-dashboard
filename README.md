# AI Dashboard

Локален уеб дашборд за мониторинг на **Claude Code**, **OpenClaw**, **Codex CLI** и **GitHub Copilot CLI** сесии.

![Python](https://img.shields.io/badge/python-3.8+-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## Какво показва

| Панел | Информация |
|-------|-----------|
| **OpenClaw** | Сесии, модел, токени + прогрес бар, последна активност |
| **Claude Code** | Session ID, директория, модел, токени, rate limits (5ч лимит + reset) |
| **Codex CLI** | Session ID, директория, модел, токени, rate limits (5ч + седмичен + план) |
| **GitHub Copilot CLI** | Session ID, директория, модел, токени, последна активност |

### Функционалности

- 🔍 **Търсене** — глобален филтър по всички панели
- ↕️ **Сортиране** — клик на заглавие на колона (asc/desc)
- 📊 **Rate limits** — Claude (статус + reset) и Codex (% + reset + план)
- ⌨️ **Терминал** — отваря сесията директно в Terminal.app / gnome-terminal
- ✕ **Изтриване** — трие сесии за Claude, Codex, Copilot
- 🔄 **Auto-refresh** — на 30 сек (от кеш); бутон „Обнови" за свежи лимити

## Изисквания

- Python 3.8+ (без допълнителни пакети — само stdlib)
- macOS или Linux
- Поне един от: `claude`, `openclaw`, `codex`, `gh` (Copilot)

## Стартиране

```bash
python3 dashboard.py
# Dashboard: http://localhost:7788
# Ctrl+C за спиране
```

Портът може да се смени: `PORT=8080 python3 dashboard.py`

## Как работят Rate Limits

| CLI | Метод | Какво показва |
|-----|-------|---------------|
| **Claude** | `claude -p ok --no-session-persistence` → парсва `rate_limit_event` | Статус (✅/⚠️/❌) + кога се нулира |
| **Codex** | `codex app-server` → WebSocket JSON-RPC | % остатък (5ч + седмичен) + план |

- **Нормално зареждане** (~0.5 сек): чете от локален кеш файл
- **Бутон „Обнови"** (~5-8 сек): вика API-тата за свежи данни
- Кеш файлове: `~/.claude/.rate_limits_cache.json`, `~/.codex/.rate_limits_cache.json`
