# AI Dashboard

Локален уеб дашборд за мониторинг на **Claude Code** и **OpenClaw** сесии в реално време.

![Python](https://img.shields.io/badge/python-3.8+-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## Какво показва

| Панел | Информация |
|-------|-----------|
| **OpenClaw сесии** | Тип (Telegram/Slack/Direct), ключ, последна активност, модел, токени + прогрес бар |
| **Claude Code сесии** | Session ID, директория, модел, токени + прогрес бар, последна активност |

- Токен барът е **зелен** (<30%), **жълт** (30–50%), **червен** (>50%)
- Бутон **⌨** на всеки ред — отваря терминал директно в сесията
- Бутон **✕** — изтрива сесията
- Авто-обновяване на всеки 30 секунди

## Изисквания

- Python 3.8+ (без допълнителни пакети — само stdlib)
- [OpenClaw](https://openclaw.ai) — за OpenClaw панела
- `gnome-terminal` — за бутона "Отвори в терминал" (на Linux)

> На macOS замени `gnome-terminal` с `Terminal` или `iTerm2` — виж [Конфигурация](#конфигурация).

## Инсталация

```bash
git clone https://github.com/timganev/ai-dashboard
cd ai-dashboard
python3 dashboard.py
```

После отвори браузъра на: **http://localhost:7788**

## Конфигурация

В горната част на `dashboard.py` има няколко константи:

```python
# Порт на дашборда
port = 7788

# Директория с Claude Code сесии (смени username-а)
PROJECTS_DIR = Path.home() / ".claude" / "projects" / "-home-USERNAME"
```

### macOS — смяна на терминал

Намери функцията `launch_terminal` и смени командата:

```python
# macOS с Terminal.app
subprocess.Popen(["open", "-a", "Terminal", "."])

# macOS с iTerm2
subprocess.Popen(["osascript", "-e",
    f'tell app "iTerm2" to create window with default profile command "{cmd}"'])
```

### Linux с различен терминал

```python
# xterm
subprocess.Popen(["xterm", "-e", f"bash -c '{cmd}; exec bash'"])

# kitty
subprocess.Popen(["kitty", "bash", "-c", f"{cmd}; exec bash"])
```

## Използване

```
python3 dashboard.py
# Dashboard: http://localhost:7788
# Ctrl+C за спиране
```
