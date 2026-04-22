#!/usr/bin/env python3
"""AI Dashboard — unified view for Claude Code, OpenClaw, Codex CLI, Copilot CLI."""

import json
import os
import shlex
import shutil
import socket
import sqlite3
import struct
import subprocess
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOME = Path.home()

# ── Panel definitions ────────────────────────────────────────────────────────

PANELS = [
    {"key": "openclaw",  "title": "OpenClaw",          "color": "#22c55e", "install": "npm install -g openclaw"},
    {"key": "claude",    "title": "Claude Code",        "color": "#6366f1", "install": "npm install -g @anthropic-ai/claude-code"},
    {"key": "codex",     "title": "Codex CLI",          "color": "#f97316", "install": "npm install -g @openai/codex"},
    {"key": "copilot",   "title": "GitHub Copilot CLI", "color": "#06b6d4", "install": "brew install gh && gh copilot"},
]

# ── Auto-detect paths ────────────────────────────────────────────────────────

CLAUDE_PROJECTS_BASE = HOME / ".claude" / "projects"
OPENCLAW_SESSIONS_FILE = HOME / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"


# ── Terminal launcher ────────────────────────────────────────────────────────

import platform
_IS_MAC = platform.system() == "Darwin"

def _find_terminal():
    if _IS_MAC:
        return "Terminal.app"
    for t in ["gnome-terminal", "xterm", "kitty", "alacritty", "xfce4-terminal"]:
        if shutil.which(t):
            return t
    return None

TERMINAL = _find_terminal()

def launch_terminal(cmd: str) -> tuple[bool, str]:
    if not TERMINAL:
        return False, "Няма намерен терминал"
    try:
        if _IS_MAC:
            escaped = cmd.replace('\\', '\\\\').replace('"', '\\"')
            script = (
                'tell application "Terminal"\n'
                '  activate\n'
                f'  do script "{escaped}"\n'
                'end tell'
            )
            subprocess.Popen(["osascript", "-e", script], start_new_session=True)
        elif TERMINAL == "gnome-terminal":
            subprocess.Popen([TERMINAL, "--", "bash", "-c", f"{cmd}; exec bash"],
                             start_new_session=True)
        else:
            subprocess.Popen([TERMINAL, "-e", f"bash -c '{cmd}; exec bash'"],
                             start_new_session=True)
        return True, "Терминалът е отворен"
    except Exception as e:
        return False, str(e)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_age(seconds):
    if seconds < 60: return f"{seconds}с"
    if seconds < 3600: return f"{seconds // 60}мин"
    if seconds < 86400: return f"{seconds // 3600}ч"
    return f"{seconds // 86400}д"

def _age_from_dt(dt):
    s = int((datetime.now(timezone.utc) - dt).total_seconds())
    return _fmt_age(s), s

def _fmt_tokens(total, ctx):
    if not total:
        return '<span class="dim">–</span>'
    pct = round(total / ctx * 100) if ctx else 0
    color = "#ef4444" if pct > 50 else "#f59e0b" if pct > 30 else "#22c55e"
    bar_w = max(2, min(pct, 100))
    return (f'<div class="tok-wrap">'
            f'<span class="tok-num">{total // 1000}k/{ctx // 1000}k</span>'
            f'<div class="bar-bg"><div class="bar-fill" style="width:{bar_w}%;background:{color}"></div></div>'
            f'<span class="tok-pct" style="color:{color}">{pct}%</span>'
            f'</div>')

def _shorten(p):
    return str(p).replace(str(HOME), "~") if p else "~"

CTX_WINDOWS = {
    "gpt-5.4": 1_000_000, "gpt-5.2": 1_000_000, "gpt-5": 1_000_000,
    "gpt-4.1": 1_000_000, "gpt-4o": 128_000, "o3": 200_000, "o4-mini": 200_000,
}

def _ctx_for_model(model, default=200_000):
    for prefix, ctx in CTX_WINDOWS.items():
        if prefix in (model or ""):
            return ctx
    return default

def _session(sid, cwd, model, age, age_s, tokens, ctx, last_ts, last_sort=""):
    return {
        "id": sid[:8], "full_id": sid,
        "cwd": _shorten(cwd), "cwd_full": cwd or str(HOME),
        "model": model or "–", "age": age, "age_s": age_s,
        "tokens": tokens, "ctx_tokens": ctx,
        "last_ts": last_ts, "last_sort": last_sort,
    }


# ── Session collectors ───────────────────────────────────────────────────────

def get_sessions(key, force_rl=False):
    fns = {"openclaw": _get_openclaw, "claude": _get_claude,
           "codex": _get_codex, "copilot": _get_copilot}
    fn = fns[key]
    if key in ("codex", "claude"):
        return fn(force_rl=force_rl)
    return fn()

def _get_openclaw():
    if not shutil.which("openclaw"):
        return {"ok": False, "error": "not_installed"}
    try:
        r = subprocess.run(["openclaw", "sessions", "--json"],
                           capture_output=True, text=True, timeout=5)
        data = json.loads(r.stdout)
        sessions = []
        for s in data.get("sessions", []):
            key = s.get("key", "")
            age_s = s.get("ageMs", 0) // 1000
            sessions.append(_session(
                sid=key, cwd=key.split(":")[-1][:24],
                model=s.get("model", "–"),
                age=_fmt_age(age_s), age_s=age_s,
                tokens=s.get("totalTokens"),
                ctx=s.get("contextTokens", 272000),
                last_ts=_fmt_age(age_s), last_sort=str(age_s),
            ))
        return {"ok": True, "sessions": sessions}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _get_claude(force_rl=False):
    if not shutil.which("claude"):
        return {"ok": False, "error": "not_installed"}
    if not CLAUDE_PROJECTS_BASE.exists():
        return {"ok": True, "sessions": []}
    sessions = []
    # Scan ALL project subdirectories
    all_jsonl = []
    for proj_dir in CLAUDE_PROJECTS_BASE.iterdir():
        if proj_dir.is_dir():
            all_jsonl.extend(proj_dir.glob("*.jsonl"))
    for f in sorted(all_jsonl, key=lambda p: p.stat().st_mtime, reverse=True):
        sid = f.stem
        if len(sid) != 36:
            continue
        model = cwd = None
        last_ts = None
        last_tokens = None
        try:
            with open(f) as fh:
                for line in fh:
                    try: obj = json.loads(line)
                    except Exception: continue
                    if not model and obj.get("message", {}).get("model"):
                        model = obj["message"]["model"]
                    if not cwd and obj.get("cwd"):
                        cwd = obj["cwd"]
                    ts = obj.get("timestamp")
                    if ts:
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if last_ts is None or dt > last_ts:
                                last_ts = dt
                        except Exception:
                            pass
                    usage = obj.get("message", {}).get("usage")
                    if usage:
                        t = sum(usage.get(k, 0) or 0 for k in
                                ("input_tokens", "cache_creation_input_tokens",
                                 "cache_read_input_tokens", "output_tokens"))
                        if t:
                            last_tokens = t
        except Exception:
            continue
        if not last_ts:
            continue
        age_str, age_s = _age_from_dt(last_ts)
        sessions.append(_session(
            sid=sid, cwd=cwd,
            model=(model or "–").replace("claude-", "").replace("-latest", ""),
            age=age_str, age_s=age_s,
            tokens=last_tokens, ctx=200_000,
            last_ts=last_ts.strftime("%d.%m %H:%M"),
            last_sort=last_ts.strftime("%Y-%m-%d %H:%M"),
        ))
    result = {"ok": True, "sessions": sessions}
    if force_rl:
        rl = _claude_rate_limits()
    elif CLAUDE_RL_CACHE.exists():
        try:
            with open(CLAUDE_RL_CACHE) as fh:
                rl = json.load(fh)
        except Exception:
            rl = None
    else:
        rl = None
    if rl:
        result["rate_limits"] = rl
    return result

CLAUDE_RL_CACHE = HOME / ".claude" / ".rate_limits_cache.json"

def _claude_rate_limits():
    """Get Claude rate limits via `claude -p` stream-json output."""
    if not shutil.which("claude"):
        return None
    # Snapshot existing JSONL files to clean up phantoms after
    before = set()
    if CLAUDE_PROJECTS_BASE.exists():
        for d in CLAUDE_PROJECTS_BASE.iterdir():
            if d.is_dir():
                before.update(d.glob("*.jsonl"))
    try:
        r = subprocess.run(
            ["claude", "-p", "ok", "--output-format", "stream-json",
             "--verbose", "--no-session-persistence"],
            capture_output=True, text=True, timeout=20
        )
        # Delete phantom sessions created by claude -p
        if CLAUDE_PROJECTS_BASE.exists():
            for d in CLAUDE_PROJECTS_BASE.iterdir():
                if d.is_dir():
                    for f in d.glob("*.jsonl"):
                        if f not in before:
                            try: f.unlink()
                            except Exception: pass
        for line in r.stdout.splitlines():
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") == "rate_limit_event":
                info = obj.get("rate_limit_info", {})
                rl = {
                    "status": info.get("status", "unknown"),
                    "resetsAt": info.get("resetsAt"),
                    "rateLimitType": info.get("rateLimitType"),
                    "ts": int(datetime.now(tz=timezone.utc).timestamp()),
                }
                try:
                    with open(CLAUDE_RL_CACHE, "w") as fh:
                        json.dump(rl, fh)
                except Exception:
                    pass
                return rl
    except Exception:
        pass
    return None

CODEX_RL_CACHE = HOME / ".codex" / ".rate_limits_cache.json"

def _codex_rate_limits():
    """Get Codex rate limits via app-server JSON-RPC over WebSocket."""
    import base64
    proc = None
    port = 19299
    try:
        proc = subprocess.Popen(
            ["codex", "app-server", "--listen", f"ws://127.0.0.1:{port}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        # Wait for ready
        import time
        for _ in range(20):
            time.sleep(0.2)
            try:
                from urllib.request import urlopen
                urlopen(f"http://127.0.0.1:{port}/readyz", timeout=1)
                break
            except Exception:
                continue
        else:
            return None

        def _ws_frame(data):
            d = data.encode()
            f = bytearray([0x81])
            mk = os.urandom(4)
            if len(d) < 126:
                f.append(0x80 | len(d))
            elif len(d) < 65536:
                f.append(0x80 | 126)
                f.extend(struct.pack(">H", len(d)))
            f.extend(mk)
            f.extend(bytearray(b ^ mk[i % 4] for i, b in enumerate(d)))
            return bytes(f)

        def _ws_read(s, t=8):
            s.settimeout(t)
            h = s.recv(2)
            if len(h) < 2:
                return None
            op = h[0] & 0xF
            ln = h[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", s.recv(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", s.recv(8))[0]
            d = b""
            while len(d) < ln:
                d += s.recv(ln - len(d))
            if op == 0x09:
                return _ws_read(s, t)
            return d.decode() if op == 0x01 else None

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        sock.settimeout(5)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.send(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                   f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        sock.recv(4096)

        # Initialize
        sock.send(_ws_frame(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"clientInfo": {"name": "dashboard", "version": "1.0"}}})))
        for _ in range(10):
            raw = _ws_read(sock, 5)
            if raw and '"id":1' in raw or (raw and json.loads(raw).get("id") == 1):
                break

        sock.send(_ws_frame(json.dumps({"jsonrpc": "2.0", "method": "initialized"})))

        # Request rate limits
        sock.send(_ws_frame(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"})))
        for _ in range(20):
            raw = _ws_read(sock, 8)
            if not raw:
                break
            msg = json.loads(raw)
            if msg.get("id") == 2 and "result" in msg:
                sock.close()
                rl = msg["result"].get("rateLimits")
                if rl:
                    try:
                        with open(CODEX_RL_CACHE, "w") as fh:
                            json.dump(rl, fh)
                    except Exception:
                        pass
                return rl
        sock.close()
    except Exception:
        pass
    finally:
        if proc:
            proc.terminate()
            proc.wait()
    return None

def _get_codex(force_rl=False):
    if not shutil.which("codex"):
        return {"ok": False, "error": "not_installed"}
    sessions = []
    db_path = next((p for p in [HOME / ".codex" / "state_5.sqlite",
                                 HOME / ".codex" / "state.sqlite"]
                    if p.exists()), None)
    if db_path:
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            for r in conn.execute(
                "SELECT id, title, model, cwd, tokens_used, updated_at "
                "FROM threads WHERE archived = 0 ORDER BY updated_at DESC LIMIT 20"
            ).fetchall():
                dt = datetime.fromtimestamp(r["updated_at"], tz=timezone.utc)
                age_str, age_s = _age_from_dt(dt)
                sessions.append(_session(
                    sid=r["id"], cwd=r["cwd"],
                    model=r["model"] or "–",
                    age=age_str, age_s=age_s,
                    tokens=r["tokens_used"] or None,
                    ctx=_ctx_for_model(r["model"]),
                    last_ts=dt.strftime("%d.%m %H:%M"),
                    last_sort=dt.strftime("%Y-%m-%d %H:%M"),
                ))
            conn.close()
        except Exception:
            pass
    result = {"ok": True, "sessions": sessions}
    if force_rl:
        rl = _codex_rate_limits()
    elif CODEX_RL_CACHE.exists():
        try:
            with open(CODEX_RL_CACHE) as fh:
                rl = json.load(fh)
        except Exception:
            rl = None
    else:
        rl = None
    if rl:
        result["rate_limits"] = rl
    return result

def _get_copilot():
    if not shutil.which("gh"):
        return {"ok": False, "error": "not_installed"}
    try:
        r = subprocess.run(["gh", "copilot", "--", "--version"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return {"ok": False, "error": "ext_not_installed"}
    except Exception:
        return {"ok": False, "error": "ext_not_installed"}
    # Parse sessions from ~/.copilot/session-state/
    sessions = []
    state_dir = HOME / ".copilot" / "session-state"
    if state_dir.exists():
        dirs = sorted((d for d in state_dir.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
        for d in dirs[:20]:
            try:
                meta = {}
                ws = d / "workspace.yaml"
                if ws.exists():
                    for line in ws.read_text().splitlines():
                        if ":" in line:
                            k, _, v = line.partition(":")
                            meta[k.strip()] = v.strip()
                sid = meta.get("id", d.name)
                cwd = meta.get("cwd", "")
                model = ""
                total_tokens = 0
                events_file = d / "events.jsonl"
                if events_file.exists():
                    with open(events_file) as fh:
                        for eline in fh:
                            try:
                                ev = json.loads(eline)
                                etype = ev.get("type", "")
                                edata = ev.get("data", {})
                                if etype == "session.model_change":
                                    model = edata.get("newModel", "")
                                elif etype == "tool.execution_complete" and not model:
                                    model = edata.get("model", "")
                                if etype == "assistant.message":
                                    total_tokens += edata.get("outputTokens", 0)
                                if not cwd and etype == "system.message":
                                    c = edata.get("content", "")
                                    m = c.find("Current working directory: ")
                                    if m >= 0:
                                        cwd = c[m+26:].split("\n", 1)[0].strip()
                            except Exception:
                                pass
                # Use events.jsonl mtime (most accurate for active sessions)
                if events_file.exists():
                    dt = datetime.fromtimestamp(events_file.stat().st_mtime, tz=timezone.utc)
                else:
                    updated = meta.get("updated_at") or meta.get("created_at")
                    dt = (datetime.fromisoformat(updated.replace("Z", "+00:00"))
                          if updated else
                          datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc))
                age_str, age_s = _age_from_dt(dt)
                sessions.append(_session(
                    sid=sid, cwd=cwd,
                    model=(model or "–").replace("claude-", "").replace("-latest", ""),
                    age=age_str, age_s=age_s,
                    tokens=total_tokens or None,
                    ctx=_ctx_for_model(model),
                    last_ts=dt.strftime("%d.%m %H:%M"),
                    last_sort=dt.strftime("%Y-%m-%d %H:%M"),
                ))
            except Exception:
                continue
    return {"ok": True, "sessions": sessions}


# ── Delete / Launch handlers ─────────────────────────────────────────────────

def delete_session(kind, body):
    full_id = body.get("full_id", "")
    if kind == "openclaw":
        try:
            data = json.loads(OPENCLAW_SESSIONS_FILE.read_text())
            if full_id not in data:
                return False, f"Ключ не е намерен: {full_id}"
            del data[full_id]
            OPENCLAW_SESSIONS_FILE.write_text(json.dumps(data, indent=2))
            return True, f"Изтрита сесия: {full_id}"
        except Exception as e:
            return False, str(e)
    if kind == "claude":
        if len(full_id) != 36 or "/" in full_id or ".." in full_id:
            return False, "Невалиден session ID"
        if not CLAUDE_PROJECTS_BASE.exists():
            return False, "Директорията не е намерена"
        deleted = []
        for proj_dir in CLAUDE_PROJECTS_BASE.iterdir():
            if not proj_dir.is_dir():
                continue
            for path in [proj_dir / f"{full_id}.jsonl", proj_dir / full_id]:
                if path.exists():
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
                    deleted.append(path.name)
        return (True, f"Изтрито: {', '.join(deleted)}") if deleted else (False, "Файлът не е намерен")
    if kind == "copilot":
        if "/" in full_id or ".." in full_id:
            return False, "Невалиден session ID"
        session_dir = HOME / ".copilot" / "session-state" / full_id
        if session_dir.exists() and session_dir.is_dir():
            shutil.rmtree(session_dir)
            return True, f"Изтрита Copilot сесия: {full_id[:8]}…"
        return False, "Сесията не е намерена"
    if kind == "codex":
        if "/" in full_id or ".." in full_id:
            return False, "Невалиден session ID"
        db_path = next((p for p in [HOME / ".codex" / "state_5.sqlite",
                                     HOME / ".codex" / "state.sqlite"]
                        if p.exists()), None)
        if not db_path:
            return False, "Базата данни не е намерена"
        try:
            conn = sqlite3.connect(str(db_path))
            import time
            conn.execute(
                "UPDATE threads SET archived = 1, archived_at = ? WHERE id = ?",
                (int(time.time()), full_id))
            conn.commit()
            conn.close()
            return True, f"Архивирана Codex сесия: {full_id[:8]}…"
        except Exception as e:
            return False, str(e)
    return False, "Изтриването не се поддържа за този тип"

def launch_session(kind, body):
    cwd = body.get("cwd", str(HOME))
    sid = body.get("full_id", "")
    if sid:
        cmds = {
            "openclaw": f"cd {shlex.quote(cwd)} && openclaw tui --session {shlex.quote(sid)}",
            "claude":   f"cd {shlex.quote(cwd)} && claude --resume {shlex.quote(sid)}",
            "codex":    f"cd {shlex.quote(cwd)} && codex resume {shlex.quote(sid)}",
            "copilot":  f"cd {shlex.quote(cwd)} && gh copilot",
        }
    else:
        cmds = {
            "openclaw": f"cd {shlex.quote(cwd)} && openclaw tui",
            "claude":   f"cd {shlex.quote(cwd)} && claude --verbose",
            "codex":    f"cd {shlex.quote(cwd)} && codex",
            "copilot":  f"cd {shlex.quote(cwd)} && gh copilot",
        }
    cmd = cmds.get(kind)
    if not cmd:
        return False, "Непознат тип"
    return launch_terminal(cmd)


# ── HTML builder ─────────────────────────────────────────────────────────────

def _build_panel(cfg, result):
    key, title, color = cfg["key"], cfg["title"], cfg["color"]

    if not result["ok"] and result.get("error") == "not_installed":
        return f"""
  <div class="panel panel-offline">
    <div class="panel-header">
      <div class="panel-title">
        <div class="dot" style="background:var(--dim);box-shadow:none"></div>{title}</div>
      <span class="count-badge offline-badge">не е инсталиран</span>
    </div>
    <div class="offline-body">
      <span class="dim">Инсталирай с:</span>
      <code>{cfg['install']}</code>
    </div>
  </div>"""

    sessions = result.get("sessions", [])
    rows = ""
    for i, s in enumerate(sessions, 1):
        enc_id = urllib.parse.quote(s["full_id"], safe="")
        enc_cwd = urllib.parse.quote(s["cwd_full"], safe="")
        tok_raw = s['tokens'] or 0
        rows += f"""<tr>
          <td class="num" data-v="{i}">{i}</td>
          <td data-v="{s['id']}"><code class="sid">{s['id']}…</code></td>
          <td class="cwd" title="{s['cwd_full']}" data-v="{s['cwd']}">{s['cwd']}</td>
          <td class="model" data-v="{s['model']}">{s['model']}</td>
          <td class="age" data-v="{s['age_s']}">{s['age']}</td>
          <td data-v="{tok_raw}">{_fmt_tokens(s['tokens'], s['ctx_tokens'])}</td>
          <td class="dim" data-v="{s['last_sort']}">{s['last_ts']}</td>
          <td class="action-cell">
            <button class="term-btn" title="Отвори" onclick="doLaunch(this,'{key}','{enc_id}','{enc_cwd}')">⌨</button>
            <button class="del-btn" onclick="doDel(this,'{key}','{enc_id}')">✕</button>
          </td>
        </tr>"""
    if not rows:
        rows = '<tr><td colspan="8" class="dim" style="padding:20px;text-align:center">Няма намерени сесии</td></tr>'
    err = ""
    if not result["ok"]:
        err = f'<tr><td colspan="8" class="err">{result.get("error","")}</td></tr>'

    # Rate limits bar (unified for Codex + Claude)
    rl_html = ""
    rl = result.get("rate_limits")
    if rl:
        parts = []
        windows = []
        if rl.get("primary") or rl.get("secondary"):
            # Codex format: primary/secondary with usedPercent/resetsAt
            for label, win in [("5ч", rl.get("primary")), ("Седм", rl.get("secondary"))]:
                if win:
                    windows.append((label, win.get("usedPercent", 0), win.get("resetsAt")))
        elif rl.get("rateLimitType"):
            # Claude format: flat dict from rate_limit_event
            status = rl.get("status", "")
            rst = rl.get("resetsAt")
            rt = rl.get("rateLimitType", "")
            label = "5ч" if "five" in rt else "Седм" if "seven" in rt else rt
            # status: "allowed" → green, "allowed_warning" → yellow, "denied"/"exceeded" → red
            if "denied" in status or "exceeded" in status:
                icon, clr = "❌", "#ef4444"
            elif "warning" in status:
                icon, clr = "⚠️", "#f59e0b"
            else:
                icon, clr = "✅", "#22c55e"
            reset_str = ""
            if rst:
                dt_r = datetime.fromtimestamp(int(rst), tz=timezone.utc).astimezone()
                reset_str = f" ↻ {dt_r.strftime('%d.%m %H:%M')}"
            parts.append(
                f'<span class="rl-item">{label}: {icon}'
                f'<span class="dim">{reset_str}</span></span>'
            )
        for label, used, reset_ts in windows:
            left = max(0, 100 - int(used))
            clr = "#22c55e" if left > 60 else "#f59e0b" if left > 30 else "#ef4444"
            reset_str = ""
            if reset_ts:
                dt_r = datetime.fromtimestamp(reset_ts, tz=timezone.utc).astimezone()
                reset_str = f" ↻ {dt_r.strftime('%d.%m %H:%M')}"
            parts.append(
                f'<span class="rl-item">{label}: '
                f'<span class="rl-bar-bg"><span class="rl-bar-fill" style="width:{left}%;background:{clr}"></span></span>'
                f'<span style="color:{clr}">{left}%</span>'
                f'<span class="dim">{reset_str}</span></span>'
            )
        plan = rl.get("planType", "")
        plan_html = f'<span class="rl-plan">{plan.title()}</span>' if plan else ""
        rl_html = f'<div class="rl-row">{plan_html}{" ".join(parts)}</div>'

    return f"""
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title"><div class="dot" style="background:{color};box-shadow:0 0 6px {color}"></div>{title}<button class="new-btn" title="Нова сесия" onclick="doNewSession('{key}')">+</button></div>
      <div class="header-badges">{rl_html}<span class="count-badge">{len(sessions)} сесии</span></div>
    </div>
    <table>
    <colgroup>
      <col style="width:40px"><col style="width:90px"><col><col style="width:120px">
      <col style="width:60px"><col style="width:180px"><col style="width:100px"><col style="width:60px">
    </colgroup>
    <thead><tr>
      <th class="sortable" data-col="0" data-type="num">#</th>
      <th class="sortable" data-col="1" data-type="str">ID</th>
      <th class="sortable" data-col="2" data-type="str">Директория</th>
      <th class="sortable" data-col="3" data-type="str">Модел</th>
      <th class="sortable" data-col="4" data-type="num">Преди</th>
      <th class="sortable" data-col="5" data-type="num">Токени</th>
      <th class="sortable" data-col="6" data-type="str">Последно</th>
      <th></th>
    </tr></thead><tbody>{err}{rows}</tbody></table>
  </div>"""


def build_html(force_rl=False):
    panels_html = ""
    for cfg in PANELS:
        panels_html += _build_panel(cfg, get_sessions(cfg["key"], force_rl=force_rl))
    now_str = datetime.now().strftime("%H:%M:%S")

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
  header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid var(--border);flex-wrap:wrap;gap:12px}}
  header h1{{font-size:18px;font-weight:600;letter-spacing:.5px}}
  header h1 span{{color:var(--accent)}}
  .header-right{{display:flex;align-items:center;gap:16px}}
  .updated{{color:var(--dim);font-size:11px}}
  .refresh-btn{{background:var(--border);border:1px solid #2d3a4e;color:var(--text);padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px;font-family:inherit;transition:background .15s}}
  .refresh-btn:hover{{background:#1e2d42}}
  .grid{{display:grid;grid-template-columns:1fr;gap:20px}}
  .panel{{background:var(--panel);border:1px solid var(--border);border-radius:12px;overflow:hidden}}
  .panel-header{{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid var(--border);background:rgba(99,102,241,.05)}}
  .panel-title{{font-size:13px;font-weight:600;display:flex;align-items:center;gap:8px}}
  .dot{{width:8px;height:8px;border-radius:50%}}
  .count-badge{{background:var(--border);color:var(--dim);font-size:11px;padding:2px 8px;border-radius:10px}}
  .offline-badge{{color:var(--red);border:1px solid rgba(239,68,68,.3)}}
  .offline-body{{display:flex;align-items:center;gap:12px;padding:20px;color:var(--dim);font-size:12px}}
  .offline-body code{{background:rgba(255,255,255,.06);padding:4px 10px;border-radius:6px;font-size:12px;color:var(--yellow)}}
  table{{width:100%;border-collapse:collapse;table-layout:fixed}}
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
  .tok-wrap{{display:flex;align-items:center;gap:6px;white-space:nowrap}}
  .tok-num{{font-size:11px;color:var(--dim);min-width:80px}}
  .bar-bg{{width:60px;height:4px;background:var(--border);border-radius:2px;overflow:hidden}}
  .bar-fill{{height:100%;border-radius:2px;transition:width .3s}}
  .tok-pct{{font-size:11px;font-weight:600;min-width:30px}}
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
  .search-box{{background:var(--panel);border:1px solid var(--border);color:var(--text);padding:6px 12px 6px 30px;border-radius:8px;font-size:12px;font-family:inherit;width:260px;outline:none;transition:border-color .15s}}
  .search-box:focus{{border-color:var(--accent)}}
  .search-wrap{{position:relative;display:flex;align-items:center}}
  .search-wrap::before{{content:'🔍';position:absolute;left:8px;font-size:12px;opacity:.5;pointer-events:none}}
  th.sortable{{cursor:pointer;user-select:none;position:relative;transition:color .15s}}
  th.sortable:hover{{color:var(--text)}}
  th.sortable::after{{content:'';margin-left:4px;font-size:8px;opacity:.3}}
  th.sortable.asc::after{{content:'▲';opacity:.8}}
  th.sortable.desc::after{{content:'▼';opacity:.8}}
  tr.filter-hidden{{display:none}}
  .rl-row{{display:flex;align-items:center;gap:12px;font-size:11px;margin-right:12px}}
  .rl-item{{display:flex;align-items:center;gap:4px;white-space:nowrap}}
  .rl-bar-bg{{width:50px;height:5px;background:var(--border);border-radius:3px;overflow:hidden;display:inline-block}}
  .rl-bar-fill{{height:100%;border-radius:3px;transition:width .3s}}
  .rl-plan{{background:rgba(99,102,241,.15);color:var(--accent);padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600}}
  .header-badges{{display:flex;align-items:center;gap:8px}}
  .new-btn{{background:transparent;border:1px solid var(--border);color:var(--dim);font-size:14px;width:22px;height:22px;border-radius:6px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;font-family:inherit;transition:all .15s;line-height:1;margin-left:4px}}
  .new-btn:hover{{background:rgba(99,102,241,.15);border-color:rgba(99,102,241,.3);color:var(--accent)}}
</style>
</head>
<body>
<header>
  <h1>⚡ AI <span>Dashboard</span></h1>
  <div class="header-right">
    <div class="search-wrap"><input type="text" class="search-box" id="globalSearch" placeholder="Търси..." autocomplete="off"></div>
    <span class="updated">Обновено в {now_str}</span>
    <button class="refresh-btn" onclick="location.href='/?force=1'">↻ Обнови</button>
  </div>
</header>
<div class="grid">
  {panels_html}
</div>
<script>
  setTimeout(() => {{ window.location.href = '/'; }}, 30000);

  function showToast(msg, isErr) {{
    const t = document.createElement('div');
    t.className = 'toast' + (isErr ? ' err' : '');
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3000);
  }}

  async function doDel(btn, type, fullId) {{
    const row = btn.closest('tr');
    btn.classList.add('loading'); btn.textContent = '↻';
    row.classList.add('deleting');
    try {{
      const j = await fetch('/delete', {{method:'POST',headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{type, full_id:decodeURIComponent(fullId)}})
      }}).then(r=>r.json());
      if (j.ok) {{ btn.classList.remove('loading'); btn.classList.add('done'); btn.textContent='✓'; showToast(j.msg); setTimeout(()=>row.remove(),600); }}
      else {{ btn.classList.remove('loading'); btn.textContent='✕'; row.classList.remove('deleting'); showToast(j.msg,true); }}
    }} catch(e) {{ btn.classList.remove('loading'); btn.textContent='✕'; row.classList.remove('deleting'); showToast('Грешка: '+e.message,true); }}
  }}

  async function doLaunch(btn, type, fullId, cwd) {{
    btn.classList.add('done');
    try {{
      const j = await fetch('/launch', {{method:'POST',headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{type, full_id:decodeURIComponent(fullId), cwd:decodeURIComponent(cwd)}})
      }}).then(r=>r.json());
      showToast(j.msg, !j.ok);
    }} catch(e) {{ showToast('Грешка: '+e.message,true); }}
    setTimeout(()=>btn.classList.remove('done'),1500);
  }}

  async function doNewSession(type) {{
    try {{
      const j = await fetch('/launch', {{method:'POST',headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{type}})
      }}).then(r=>r.json());
      showToast(j.msg, !j.ok);
    }} catch(e) {{ showToast('Грешка: '+e.message,true); }}
  }}

  // ── Filter ──
  document.getElementById('globalSearch').addEventListener('input', function() {{
    const q = this.value.toLowerCase().trim();
    document.querySelectorAll('.panel tbody tr').forEach(row => {{
      if (row.querySelector('.err') || row.querySelector('td[colspan]')) return;
      const text = row.textContent.toLowerCase();
      row.classList.toggle('filter-hidden', q && !text.includes(q));
    }});
    // update counts
    document.querySelectorAll('.panel').forEach(panel => {{
      const badge = panel.querySelector('.count-badge');
      if (!badge || badge.classList.contains('offline-badge')) return;
      const visible = panel.querySelectorAll('tbody tr:not(.filter-hidden):not(.deleting)').length;
      const total = panel.querySelectorAll('tbody tr:not([style])').length;
      badge.textContent = q ? visible + '/' + total + ' сесии' : total + ' сесии';
    }});
  }});

  // ── Sort ──
  document.querySelectorAll('th.sortable').forEach(th => {{
    th.addEventListener('click', function() {{
      const table = this.closest('table');
      const tbody = table.querySelector('tbody');
      const col = parseInt(this.dataset.col);
      const isNum = this.dataset.type === 'num';
      const curDir = this.classList.contains('asc') ? 'asc' : this.classList.contains('desc') ? 'desc' : '';
      const newDir = curDir === 'asc' ? 'desc' : 'asc';

      // clear sort from sibling headers
      this.closest('tr').querySelectorAll('th.sortable').forEach(h => h.classList.remove('asc','desc'));
      this.classList.add(newDir);

      const rows = Array.from(tbody.querySelectorAll('tr')).filter(r => !r.querySelector('.err') && !r.querySelector('td[colspan]'));
      rows.sort((a, b) => {{
        const av = a.children[col]?.dataset.v || a.children[col]?.textContent || '';
        const bv = b.children[col]?.dataset.v || b.children[col]?.textContent || '';
        let cmp;
        if (isNum) {{ cmp = (parseFloat(av) || 0) - (parseFloat(bv) || 0); }}
        else {{ cmp = av.localeCompare(bv, 'bg'); }}
        return newDir === 'desc' ? -cmp : cmp;
      }});
      rows.forEach(r => tbody.appendChild(r));
    }});
  }});
</script>
</body>
</html>"""


# ── HTTP server ───────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        force_rl = "force=1" in (self.path or "")
        html = build_html(force_rl=force_rl).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        kind = body.get("type", "")
        if self.path == "/delete":
            ok, msg = delete_session(kind, body)
        elif self.path == "/launch":
            ok, msg = launch_session(kind, body)
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
