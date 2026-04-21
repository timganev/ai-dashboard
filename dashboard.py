#!/usr/bin/env python3
"""Claude Code + OpenClaw dashboard server."""

import json
import os
import shlex
import subprocess
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

OPENCLAW_SESSIONS_FILE = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"


def launch_terminal(cmd: str) -> tuple[bool, str]:
    try:
        subprocess.Popen(["gnome-terminal", "--", "bash", "-c", f"{cmd}; exec bash"],
                         start_new_session=True)
        return True, "Терминалът е отворен"
    except Exception as e:
        return False, str(e)


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


def delete_claude_session(sid: str) -> tuple[bool, str]:
    if len(sid) != 36 or "/" in sid or ".." in sid:
        return False, "Невалиден session ID"
    deleted = []
    for path in [PROJECTS_DIR / f"{sid}.jsonl", PROJECTS_DIR / sid]:
        if path.exists():
            if path.is_dir():
                import shutil
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted.append(path.name)
    if not deleted:
        return False, "Файлът не е намерен"
    return True, f"Изтрито: {', '.join(deleted)}"

PROJECTS_DIR = Path.home() / ".claude" / "projects" / "-home-tim"
OPENCLAW_CMD = ["openclaw", "sessions", "--json"]


def fmt_age(ms):
    s = ms // 1000
    if s < 60: return f"{s}с"
    if s < 3600: return f"{s//60}мин"
    if s < 86400: return f"{s//3600}ч"
    return f"{s//86400}д"


def fmt_tokens(total, ctx):
    if total is None:
        return '<span class="dim">–</span>'
    pct = round(total / ctx * 100) if ctx else 0
    color = "#ef4444" if pct > 50 else "#f59e0b" if pct > 30 else "#22c55e"
    bar_w = max(2, pct)
    return f'''<div class="tok-wrap">
        <span class="tok-num">{total//1000}k/{ctx//1000}k</span>
        <div class="bar-bg"><div class="bar-fill" style="width:{bar_w}%;background:{color}"></div></div>
        <span class="tok-pct" style="color:{color}">{pct}%</span>
    </div>'''


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
    try:
        r = subprocess.run(OPENCLAW_CMD, capture_output=True, text=True, timeout=5)
        data = json.loads(r.stdout)
        return data.get("sessions", [])
    except Exception as e:
        return [{"error": str(e)}]


def parse_claude_sessions():
    sessions = []
    if not PROJECTS_DIR.exists():
        return sessions
    for f in sorted(PROJECTS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        sid = f.stem
        if len(sid) != 36:
            continue
        msgs = []
        model = None
        cwd = None
        try:
            with open(f) as fh:
                for line in fh:
                    try:
                        obj = json.loads(line)
                        msgs.append(obj)
                        if not model and obj.get("message", {}).get("model"):
                            model = obj["message"]["model"]
                        if not cwd and obj.get("cwd"):
                            cwd = obj["cwd"]
                    except Exception:
                        pass
        except Exception:
            continue
        if not msgs:
            continue
        first_ts = None
        last_ts = None
        user_count = 0
        last_input_tokens = None
        ctx_tokens = 200000
        for m in msgs:
            ts = m.get("timestamp")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if first_ts is None or dt < first_ts:
                        first_ts = dt
                    if last_ts is None or dt > last_ts:
                        last_ts = dt
                except Exception:
                    pass
            if m.get("type") == "user":
                user_count += 1
            usage = m.get("message", {}).get("usage")
            if usage:
                total_in = (usage.get("input_tokens") or 0) + \
                           (usage.get("cache_creation_input_tokens") or 0) + \
                           (usage.get("cache_read_input_tokens") or 0) + \
                           (usage.get("output_tokens") or 0)
                if total_in:
                    last_input_tokens = total_in
        now = datetime.now(timezone.utc)
        age_str = "–"
        if last_ts:
            diff = now - last_ts
            s = int(diff.total_seconds())
            if s < 60: age_str = f"{s}с"
            elif s < 3600: age_str = f"{s//60}мин"
            elif s < 86400: age_str = f"{s//3600}ч"
            else: age_str = f"{s//86400}д"
        short_model = (model or "–").replace("claude-", "").replace("-latest", "")
        short_cwd = (cwd or "~").replace("/home/tim", "~")
        sessions.append({
            "sid": sid,
            "short_sid": sid[:8],
            "model": short_model,
            "cwd": short_cwd,
            "age": age_str,
            "msgs": user_count,
            "tokens": last_input_tokens,
            "ctx_tokens": ctx_tokens,
            "last_ts": last_ts.strftime("%d.%m %H:%M") if last_ts else "–",
        })
    return sessions


def build_html():
    oc_sessions = get_openclaw()
    cc_sessions = parse_claude_sessions()
    now_str = datetime.now().strftime("%H:%M:%S")

    # OpenClaw rows
    oc_rows = ""
    for i, s in enumerate(oc_sessions, 1):
        if "error" in s:
            oc_rows += f'<tr><td colspan="6" class="err">{s["error"]}</td></tr>'
            continue
        key = s.get("key", "")
        icon = session_icon(key)
        label = session_label(key)
        short_key = key.split(":")[-1][:18] if ":" in key else key[:18]
        age = fmt_age(s.get("ageMs", 0))
        model = s.get("model", "–")
        tok_html = fmt_tokens(s.get("totalTokens"), s.get("contextTokens", 272000))
        fresh = "✓" if s.get("totalTokensFresh") else "~"
        enc_key = urllib.parse.quote(key, safe='')
        oc_rows += f"""<tr>
            <td class="num">{i}</td>
            <td><span class="badge badge-{label.split()[0].lower()}">{icon} {label}</span></td>
            <td><code>{short_key}</code></td>
            <td class="age">{age}</td>
            <td class="model">{model}</td>
            <td>{tok_html} <span class="fresh">{fresh}</span></td>
            <td class="action-cell">
              <button class="term-btn" title="Отвори в терминал" onclick="launchOC(this,'{enc_key}')">⌨</button>
              <button class="del-btn" onclick="delOC(this,'{enc_key}')">✕</button>
            </td>
        </tr>"""

    # Claude Code rows
    cc_rows = ""
    for i, s in enumerate(cc_sessions, 1):
        tok_html = fmt_tokens(s['tokens'], s['ctx_tokens'])
        enc_cwd = urllib.parse.quote(s['cwd'].replace('~', '/home/tim'), safe='')
        cc_rows += f"""<tr>
            <td class="num">{i}</td>
            <td><code class="sid">{s['short_sid']}…</code></td>
            <td class="cwd">{s['cwd']}</td>
            <td class="model">{s['model']}</td>
            <td class="age">{s['age']}</td>
            <td>{tok_html}</td>
            <td class="dim">{s['last_ts']}</td>
            <td class="action-cell">
              <button class="term-btn" title="Продължи сесията" onclick="launchCC(this,'{s['sid']}','{enc_cwd}')">⌨</button>
              <button class="del-btn" onclick="delCC(this,'{s['sid']}')">✕</button>
            </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="bg">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Dashboard</title>
<style>
  :root {{
    --bg: #0f1117;
    --panel: #161b27;
    --border: #1e2a3a;
    --text: #e2e8f0;
    --dim: #64748b;
    --accent: #6366f1;
    --green: #22c55e;
    --yellow: #f59e0b;
    --red: #ef4444;
    --blue: #38bdf8;
    --purple: #a78bfa;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 13px;
    min-height: 100vh;
    padding: 20px;
  }}
  header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
  }}
  header h1 {{
    font-size: 18px;
    font-weight: 600;
    color: var(--text);
    letter-spacing: 0.5px;
  }}
  header h1 span {{ color: var(--accent); }}
  .header-right {{ display: flex; align-items: center; gap: 16px; }}
  .updated {{ color: var(--dim); font-size: 11px; }}
  .refresh-btn {{
    background: var(--border);
    border: 1px solid #2d3a4e;
    color: var(--text);
    padding: 5px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
    font-family: inherit;
    transition: background 0.15s;
  }}
  .refresh-btn:hover {{ background: #1e2d42; }}
  .grid {{
    display: grid;
    grid-template-columns: 1fr;
    gap: 20px;
  }}
  .panel {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }}
  .panel-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
    background: rgba(99,102,241,0.05);
  }}
  .panel-title {{
    font-size: 13px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .panel-title .dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 6px var(--accent);
  }}
  .panel-title .dot.green {{ background: var(--green); box-shadow: 0 0 6px var(--green); }}
  .count-badge {{
    background: var(--border);
    color: var(--dim);
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
  }}
  thead th {{
    padding: 10px 16px;
    text-align: left;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--dim);
    border-bottom: 1px solid var(--border);
  }}
  tbody tr {{
    border-bottom: 1px solid rgba(30,42,58,0.5);
    transition: background 0.1s;
  }}
  tbody tr:last-child {{ border-bottom: none; }}
  tbody tr:hover {{ background: rgba(99,102,241,0.04); }}
  td {{ padding: 11px 16px; vertical-align: middle; }}
  .num {{ color: var(--dim); text-align: right; width: 28px; }}
  .age {{ color: var(--yellow); font-size: 12px; white-space: nowrap; }}
  .model {{ color: var(--blue); font-size: 11px; white-space: nowrap; }}
  .dim {{ color: var(--dim); font-size: 11px; }}
  .err {{ color: var(--red); }}
  .cwd {{ color: var(--purple); font-size: 11px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  code {{
    background: rgba(255,255,255,0.06);
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    color: #94a3b8;
  }}
  .sid {{ color: var(--dim); }}
  .badge {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 8px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 500;
    white-space: nowrap;
  }}
  .badge-директна {{ background: rgba(99,102,241,0.15); color: var(--accent); }}
  .badge-telegram {{ background: rgba(56,189,248,0.12); color: var(--blue); }}
  .badge-slack {{ background: rgba(167,139,250,0.12); color: var(--purple); }}
  .tok-wrap {{ display: flex; align-items: center; gap: 6px; white-space: nowrap; }}
  .tok-num {{ font-size: 11px; color: var(--dim); min-width: 80px; }}
  .bar-bg {{ width: 60px; height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 2px; transition: width 0.3s; }}
  .tok-pct {{ font-size: 11px; font-weight: 600; min-width: 30px; }}
  .fresh {{ color: var(--dim); font-size: 10px; }}
  .stats-row {{
    display: flex;
    gap: 16px;
    padding: 14px 20px;
    border-top: 1px solid var(--border);
    background: rgba(0,0,0,0.15);
  }}
  .stat {{
    display: flex;
    flex-direction: column;
    gap: 2px;
  }}
  .stat-val {{ font-size: 20px; font-weight: 700; color: var(--text); }}
  .stat-lbl {{ font-size: 10px; color: var(--dim); text-transform: uppercase; letter-spacing: 0.5px; }}
  .action-cell {{ width: 60px; padding: 0 8px 0 4px; display: table-cell; white-space: nowrap; }}
  .del-btn, .term-btn {{
    background: transparent;
    border: 1px solid transparent;
    color: var(--dim);
    font-size: 12px;
    width: 24px; height: 24px;
    border-radius: 6px;
    cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    font-family: inherit;
    transition: all 0.15s;
    opacity: 0.35;
    vertical-align: middle;
  }}
  tr:hover .del-btn, tr:hover .term-btn {{ opacity: 1; }}
  .del-btn:hover {{ background: rgba(239,68,68,0.15); border-color: rgba(239,68,68,0.3); color: var(--red); }}
  .term-btn:hover {{ background: rgba(99,102,241,0.15); border-color: rgba(99,102,241,0.3); color: var(--accent); }}
  .del-btn:active, .term-btn:active {{ transform: scale(0.9); }}
  .del-btn.loading {{ opacity: 1; color: var(--yellow); border-color: transparent; animation: spin 0.6s linear infinite; }}
  .del-btn.done {{ opacity: 1; color: var(--green); }}
  .term-btn.done {{ opacity: 1; color: var(--accent); }}
  tr.deleting {{ opacity: 0.4; pointer-events: none; transition: opacity 0.3s; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .toast {{
    position: fixed; bottom: 24px; right: 24px;
    background: var(--panel); border: 1px solid var(--border);
    color: var(--text); padding: 10px 16px; border-radius: 8px;
    font-size: 12px; z-index: 999;
    animation: fadeIn 0.2s ease;
  }}
  .toast.err {{ border-color: rgba(239,68,68,0.4); color: var(--red); }}
  @keyframes fadeIn {{ from {{ opacity:0; transform:translateY(8px) }} to {{ opacity:1; transform:none }} }}
  @media (min-width: 1100px) {{
    .grid {{ grid-template-columns: 1fr 1fr; }}
  }}
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

  <div class="panel">
    <div class="panel-header">
      <div class="panel-title">
        <div class="dot green"></div>
        OpenClaw сесии
      </div>
      <span class="count-badge">{len(oc_sessions)} активни</span>
    </div>
    <table>
      <thead><tr>
        <th>#</th><th>Тип</th><th>Ключ</th><th>Преди</th><th>Модел</th><th>Токени</th><th></th>
      </tr></thead>
      <tbody>{oc_rows}</tbody>
    </table>
    <div class="stats-row">
      {"".join(f'<div class="stat"><div class="stat-val" style="color:var(--green)">{s.get("totalTokens",0)//1000 if s.get("totalTokens") else 0}k</div><div class="stat-lbl">{session_label(s.get("key","")).split()[0]}</div></div>' for s in oc_sessions if "error" not in s)}
    </div>
  </div>

  <div class="panel">
    <div class="panel-header">
      <div class="panel-title">
        <div class="dot"></div>
        Claude Code сесии
      </div>
      <span class="count-badge">{len(cc_sessions)} сесии</span>
    </div>
    <table>
      <thead><tr>
        <th>#</th><th>ID</th><th>Директория</th><th>Модел</th><th>Преди</th><th>Токени</th><th>Последно</th><th></th>
      </tr></thead>
      <tbody>{cc_rows}</tbody>
    </table>
  </div>

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
    btn.classList.add('loading');
    btn.textContent = '↻';
    row.classList.add('deleting');
    try {{
      const r = await fetch('/delete', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(payload)
      }});
      const j = await r.json();
      if (j.ok) {{
        btn.classList.remove('loading');
        btn.classList.add('done');
        btn.textContent = '✓';
        showToast(j.msg);
        setTimeout(() => row.remove(), 600);
      }} else {{
        btn.classList.remove('loading');
        btn.textContent = '✕';
        row.classList.remove('deleting');
        showToast(j.msg, true);
      }}
    }} catch(e) {{
      btn.classList.remove('loading');
      btn.textContent = '✕';
      row.classList.remove('deleting');
      showToast('Грешка: ' + e.message, true);
    }}
  }}

  function delOC(btn, key) {{
    delSession(btn, {{type: 'openclaw', key: decodeURIComponent(key)}});
  }}
  function delCC(btn, sid) {{
    delSession(btn, {{type: 'claude', sid}});
  }}

  async function launchSession(btn, payload) {{
    btn.classList.add('done');
    try {{
      const r = await fetch('/launch', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(payload)
      }});
      const j = await r.json();
      showToast(j.msg, !j.ok);
    }} catch(e) {{
      showToast('Грешка: ' + e.message, true);
    }}
    setTimeout(() => btn.classList.remove('done'), 1500);
  }}
  function launchOC(btn, key) {{
    launchSession(btn, {{type: 'openclaw', key: decodeURIComponent(key)}});
  }}
  function launchCC(btn, sid, cwd) {{
    launchSession(btn, {{type: 'claude', sid, cwd: decodeURIComponent(cwd)}});
  }}
</script>
</body>
</html>"""


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
                key = body.get("key", "")
                ok, msg = launch_terminal(f"openclaw tui --session {shlex.quote(key)}")
            elif kind == "claude":
                sid = body.get("sid", "")
                cwd = body.get("cwd", "/home/tim")
                ok, msg = launch_terminal(f"cd {shlex.quote(cwd)} && claude --resume {shlex.quote(sid)}")
            else:
                ok, msg = False, "Непознат тип"
        else:
            self.send_response(404)
            self.end_headers()
            return
        resp = json.dumps({"ok": ok, "msg": msg}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    port = 7788
    server = HTTPServer(("localhost", port), Handler)
    print(f"Dashboard: http://localhost:{port}")
    print("Ctrl+C за спиране")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСпрян.")
