#!/usr/bin/env python3
"""AI Dashboard — Claude Code, OpenClaw, Codex CLI, Copilot CLI."""

import json
import os
import shlex
import shutil
import subprocess
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOME = Path.home()
USERNAME = os.environ.get("USER") or HOME.name

# Auto-detect Claude Code projects dir
def _find_claude_projects_dir():
    base = HOME / ".claude" / "projects"
    if not base.exists():
        return None
    # prefer dir matching current user's home
    slug = str(HOME).replace("/", "-").lstrip("-")
    candidate = base / slug
    if candidate.exists():
        return candidate
    # fallback: largest dir with .jsonl files
    dirs = [d for d in base.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: sum(f.stat().st_size for f in d.glob("*.jsonl")), reverse=True)
    return dirs[0] if dirs else None

CLAUDE_PROJECTS_DIR = _find_claude_projects_dir()
OPENCLAW_SESSIONS_FILE = HOME / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
CODEX_HISTORY_DIR = HOME / ".codex" / "history"
CODEX_CMD = "codex"
OPENCLAW_CMD = "openclaw"
COPILOT_CMD = ["gh", "copilot"]


# ── Terminal launcher ────────────────────────────────────────────────────────

def _find_terminal():
    for t in ["gnome-terminal", "xterm", "kitty", "alacritty", "xfce4-terminal"]:
        if shutil.which(t):
            return t
    return None

TERMINAL = _find_terminal()

def launch_terminal(cmd: str) -> tuple[bool, str]:
    if not TERMINAL:
        return False, "Няма намерен терминал (gnome-terminal, xterm, kitty...)"
    try:
        if TERMINAL == "gnome-terminal":
            args = [TERMINAL, "--", "bash", "-c", f"{cmd}; exec bash"]
        else:
            args = [TERMINAL, "-e", f"bash -c '{cmd}; exec bash'"]
        subprocess.Popen(args, start_new_session=True)
        return True, "Терминалът е отворен"
    except Exception as e:
        return False, str(e)


# ── Helpers ──────────────────────────────────────────────────────────────────

def fmt_age_ms(ms):
    return fmt_age_s(ms // 1000)

def fmt_age_s(s):
    if s < 60: return f"{s}с"
    if s < 3600: return f"{s//60}мин"
    if s < 86400: return f"{s//3600}ч"
    return f"{s//86400}д"

def fmt_tokens(total, ctx):
    if total is None:
        return '<span class="dim">–</span>'
    pct = round(total / ctx * 100) if ctx else 0
    color = "#ef4444" if pct > 50 else "#f59e0b" if pct > 30 else "#22c55e"
    bar_w = max(2, min(pct, 100))
    return (f'<div class="tok-wrap">'
            f'<span class="tok-num">{total//1000}k/{ctx//1000}k</span>'
            f'<div class="bar-bg"><div class="bar-fill" style="width:{bar_w}%;background:{color}"></div></div>'
            f'<span class="tok-pct" style="color:{color}">{pct}%</span>'
            f'</div>')

def shorten_path(p):
    return str(p).replace(str(HOME), "~") if p else "~"

def offline_panel(title, dot_class, install_cmd):
    return f"""
  <div class="panel panel-offline">
    <div class="panel-header">
      <div class="panel-title">
        <div class="dot {dot_class}" style="background:var(--dim);box-shadow:none"></div>
        {title}
      </div>
      <span class="count-badge offline-badge">не е инсталиран</span>
    </div>
    <div class="offline-body">
      <span class="dim">Инсталирай с:</span>
      <code>{install_cmd}</code>
    </div>
  </div>"""


# ── OpenClaw ─────────────────────────────────────────────────────────────────

def session_icon(key):
    if "telegram" in key: return "✈"
    if "slack" in key and "channel" in key: return "#"
    if "slack" in key: return "💬"
    return "⚡"

def session_label(key):
    if "telegram:direct" in key: return "Telegram DM"
    if "telegram:slash" in key: return "Telegram /"
    if "slack:channel" in key: return "Slack канал"
    if "slack:direct" in key: return "Slack DM"
    return "Директна"

def get_openclaw():
    if not shutil.which(OPENCLAW_CMD):
        return {"ok": False, "error": "not_installed"}
    try:
        r = subprocess.run([OPENCLAW_CMD, "sessions", "--json"],
                           capture_output=True, text=True, timeout=5)
        data = json.loads(r.stdout)
        return {"ok": True, "sessions": data.get("sessions", [])}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def delete_openclaw_session(key: str) -> tuple[bool, str]:
    try:
        data = json.loads(OPENCLAW_SESSIONS_FILE.read_text())
        if key not in data:
            return False, f"Ключ не е намерен: {key}"
        del data[key]
        OPENCLAW_SESSIONS_FILE.write_text(json.dumps(data, indent=2))
        return True, f"Изтрита сесия: {key}"
    except Exception as e:
        return False, str(e)


# ── Claude Code ───────────────────────────────────────────────────────────────

def get_claude_sessions():
    if not shutil.which("claude"):
        return {"ok": False, "error": "not_installed"}
    if not CLAUDE_PROJECTS_DIR:
        return {"ok": True, "sessions": []}
    sessions = []
    for f in sorted(CLAUDE_PROJECTS_DIR.glob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        sid = f.stem
        if len(sid) != 36:
            continue
        model = cwd = None
        first_ts = last_ts = None
        user_count = 0
        last_tokens = None
        try:
            with open(f) as fh:
                for line in fh:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not model and obj.get("message", {}).get("model"):
                        model = obj["message"]["model"]
                    if not cwd and obj.get("cwd"):
                        cwd = obj["cwd"]
                    ts = obj.get("timestamp")
                    if ts:
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if first_ts is None or dt < first_ts: first_ts = dt
                            if last_ts is None or dt > last_ts: last_ts = dt
                        except Exception:
                            pass
                    if obj.get("type") == "user":
                        user_count += 1
                    usage = obj.get("message", {}).get("usage")
                    if usage:
                        t = ((usage.get("input_tokens") or 0) +
                             (usage.get("cache_creation_input_tokens") or 0) +
                             (usage.get("cache_read_input_tokens") or 0) +
                             (usage.get("output_tokens") or 0))
                        if t: last_tokens = t
        except Exception:
            continue
        if not (first_ts or last_ts):
            continue
        now = datetime.now(timezone.utc)
        age_str = fmt_age_s(int((now - last_ts).total_seconds())) if last_ts else "–"
        sessions.append({
            "sid": sid,
            "short_sid": sid[:8],
            "model": (model or "–").replace("claude-", "").replace("-latest", ""),
            "cwd": shorten_path(cwd),
            "cwd_full": cwd or str(HOME),
            "age": age_str,
            "msgs": user_count,
            "tokens": last_tokens,
            "ctx_tokens": 200000,
            "last_ts": last_ts.strftime("%d.%m %H:%M") if last_ts else "–",
        })
    return {"ok": True, "sessions": sessions}

def delete_claude_session(sid: str) -> tuple[bool, str]:
    if len(sid) != 36 or "/" in sid or ".." in sid:
        return False, "Невалиден session ID"
    if not CLAUDE_PROJECTS_DIR:
        return False, "Директорията не е намерена"
    deleted = []
    for path in [CLAUDE_PROJECTS_DIR / f"{sid}.jsonl", CLAUDE_PROJECTS_DIR / sid]:
        if path.exists():
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            deleted.append(path.name)
    return (True, f"Изтрито: {', '.join(deleted)}") if deleted else (False, "Файлът не е намерен")


# ── Codex CLI ─────────────────────────────────────────────────────────────────

def get_codex_sessions():
    if not shutil.which(CODEX_CMD):
        return {"ok": False, "error": "not_installed"}
    sessions = []
    search_dirs = [
        HOME / ".codex" / "history",
        HOME / ".codex" / "sessions",
        HOME / ".config" / "codex" / "history",
    ]
    found_dir = next((d for d in search_dirs if d.exists()), None)
    if found_dir:
        for f in sorted(found_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                data = json.loads(f.read_text())
                ts = data.get("updatedAt") or data.get("createdAt") or f.stat().st_mtime
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts / 1000 if ts > 1e10 else ts, tz=timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                age = fmt_age_s(int((now - dt).total_seconds()))
                sessions.append({
                    "id": f.stem[:8],
                    "full_id": f.stem,
                    "title": data.get("title") or data.get("name") or f.stem[:16],
                    "model": data.get("model") or "–",
                    "age": age,
                    "msgs": len(data.get("messages") or []),
                })
            except Exception:
                pass
    return {"ok": True, "sessions": sessions}


# ── GitHub Copilot CLI ────────────────────────────────────────────────────────

def get_copilot_status():
    if not shutil.which("gh"):
        return {"ok": False, "error": "not_installed"}
    # Check if copilot extension is installed
    try:
        ext_list = subprocess.run(["gh", "extension", "list"],
                                  capture_output=True, text=True, timeout=5)
        if "copilot" not in ext_list.stdout.lower():
            return {"ok": False, "error": "ext_not_installed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    # Get copilot status
    try:
        r = subprocess.run(["gh", "copilot", "status"],
                           capture_output=True, text=True, timeout=5)
        return {"ok": True, "output": (r.stdout + r.stderr).strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── HTML builder ─────────────────────────────────────────────────────────────

def build_html():
    oc_result = get_openclaw()
    cc_result = get_claude_sessions()
    codex_result = get_codex_sessions()
    copilot_result = get_copilot_status()
    now_str = datetime.now().strftime("%H:%M:%S")

    # ── OpenClaw panel ───────────────────────────────────────────────────────
    if not oc_result["ok"] and oc_result["error"] == "not_installed":
        oc_panel = offline_panel("OpenClaw сесии", "green",
                                 "npm install -g openclaw")
    else:
        oc_sessions = oc_result.get("sessions", [])
        rows = ""
        for i, s in enumerate(oc_sessions, 1):
            key = s.get("key", "")
            label = session_label(key)
            enc_key = urllib.parse.quote(key, safe='')
            rows += f"""<tr>
              <td class="num">{i}</td>
              <td><span class="badge badge-{label.split()[0].lower()}">{session_icon(key)} {label}</span></td>
              <td><code>{key.split(":")[-1][:18]}</code></td>
              <td class="age">{fmt_age_ms(s.get("ageMs", 0))}</td>
              <td class="model">{s.get("model","–")}</td>
              <td>{fmt_tokens(s.get("totalTokens"), s.get("contextTokens", 272000))}
                  <span class="fresh">{"✓" if s.get("totalTokensFresh") else "~"}</span></td>
              <td class="action-cell">
                <button class="term-btn" title="Отвори в терминал" onclick="launchOC(this,'{enc_key}')">⌨</button>
                <button class="del-btn" onclick="delOC(this,'{enc_key}')">✕</button>
              </td>
            </tr>"""
        stats = "".join(
            f'<div class="stat"><div class="stat-val" style="color:var(--green)">'
            f'{s.get("totalTokens",0)//1000 if s.get("totalTokens") else 0}k</div>'
            f'<div class="stat-lbl">{session_label(s.get("key","")).split()[0]}</div></div>'
            for s in oc_sessions
        )
        err_msg = f'<tr><td colspan="7" class="err">{oc_result["error"]}</td></tr>' if not oc_result["ok"] else ""
        oc_panel = f"""
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title"><div class="dot green"></div>OpenClaw сесии</div>
      <span class="count-badge">{len(oc_sessions)} активни</span>
    </div>
    <table><thead><tr>
      <th>#</th><th>Тип</th><th>Ключ</th><th>Преди</th><th>Модел</th><th>Токени</th><th></th>
    </tr></thead><tbody>{err_msg}{rows}</tbody></table>
    <div class="stats-row">{stats}</div>
  </div>"""

    # ── Claude Code panel ────────────────────────────────────────────────────
    if not cc_result["ok"] and cc_result["error"] == "not_installed":
        cc_panel = offline_panel("Claude Code сесии", "",
                                 "npm install -g @anthropic-ai/claude-code")
    else:
        cc_sessions = cc_result.get("sessions", [])
        rows = ""
        for i, s in enumerate(cc_sessions, 1):
            enc_cwd = urllib.parse.quote(s["cwd_full"], safe='')
            rows += f"""<tr>
              <td class="num">{i}</td>
              <td><code class="sid">{s['short_sid']}…</code></td>
              <td class="cwd">{s['cwd']}</td>
              <td class="model">{s['model']}</td>
              <td class="age">{s['age']}</td>
              <td>{fmt_tokens(s['tokens'], s['ctx_tokens'])}</td>
              <td class="dim">{s['last_ts']}</td>
              <td class="action-cell">
                <button class="term-btn" title="Продължи сесията" onclick="launchCC(this,'{s['sid']}','{enc_cwd}')">⌨</button>
                <button class="del-btn" onclick="delCC(this,'{s['sid']}')">✕</button>
              </td>
            </tr>"""
        cc_panel = f"""
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title"><div class="dot"></div>Claude Code сесии</div>
      <span class="count-badge">{len(cc_sessions)} сесии</span>
    </div>
    <table><thead><tr>
      <th>#</th><th>ID</th><th>Директория</th><th>Модел</th><th>Преди</th><th>Токени</th><th>Последно</th><th></th>
    </tr></thead><tbody>{rows}</tbody></table>
  </div>"""

    # ── Codex CLI panel ──────────────────────────────────────────────────────
    if not codex_result["ok"] and codex_result["error"] == "not_installed":
        codex_panel = offline_panel("Codex CLI сесии", "orange",
                                    "npm install -g @openai/codex")
    else:
        codex_sessions = codex_result.get("sessions", [])
        if not codex_sessions:
            body = '<tr><td colspan="5" class="dim" style="padding:20px;text-align:center">Няма намерени сесии</td></tr>'
        else:
            body = ""
            for i, s in enumerate(codex_sessions, 1):
                body += f"""<tr>
                  <td class="num">{i}</td>
                  <td><code class="sid">{s['id']}…</code></td>
                  <td class="cwd">{s['title'][:30]}</td>
                  <td class="model">{s['model']}</td>
                  <td class="age">{s['age']}</td>
                  <td class="dim">{s['msgs']} съобщ.</td>
                  <td class="action-cell">
                    <button class="term-btn" title="Отвори Codex" onclick="launchCLI(this,'codex')">⌨</button>
                  </td>
                </tr>"""
        codex_panel = f"""
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title"><div class="dot" style="background:#f97316;box-shadow:0 0 6px #f97316"></div>Codex CLI сесии</div>
      <span class="count-badge">{len(codex_sessions)} сесии</span>
    </div>
    <table><thead><tr>
      <th>#</th><th>ID</th><th>Заглавие</th><th>Модел</th><th>Преди</th><th>Съобщ.</th><th></th>
    </tr></thead><tbody>{body}</tbody></table>
  </div>"""

    # ── Copilot CLI panel ────────────────────────────────────────────────────
    if not copilot_result["ok"]:
        if copilot_result["error"] == "not_installed":
            install = "brew install gh"
        else:
            install = "gh extension install github/gh-copilot"
        copilot_panel = offline_panel("GitHub Copilot CLI", "cyan", install)
    else:
        output = copilot_result.get("output", "")
        lines = [l.strip() for l in output.splitlines() if l.strip()]
        rows = "".join(
            f'<tr><td colspan="2" style="padding:8px 16px;font-size:12px;color:var(--text)">{l}</td></tr>'
            for l in lines
        ) or '<tr><td colspan="2" class="dim" style="padding:20px;text-align:center">Няма данни</td></tr>'
        copilot_panel = f"""
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title"><div class="dot" style="background:#06b6d4;box-shadow:0 0 6px #06b6d4"></div>GitHub Copilot CLI</div>
      <span class="count-badge">активен</span>
    </div>
    <table><tbody>{rows}</tbody></table>
    <div class="offline-body" style="border-top:1px solid var(--border)">
      <button class="refresh-btn" onclick="launchCLI(this,'gh copilot suggest')">⌨ Отвори</button>
    </div>
  </div>"""

    return f"""<!DOCTYPE html>
<html lang="bg">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Dashboard</title>
<style>
  :root {{
    --bg:#0f1117;--panel:#161b27;--border:#1e2a3a;
    --text:#e2e8f0;--dim:#64748b;--accent:#6366f1;
    --green:#22c55e;--yellow:#f59e0b;--red:#ef4444;
    --blue:#38bdf8;--purple:#a78bfa;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:'JetBrains Mono','Fira Code',monospace;font-size:13px;min-height:100vh;padding:20px}}
  header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid var(--border)}}
  header h1{{font-size:18px;font-weight:600;letter-spacing:.5px}}
  header h1 span{{color:var(--accent)}}
  .header-right{{display:flex;align-items:center;gap:16px}}
  .updated{{color:var(--dim);font-size:11px}}
  .refresh-btn{{background:var(--border);border:1px solid #2d3a4e;color:var(--text);padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px;font-family:inherit;transition:background .15s}}
  .refresh-btn:hover{{background:#1e2d42}}
  .grid{{display:grid;grid-template-columns:1fr;gap:20px}}
  @media(min-width:1100px){{.grid{{grid-template-columns:1fr 1fr}}}}
  .panel{{background:var(--panel);border:1px solid var(--border);border-radius:12px;overflow:hidden}}
  .panel-header{{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid var(--border);background:rgba(99,102,241,.05)}}
  .panel-title{{font-size:13px;font-weight:600;display:flex;align-items:center;gap:8px}}
  .dot{{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 6px var(--accent)}}
  .dot.green{{background:var(--green);box-shadow:0 0 6px var(--green)}}
  .count-badge{{background:var(--border);color:var(--dim);font-size:11px;padding:2px 8px;border-radius:10px}}
  .offline-badge{{color:var(--red);border:1px solid rgba(239,68,68,.3)}}
  .offline-body{{display:flex;align-items:center;gap:12px;padding:20px;color:var(--dim);font-size:12px}}
  .offline-body code{{background:rgba(255,255,255,.06);padding:4px 10px;border-radius:6px;font-size:12px;color:var(--yellow)}}
  table{{width:100%;border-collapse:collapse}}
  thead th{{padding:10px 16px;text-align:left;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;color:var(--dim);border-bottom:1px solid var(--border)}}
  tbody tr{{border-bottom:1px solid rgba(30,42,58,.5);transition:background .1s}}
  tbody tr:last-child{{border-bottom:none}}
  tbody tr:hover{{background:rgba(99,102,241,.04)}}
  td{{padding:11px 16px;vertical-align:middle}}
  .num{{color:var(--dim);text-align:right;width:28px}}
  .age{{color:var(--yellow);font-size:12px;white-space:nowrap}}
  .model{{color:var(--blue);font-size:11px;white-space:nowrap}}
  .dim{{color:var(--dim);font-size:11px}}
  .err{{color:var(--red)}}
  .cwd{{color:var(--purple);font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  code{{background:rgba(255,255,255,.06);padding:2px 6px;border-radius:4px;font-size:11px;color:#94a3b8}}
  .sid{{color:var(--dim)}}
  .badge{{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:500;white-space:nowrap}}
  .badge-директна{{background:rgba(99,102,241,.15);color:var(--accent)}}
  .badge-telegram{{background:rgba(56,189,248,.12);color:var(--blue)}}
  .badge-slack{{background:rgba(167,139,250,.12);color:var(--purple)}}
  .tok-wrap{{display:flex;align-items:center;gap:6px;white-space:nowrap}}
  .tok-num{{font-size:11px;color:var(--dim);min-width:80px}}
  .bar-bg{{width:60px;height:4px;background:var(--border);border-radius:2px;overflow:hidden}}
  .bar-fill{{height:100%;border-radius:2px;transition:width .3s}}
  .tok-pct{{font-size:11px;font-weight:600;min-width:30px}}
  .fresh{{color:var(--dim);font-size:10px}}
  .stats-row{{display:flex;gap:16px;padding:14px 20px;border-top:1px solid var(--border);background:rgba(0,0,0,.15)}}
  .stat{{display:flex;flex-direction:column;gap:2px}}
  .stat-val{{font-size:20px;font-weight:700}}
  .stat-lbl{{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}}
  .action-cell{{width:60px;padding:0 8px 0 4px;white-space:nowrap}}
  .del-btn,.term-btn{{background:transparent;border:1px solid transparent;color:var(--dim);font-size:12px;width:24px;height:24px;border-radius:6px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;font-family:inherit;transition:all .15s;opacity:.35;vertical-align:middle}}
  tr:hover .del-btn,tr:hover .term-btn{{opacity:1}}
  .del-btn:hover{{background:rgba(239,68,68,.15);border-color:rgba(239,68,68,.3);color:var(--red)}}
  .term-btn:hover{{background:rgba(99,102,241,.15);border-color:rgba(99,102,241,.3);color:var(--accent)}}
  .del-btn:active,.term-btn:active{{transform:scale(.9)}}
  .del-btn.loading{{opacity:1;color:var(--yellow);animation:spin .6s linear infinite}}
  .del-btn.done{{opacity:1;color:var(--green)}}
  .term-btn.done{{opacity:1;color:var(--accent)}}
  tr.deleting{{opacity:.4;pointer-events:none;transition:opacity .3s}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
  .toast{{position:fixed;bottom:24px;right:24px;background:var(--panel);border:1px solid var(--border);color:var(--text);padding:10px 16px;border-radius:8px;font-size:12px;z-index:999;animation:fadeIn .2s ease}}
  .toast.err{{border-color:rgba(239,68,68,.4);color:var(--red)}}
  @keyframes fadeIn{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:none}}}}
</style>
</head>
<body>
<header>
  <h1>⚡ AI <span>Dashboard</span></h1>
  <div class="header-right">
    <span class="updated">Обновено в {now_str}</span>
    <button class="refresh-btn" onclick="location.reload()">↻ Обнови</button>
  </div>
</header>
<div class="grid">
  {oc_panel}
  {cc_panel}
  {codex_panel}
  {copilot_panel}
</div>
<script>
  setTimeout(() => location.reload(), 30000);

  function showToast(msg, isErr) {{
    const t = document.createElement('div');
    t.className = 'toast' + (isErr ? ' err' : '');
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3000);
  }}

  async function delSession(btn, payload) {{
    const row = btn.closest('tr');
    btn.classList.add('loading'); btn.textContent = '↻';
    row.classList.add('deleting');
    try {{
      const j = await fetch('/delete', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}}).then(r=>r.json());
      if (j.ok) {{ btn.classList.remove('loading'); btn.classList.add('done'); btn.textContent = '✓'; showToast(j.msg); setTimeout(()=>row.remove(),600); }}
      else {{ btn.classList.remove('loading'); btn.textContent='✕'; row.classList.remove('deleting'); showToast(j.msg,true); }}
    }} catch(e) {{ btn.classList.remove('loading'); btn.textContent='✕'; row.classList.remove('deleting'); showToast('Грешка: '+e.message,true); }}
  }}

  async function launchSession(btn, payload) {{
    btn.classList.add('done');
    try {{
      const j = await fetch('/launch', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}}).then(r=>r.json());
      showToast(j.msg, !j.ok);
    }} catch(e) {{ showToast('Грешка: '+e.message,true); }}
    setTimeout(()=>btn.classList.remove('done'),1500);
  }}

  function delOC(btn,key)  {{ delSession(btn,  {{type:'openclaw', key:decodeURIComponent(key)}}); }}
  function delCC(btn,sid)  {{ delSession(btn,  {{type:'claude',   sid}}); }}
  function launchOC(btn,key)       {{ launchSession(btn, {{type:'openclaw', key:decodeURIComponent(key)}}); }}
  function launchCC(btn,sid,cwd)   {{ launchSession(btn, {{type:'claude',   sid, cwd:decodeURIComponent(cwd)}}); }}
  function launchCLI(btn,cmd)      {{ launchSession(btn, {{type:'cli',      cmd}}); }}
</script>
</body>
</html>"""


# ── HTTP server ───────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = build_html().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        if self.path == "/delete":
            kind = body.get("type")
            if kind == "openclaw":
                ok, msg = delete_openclaw_session(body.get("key", ""))
            elif kind == "claude":
                ok, msg = delete_claude_session(body.get("sid", ""))
            else:
                ok, msg = False, "Непознат тип"
        elif self.path == "/launch":
            kind = body.get("type")
            if kind == "openclaw":
                ok, msg = launch_terminal(f"openclaw tui --session {shlex.quote(body.get('key',''))}")
            elif kind == "claude":
                ok, msg = launch_terminal(
                    f"cd {shlex.quote(body.get('cwd', str(HOME)))} && claude --resume {shlex.quote(body.get('sid',''))}")
            elif kind == "cli":
                ok, msg = launch_terminal(body.get("cmd", ""))
            else:
                ok, msg = False, "Непознат тип"
        else:
            self.send_response(404); self.end_headers(); return
        resp = json.dumps({"ok": ok, "msg": msg}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7788))
    server = HTTPServer(("localhost", port), Handler)
    print(f"Dashboard: http://localhost:{port}  (Ctrl+C за спиране)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСпрян.")
