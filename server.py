"""Aqua Code - your own coding agent (DeepSeek / Gemini) with credit limits and
cheap-to-strong model escalation.  Pure standard library, runs on 127.0.0.1 only.

    python server.py            -> http://127.0.0.1:8765
"""
import collections
import copy
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("AQUA_CODE_DATA") or os.path.join(ROOT, "data")
os.makedirs(DATA, exist_ok=True)
CFG_PATH = os.path.join(DATA, "config.json")
USAGE_PATH = os.path.join(DATA, "usage.json")
SESS_PATH = os.path.join(DATA, "sessions.json")
PORT = int(os.environ.get("AQUA_CODE_PORT", "8765"))

# --------------------------------------------------------------------------- config
DEFAULT_CONFIG = {
    "provider": "deepseek",
    "workspace": os.path.join(ROOT, "workspace"),
    "approval": "edits",          # ask = confirm every write+command, edits = confirm commands only, auto = never ask
    "start_tier": 0,              # every task starts on this tier (0 = cheapest)
    "max_tier": 2,                # never escalate above this tier
    "auto_escalate": True,
    "esc_errors": 2,              # consecutive failing steps before escalating
    "esc_steps": 8,               # steps on one tier without finishing before escalating
    "sticky_tier": False,         # keep the stronger tier for the next message
    "failover": True,             # when an AI runs out of credit or hits its limit, switch to the next connected AI
    "failover_paid": False,       # ...but only to FREE providers unless this is on (so it never spends money by surprise)
    "max_steps": 40,
    "daily_budget": 1.0,          # USD, 0 = unlimited
    "total_budget": 10.0,         # USD, 0 = unlimited
    "providers": {
        "deepseek": {
            "label": "DeepSeek",
            "base": "https://api.deepseek.com",
            "key": "",
            "free": False,
            "tiers": [
                {"name": "Lite", "model": "deepseek-chat", "in": 0.28, "cache": 0.028, "out": 0.42, "rpm": 0, "rpd": 0},
                {"name": "Pro", "model": "deepseek-reasoner", "in": 0.28, "cache": 0.028, "out": 0.42, "rpm": 0, "rpd": 0},
            ],
        },
        "gemini": {
            "label": "Gemini",
            "base": "https://generativelanguage.googleapis.com/v1beta/openai",
            "key": "",
            "free": True,                 # free-tier key: cost counted as $0, request limits respected
            "tiers": [
                # "-latest" aliases are Google's own pointers to the current model, so they do not get retired like pinned ids
                {"name": "Lite", "model": "gemini-flash-lite-latest", "in": 0.10, "cache": 0.0, "out": 0.40, "rpm": 15, "rpd": 1000},
                {"name": "Standard", "model": "gemini-flash-latest", "in": 0.30, "cache": 0.0, "out": 2.50, "rpm": 10, "rpd": 250},
                {"name": "Pro", "model": "gemini-pro-latest", "in": 1.25, "cache": 0.0, "out": 10.0, "rpm": 5, "rpd": 100},
            ],
        },
        "openrouter": {               # free models through OpenRouter (":free" variants; the list changes, see /api/openrouter/autoconfig)
            "label": "OpenRouter",
            "base": "https://openrouter.ai/api/v1",
            "key": "",
            "free": True,
            "tiers": [
                {"name": "Lite", "model": "nvidia/nemotron-3.5-lightning:free", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 20, "rpd": 50},
                {"name": "Pro", "model": "nvidia/nemotron-3-ultra-550b-a55b:free", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 20, "rpd": 50},
            ],
        },
        # --- permanent free tiers from the "awesome free LLM APIs" lists (own key each; limits as the lists state them) ---
        "groq": {
            "label": "Groq", "base": "https://api.groq.com/openai/v1", "key": "", "free": True,
            "tiers": [
                {"name": "Lite", "model": "openai/gpt-oss-20b", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 30, "rpd": 1000},
                {"name": "Pro", "model": "openai/gpt-oss-120b", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 30, "rpd": 1000},
            ],
        },
        "cerebras": {
            "label": "Cerebras", "base": "https://api.cerebras.ai/v1", "key": "", "free": True,
            "tiers": [
                {"name": "Lite", "model": "llama3.1-8b", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 30, "rpd": 14400},
                {"name": "Pro", "model": "gpt-oss-120b", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 30, "rpd": 14400},
            ],
        },
        "mistral": {
            "label": "Mistral", "base": "https://api.mistral.ai/v1", "key": "", "free": True,
            "tiers": [
                {"name": "Lite", "model": "mistral-small-latest", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 60, "rpd": 0},
                {"name": "Standard", "model": "codestral-latest", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 60, "rpd": 0},
                {"name": "Pro", "model": "mistral-large-latest", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 60, "rpd": 0},
            ],
        },
        "nvidia": {
            "label": "NVIDIA NIM", "base": "https://integrate.api.nvidia.com/v1", "key": "", "free": True,
            "tiers": [
                {"name": "Lite", "model": "meta/llama-3.3-70b-instruct", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 30, "rpd": 0},
                {"name": "Pro", "model": "nvidia/llama-3.1-nemotron-ultra-253b-v1", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 30, "rpd": 0},
            ],
        },
    },
}

_lock = threading.RLock()


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def has_access(p):
    """A provider is usable when it has a key, or it needs no key and the user enabled it."""
    return bool(p.get("key")) or bool(p.get("nokey") and p.get("enabled"))


def auth_key(p):
    return p.get("key") or "none"


def load_catalog():
    """catalog.json is re-read on every config load, so adding providers there needs no restart."""
    cat = _read_json(os.path.join(ROOT, "catalog.json"), {})
    out = {}
    for pid, v in (cat.get("providers") or {}).items():
        if not isinstance(v, dict) or not v.get("base"):
            continue
        tiers = []
        for t in (v.get("tiers") or [{"name": "Lite", "model": ""}]):
            tiers.append({"name": t.get("name", "Lite"), "model": t.get("model", ""), "in": float(t.get("in", 0)), "cache": float(t.get("cache", 0)),
                          "out": float(t.get("out", 0)), "rpm": int(t.get("rpm", 0)), "rpd": int(t.get("rpd", 0))})
        d = {"label": v.get("label", pid), "base": v["base"], "key": "", "free": bool(v.get("free", True)), "tiers": tiers,
             "group": v.get("group", "More providers"), "nokey": bool(v.get("nokey")), "enabled": False}
        if v.get("info"):
            d["info"] = v["info"]
        out[pid] = d
    return out


def load_cfg():
    with _lock:
        cat = load_catalog()
        cfg = _merge(_merge(DEFAULT_CONFIG, {"providers": cat}), _read_json(CFG_PATH, {}))
        for pid, cv in cat.items():                      # descriptive fields always follow the catalog
            for k in ("info", "group", "nokey"):
                if k in cv:
                    cfg["providers"][pid][k] = cv[k]
    os.makedirs(cfg["workspace"], exist_ok=True)
    return cfg


def save_cfg(cfg):
    with _lock:
        _write_json(CFG_PATH, cfg)


def masked_cfg(cfg):
    c = copy.deepcopy(cfg)
    for p in c["providers"].values():
        k = p.get("key", "")
        p["has_key"] = has_access(p)
        p["key"] = ("****" + k[-4:]) if len(k) >= 4 else ("****" if k else "")
    return c


NUM_KEYS = {"start_tier", "max_tier", "esc_errors", "esc_steps", "max_steps", "daily_budget", "total_budget"}


def apply_config_update(new):
    with _lock:
        return _apply_config_update(new)


def _apply_config_update(new):
    cfg = load_cfg()
    for k in ("provider", "workspace", "approval"):
        if isinstance(new.get(k), str) and new[k].strip():
            cfg[k] = new[k].strip()
    for k in ("auto_escalate", "sticky_tier", "failover", "failover_paid"):
        if k in new:
            cfg[k] = bool(new[k])
    for k in NUM_KEYS:
        if k in new:
            try:
                cfg[k] = float(new[k]) if "budget" in k else int(new[k])
            except Exception:
                pass
    for pid, pv in (new.get("providers") or {}).items():
        if pid not in cfg["providers"]:
            continue
        cur = cfg["providers"][pid]
        if "free" in pv:
            cur["free"] = bool(pv["free"])
        if isinstance(pv.get("base"), str) and pv["base"].strip():
            cur["base"] = pv["base"].strip().rstrip("/")
        key = pv.get("key")
        if isinstance(key, str) and not key.startswith("****"):
            cur["key"] = key.strip()
        if isinstance(pv.get("tiers"), list) and pv["tiers"]:
            tiers = []
            for t in pv["tiers"][:6]:
                try:
                    tiers.append({
                        "name": str(t.get("name", "Tier"))[:20],
                        "model": str(t.get("model", "")).strip(),
                        "in": float(t.get("in", 0)), "cache": float(t.get("cache", 0)),
                        "out": float(t.get("out", 0)),
                        "rpm": int(t.get("rpm", 0)), "rpd": int(t.get("rpd", 0)),
                    })
                except Exception:
                    pass
            if tiers:
                cur["tiers"] = tiers
    cfg["start_tier"] = max(0, cfg["start_tier"])
    cfg["max_tier"] = max(0, cfg["max_tier"])
    save_cfg(cfg)
    return cfg


# --------------------------------------------------------------------------- usage / limits
usage = _read_json(USAGE_PATH, {"days": {}})
usage.setdefault("days", {})
LAST_PING = time.time()
recent = collections.defaultdict(collections.deque)   # "prov:model" -> request timestamps (for RPM)


def today():
    return datetime.date.today().isoformat()


def day_rec(day):
    return usage["days"].setdefault(day, {"requests": 0, "in": 0, "out": 0, "cost": 0.0, "saved": 0.0, "models": {}})


def total_cost():
    return sum(d.get("cost", 0.0) for d in usage["days"].values())


def total_saved():
    return sum(d.get("saved", 0.0) for d in usage["days"].values())


def price(t, miss, hit, out):
    cache = t.get("cache") or t.get("in", 0)
    return (miss * t.get("in", 0) + hit * cache + out * t.get("out", 0)) / 1e6


def record_usage(pid, tiers, tier_idx, miss, hit, out, free=False):
    t = tiers[tier_idx]
    cost = 0.0 if free else price(t, miss, hit, out)
    top_cost = 0.0 if free else price(tiers[-1], miss, hit, out)
    with _lock:
        d = day_rec(today())
        d["requests"] += 1
        d["in"] += miss + hit
        d["out"] += out
        d["cost"] += cost
        d["saved"] += max(0.0, top_cost - cost)
        m = d["models"].setdefault(pid + ":" + t["model"], {"requests": 0, "in": 0, "out": 0, "cost": 0.0})
        m["requests"] += 1
        m["in"] += miss + hit
        m["out"] += out
        m["cost"] += cost
        for k in list(usage["days"].keys()):   # keep 90 days
            if len(usage["days"]) > 90:
                del usage["days"][sorted(usage["days"])[0]]
        _write_json(USAGE_PATH, usage)
    return cost


def usage_snapshot(cfg):
    d = day_rec(today())
    return {
        "today": d, "total_cost": total_cost(), "total_saved": total_saved(),
        "daily_budget": cfg["daily_budget"], "total_budget": cfg["total_budget"],
        "rpm_now": {k: len([x for x in v if x > time.time() - 60]) for k, v in recent.items()},
    }


def budget_state(cfg):
    d = day_rec(today())["cost"]
    t = total_cost()
    if cfg["daily_budget"] > 0 and d >= cfg["daily_budget"]:
        return "daily"
    if cfg["total_budget"] > 0 and t >= cfg["total_budget"]:
        return "total"
    return "ok"


# --------------------------------------------------------------------------- LLM client (OpenAI-compatible)
def friendly(msg):
    """Explain provider errors that are not the app's fault."""
    if re.search(r"free tier is not available|project-unavailable", msg or "", re.I):
        return (msg + "\n\nThis comes from Google, not from Aqua Code: your Google Cloud project is not eligible for the Gemini "
                "free tier. Check your key's status at aistudio.google.com/apikey (Restricted, Verification needed...), finish any "
                "verification, try a key from a personal Google account, or switch to another provider (OpenRouter has free models that need no card).")
    return msg


class LLMError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


def http_json(url, key, payload=None, timeout=240, method="POST"):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key,
        "User-Agent": "aqua-code/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        try:
            j = json.loads(body)
            if isinstance(j, list) and j:
                j = j[0]
            body = (j.get("error") or {}).get("message") or body
        except Exception:
            pass
        raise LLMError(e.code, body[:600] or str(e))
    except urllib.error.URLError as e:
        raise LLMError(0, "Network error: %s" % e.reason)
    except Exception as e:
        raise LLMError(0, "Request failed: %s" % e)


def clean_messages(messages, model):
    """Strip internal keys; keep reasoning_content only for DeepSeek reasoner inside the current turn."""
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
    out = []
    for i, m in enumerate(messages):
        c = {k: v for k, v in m.items() if not k.startswith("_")}
        if c["role"] == "assistant":
            if c.get("content") is None:
                c["content"] = ""
            if not c.get("tool_calls"):
                c.pop("tool_calls", None)
            keep = "reasoner" in model and i > last_user
            if not keep:
                c.pop("reasoning_content", None)
        out.append(c)
    return out


# --------------------------------------------------------------------------- tools
TOOLS = [
    {"type": "function", "function": {"name": "list_dir", "description": "List files and folders in the workspace.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Relative folder, default '.'"},
                                                      "depth": {"type": "integer", "description": "1-3"}}, "required": []}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file with line numbers.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"},
                                                      "end_line": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a file with full content.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Replace one exact, unique snippet in a file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"},
                                                      "new": {"type": "string"}}, "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {"name": "search", "description": "Regex search across workspace text files.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                    "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run a PowerShell command in the workspace (build, test, run scripts).",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
                    "required": ["command"]}}},
    {"type": "function", "function": {"name": "start_server", "description": "Start a long-running dev server in the background (e.g. python -m http.server 8000, npm run dev) and show it in the Preview pane. Use run_command for anything that finishes.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "port": {"type": "integer", "description": "Port the server listens on, if known"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "request_stronger_model", "description": "Hand the task to a stronger (more expensive) model when you are stuck or the task is too hard.",
     "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
]
WRITE_TOOLS = {"write_file", "edit_file"}
DENY = re.compile(r"(?i)(format-volume|diskpart|bcdedit|shutdown|restart-computer|stop-computer|set-executionpolicy|"
                  r"\breg(\.exe)?\s+(add|delete)|remove-item\s+[^|;]*-recurse[^|;]*\s[a-z]:\\?\s*($|[;|])|"
                  r"\brmdir\s+/s\s+[a-z]:\\?\s*$|\bdel\s+/[sfq]+\s+[a-z]:\\|"
                  r"\brm\s+-[a-z]*[rf][a-z]*\s+(/|~|\*)(\s|$)|\bmkfs\b|\bdd\s+if=|:\(\)\s*\{)")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vs"}


class ToolErr(Exception):
    pass


def safe_path(ws, rel):
    rel = (rel or ".").strip().strip('"')
    p = os.path.realpath(os.path.join(ws, rel))
    w = os.path.realpath(ws)
    if p != w and not p.startswith(w + os.sep):
        raise ToolErr("Path is outside the workspace: %s" % rel)
    return p


def rel_of(ws, p):
    return os.path.relpath(p, ws).replace("\\", "/")


def walk_tree(ws, base, depth, cap=400):
    out = []

    def rec(d, lvl):
        try:
            names = sorted(os.listdir(d), key=lambda n: (not os.path.isdir(os.path.join(d, n)), n.lower()))
        except Exception:
            return
        for n in names:
            if len(out) >= cap:
                return
            full = os.path.join(d, n)
            isdir = os.path.isdir(full)
            if isdir and n in SKIP_DIRS:
                continue
            out.append({"path": rel_of(ws, full), "dir": isdir, "level": lvl,
                        "size": 0 if isdir else os.path.getsize(full)})
            if isdir and lvl < depth:
                rec(full, lvl + 1)

    rec(base, 0)
    return out


def check_syntax(path, content):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".py":
            compile(content, path, "exec")
        elif ext == ".json":
            json.loads(content)
    except SyntaxError as e:
        return "Syntax error line %s: %s" % (e.lineno, e.msg)
    except Exception as e:
        return "Invalid %s: %s" % (ext[1:], e)
    return None


def tool_list_dir(ws, a):
    base = safe_path(ws, a.get("path") or ".")
    depth = max(1, min(3, int(a.get("depth") or 1)))
    items = walk_tree(ws, base, depth - 1, 200)
    if not items:
        return "(empty)"
    return "\n".join("  " * i["level"] + os.path.basename(i["path"]) + ("/" if i["dir"] else "") for i in items)


def tool_read_file(ws, a):
    p = safe_path(ws, a.get("path"))
    if not os.path.isfile(p):
        raise ToolErr("File not found: %s" % a.get("path"))
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    s = max(1, int(a.get("start_line") or 1))
    e = min(len(lines), int(a.get("end_line") or s + 399))
    body = "\n".join("%d\t%s" % (i + 1, lines[i]) for i in range(s - 1, e))
    more = "\n... (%d more lines)" % (len(lines) - e) if e < len(lines) else ""
    return body[:20000] + more


def tool_write_file(ws, a):
    p = safe_path(ws, a.get("path"))
    content = a.get("content")
    if not isinstance(content, str):
        raise ToolErr("content must be a string")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    msg = "Wrote %s (%d lines)." % (rel_of(ws, p), content.count("\n") + 1)
    bad = check_syntax(p, content)
    if bad:
        raise ToolErr(msg + " BUT " + bad)
    return msg


def tool_edit_file(ws, a):
    p = safe_path(ws, a.get("path"))
    if not os.path.isfile(p):
        raise ToolErr("File not found: %s" % a.get("path"))
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        txt = f.read()
    old, new = a.get("old"), a.get("new")
    if not isinstance(old, str) or not old or not isinstance(new, str):
        raise ToolErr("old and new must be strings, old non-empty")
    n = txt.count(old)
    if n == 0:
        raise ToolErr("Snippet not found. Re-read the file and copy the exact text.")
    if n > 1:
        raise ToolErr("Snippet appears %d times. Include more surrounding lines to make it unique." % n)
    txt = txt.replace(old, new)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    msg = "Edited %s." % rel_of(ws, p)
    bad = check_syntax(p, txt)
    if bad:
        raise ToolErr(msg + " BUT " + bad)
    return msg


def tool_search(ws, a):
    try:
        rx = re.compile(a.get("pattern") or "", re.I)
    except re.error as e:
        raise ToolErr("Bad regex: %s" % e)
    base = safe_path(ws, a.get("path") or ".")
    hits = []
    def iter_files():
        if os.path.isfile(base):
            yield base
            return
        for r, ds, ns in os.walk(base):
            ds[:] = [d for d in ds if d not in SKIP_DIRS]
            for n in ns:
                yield os.path.join(r, n)

    for fp in iter_files():
        try:
            if os.path.getsize(fp) > 1_500_000:
                continue
            with open(fp, "r", encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if rx.search(line):
                        hits.append("%s:%d: %s" % (rel_of(ws, fp), i, line.strip()[:160]))
                        if len(hits) >= 60:
                            return "\n".join(hits) + "\n... (truncated)"
        except Exception:
            continue
    return "\n".join(hits) or "No matches."


def tool_run_command(ws, a):
    cmd = a.get("command") or ""
    if not cmd.strip():
        raise ToolErr("Empty command")
    if DENY.search(cmd):
        raise ToolErr("Blocked: this command is not allowed for safety.")
    timeout = max(5, min(300, int(a.get("timeout") or 60)))
    argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd] if os.name == "nt" else ["bash", "-lc", cmd]
    try:
        r = subprocess.run(argv, cwd=ws, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise ToolErr("Command timed out after %ds." % timeout)
    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
    out = out.strip()[-8000:] or "(no output)"
    if r.returncode != 0:
        raise ToolErr("Exit code %d\n%s" % (r.returncode, out))
    return "Exit code 0\n" + out


TOOL_FUNCS = {"start_server": lambda ws, a: None, "list_dir": tool_list_dir, "read_file": tool_read_file, "write_file": tool_write_file,
              "edit_file": tool_edit_file, "search": tool_search, "run_command": tool_run_command}


# --------------------------------------------------------------------------- runs & sessions
sessions = _read_json(SESS_PATH, {})
runs = {}


def save_sessions():
    with _lock:
        _write_json(SESS_PATH, sessions)


def clip_event(ev):
    c = dict(ev)
    for k, v in list(c.items()):
        if isinstance(v, str) and len(v) > 6000:
            c[k] = v[:6000] + "\n... (clipped)"
        elif isinstance(v, dict):
            c[k] = {kk: (vv[:3000] + "...") if isinstance(vv, str) and len(vv) > 3000 else vv for kk, vv in v.items()}
    return c


class Run:
    def __init__(self, sess):
        self.id = uuid.uuid4().hex[:12]
        self.sess = sess
        self.events = []
        self.cond = threading.Condition()
        self.done = False
        self.stop = False
        self.force_escalate = False
        self.pending = {}

    def emit(self, type_, persist=True, **kw):
        ev = dict(type=type_, t=time.time(), **kw)
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()
        if persist:
            self.sess["events"].append(clip_event(ev))
            if len(self.sess["events"]) > 2500:
                del self.sess["events"][:300]


def system_prompt(ws):
    return ("You are Aqua Code, an autonomous coding agent working inside the user's workspace folder on %s. "
            "Workspace: %s\n"
            "Work like a careful senior engineer: look before you edit, prefer small exact edit_file changes over "
            "rewriting files, and verify by running the code or tests with run_command when that makes sense. "
            "All paths are relative to the workspace. For a plain web page just write index.html in the workspace: it is shown live in the user's Preview pane and reloads when you edit it. For frameworks use start_server. Keep prose short. If the task is beyond you or you keep "
            "failing, call request_stronger_model with a reason instead of guessing. When the work is complete, "
            "answer with a brief summary and no tool call." % ("Windows (PowerShell)" if os.name == "nt" else "Linux/macOS (bash)", ws))


def new_session(title="New chat"):
    sid = uuid.uuid4().hex[:10]
    sessions[sid] = {"id": sid, "title": title, "created": time.time(), "messages": [], "events": [],
                     "cost": 0.0, "tier": None}
    return sessions[sid]


def wait_limits(run, cfg, pid, tiers, ti):
    """Honour RPM (sleep) and RPD (signal) before each request. Returns 'ok' or 'daily'."""
    t = tiers[ti]
    key = pid + ":" + t["model"]
    if t.get("rpd", 0) > 0:
        n = day_rec(today())["models"].get(key, {}).get("requests", 0)
        if n >= t["rpd"]:
            return "daily"
    if t.get("rpm", 0) > 0:
        dq = recent[key]
        while True:
            now = time.time()
            while dq and dq[0] < now - 60:
                dq.popleft()
            if len(dq) < t["rpm"]:
                break
            wait = int(dq[0] + 60 - now) + 1
            run.emit("status", persist=False, text="Rate limit for %s: waiting %ds" % (t["model"], wait))
            for _ in range(wait * 2):
                if run.stop:
                    return "ok"
                time.sleep(0.5)
    recent[key].append(time.time())
    return "ok"


def call_llm(run, cfg, pid, tiers, ti, messages):
    prov = cfg["providers"][pid]
    if not has_access(prov):
        raise LLMError(401, "No API key for %s. Open Settings and paste your key." % prov["label"])
    if "{" in prov["base"]:
        raise LLMError(400, "The endpoint for %s still contains a placeholder like {account_id}. Open Settings, Provider and models, and fill it in." % prov["label"])
    t = tiers[ti]
    if not t["model"]:
        raise LLMError(400, "No model name is set for the %s tier. Open Settings, Provider and models, and pick one." % t["name"])
    payload = {"model": t["model"], "messages": [{"role": "system", "content": system_prompt(cfg["workspace"])}] +
               clean_messages(messages, t["model"]), "tools": TOOLS}
    url = prov["base"].rstrip("/") + "/chat/completions"
    delays = [3, 8, 20]
    for attempt in range(len(delays) + 1):
        try:
            return http_json(url, auth_key(prov), payload)
        except LLMError as e:
            per_day = e.status == 429 and re.search(r"per.?day|daily|limit:\s*0|free.?models?.*per.?day", e.msg, re.I)
            if e.status in (429, 500, 502, 503, 504) and attempt < len(delays) and not run.stop and not per_day:
                run.emit("status", persist=False, text="%s returned %s, retrying in %ds" % (t["model"], e.status, delays[attempt]))
                time.sleep(delays[attempt])
                continue
            raise


def exec_tool(run, cfg, name, args):
    """Returns (text, is_error, ui_extra)."""
    ws = cfg["workspace"]
    fn = TOOL_FUNCS.get(name)
    if not fn:
        return "Unknown tool: %s" % name, True, None
    needs = (name in ("run_command", "start_server") and cfg["approval"] in ("ask", "edits")) or \
            (name in WRITE_TOOLS and cfg["approval"] == "ask")
    if needs:
        aid = uuid.uuid4().hex[:8]
        ev = threading.Event()
        run.pending[aid] = {"ev": ev, "ok": False}
        run.emit("approval", id=aid, tool=name, args=args)
        while not ev.wait(0.5):
            if run.stop:
                return "Cancelled.", True, None
        ok = run.pending[aid]["ok"]
        run.emit("approval_result", id=aid, ok=ok)
        if not ok:
            return "The user denied this action. Propose another approach or ask what to do.", False, None
    try:
        if name == "start_server":
            return tool_start_server(run, ws, args), False, None
        out = fn(ws, args)
        if name in WRITE_TOOLS and PREVIEW_PORT and str(args.get("path", "")).lower().endswith((".html", ".htm")):
            run.emit("preview", url="http://127.0.0.1:%d/%s" % (PREVIEW_PORT, str(args["path"]).replace("\\", "/").lstrip("/")), auto=True)
        return out, False, None
    except ToolErr as e:
        return str(e), True, None
    except Exception as e:
        return "Tool crashed: %s" % e, True, None


def quota_left_for(pid, t):
    r = t.get("rpd", 0)
    return r <= 0 or day_rec(today())["models"].get(pid + ":" + t["model"], {}).get("requests", 0) < r


def is_limit_error(e):
    if e.status in (402, 429, 503):
        return True
    return e.status in (403, 404) and bool(re.search(
        r"quota|limit|billing|credit|balance|free tier|not found|no longer available|does not exist|decommission|deprecat|not available", e.msg or "", re.I))


def out_of_credit(e):
    return e.status == 402 or bool(re.search(r"insufficient (balance|credit|funds)|out of credit|credits? (are )?exhausted", e.msg or "", re.I))


def agent_loop(run, user_text, forced_tier):
    sess = run.sess
    cfg = load_cfg()
    pid = cfg["provider"]
    plabel = cfg["providers"][pid]["label"]
    tiers = cfg["providers"][pid]["tiers"]
    top = max(0, min(cfg["max_tier"], len(tiers) - 1))
    if forced_tier is not None:
        ti = max(0, min(int(forced_tier), len(tiers) - 1))
        top = max(top, ti)
    elif cfg["sticky_tier"] and sess.get("tier") is not None:
        ti = min(sess["tier"], top)
    else:
        ti = min(cfg["start_tier"], top)

    messages = sess["messages"]
    messages.append({"role": "user", "content": user_text})
    run.emit("user", text=user_text)
    steps_here = errs = 0
    sigs = []
    run_cost = 0.0
    steps = 0
    status = "done"

    free = bool(cfg["providers"][pid].get("free"))
    blocked = set()                       # (provider, tier) pairs that hit a limit during this run
    dead = set()                          # providers that ran out of credit or rejected the key during this run

    def quota_left(i):
        return quota_left_for(pid, tiers[i])

    def switch_to(j, reason):
        nonlocal ti, steps_here, errs
        up = j > ti
        run.emit("escalate", frm=ti, to=j, reason=reason, model=tiers[j]["model"], name=tiers[j]["name"], up=up)
        messages.append({"role": "user", "content": "[aqua note] Switched from %s to %s (%s). Review the work so far, "
                         "fix what failed, and continue the task." % (tiers[ti]["name"], tiers[j]["name"], reason)})
        ti = j
        steps_here = errs = 0

    def escalate(reason):
        if ti >= top or (pid, ti + 1) in blocked:
            return False
        switch_to(ti + 1, reason)
        return True

    def fallback(reason):
        """The current model hit a limit: move to the next model that still has quota (higher first, then lower)."""
        blocked.add((pid, ti))
        order = list(range(ti + 1, len(tiers))) + list(range(ti - 1, -1, -1))
        for j in order:
            if (pid, j) not in blocked and quota_left(j):
                switch_to(j, reason)
                return True
        return False

    def next_provider(reason):
        """This AI is out of credit / limited / rejected: move to the next connected AI (free ones only unless failover_paid)."""
        nonlocal pid, plabel, tiers, top, free, ti, steps_here, errs
        if not cfg.get("failover", True):
            return False
        dead.add(pid)
        for k, v in cfg["providers"].items():
            if k in dead or not has_access(v):
                continue
            if not v.get("free") and not cfg.get("failover_paid", False):
                continue
            tt = v["tiers"]
            ok = [i for i, t in enumerate(tt) if t.get("model") and (k, i) not in blocked and quota_left_for(k, t)]
            if not ok:
                continue
            old = plabel
            pid, plabel, tiers, free = k, v["label"], tt, bool(v.get("free"))
            top = max(0, min(cfg["max_tier"], len(tt) - 1))
            ti = min(ok[0], top)
            steps_here = errs = 0
            run.emit("failover", frm=old, to=plabel, reason=reason, model=tiers[ti]["model"], name=tiers[ti]["name"])
            messages.append({"role": "user", "content": "[aqua note] Switched AI from %s to %s (%s). Review the work so far, "
                             "fix what failed, and continue the task." % (old, plabel, reason)})
            return True
        return False

    try:
        while steps < cfg["max_steps"] and not run.stop:
            if run.force_escalate:
                run.force_escalate = False
                escalate("manual request")
            bs = budget_state(cfg)
            if bs != "ok" and not free:
                if next_provider("your %s money budget is used up" % ("daily" if bs == "daily" else "total")):
                    continue
                run.emit("error", text="%s budget reached. Raise it in Settings, or connect a free provider as a backup." % ("Daily" if bs == "daily" else "Total"))
                status = "budget"
                break
            lim = wait_limits(run, cfg, pid, tiers, ti)
            if lim == "daily":
                why = "daily request limit of %s reached" % tiers[ti]["model"]
                if fallback(why) or next_provider(why):
                    continue
                run.emit("error", text="Every connected AI has used up its limit for now. Free limits reset at midnight (Gemini: Pacific time, OpenRouter: UTC). Connect another free provider in the AI menu to keep going.")
                status = "limit"
                break
            run.emit("model", tier=ti, name=tiers[ti]["name"], model=tiers[ti]["model"], tiers=len(tiers), provider=plabel)
            run.emit("status", persist=False, text="%s - %s is working" % (plabel, tiers[ti]["model"]))
            try:
                resp = call_llm(run, cfg, pid, tiers, ti, messages)
            except LLMError as e:
                if e.status == 401 and next_provider("%s rejected the key" % plabel):
                    continue
                if is_limit_error(e):
                    m = tiers[ti]["model"]
                    if out_of_credit(e):
                        if next_provider("%s is out of credit" % plabel):
                            continue
                    else:
                        why = ("rate limited on %s" if e.status == 429 else "%s is overloaded" if e.status == 503 else "%s is not available") % m
                        if fallback(why) or next_provider(why):
                            continue
                    e.msg += " (no other connected AI could take over. Add another free provider in the AI menu, or wait for the limit to reset.)"
                run.emit("error", text="%s: %s" % (tiers[ti]["model"], friendly(e.msg)))
                status = "error"
                break
            steps += 1
            steps_here += 1
            u = resp.get("usage") or {}
            pin = int(u.get("prompt_tokens") or 0)
            hit = int(u.get("prompt_cache_hit_tokens") or (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
            pout = int(u.get("completion_tokens") or 0)
            cost = record_usage(pid, tiers, ti, max(0, pin - hit), hit, pout, free)
            run_cost += cost
            sess["cost"] = sess.get("cost", 0.0) + cost
            run.emit("usage", persist=False, cost=cost, run_cost=run_cost, tokens_in=pin, tokens_out=pout,
                     usage=usage_snapshot(cfg), session_cost=sess["cost"])
            try:
                msg = resp["choices"][0]["message"]
            except Exception:
                run.emit("error", text="Unexpected response from provider: %s" % json.dumps(resp)[:300])
                status = "error"
                break
            calls = msg.get("tool_calls") or []
            am = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("reasoning_content"):
                am["reasoning_content"] = msg["reasoning_content"]
            if calls:
                for c in calls:
                    if not c.get("id"):
                        c["id"] = "call_" + uuid.uuid4().hex[:10]
                am["tool_calls"] = calls
            messages.append(am)
            if am["content"].strip():
                run.emit("text", text=am["content"], tier=ti, model=tiers[ti]["model"], name=tiers[ti]["name"], cost=cost, provider=plabel)
            if not calls:
                break

            step_err = False
            want_stronger = None
            for c in calls:
                fn = c.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be an object")
                except Exception as e:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": "Invalid JSON arguments: %s" % e})
                    run.emit("tool_call", id=c["id"], tool=name, args={})
                    run.emit("tool_result", id=c["id"], ok=False, output="Invalid JSON arguments: %s" % e)
                    step_err = True
                    continue
                run.emit("tool_call", id=c["id"], tool=name, args=args)
                if run.stop:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": "Cancelled by user."})
                    run.emit("tool_result", id=c["id"], ok=False, output="Cancelled.")
                    continue
                if name == "request_stronger_model":
                    want_stronger = str(args.get("reason") or "model asked for help")
                    out, err = "Acknowledged. A stronger model will take over.", False
                else:
                    out, err, _ = exec_tool(run, cfg, name, args)
                sig = name + json.dumps(args, sort_keys=True)[:400]
                sigs.append(sig)
                if sigs[-6:].count(sig) >= 3 and name != "run_command":
                    out += "\n[aqua] You repeated this exact call 3 times. Change approach."
                    err = True
                if err:
                    step_err = True
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": out[:12000]})
                run.emit("tool_result", id=c["id"], ok=not err, output=out)
            errs = errs + 1 if step_err else 0
            if run.stop:
                break
            if want_stronger:
                if not escalate("model asked for help: " + want_stronger):
                    messages.append({"role": "user", "content": "[aqua note] Already on the strongest allowed tier. Keep going."})
            elif cfg["auto_escalate"]:
                if errs >= cfg["esc_errors"]:
                    escalate("%d failing steps in a row" % errs)
                elif steps_here >= cfg["esc_steps"]:
                    escalate("%d steps without finishing" % steps_here)
        else:
            if not run.stop:
                run.emit("error", text="Stopped after %d steps (max_steps)." % cfg["max_steps"])
                status = "max_steps"
    except Exception as e:
        run.emit("error", text="Agent crashed: %s" % e)
        status = "error"
        traceback.print_exc()
    if run.stop:
        status = "stopped"
        # keep tool-call/response pairs valid after a stop
        pend = {tc["id"] for m in messages if m["role"] == "assistant" for tc in (m.get("tool_calls") or [])}
        done_ids = {m.get("tool_call_id") for m in messages if m["role"] == "tool"}
        for i in pend - done_ids:
            messages.append({"role": "tool", "tool_call_id": i, "content": "Cancelled by user."})
    sess["tier"] = ti
    run.emit("done", status=status, tier=ti, run_cost=run_cost, steps=steps, session_cost=sess.get("cost", 0.0),
             tier_name=tiers[ti]["name"], model=tiers[ti]["model"], provider=plabel, usage=usage_snapshot(cfg))
    run.done = True
    with run.cond:
        run.cond.notify_all()
    save_sessions()


def start_run(sid, text, tier):
    with _lock:
        sess = sessions.get(sid) if sid else None
        if sess is None:
            sess = new_session()
        for r in runs.values():
            if r.sess["id"] == sess["id"] and not r.done:
                return None, "This chat already has a running task."
        if not sess["messages"]:
            sess["title"] = (text.strip().split("\n")[0])[:48] or "New chat"
        run = Run(sess)
        runs[run.id] = run
    threading.Thread(target=agent_loop, args=(run, text, tier), daemon=True).start()
    return run, None


# --------------------------------------------------------------------------- HTTP
def _norm_id(i):
    return i[7:] if i.startswith("models/") else i


def list_models(pid):
    cfg = load_cfg()
    prov = cfg["providers"].get(pid)
    if not prov:
        return {"ok": False, "error": "Unknown provider."}
    if not has_access(prov):
        return {"ok": False, "error": "No API key saved for %s." % prov["label"]}
    try:
        j = http_json(prov["base"].rstrip("/") + "/models", auth_key(prov), method="GET", timeout=25)
    except LLMError as e:
        return {"ok": False, "error": friendly(e.msg)}
    rows = j.get("data") if isinstance(j, dict) else j
    ids = sorted({_norm_id(m["id"]) for m in (rows or []) if isinstance(m, dict) and m.get("id")})
    return {"ok": True, "ids": ids}


def _tokens(s):
    return {t for t in re.split(r"[-_/:.\s]+", s.lower()) if t and t not in ("latest", "free", "instruct", "it", "preview")}


def closest_model(old, ids):
    a = _tokens(old)
    bad = {"embed", "embedding", "tts", "audio", "image", "vision", "live", "transcribe", "guard", "moderation", "rerank"}
    best, score = None, 0.0
    for i in ids:
        b = _tokens(i)
        if (b & bad) - a or not a or not b:
            continue
        sc = len(a & b) / len(a | b)
        if sc > score:
            best, score = i, sc
    return best if score >= 0.5 else None


def models_sync(pid):
    """After a key is saved: make sure every tier's model really exists at the provider; swap in the closest match if not."""
    r = list_models(pid)
    if not r["ok"]:
        return r
    ids = set(r["ids"])
    with _lock:
        cfg = load_cfg()
        replaced, missing = [], []
        for t in cfg["providers"][pid]["tiers"]:
            if not t["model"] or t["model"] in ids:
                continue
            alt = closest_model(t["model"], r["ids"])
            if alt:
                replaced.append([t["model"], alt])
                t["model"] = alt
            else:
                missing.append(t["model"])
        if replaced:
            save_cfg(cfg)
    return {"ok": True, "replaced": replaced, "missing": missing, "count": len(ids), "config": masked_cfg(load_cfg())}


def add_custom_provider(label, base):
    label = str(label or "").strip()[:24]
    base = str(base or "").strip().rstrip("/")
    if not label or not base.startswith("http"):
        raise ValueError("Give it a name and an endpoint that starts with https://")
    with _lock:
        cfg = load_cfg()
        pid = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:24] or "custom"
        base_id, n = pid, 2
        while pid in cfg["providers"]:
            pid = "%s-%d" % (base_id, n)
            n += 1
        cfg["providers"][pid] = {"label": label, "base": base, "key": "", "free": True, "custom": True,
                                 "tiers": [{"name": "Lite", "model": "", "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 0, "rpd": 0}]}
        save_cfg(cfg)
    return pid


def set_provider_enabled(pid, on):
    with _lock:
        cfg = load_cfg()
        p = cfg["providers"].get(pid)
        if not p or not p.get("nokey"):
            raise ValueError("This provider needs a key.")
        p["enabled"] = bool(on)
        save_cfg(cfg)
    sync = models_sync(pid) if on else {}
    return {"ok": True, "sync": {k: v for k, v in sync.items() if k != "config"}, "config": masked_cfg(load_cfg())}


def remove_custom_provider(pid):
    with _lock:
        cfg = load_cfg()
        if not cfg["providers"].get(pid, {}).get("custom"):
            raise ValueError("Only custom providers can be removed.")
        del cfg["providers"][pid]
        if cfg["provider"] == pid:
            cfg["provider"] = "gemini"
        save_cfg(cfg)


def openrouter_autoconfig():
    """Ask OpenRouter's public model list which free models can use tools, and put two of them in the ladder."""
    req = urllib.request.Request("https://openrouter.ai/api/v1/models", headers={"User-Agent": "aqua-code/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read().decode("utf-8")).get("data", [])
    ids = [m["id"] for m in data if str(m.get("id", "")).endswith(":free") and "tools" in (m.get("supported_parameters") or [])]
    if not ids:
        return {"ok": False, "error": "OpenRouter lists no free model with tool support right now."}

    def pick(prefs, exclude):
        for p in prefs:
            for i in ids:
                if p in i and i not in exclude:
                    return i
        return next((i for i in ids if i not in exclude), None)

    lite = pick(["lightning", "gemma-4-26b", "flash", "nano"], set())
    pro = pick(["ultra", "super", "gemma-4-31b", "pro"], {lite}) or lite
    with _lock:
        cfg = load_cfg()
        tiers = [{"name": "Lite", "model": lite, "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 20, "rpd": 50}]
        if pro != lite:
            tiers.append({"name": "Pro", "model": pro, "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 20, "rpd": 50})
        cfg["providers"]["openrouter"]["tiers"] = tiers
        cfg["providers"]["openrouter"]["free"] = True
        save_cfg(cfg)
    return {"ok": True, "models": ids, "chosen": [lite, pro], "config": masked_cfg(cfg)}


def test_connection(pid, tier_idx):
    cfg = load_cfg()
    prov = cfg["providers"][pid]
    tiers = prov["tiers"]
    tier_idx = max(0, min(tier_idx, len(tiers) - 1))
    if not has_access(prov):
        return {"ok": False, "error": "No API key saved for %s." % prov["label"]}
    t0 = time.time()
    try:
        r = http_json(prov["base"].rstrip("/") + "/chat/completions", auth_key(prov), {
            "model": tiers[tier_idx]["model"], "max_tokens": 8,
            "messages": [{"role": "user", "content": "Reply with: ok"}]}, timeout=90)
        return {"ok": True, "ms": int((time.time() - t0) * 1000), "model": tiers[tier_idx]["model"],
                "reply": (r["choices"][0]["message"].get("content") or "").strip()[:40]}
    except LLMError as e:
        return {"ok": False, "error": "%s (HTTP %s)" % (friendly(e.msg), e.status)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fetch_balance(pid):
    cfg = load_cfg()
    prov = cfg["providers"][pid]
    if pid != "deepseek":
        return {"ok": False, "error": "%s has no balance API. Use the budget meters (set your own limit in Settings)." % prov["label"]}
    if not prov.get("key"):
        return {"ok": False, "error": "No API key saved."}
    try:
        return {"ok": True, "data": http_json(prov["base"].rstrip("/") + "/user/balance", prov["key"], method="GET", timeout=20)}
    except LLMError as e:
        return {"ok": False, "error": e.msg}



# --------------------------------------------------------------------------- preview: workspace server + dev servers
import atexit
import mimetypes
from urllib.parse import unquote

PREVIEW_PORT = None
SERVERS = {}          # id -> {"cmd", "proc", "url", "log", "started"}
URL_RX = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):(\d+)[^\s'\"<>]*", re.I)

RELOAD_JS = ("<script>(function(){var v=null;setInterval(function(){fetch('/__aqua/ver',{cache:'no-store'}).then(function(r){return r.json()})"
             ".then(function(j){if(v===null)v=j.v;else if(v!==j.v)location.reload()}).catch(function(){})},1000)})()</script>")


def workspace_version(ws):
    n, newest = 0, 0.0
    for r, ds, fs in os.walk(ws):
        ds[:] = [d for d in ds if d not in SKIP_DIRS]
        for f in fs:
            n += 1
            if n > 3000:
                return "%d-%d" % (n, newest)
            try:
                newest = max(newest, os.path.getmtime(os.path.join(r, f)))
            except OSError:
                pass
    return "%d-%d" % (n, newest * 1000)


class PreviewHandler(BaseHTTPRequestHandler):
    """Serves the workspace folder on its own port (its own origin, so previewed pages cannot reach the Aqua API)."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            u = urlparse(self.path)
            ws = load_cfg()["workspace"]
            if u.path == "/__aqua/ver":
                return self._send(200, json.dumps({"v": workspace_version(ws)}), "application/json")
            try:
                p = safe_path(ws, unquote(u.path).lstrip("/") or ".")
            except ToolErr:
                return self._send(403, "Forbidden", "text/plain")
            if os.path.isdir(p):
                idx = os.path.join(p, "index.html")
                if os.path.isfile(idx):
                    p = idx
                else:
                    items = sorted(os.listdir(p), key=str.lower)
                    rows = "".join('<li><a href="%s">%s</a></li>' % (
                        (u.path.rstrip("/") + "/" + n).replace("//", "/"), n + ("/" if os.path.isdir(os.path.join(p, n)) else "")) for n in items)
                    page = ("<!doctype html><meta charset=utf-8><title>Workspace</title><body style='font:15px system-ui;padding:24px;background:#111;color:#ddd'>"
                            "<h3>Workspace</h3><p style='color:#888'>No index.html yet. Ask the agent to create one.</p><ul>%s</ul>" % rows) + RELOAD_JS
                    return self._send(200, page, "text/html; charset=utf-8")
            if not os.path.isfile(p):
                return self._send(404, "<!doctype html><body style='font:15px system-ui;padding:24px;background:#111;color:#ddd'>404: not found</body>" + RELOAD_JS, "text/html; charset=utf-8")
            ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
            with open(p, "rb") as f:
                data = f.read()
            if ctype == "text/html":
                txt = data.decode("utf-8", "replace")
                txt = txt.replace("</body>", RELOAD_JS + "</body>", 1) if "</body>" in txt else txt + RELOAD_JS
                return self._send(200, txt, "text/html; charset=utf-8")
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            return self._send(200, data, ctype)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._send(500, "error: %s" % e, "text/plain")
            except Exception:
                pass


def start_preview_server():
    global PREVIEW_PORT
    if PREVIEW_PORT:
        return PREVIEW_PORT
    for p in range(PORT + 1, PORT + 12):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), PreviewHandler)
        except OSError:
            continue
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        PREVIEW_PORT = p
        return p
    return None


def _port_open(p):
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", int(p))) == 0


def _kill(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            proc.terminate()
    except Exception:
        pass


def stop_server(sid):
    s = SERVERS.pop(sid, None)
    if s:
        _kill(s["proc"])
    return bool(s)


def stop_all_servers():
    for sid in list(SERVERS):
        stop_server(sid)


atexit.register(stop_all_servers)


def servers_list():
    out = []
    for sid, s in list(SERVERS.items()):
        alive = s["proc"].poll() is None
        if not alive:
            SERVERS.pop(sid, None)
            continue
        out.append({"id": sid, "cmd": s["cmd"], "url": s["url"], "log": "".join(list(s["log"])[-12:])})
    return out


def tool_start_server(run, ws, a):
    cmd = (a.get("command") or "").strip()
    if not cmd:
        raise ToolErr("Empty command")
    if DENY.search(cmd):
        raise ToolErr("Blocked: this command is not allowed for safety.")
    argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd] if os.name == "nt" else ["bash", "-lc", cmd]
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
    env = dict(os.environ, PYTHONUNBUFFERED="1", FORCE_COLOR="0", NO_COLOR="1")
    proc = subprocess.Popen(argv, cwd=ws, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env, **kw)
    log = collections.deque(maxlen=300)

    def pump():
        try:
            for line in proc.stdout:
                log.append(line)
        except Exception:
            pass

    threading.Thread(target=pump, daemon=True).start()
    want = int(a["port"]) if str(a.get("port") or "").isdigit() else None
    guess = [int(x) for x in re.findall(r"(?<![\d.])(\d{4,5})(?![\d.])", cmd) if 1024 <= int(x) <= 65535]
    url, t0 = None, time.time()
    while time.time() - t0 < 14 and not run.stop:
        if proc.poll() is not None:
            break
        m = URL_RX.search("".join(log))
        if m:
            url = "http://localhost:%s" % m.group(1)
            break
        hit = next((p for p in ([want] if want else []) + guess if _port_open(p)), None)
        if hit:
            url = "http://localhost:%d" % hit
            break
        time.sleep(0.3)
    out = "".join(list(log)[-25:]).strip()[-3000:]
    if proc.poll() is not None:
        raise ToolErr("The server exited right away (code %s).\n%s" % (proc.returncode, out or "(no output)"))
    sid = uuid.uuid4().hex[:8]
    SERVERS[sid] = {"cmd": cmd, "proc": proc, "url": url, "log": log, "started": time.time()}
    if url:
        run.emit("preview", url=url, cmd=cmd, auto=False)
        return "Server running at %s and opened in the Preview pane. It keeps running in the background.\n%s" % (url, out)
    return ("The process is running but no URL was detected yet. Pass the port in the 'port' argument next time. Output so far:\n%s" % (out or "(none)"))


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype == "application/json" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(ROOT, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html")
            if u.path == "/api/ping":
                global LAST_PING
                LAST_PING = time.time()
                try:
                    ui = os.path.getmtime(os.path.join(ROOT, "index.html"))
                except OSError:
                    ui = 0
                return self._send(200, {"ok": True, "ui": ui})
            if u.path.startswith("/assets/"):
                fp = os.path.join(ROOT, "assets", os.path.basename(u.path))
                ext = os.path.splitext(fp)[1].lower()
                ct = {".png": "image/png", ".ico": "image/x-icon", ".svg": "image/svg+xml"}.get(ext)
                if not ct or not os.path.isfile(fp):
                    return self._send(404, {"error": "not found"})
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), ct)
            if u.path == "/api/servers":
                return self._send(200, {"servers": servers_list()})
            if u.path == "/api/models":
                return self._send(200, list_models(q.get("provider")))
            if u.path == "/api/state":
                cfg = load_cfg()
                lst = sorted(({"id": s["id"], "title": s["title"], "created": s["created"], "cost": s.get("cost", 0.0)}
                              for s in sessions.values()), key=lambda s: -s["created"])
                active = {r.sess["id"]: r.id for r in runs.values() if not r.done}
                return self._send(200, {"config": masked_cfg(cfg), "usage": usage_snapshot(cfg), "sessions": lst, "preview_port": PREVIEW_PORT,
                                        "active": active, "budget": budget_state(cfg)})
            if u.path == "/api/session":
                s = sessions.get(q.get("id"))
                if not s:
                    return self._send(404, {"error": "no such chat"})
                run = next((r for r in runs.values() if r.sess["id"] == s["id"] and not r.done), None)
                return self._send(200, {"id": s["id"], "title": s["title"], "events": s["events"], "cost": s.get("cost", 0.0),
                                        "run": run.id if run else None, "run_pos": len(run.events) if run else 0,
                                        "tier": s.get("tier")})
            if u.path == "/api/tree":
                cfg = load_cfg()
                return self._send(200, {"workspace": cfg["workspace"], "items": walk_tree(cfg["workspace"], cfg["workspace"], 3)})
            if u.path == "/api/file":
                cfg = load_cfg()
                p = safe_path(cfg["workspace"], q.get("path"))
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    return self._send(200, {"path": q.get("path"), "content": f.read(200000)})
            if u.path == "/api/stream":
                return self.stream(q.get("run"), int(q.get("from") or 0))
            self._send(404, {"error": "not found"})
        except ToolErr as e:
            self._send(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._send(500, {"error": str(e)})

    def stream(self, rid, start):
        run = runs.get(rid)
        if not run:
            return self._send(404, {"error": "no such run"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        i = start
        try:
            while True:
                with run.cond:
                    while i >= len(run.events) and not run.done:
                        if not run.cond.wait(12):
                            break
                    batch = run.events[i:]
                    finished = run.done and i + len(batch) >= len(run.events)
                if batch:
                    for ev in batch:
                        self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode("utf-8"))
                    i += len(batch)
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                if finished:
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        self.close_connection = True

    def do_POST(self):
        u = urlparse(self.path)
        b = self._body()
        try:
            if u.path == "/api/run":
                text = (b.get("text") or "").strip()
                if not text:
                    return self._send(400, {"error": "empty message"})
                tier = b.get("tier")
                run, err = start_run(b.get("session"), text, None if tier in (None, "auto", "") else int(tier))
                if err:
                    return self._send(409, {"error": err})
                return self._send(200, {"run": run.id, "session": run.sess["id"]})
            if u.path == "/api/config":
                cfg = apply_config_update(b)
                return self._send(200, {"config": masked_cfg(cfg), "usage": usage_snapshot(cfg)})
            if u.path == "/api/approve":
                r = runs.get(b.get("run"))
                p = r.pending.get(b.get("id")) if r else None
                if not p:
                    return self._send(404, {"error": "no pending approval"})
                p["ok"] = bool(b.get("ok"))
                p["ev"].set()
                return self._send(200, {"ok": True})
            if u.path == "/api/stop":
                r = runs.get(b.get("run"))
                if r:
                    r.stop = True
                    for p in r.pending.values():
                        p["ev"].set()
                return self._send(200, {"ok": True})
            if u.path == "/api/escalate":
                r = runs.get(b.get("run"))
                if r:
                    r.force_escalate = True
                return self._send(200, {"ok": bool(r)})
            if u.path == "/api/servers/stop":
                return self._send(200, {"ok": stop_server(b.get("id"))})
            if u.path == "/api/models/sync":
                return self._send(200, models_sync(b.get("provider")))
            if u.path == "/api/provider/add":
                try:
                    pid = add_custom_provider(b.get("label"), b.get("base"))
                    return self._send(200, {"ok": True, "id": pid, "config": masked_cfg(load_cfg())})
                except ValueError as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            if u.path == "/api/provider/enable":
                try:
                    return self._send(200, set_provider_enabled(b.get("id"), b.get("on", True)))
                except ValueError as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            if u.path == "/api/provider/remove":
                try:
                    remove_custom_provider(b.get("id"))
                    return self._send(200, {"ok": True, "config": masked_cfg(load_cfg())})
                except ValueError as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            if u.path == "/api/openrouter/autoconfig":
                try:
                    return self._send(200, openrouter_autoconfig())
                except Exception as e:
                    return self._send(200, {"ok": False, "error": "Could not reach OpenRouter: %s" % e})
            if u.path == "/api/test":
                return self._send(200, test_connection(b.get("provider"), int(b.get("tier") or 0)))
            if u.path == "/api/balance":
                return self._send(200, fetch_balance(b.get("provider") or load_cfg()["provider"]))
            if u.path == "/api/session/delete":
                with _lock:
                    sessions.pop(b.get("id"), None)
                save_sessions()
                return self._send(200, {"ok": True})
            if u.path == "/api/session/rename":
                s = sessions.get(b.get("id"))
                if s:
                    s["title"] = str(b.get("title") or s["title"])[:60]
                    save_sessions()
                return self._send(200, {"ok": True})
            if u.path == "/api/usage/reset":
                with _lock:
                    usage["days"] = {}
                    _write_json(USAGE_PATH, usage)
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._send(500, {"error": str(e)})


if __name__ == "__main__":
    load_cfg()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    start_preview_server()
    print("Aqua Code running at http://127.0.0.1:%d  (Ctrl+C to stop)" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
