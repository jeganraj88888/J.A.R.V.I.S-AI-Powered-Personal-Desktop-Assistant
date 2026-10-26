import base64
import io
import json
import os
import platform
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

# ElevenLabs neural TTS is accessed through the REST API.
# The API key and voice ID stay server-side in Windows environment variables.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "astra.db")
TZ = ZoneInfo("Asia/Kolkata")
TEXT_MODEL = os.environ.get("JARVIS_TEXT_MODEL", "gemini-3.5-flash-lite")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()
ELEVENLABS_OUTPUT_FORMAT = os.environ.get("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128").strip()
VOICE_NAME = os.environ.get("JARVIS_VOICE", "ElevenLabs neural voice")
USER_TITLE = os.environ.get("JARVIS_USER_TITLE", "Sir")
API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

app = Flask(__name__, static_folder=BASE_DIR)
CORS(app)
_system_prev = {"t": 0.0, "rx": 0, "tx": 0}

SYSTEM_PROMPT = f"""You are J.A.R.V.I.S., a personal AI assistant for {USER_TITLE}.
Be natural, concise, calm and conversational. Answer the question directly.
Default to 1-3 short spoken sentences. Expand only when asked.
Do not use markdown unless the user explicitly asks for formatted text.
Never invent current facts. When live search context is supplied, use it for current facts.
Do not claim you performed an action unless the server confirms it.
"""

FAST_REPLIES = {
    "hi": "Hello, Sir.",
    "hello": "Hello, Sir.",
    "hey": "Hello, Sir.",
    "hi jarvis": "Hello, Sir.",
    "hello jarvis": "Hello, Sir.",
    "hey jarvis": "Yes, Sir?",
    "good morning": "Good morning, Sir.",
    "good afternoon": "Good afternoon, Sir.",
    "good evening": "Good evening, Sir.",
    "how are you": "All systems are operating normally, Sir.",
}

APP_COMMANDS = {
    "calculator": ["calc.exe"],
    "calc": ["calc.exe"],
    "notepad": ["notepad.exe"],
    "paint": ["mspaint.exe"],
    "explorer": ["explorer.exe"],
    "task manager": ["taskmgr.exe"],
    "command prompt": ["cmd.exe"],
    "cmd": ["cmd.exe"],
    "powershell": ["powershell.exe"],
    "settings": ["cmd.exe", "/c", "start", "", "ms-settings:"],
}

WEBSITES = {
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "chatgpt": "https://chatgpt.com",
}

PC_PATTERNS = [
    re.compile(r"^(?:open|launch|start|run) (.+)$", re.I),
    re.compile(r"^(?:go to) (.+)$", re.I),
    re.compile(r"^(?:close|quit|exit) (.+)$", re.I),
]


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        """
    )
    db.commit()
    db.close()


def save_message(role, message):
    try:
        db = sqlite3.connect(DB_PATH, timeout=5)
        db.execute(
            "INSERT INTO conversations(role, message, created_at) VALUES(?,?,?)",
            (role, message, now_iso()),
        )
        db.commit()
        db.close()
    except Exception:
        pass


def recent_history(limit=4):
    try:
        db = sqlite3.connect(DB_PATH)
        rows = db.execute(
            "SELECT role, message FROM conversations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        db.close()
        return list(reversed(rows))
    except Exception:
        return []


def memories(limit=6):
    try:
        db = sqlite3.connect(DB_PATH)
        rows = db.execute(
            "SELECT id, memory, created_at FROM memories ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        db.close()
        return [{"id": r[0], "memory": r[1], "created_at": r[2]} for r in rows]
    except Exception:
        return []


def add_memory(text):
    text = text.strip()
    if not text:
        return False
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute("INSERT OR IGNORE INTO memories(memory, created_at) VALUES(?,?)", (text, now_iso()))
        db.commit()
        changed = db.total_changes > 0
        db.close()
        return changed
    except Exception:
        return False


def delete_memory(memory_id):
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        db.commit()
        changed = db.total_changes > 0
        db.close()
        return changed
    except Exception:
        return False


def normalize(text):
    s = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())
    s = re.sub(r"(.)\1+", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def quick_reply(message):
    return FAST_REPLIES.get(normalize(message))


def local_time_reply(message):
    n = normalize(message)
    if any(x in n for x in ["what time", "current time", "time now", "tell me the time"]):
        return f"It's {datetime.now(TZ).strftime('%I:%M %p')}, {USER_TITLE}."
    if any(x in n for x in ["what date", "today date", "todays date", "what day is it"]):
        return f"Today is {datetime.now(TZ).strftime('%A, %d %B %Y')}."
    return None


def run_pc_command(message):
    n = normalize(message)
    m = PC_PATTERNS[0].match(n)
    action = None
    target = None
    if m:
        action, target = "open", m.group(1).strip()
    else:
        m = PC_PATTERNS[1].match(n)
        if m:
            action, target = "open", m.group(1).strip()
        else:
            m = PC_PATTERNS[2].match(n)
            if m:
                action, target = "close", m.group(1).strip()

    if not action or not target:
        return None

    if action == "open":
        if target in APP_COMMANDS:
            try:
                subprocess.Popen(APP_COMMANDS[target], shell=False)
                return f"Opening {target.title()}.", "pc"
            except Exception as exc:
                return f"I couldn't open {target.title()}: {exc}", "error"
        if target in WEBSITES:
            try:
                webbrowser.open(WEBSITES[target], new=2)
                return f"Opening {target.title()}.", "pc"
            except Exception as exc:
                return f"I couldn't open {target.title()}: {exc}", "error"

    if action == "close":
        close_map = {
            "notepad": "notepad.exe", "calculator": "CalculatorApp.exe", "chrome": "chrome.exe",
            "edge": "msedge.exe", "paint": "mspaint.exe", "spotify": "Spotify.exe",
        }
        exe = close_map.get(target)
        if exe:
            try:
                subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True, text=True, timeout=5)
                return f"Closing {target.title()}.", "pc"
            except Exception as exc:
                return f"I couldn't close {target.title()}: {exc}", "error"

    return None


def live_search(query, max_results=4):
    url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-IN&gl=IN&ceid=IN:en"
    import requests
    r = requests.get(url, headers={"User-Agent": "JARVIS/1.0"}, timeout=6)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        source = item.find("source")
        publisher = (source.text or "").strip() if source is not None else ""
        if title:
            out.append({"title": title, "publisher": publisher, "url": item.findtext("link") or ""})
        if len(out) >= max_results:
            break
    return out


def needs_live_search(message):
    n = normalize(message)
    return any(k in n.split() for k in ["latest", "today", "current", "news", "recently", "breaking"]) or "right now" in n


def build_prompt(message):
    parts = [SYSTEM_PROMPT]
    hist = recent_history(4)
    if hist:
        parts.append("Recent conversation:\n" + "\n".join(f"{r.title()}: {m}" for r, m in hist))
    mems = memories(6)
    if mems:
        parts.append("Saved memory:\n" + "\n".join(f"- {m['memory']}" for m in mems))
    if needs_live_search(message):
        try:
            news = live_search(message, 4)
            if news:
                parts.append("Live search context:\n" + "\n".join(f"- {x['title']} ({x['publisher']})" for x in news))
                parts.append("For current claims, use only the live context. Mention sources briefly if useful.")
        except Exception:
            parts.append("Live search failed. Do not pretend you have current results.")
    parts.append(f"Current India time: {datetime.now(TZ).strftime('%A, %d %B %Y, %I:%M %p')}")
    return "\n\n".join(parts)


# Keep one Gemini client alive for the entire Flask process.
# This avoids the httpx "Cannot send a request, as the client has been closed"
# failure that can happen when a temporary google-genai client is created
# inside a request and a streaming iterator outlives that local object.
_GEMINI_CLIENT = None
_GEMINI_LOCK = threading.Lock()


def gemini_client():
    global _GEMINI_CLIENT
    if genai is None:
        raise RuntimeError("google-genai is not installed. Run: py -m pip install -r requirements.txt")
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured in Windows.")
    with _GEMINI_LOCK:
        if _GEMINI_CLIENT is None:
            _GEMINI_CLIENT = genai.Client(api_key=API_KEY)
        return _GEMINI_CLIENT


def reset_gemini_client():
    global _GEMINI_CLIENT
    with _GEMINI_LOCK:
        old = _GEMINI_CLIENT
        _GEMINI_CLIENT = None
        try:
            close = getattr(old, "close", None)
            if callable(close):
                close()
        except Exception:
            pass


def gemini_stream(message):
    client = gemini_client()
    prompt = build_prompt(message)
    config = None
    if types is not None:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=220,
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        )
    return client.models.generate_content_stream(
        model=TEXT_MODEL,
        contents=prompt + "\n\nUser request: " + message,
        config=config,
    )


def strip_for_voice(text):
    text = re.sub(r"\s*Sources?:.*$", "", text, flags=re.I | re.S)
    return re.sub(r"[*_`#]", "", text).strip()[:900]


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify({
        "ok": bool(API_KEY and genai is not None),
        "provider": "Google Gemini API",
        "text_model": TEXT_MODEL,
        "voice": VOICE_NAME,
        "tts_provider": "ElevenLabs",
        "tts_configured": bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID),
        "api_key_configured": bool(API_KEY),
        "sdk_installed": genai is not None,
    })


@app.get("/api/system")
def system():
    ram = cpu = disk = 0
    net_up = net_down = 0.0
    battery = None
    charging = True
    try:
        import psutil
        ram = round(psutil.virtual_memory().percent)
        cpu = round(psutil.cpu_percent(interval=None))
        disk = round(psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\").percent)
        if _system_prev.get("t"):
            dt = max(time.time() - _system_prev["t"], 0.001)
            net = psutil.net_io_counters()
            net_down = round((net.bytes_recv - _system_prev["rx"]) * 8 / dt / 1e6, 1)
            net_up = round((net.bytes_sent - _system_prev["tx"]) * 8 / dt / 1e6, 1)
            _system_prev.update(t=time.time(), rx=net.bytes_recv, tx=net.bytes_sent)
        else:
            net = psutil.net_io_counters(); _system_prev.update(t=time.time(), rx=net.bytes_recv, tx=net.bytes_sent)
        b = psutil.sensors_battery()
        if b:
            battery = round(b.percent); charging = bool(b.power_plugged)
    except Exception:
        pass
    return jsonify({
        "cpu": cpu, "ram": ram, "disk": disk,
        "net_up": net_up, "net_down": net_down,
        "battery": battery, "charging": charging,
        "host": platform.node(), "os": platform.platform(),
        "cores": os.cpu_count() or 0, "ram_total_gb": 0,
        "source": "psutil"
    })

@app.get("/api/history")
def history():
    return jsonify({"history": [{"role": r, "message": m} for r, m in recent_history(20)]})


@app.get("/api/memory")
def get_memory():
    return jsonify({"memories": memories(30)})


@app.post("/api/memory")
def post_memory():
    body = request.get_json(silent=True) or {}
    text = str(body.get("memory", "")).strip()
    return jsonify({"ok": add_memory(text)})


@app.delete("/api/memory/<int:memory_id>")
def remove_memory(memory_id):
    return jsonify({"ok": delete_memory(memory_id)})


@app.post("/api/command")
def command():
    body = request.get_json(silent=True) or {}
    result = run_pc_command(str(body.get("command", "")))
    if result is None:
        return jsonify({"ok": False, "reply": "That PC command is not available."}), 400
    reply, state = result
    return jsonify({"ok": state != "error", "reply": reply})


@app.post("/api/tts")
def tts():
    """Generate J.A.R.V.I.S. speech with ElevenLabs and return MP3 audio."""
    body = request.get_json(silent=True) or {}
    text = strip_for_voice(str(body.get("text", "")))
    if not text:
        return jsonify({"error": "Text is required."}), 400

    if not ELEVENLABS_API_KEY:
        return jsonify({
            "ok": False,
            "provider": "ElevenLabs",
            "error": "ELEVENLABS_API_KEY is not configured in Windows."
        }), 503

    if not ELEVENLABS_VOICE_ID:
        return jsonify({
            "ok": False,
            "provider": "ElevenLabs",
            "error": "ELEVENLABS_VOICE_ID is not configured in Windows."
        }), 503

    try:
        import requests

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{quote_plus(ELEVENLABS_VOICE_ID)}"
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": ELEVENLABS_MODEL_ID,
            "output_format": ELEVENLABS_OUTPUT_FORMAT,
            "voice_settings": {
                "stability": 0.48,
                "similarity_boost": 0.78,
                "style": 0.18,
                "use_speaker_boost": True,
            },
        }

        r = requests.post(url, headers=headers, json=payload, timeout=30)
        if not r.ok:
            detail = r.text[:500]
            return jsonify({
                "ok": False,
                "provider": "ElevenLabs",
                "error": f"ElevenLabs HTTP {r.status_code}: {detail}"
            }), 502

        return Response(
            r.content,
            mimetype="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "X-JARVIS-TTS-Provider": "ElevenLabs",
                "X-JARVIS-TTS-Voice": ELEVENLABS_VOICE_ID,
            },
        )
    except requests.RequestException as exc:
        return jsonify({
            "ok": False,
            "provider": "ElevenLabs",
            "error": f"ElevenLabs connection error: {exc}"
        }), 502
    except Exception as exc:
        app.logger.exception("J.A.R.V.I.S. ElevenLabs TTS error")
        return jsonify({
            "ok": False,
            "provider": "ElevenLabs",
            "error": f"TTS error: {type(exc).__name__}: {exc}"
        }), 503



@app.get("/api/search")
def search_api():
    q = str(request.args.get("q", "")).strip()
    if not q:
        return jsonify({"ok": False, "results": []})
    try:
        results = live_search(q, 6)
        formatted = []
        for item in results:
            # live_search only returns title/publisher in this clean backend;
            # keep a stable shape for the restored original frontend.
            formatted.append({
                "title": item.get("title", ""),
                "source": item.get("publisher", ""),
                "url": item.get("url", ""),
            })
        return jsonify({"ok": True, "results": formatted})
    except Exception as exc:
        return jsonify({"ok": False, "results": [], "error": str(exc)})


@app.get("/api/diagnostics")
def diagnostics():
    checks = []
    checks.append({"name": "Brain response", "ok": bool(API_KEY and genai is not None),
                   "detail": "Gemini API configured." if API_KEY and genai is not None else "Gemini API unavailable."})
    try:
        init_db()
        db_ok = os.path.exists(DB_PATH)
    except Exception:
        db_ok = False
    checks.append({"name": "Memory database", "ok": db_ok,
                   "detail": "SQLite memory database ready." if db_ok else "Database unavailable."})
    try:
        # Cheap connectivity check; do not force a long search here.
        import requests as _requests
        rr = _requests.get("https://news.google.com", timeout=2)
        search_ok = rr.ok
    except Exception:
        search_ok = False
    checks.append({"name": "Web search", "ok": search_ok,
                   "detail": "Internet connection available." if search_ok else "Internet search unavailable."})
    checks.append({"name": "PC control", "ok": platform.system() == "Windows",
                   "detail": "Windows allowlisted controls enabled." if platform.system() == "Windows" else "Windows controls unavailable."})
    tts_ok = bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID)
    checks.append({"name": "Voice engine", "ok": tts_ok,
                   "detail": "ElevenLabs TTS configured." if tts_ok else "ElevenLabs API key or voice ID missing."})
    return jsonify({"ok": all(c["ok"] for c in checks), "checks": checks})


@app.post("/api/chat_stream")
def chat_stream():
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", "")).strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400

    quick = quick_reply(message)
    local = local_time_reply(message)
    pc = run_pc_command(message)
    if quick or local:
        reply = quick or local
        save_message("user", message); save_message("assistant", reply)
        return Response(
            json.dumps({"type": "done", "reply": reply, "model": "local-fast"}) + "\n",
            mimetype="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )
    if pc:
        reply, state = pc
        save_message("user", message); save_message("assistant", reply)
        return Response(
            json.dumps({"type": "done", "reply": reply, "model": "pc-control"}) + "\n",
            mimetype="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    save_message("user", message)

    def generate():
        full = []
        attempted_reset = False
        while True:
            try:
                for chunk in gemini_stream(message):
                    text = getattr(chunk, "text", None) or ""
                    if text:
                        full.append(text)
                        yield json.dumps({"type": "token", "text": text}, ensure_ascii=False) + "\n"
                reply = "".join(full).strip() or "Standing by."
                save_message("assistant", reply)
                yield json.dumps({"type": "done", "reply": reply, "model": TEXT_MODEL}, ensure_ascii=False) + "\n"
                return
            except RuntimeError as exc:
                msg = str(exc)
                if not attempted_reset and "client has been closed" in msg.lower():
                    attempted_reset = True
                    full.clear()
                    reset_gemini_client()
                    continue
                yield json.dumps({"type": "error", "error": msg}, ensure_ascii=False) + "\n"
                return
            except Exception as exc:
                yield json.dumps({"type": "error", "error": str(exc)}, ensure_ascii=False) + "\n"
                return

    return Response(generate(), mimetype="application/x-ndjson", headers={"Cache-Control": "no-cache"})


@app.post("/api/chat")
def chat():
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", "")).strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400
    quick = quick_reply(message)
    local = local_time_reply(message)
    pc = run_pc_command(message)
    if quick or local:
        reply = quick or local
        save_message("user", message); save_message("assistant", reply)
        return jsonify({"reply": reply, "model": "local-fast"})
    if pc:
        reply, _ = pc
        save_message("user", message); save_message("assistant", reply)
        return jsonify({"reply": reply, "model": "pc-control"})
    try:
        save_message("user", message)
        text = "".join((getattr(c, "text", None) or "") for c in gemini_stream(message)).strip() or "Standing by."
        save_message("assistant", text)
        return jsonify({"reply": text, "model": TEXT_MODEL})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    init_db()
    print("=" * 56)
    print("  J.A.R.V.I.S — FINAL CLEAN BUILD")
    print("=" * 56)
    print(" Interface : http://127.0.0.1:5000")
    print(f" Provider  : Google Gemini API")
    print(f" Text      : {TEXT_MODEL}")
    print(" Voice     : ElevenLabs")
    print(f" Voice ID  : {'configured' if ELEVENLABS_VOICE_ID else 'MISSING'}")
    print(f" Gemini key: {'configured' if API_KEY else 'MISSING'}")
    print(f" Eleven key: {'configured' if ELEVENLABS_API_KEY else 'MISSING'}")
    print(" Memory    : astra.db")
    print("=" * 56)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
