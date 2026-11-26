"""Aqua Code - your own coding agent (DeepSeek / Gemini) with credit limits and
cheap-to-strong model escalation.  Pure standard library, runs on 127.0.0.1 only.

    python server.py            -> http://127.0.0.1:8765
"""
import base64
import collections
import copy
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import platform
import shutil
import signal
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
    "review_gate": True,          # a stronger model checks the finished work against the brief
    "understand_first": True,     # a stronger model first turns a messy request into a clear brief
    "pool_models": True,          # when every configured model is limited, also try other models of the same provider
    "level": "balanced",          # working level: low (gentle), balanced, high (full speed)
    "no_key_fallback": True,      # when no API key is saved, use the free anonymous models that still work without one
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
    old_ws = cfg.get("workspace")
    for k in ("provider", "workspace", "approval"):
        if isinstance(new.get(k), str) and new[k].strip():
            cfg[k] = new[k].strip()
    if cfg.get("workspace") != old_ws:                       # remember every project folder so you can switch back to it
        kn = [p for p in (cfg.get("known_workspaces") or []) if isinstance(p, str)]
        for p in (old_ws, cfg.get("workspace")):
            if p and p not in kn:
                kn.append(p)
        cfg["known_workspaces"] = kn[-30:]
    if new.get("level") in ("low", "balanced", "high"):
        cfg["level"] = new["level"]
    for k in ("auto_escalate", "sticky_tier", "failover", "failover_paid", "no_key_fallback", "pool_models", "understand_first", "review_gate"):
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
    apply_level(cfg["level"])
    return cfg


# How long the AI keeps improving its work after it first says "done" (the working level also sets how hard Aqua uses this PC)
LEVEL_WORK = {"low": 0, "balanced": 30 * 60, "high": 60 * 60}      # seconds
LEVEL_STEPS = {"low": 0, "balanced": 600, "high": 2000}              # step ceiling for long work
KEEP_FOCI = ["test and review everything: run it, look at it, and fix every bug and visual problem you find",
             "add the missing depth: more detail, features, content and interactions that the request implies",
             "polish: visuals, animation, UX, phone-size layout, edge cases and speed",
             "code quality: clean structure, error handling, and a short README or usage notes"]

# --------------------------------------------------------------------------- working level (how hard Aqua works on this PC)
_nice_done = [0]


def _level_cores(level):
    n = os.cpu_count() or 1
    return max(1, min(n, max(2, n // 4))) if level == "low" else n


def apply_level(level):
    """low = gentle: lowest sensible priority and a quarter of the cores. balanced / high = normal priority, all cores.
    Child processes (commands, dev servers) inherit this. On macOS/Linux a lower priority cannot be raised again without
    administrator rights, so going back up from low needs an app restart there."""
    n = os.cpu_count() or 1
    cap = _level_cores(level)
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = ctypes.c_void_p
            k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            h = k.GetCurrentProcess()
            k.SetPriorityClass(h, 0x4000 if level == "low" else 0x20)          # BELOW_NORMAL / NORMAL
            k.SetProcessAffinityMask(h, (1 << cap) - 1 if level == "low" else (1 << n) - 1)
        else:
            if level == "low" and _nice_done[0] < 10:
                os.nice(10 - _nice_done[0])
                _nice_done[0] = 10
            if hasattr(os, "sched_setaffinity"):
                os.sched_setaffinity(0, set(range(cap if level == "low" else n)))
    except Exception:
        pass
    return cap


# --------------------------------------------------------------------------- usage / limits
usage = _read_json(USAGE_PATH, {"days": {}})
usage.setdefault("days", {})
LAST_PING = time.time()
recent = collections.defaultdict(collections.deque)   # "prov:model" -> request timestamps (for RPM)


def today():
    return datetime.date.today().isoformat()


# --- when do limits come back? --------------------------------------------------------------
LIMIT_UNTIL = {}       # "prov:model" -> {"until": epoch, "kind": "daily"|"rate", "provider": label, "model": name}
LIMIT_BASE = {}        # "prov:model" -> (day, requests already counted when the provider's limit came back)
DAILY_RESET = {"gemini": ("America/Los_Angeles", "midnight Pacific time"), "openrouter": ("UTC", "midnight UTC")}


def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def pacific_offset(now):
    """-7 during US daylight saving time (2nd Sunday of March to 1st Sunday of November), else -8 (no tz database needed)"""
    y = datetime.datetime.utcfromtimestamp(now).year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(10, 0)).replace(tzinfo=datetime.timezone.utc).timestamp()
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(9, 0)).replace(tzinfo=datetime.timezone.utc).timestamp()
    return -7 if start <= now < end else -8


def provider_reset_at(pid, now=None):
    """epoch of the next daily reset for providers whose rule is known, else None"""
    rule = DAILY_RESET.get(pid)
    if not rule:
        return None
    now = now or time.time()
    off = pacific_offset(now) if rule[0] != "UTC" else 0
    local = datetime.datetime.utcfromtimestamp(now + off * 3600)
    nxt = datetime.datetime.combine(local.date() + datetime.timedelta(days=1), datetime.time(0, 0))
    return nxt.replace(tzinfo=datetime.timezone.utc).timestamp() - off * 3600


def next_local_midnight(now=None):
    now = now or time.time()
    d = datetime.datetime.fromtimestamp(now)
    return time.mktime((d.date() + datetime.timedelta(days=1)).timetuple())


def retry_seconds(msg):
    """Gemini and others say 'retry in 21.5s' / 'try again in 2m3s' / retryDelay: 21s"""
    m = re.search(r"(?:retry|try again)[^0-9]{0,20}(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?!s))?\s*(?:(\d+(?:\.\d+)?)\s*s)?", msg or "", re.I)
    if m and any(m.groups()):
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)
    m = re.search(r'retryDelay"?\s*:\s*"?(\d+(?:\.\d+)?)s', msg or "")
    return float(m.group(1)) if m else 0.0


def note_limit(pid, label, model, msg="", daily=None):
    """remember that this model is limited and when it comes back"""
    now = time.time()
    if daily is None:
        m_ = msg or ""
        rs = retry_seconds(m_)
        if re.search(r"per.?minute|per.?second|\brpm\b|\btpm\b|tokens per min", m_, re.I):
            daily = False
        elif re.search(r"per.?day|daily|limit:\s*0|free.?models?.*per.?day", m_, re.I) or rs > 3600:
            daily = True
        elif rs > 0:
            daily = False
        else:
            daily = bool(re.search(r"exceeded your current quota|quota exceeded", m_, re.I))
    if daily:
        until = provider_reset_at(pid, now) or next_local_midnight(now)
        kind = "daily"
    else:
        until = now + max(15.0, min(retry_seconds(msg) or 60.0, 3600.0))
        kind = "rate"
    LIMIT_UNTIL[pid + ":" + model] = {"until": until, "kind": kind, "provider": label, "model": model, "pid": pid}
    return until


def is_limited(key):
    e = LIMIT_UNTIL.get(key)
    if not e:
        return None
    if e["until"] <= time.time():
        LIMIT_UNTIL.pop(key, None)
        if e["kind"] == "daily":
            LIMIT_BASE[key] = (today(), day_rec(today())["models"].get(key, {}).get("requests", 0))
        return None
    return e


def limits_snapshot(cfg):
    now = time.time()
    out = {"blocked": [], "resets": {}}
    for k in list(LIMIT_UNTIL):
        e = is_limited(k)
        if e:
            out["blocked"].append({"provider": e["provider"], "model": e["model"], "until": e["until"], "kind": e["kind"], "pid": e["pid"]})
    for pid, v in cfg["providers"].items():
        if pid in DAILY_RESET and has_access(v):
            out["resets"][pid] = {"at": provider_reset_at(pid, now), "label": DAILY_RESET[pid][1]}
    return out


def fmt_in(sec):
    sec = int(max(0, sec))
    if sec < 90:
        return "%d s" % sec
    if sec < 5400:
        return "%d min" % round(sec / 60.0)
    return "%d h %02d min" % (sec // 3600, (sec % 3600) // 60)


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
        "limits": limits_snapshot(cfg), "now": time.time(),
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


def compact_for_context(msgs, limit=90000, keep_last=12):
    """Long (up to 1 hour) runs would overflow the model's memory: when the chat gets big, shrink OLD tool outputs and old file dumps (the files themselves stay on disk)."""
    total = sum(len(json.dumps(m, ensure_ascii=False)) for m in msgs)
    if total <= limit:
        return msgs
    out = []
    cut = len(msgs) - keep_last
    for i, m in enumerate(msgs):
        if i >= cut or i == 0:
            out.append(m)
            continue
        c = dict(m)
        if c.get("role") == "tool" and isinstance(c.get("content"), str) and len(c["content"]) > 500:
            c["content"] = c["content"][:400] + "\n[... older output shortened to save memory ...]"
        elif c.get("role") == "assistant":
            if isinstance(c.get("content"), str) and len(c["content"]) > 1500:
                c["content"] = c["content"][:1500] + " [...]"
            if c.get("tool_calls"):
                tcs = []
                for tc in c["tool_calls"]:
                    tc = dict(tc)
                    f = dict(tc.get("function") or {})
                    a = f.get("arguments") or ""
                    if len(a) > 600:
                        try:
                            d = json.loads(a)
                            for k in ("content", "new", "old"):
                                if isinstance(d.get(k), str) and len(d[k]) > 200:
                                    d[k] = d[k][:200] + "... [omitted, it is in the file]"
                            f["arguments"] = json.dumps(d, ensure_ascii=False)
                        except Exception:
                            pass
                    tc["function"] = f
                    tcs.append(tc)
                c["tool_calls"] = tcs
        elif c.get("role") == "user" and isinstance(c.get("content"), str) and len(c["content"]) > 2500:
            c["content"] = c["content"][:2500] + " [...]"
        out.append(c)
    # still too big (a very long run): drop the oldest steps completely, at a clean boundary (a user message), and point to the notes and the files
    if sum(len(json.dumps(m, ensure_ascii=False)) for m in out) > limit * 1.6:
        j = max((i for i in range(2, max(2, len(out) - keep_last)) if out[i].get("role") == "user"), default=0)
        if j > 1:
            out = [out[0], {"role": "user", "content": "[aqua] To save memory, your older steps were removed from this chat. The project notes (in your instructions) and the files on disk hold everything done so far: use list_dir/read_file to look at the real state, then continue."}] + out[j:]
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
    {"type": "function", "function": {"name": "scaffold_3d", "description": "Install the 3D kit (Python model builder aqua3d, viewer, example, three.js) into the workspace. Call once before modelling.",
     "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "show_3d", "description": "Open a .glb/.gltf/.obj/.stl file from the workspace in the 3D viewer in the user's Preview pane.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "e.g. models/house.glb"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "look_3d", "description": "LOOK at a 3D model you built: renders 4 views (3/4, front, side, top) and returns a text report (size, floating or sunk parts, ASCII front/side/top views). If your model can read images you also get the picture. Use it after every build and fix what looks wrong.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "e.g. models/castle.glb"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "search_3d_models", "description": "Search free ready-made 3D models (Khronos glTF samples and three.js examples). Returns download URLs.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "download_3d_model", "description": "Download a .glb/.gltf/.obj/.stl from an allowed site (GitHub, threejs.org...) into models/.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "name": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "recipes", "description": "Tested, working starter projects. Call with no name to list them, or with a name to copy one into the workspace (default index.html) and then adapt it. Use this FIRST for games, shaders/slime/jelly, 3D scenes: starting from a working recipe is far better than inventing from scratch.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "path": {"type": "string", "description": "where to copy it, default index.html"}, "overwrite": {"type": "boolean"}}, "required": []}}},
    {"type": "function", "function": {"name": "look_page", "description": "LOOK at a web page you built or are fixing (the Preview or a localhost dev server): returns a screenshot (if your model can see images), the browser console errors, and the visible text. Use it after building a page and before and after fixing a bug. mobile=true checks a phone size.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "workspace path like index.html, or http://localhost:PORT/..."},
                                                      "mobile": {"type": "boolean"}, "width": {"type": "integer"}, "height": {"type": "integer"},
                                                      "wait_ms": {"type": "integer", "description": "wait for scripts/animations, default 2500"}}, "required": []}}},
    {"type": "function", "function": {"name": "ask_user", "description": "Ask the user 1-4 short questions before you build when the request is vague or an important choice is theirs (what kind, which stack, design style, features, who it is for). Give each question 2-4 short options; the user can also type their own answer. Do not ask what you can reasonably decide yourself.",
     "parameters": {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "object", "properties": {
         "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}, "multi": {"type": "boolean", "description": "true if several options can be picked"}},
         "required": ["question"]}}}, "required": ["questions"]}}},
    {"type": "function", "function": {"name": "add_note", "description": "Save a short note about what you did, decisions, problems or next steps, so any other AI that continues this project knows what happened.",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
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
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vs", ".aqua"}


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


def outside_project_paths(cmd, ws):
    """paths in a shell command that point into the user's other folders (other projects, Aqua's own data and API keys). Each project is separate."""
    import tempfile
    home = os.path.realpath(os.path.expanduser("~"))
    w = os.path.realpath(ws)
    free = [os.path.realpath(tempfile.gettempdir())] + [os.path.realpath(os.environ[k]) for k in ("LOCALAPPDATA", "APPDATA") if os.environ.get(k)]
    bad = []
    for tok in re.findall(r"[^\s\"'|;&<>(),=]+", cmd):
        if "://" in tok or not re.search(r"[\\/]|^~|^\.\.$|^[A-Za-z]:", tok):
            continue
        t = tok.replace("$env:USERPROFILE", home).replace("%USERPROFILE%", home).replace("$HOME", home)
        if t.startswith("~"):
            t = home + t[1:]
        if t.startswith("$"):
            continue
        try:
            p = os.path.realpath(os.path.join(w, t))
        except Exception:
            continue
        inside_home = p == home or p.startswith(home + os.sep)
        in_ws = p == w or p.startswith(w + os.sep)
        if inside_home and not in_ws and not any(p == f or p.startswith(f + os.sep) for f in free):
            bad.append(tok)
    return bad


def tool_run_command(ws, a):
    cmd = a.get("command") or ""
    if not cmd.strip():
        raise ToolErr("Empty command")
    if DENY.search(cmd):
        raise ToolErr("Blocked: this command is not allowed for safety.")
    out_of = outside_project_paths(cmd, ws)
    if out_of:
        raise ToolErr("Blocked: this command touches files outside this project folder (%s). Each project is separate: work only inside the workspace folder, and never read other projects or Aqua's own data." % ", ".join(out_of[:3]))
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


TOOL_FUNCS = {"recipes": lambda ws, a: None, "ask_user": lambda ws, a: None, "look_page": lambda ws, a: None, "scaffold_3d": lambda ws, a: None, "show_3d": lambda ws, a: None, "look_3d": lambda ws, a: None, "search_3d_models": lambda ws, a: None, "download_3d_model": lambda ws, a: None, "add_note": lambda ws, a: None, "start_server": lambda ws, a: None, "list_dir": tool_list_dir, "read_file": tool_read_file, "write_file": tool_write_file,
              "edit_file": tool_edit_file, "search": tool_search, "run_command": tool_run_command}


# --------------------------------------------------------------------------- runs & sessions
sessions = _read_json(SESS_PATH, {})
try:
    _cur = os.path.normcase(os.path.realpath(_merge(DEFAULT_CONFIG, _read_json(CFG_PATH, {}))["workspace"]))
    for _s in sessions.values():
        _s.setdefault("workspace", _cur)
except Exception:
    pass
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


# --------------------------------------------------------------------------- project notes shared by every AI
NOTES_DIR = ".aqua"
NOTES_MAX = 24000


def notes_path(ws):
    return os.path.join(ws, NOTES_DIR, "NOTES.md")


def read_notes(ws, limit=5000):
    try:
        with open(notes_path(ws), "r", encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return ""
    return txt if len(txt) <= limit else "...(older notes trimmed)\n" + txt[txt.find("\n## ", len(txt) - limit) + 1:] if "\n## " in txt[len(txt) - limit:] else txt[-limit:]


def append_note(ws, who, text):
    """Every AI (and Aqua itself) leaves a short note, so whoever works on the project next knows what happened."""
    text = (text or "").strip()
    if not text:
        return
    try:
        os.makedirs(os.path.join(ws, NOTES_DIR), exist_ok=True)
        p = notes_path(ws)
        head = "# Project notes\nWritten by the AIs that worked on this project, newest last.\n\n"
        try:
            with open(p, "r", encoding="utf-8") as f:
                old = f.read()
        except OSError:
            old = head
        new = old + "## %s - %s\n%s\n\n" % (time.strftime("%Y-%m-%d %H:%M"), who, text[:1800])
        if len(new) > NOTES_MAX:
            cut = new.find("\n## ", len(new) - int(NOTES_MAX * 0.7))
            new = head + new[cut + 1:] if cut >= 0 else head + new[-int(NOTES_MAX * 0.7):]
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
    except OSError:
        pass


def system_prompt(ws, extra=""):
    base = ("You are Aqua Code, an autonomous coding agent working inside the user's workspace folder on %s. "
            "Workspace: %s\n"
            "Work like a careful senior engineer: look before you edit, prefer small exact edit_file changes over "
            "rewriting files, and verify by running the code or tests with run_command when that makes sense. "
            "All paths are relative to the workspace. For a plain web page just write index.html in the workspace: it is shown live in the user's Preview pane and reloads when you edit it. For frameworks use start_server. Keep prose short. If the task is beyond you or you keep "
            "failing, call request_stronger_model with a reason instead of guessing. Other AIs may work on this project after you "
            "(the user switches models and providers), so when the work is complete call add_note once with 1-3 short lines "
            "(what you did, what is left, anything the next AI must know), then answer with a brief summary and no tool call. "
            "BUILD METHOD (this is how good engineers work, so follow it even if you are a small model): first state your plan in 3-5 lines. Then build in LAYERS: (1) a minimal version that really runs, written in a single write_file of at most ~250 lines; verify it (run it or look_page). "
            "(2) add features one or two at a time with edit_file, verifying after each. (3) polish. NEVER write one giant file in one go, never leave TODOs, placeholders or fake functions, and never claim something works without having run or looked at it. "
            "If the user message is short or vague (for example \"fix it\", \"it does not work\", \"add fur\"), work out what they mean from the files and the project notes instead of guessing or starting over, and keep everything that already works. "
            "RECIPES: for games, shaders (slime, jelly, liquid, glass), 3D scenes or arcade/2D games, FIRST call recipes: if one fits, copy it and adapt it step by step (they are tested and work) instead of inventing from scratch. "
            "SEPARATION: this folder is one project. If its existing files clearly belong to a DIFFERENT project than what the user now asks for (for example a PC-builder app when they ask for a cat game), "
            "do NOT read, reuse or overwrite them: tell the user to click the + next to Project in the sidebar to start a new empty project, then stop. Never try to open folders outside the workspace; they belong to other projects. "
            "SEEING: for anything with a screen (web pages, games, UIs) you can really look at it: after you build it, and before and after you fix a visual or runtime bug, "
            "call look_page (screenshot if your model sees images, plus browser console errors and visible text). Fix every console error and visible problem, then look again, at most 3 rounds. "
            "DEBUGGING: when the user says something is blank, black, broken or 'does not work', do NOT guess. FIRST call look_page and read the console errors: the first error is almost always the cause "
            "(for example 'X is not defined' means a variable or function was deleted or misspelled; search for it). Fix that exact cause. Never change camera, colours or other unrelated things as a guess, "
            "and never replace a big block with edit_file without checking that every name defined inside it is still defined (search each one). After a fix, call look_page again to prove it works before you say it is fixed. "
            "ASKING: like a good colleague, when the request is vague or has important open choices (what kind of app, which stack, design style, "
            "main features, who it is for), call ask_user ONCE at the start with 1-4 short questions (2-4 short options each) before you build. "
            "Do not ask about things you can decide yourself, and do not ask when the request is already clear, when the user said to just do it, or when the notes already answer it. "
            "After the answers, continue without asking again unless you are truly blocked. "
            "This workspace is ONE separate project: use only its files and the notes below. Never assume details from other projects, "
            "folders or earlier chats about something else; if the user wants another project's notes they will import them."
            % ("Windows (PowerShell)" if os.name == "nt" else "Linux/macOS (bash)", ws))
    base += extra
    notes = read_notes(ws)
    if notes:
        base += ("\n\n--- PROJECT NOTES (written by earlier AIs and by Aqua; they tell you what already happened, so trust them "
                 "and do not redo finished work) ---\n" + notes + "\n--- END OF NOTES ---")
    return base


PROJECTS_ROOT = os.path.join(ROOT, "projects")


def list_projects():
    cur = load_cfg()["workspace"]
    items = {}

    def add(p):
        try:
            rp = os.path.normcase(os.path.realpath(p))
            if rp in items or not os.path.isdir(p):
                return
            n = sum(1 for e in os.scandir(p) if not e.name.startswith("."))
            items[rp] = {"name": os.path.basename(p.rstrip("\\/")) or p, "path": p, "files": n, "current": rp == ws_key(cur)}
        except OSError:
            pass
    add(cur)
    add(os.path.join(ROOT, "workspace"))
    for kp in load_cfg().get("known_workspaces") or []:
        add(kp)
    if os.path.isdir(PROJECTS_ROOT):
        for d in sorted(os.listdir(PROJECTS_ROOT), key=str.lower):
            add(os.path.join(PROJECTS_ROOT, d))
    return list(items.values())


def new_project(name):
    nm = re.sub(r"[^\w\- ]+", "", str(name or "")).strip()[:40] or "project"
    os.makedirs(PROJECTS_ROOT, exist_ok=True)
    p, i = os.path.join(PROJECTS_ROOT, nm), 2
    while os.path.exists(p):
        p = os.path.join(PROJECTS_ROOT, "%s %d" % (nm, i))
        i += 1
    os.makedirs(p)
    return p


def ws_key(ws):
    return os.path.normcase(os.path.realpath(ws))


def new_session(title="New chat"):
    sid = uuid.uuid4().hex[:10]
    sessions[sid] = {"id": sid, "title": title, "created": time.time(), "messages": [], "events": [],
                     "cost": 0.0, "tier": None, "workspace": ws_key(load_cfg()["workspace"])}
    return sessions[sid]


def wait_limits(run, cfg, pid, tiers, ti):
    """Honour RPM (sleep) and RPD (signal) before each request. Returns 'ok' or 'daily'."""
    t = tiers[ti]
    key = pid + ":" + t["model"]
    lim = is_limited(key)
    if lim and lim["kind"] == "daily":
        return "daily"
    if t.get("rpd", 0) > 0:
        n = day_rec(today())["models"].get(key, {}).get("requests", 0)
        b = LIMIT_BASE.get(key)
        if b and b[0] == today():
            n = max(0, n - b[1])                    # the provider's own daily reset already passed
        if n >= t["rpd"]:
            note_limit(pid, cfg["providers"][pid]["label"], t["model"], daily=True)
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
    payload = {"model": t["model"], "messages": [{"role": "system", "content": system_prompt(cfg["workspace"], THREED_PROMPT if wants_3d(messages, cfg["workspace"]) else "")}] +
               compact_for_context(clean_messages(messages, t["model"])), "tools": TOOLS}
    url = prov["base"].rstrip("/") + "/chat/completions"
    img, nov = getattr(run, "look_img", None), getattr(run, "no_vision", None)
    if nov is None:
        nov = run.no_vision = set()
    if img and (pid + ":" + t["model"]) not in nov:
        payload["messages"].append({"role": "user", "content": [
            {"type": "text", "text": "[aqua] " + (getattr(run, "look_text", None) or "This is the picture of your 3D model from look_3d (3/4, front, side, top). Judge it honestly and fix what looks wrong.")},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + img}}]})
        sent_img = True
    else:
        sent_img = False
    delays = [3, 8, 20]
    for attempt in range(len(delays) + 1):
        try:
            res = http_json(url, auth_key(prov), payload)
            if sent_img:
                run.look_img = None
            return res
        except LLMError as e:
            if sent_img and e.status in (400, 404, 415, 422):
                nov.add(pid + ":" + t["model"])                # this model cannot read images: continue with the text report only
                payload["messages"].pop()
                sent_img = False
                run.look_img = None
                continue
            per_day = e.status == 429 and re.search(r"per.?day|daily|limit:\s*0|free.?models?.*per.?day", e.msg, re.I)
            if e.status in (429, 500, 502, 503, 504) and attempt < (1 if e.status == 429 else len(delays)) and not run.stop and not per_day:
                run.emit("status", persist=False, text="%s returned %s, retrying in %ds" % (t["model"], e.status, delays[attempt]))
                time.sleep(delays[attempt])
                continue
            raise


def tool_ask_user(run, a):
    qs = []
    raw = a.get("questions")
    if not isinstance(raw, list):
        raw = [{"question": a.get("question"), "options": a.get("options")}] if a.get("question") else []
    for q in raw[:4]:
        if isinstance(q, str):
            q = {"question": q}
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        opts = [str(o)[:80] for o in (q.get("options") or []) if str(o).strip()][:5]
        qs.append({"question": str(q["question"])[:300], "options": opts, "multi": bool(q.get("multi"))})
    if not qs:
        return "No questions were given. Decide sensible defaults yourself."
    aid = uuid.uuid4().hex[:8]
    ev = threading.Event()
    run.pending[aid] = {"ev": ev, "ok": False, "answers": None}
    run.emit("ask", id=aid, questions=qs)
    while not ev.wait(0.5):
        if run.stop:
            return "Cancelled."
    ans = run.pending[aid].get("answers")
    run.emit("ask_result", id=aid, answers=ans or [])
    if not ans:
        return "The user skipped the questions. Pick sensible defaults yourself, say briefly what you assumed, and continue."
    lines = []
    for i, q in enumerate(qs):
        x = ans[i] if i < len(ans) and isinstance(ans[i], dict) else {}
        v = x.get("answer")
        v = ", ".join(str(t) for t in v) if isinstance(v, list) else str(v or "").strip()
        lines.append("Q: %s\nA: %s" % (q["question"], v or "(no answer: choose yourself)"))
    return "The user answered:\n" + "\n".join(lines) + "\nContinue with these choices. Do not ask the same things again."


def exec_tool(run, cfg, name, args):
    """Returns (text, is_error, ui_extra)."""
    ws = cfg["workspace"]
    fn = TOOL_FUNCS.get(name)
    if not fn:
        return "Unknown tool: %s" % name, True, None
    if name == "add_note":
        append_note(ws, getattr(run, "who", "an AI"), str(args.get("text", "")))
        return "Note saved for the next AI.", False, None
    if name == "ask_user":
        return tool_ask_user(run, args), False, None
    needs = (name in ("run_command", "start_server", "download_3d_model") and cfg["approval"] in ("ask", "edits")) or \
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
        if name == "scaffold_3d":
            return tool_scaffold_3d(ws), False, None
        if name == "show_3d":
            return tool_show_3d(run, ws, args), False, None
        if name == "recipes":
            return tool_recipes(ws, args), False, None
        if name == "look_3d":
            return tool_look_3d(run, ws, args), False, None
        if name == "look_page":
            return tool_look_page(run, ws, args), False, None
        if name == "search_3d_models":
            return tool_search_3d(args), False, None
        if name == "download_3d_model":
            return tool_download_3d(ws, args), False, None
        out = fn(ws, args)
        if name in WRITE_TOOLS and PREVIEW_PORT and str(args.get("path", "")).lower().endswith((".html", ".htm")):
            run.emit("preview", url="http://127.0.0.1:%d/%s" % (PREVIEW_PORT, str(args["path"]).replace("\\", "/").lstrip("/")), auto=True)
        if name == "edit_file":
            out += lost_definitions(ws, args)
        if name in WRITE_TOOLS:
            out += auto_page_check(run, ws, str(args.get("path", "")))
        return out, False, None
    except ToolErr as e:
        return str(e), True, None
    except Exception as e:
        return "Tool crashed: %s" % e, True, None


def quota_left_for(pid, t):
    key = pid + ":" + t["model"]
    lim = is_limited(key)
    if lim and lim["kind"] == "daily":
        return False
    r = t.get("rpd", 0)
    n = day_rec(today())["models"].get(key, {}).get("requests", 0)
    b = LIMIT_BASE.get(key)
    if b and b[0] == today():
        n = max(0, n - b[1])
    return r <= 0 or n < r


def is_limit_error(e):
    if e.status in (402, 429, 503):
        return True
    return e.status in (403, 404) and bool(re.search(
        r"quota|limit|billing|credit|balance|free tier|not found|no longer available|does not exist|decommission|deprecat|not available", e.msg or "", re.I))


def out_of_credit(e):
    return e.status == 402 or bool(re.search(r"insufficient (balance|credit|funds)|out of credit|credits? (are )?exhausted", e.msg or "", re.I))


_pool_cache = {}


def pool_models(pid, tiers):
    """Other chat models the provider offers with this same key. Each model has its own free quota, so when the configured ones are limited, Aqua just uses these instead of waiting."""
    c = _pool_cache.get(pid)
    if not c or time.time() - c[0] > 3600:
        r = list_models(pid)
        c = (time.time(), r.get("ids") or [] if r.get("ok") else [])
        _pool_cache[pid] = c
    have = {t["model"] for t in tiers}
    bad = re.compile(r"embed|tts|audio|image|vision|live|transcribe|guard|moderation|rerank|whisper|imagen|veo|lyria|dall|ocr|robotics|computer-use|learnlm|aqa|gemma|safeguard|-exp|exp-|preview-\d|thinking|search|ranker|code-?bison|text-bison", re.I)
    ids = [i for i in c[1] if i not in have and not bad.search(i)]
    ids.sort(key=lambda i: (0 if "flash" in i.lower() else 2 if "pro" in i.lower() else 1, len(i)))
    return ids[:8]


COMPLEX_RE = re.compile(r"shader|glsl|webgl|three\.?js|3d|physics|raymarch|simulation|multiplayer|procedural|pathfind|particle|voxel|fluid|ray.?trac|game engine|ai opponent|machine learning|neural", re.I)
COMPLAINT_RE = re.compile(r"doesn.?t work|does not work|not working|still|again|broken|black|blank|nothing|empty|error|bug|crash|fix|wrong|can.?t see|cannot see|no \w+ (?:shows|appears)|i see no|why", re.I)


def followup_hint(text, first):
    """Small models follow the LAST instruction best, so add the right method to short or vague messages automatically."""
    t = text.strip()
    if first or len(t) > 400:
        return ""
    if COMPLAINT_RE.search(t):
        return ("\n\n[aqua auto-prompt: the user reports a problem. Do NOT guess and do NOT start over. 1) call look_page and read the console errors and the screenshot; the first error is usually the cause. "
                "2) find that exact cause in the files (search/read_file). 3) fix only that, keep everything else. 4) call look_page again to prove it is fixed, then say what the cause was in one sentence.]")
    if len(t.split()) <= 14:
        return ("\n\n[aqua auto-prompt: a short change request. Read the existing files first, make the change inside them with edit_file (keep everything that already works), "
                "then verify with look_page or by running it. Do not rewrite the whole project.]")
    return ""


BRIEF_SYSTEM = ("You turn a messy, typo-filled request from a non-expert into a clear build brief for a coding AI. Read it charitably, guess what the person REALLY wants, and fix typos. "
               "Never ask questions. Write at most 170 words, plain text, in this shape:\nGOAL: one sentence.\nLOOK AND FEEL: 2 sentences about what the user sees and does.\nFEATURES: 5 to 8 short bullet points (concrete, buildable).\nTECH: the simplest tech that works offline in the browser (usually ONE index.html with plain JavaScript/WebGL).\nDONE WHEN: 3 checks that prove it works.\nBUILD ORDER: 3 numbered stages (a minimal working core first, then features, then polish).\n"
               "Technique hints: a realistic slime / jelly / liquid object = a fragment shader that ray-marches a signed-distance rounded shape with spring-wobble physics, Fresnel + refraction + subsurface glow + specular highlights, mouse poke/drag adds impulses; "
               "a 3D scene = Three.js or raw WebGL; a game = a requestAnimationFrame loop, keyboard/mouse controls, score and restart; keep it small and polished rather than huge.")


def make_brief(run, cfg, pid, tiers, top, text):
    """Spend one call of the STRONGEST tier to understand the request properly, so the cheap model that builds it does not guess."""
    prov = cfg["providers"][pid]
    order = list(range(min(top, len(tiers) - 1), -1, -1))[:3]
    for i in order:
        t = tiers[i]
        if not t.get("model") or is_limited(pid + ":" + t["model"]) or not quota_left_for(pid, t):
            continue
        try:
            wait_limits(run, cfg, pid, tiers, i)
            j = http_json(prov["base"].rstrip("/") + "/chat/completions", auth_key(prov),
                          {"model": t["model"], "messages": [{"role": "system", "content": BRIEF_SYSTEM}, {"role": "user", "content": text[:3000]}], "max_tokens": 700}, timeout=90)
            out = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            u = j.get("usage") or {}
            record_usage(pid, tiers, i, int(u.get("prompt_tokens") or 0), 0, int(u.get("completion_tokens") or 0), bool(prov.get("free")))
            out = out.strip()
            if len(out) > 40:
                return out[:1800], t["model"]
        except LLMError as e:
            if e.status in (429, 503):
                note_limit(pid, prov["label"], t["model"], e.msg)
            continue
        except Exception:
            continue
    return None, None


REVIEW_SYSTEM = ("You are a strict senior code reviewer. You get a build brief and the files an AI built for it. Check the files against the brief's FEATURES and DONE WHEN. "
                 "Look for: features that are missing or only faked, code that cannot work (undefined names, wrong APIs, wrong math), controls that do nothing, missing score/restart/error handling, and anything a user would immediately notice is broken or ugly. "
                 "Answer with exactly OK if it satisfies the brief. Otherwise answer ISSUES: followed by at most 5 numbered, concrete, fixable defects (name the function or area and the fix). No praise and no long text.")


def review_work(run, cfg, pid, tiers, top, brief, ws, summary):
    """One call of the strongest tier reads the finished files against the brief. Returns a list of issues, or [] when it is fine."""
    prov = cfg["providers"][pid]
    parts, total = [], 0
    for it in walk_tree(ws, ws, 2, 60):
        if it["dir"] or not it["path"].lower().endswith((".html", ".htm", ".js", ".mjs", ".css", ".py", ".json", ".md")) or it["path"].startswith(("3d/", ".aqua/")):
            continue
        try:
            body = open(safe_path(ws, it["path"]), encoding="utf-8", errors="replace").read(14000)
        except Exception:
            continue
        if total + len(body) > 26000:
            break
        parts.append("=== %s ===\n%s" % (it["path"], body))
        total += len(body)
    if not parts:
        return []
    msg = "BRIEF:\n%s\n\nTHE BUILDER SAYS:\n%s\n\nFILES:\n%s" % (brief[:1800], (summary or "")[:600], "\n\n".join(parts))
    for i in list(range(min(top, len(tiers) - 1), -1, -1))[:3]:
        t = tiers[i]
        if not t.get("model") or is_limited(pid + ":" + t["model"]) or not quota_left_for(pid, t):
            continue
        try:
            j = http_json(prov["base"].rstrip("/") + "/chat/completions", auth_key(prov),
                          {"model": t["model"], "messages": [{"role": "system", "content": REVIEW_SYSTEM}, {"role": "user", "content": msg}], "max_tokens": 700}, timeout=120)
            out = (((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            u = j.get("usage") or {}
            record_usage(pid, tiers, i, int(u.get("prompt_tokens") or 0), 0, int(u.get("completion_tokens") or 0), bool(prov.get("free")))
            if not out or re.match(r"^\W*OK\b", out):
                return []
            issues = [re.sub(r"^\s*\d+[\.\)]\s*", "", ln).strip() for ln in out.replace("ISSUES:", "").splitlines() if re.match(r"^\s*\d+[\.\)]", ln)]
            return issues[:5] or ([out[:500]] if len(out) > 20 else [])
        except LLMError as e:
            if e.status in (429, 503):
                note_limit(pid, prov["label"], t["model"], e.msg)
            continue
        except Exception:
            continue
    return []


def agent_loop(run, user_text, forced_tier):
    sess = run.sess
    cfg = load_cfg()
    notice = None
    if cfg.get("no_key_fallback", True) and not any(has_access(v) for v in cfg["providers"].values()):
        first = nokey_enable()
        if first:
            cfg = load_cfg()
            notice = ("No API key is saved yet, so Aqua is using %s (%s), a free anonymous model. It is slow and your prompts are public. "
                      "Add a free key for faster, private use." % (cfg["providers"][first]["label"], cfg["providers"][first]["tiers"][0]["model"]))
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
    first_msg = not messages
    brief, bmodel = None, None
    if cfg.get("understand_first", True) and first_msg and len(user_text.strip()) >= 12 and not run.stop:
        run.emit("status", persist=False, text="Understanding your request first (%s)" % plabel)
        brief, bmodel = make_brief(run, cfg, pid, tiers, top, user_text)
    messages.append({"role": "user", "content": user_text + (("\n\n[aqua brief: a clean reading of the request above, written by a stronger model. Build exactly this, and ignore typos in the original]\n" + brief) if brief else "") + followup_hint(user_text, first_msg)})
    if forced_tier is None and first_msg and not sess.get("tier") and ti < 1 and top >= 1 and COMPLEX_RE.search(user_text + " " + (brief or "")):
        ti = 1                                            # shaders, 3D, physics and games are too hard for the smallest model: start one tier up
    run.emit("user", text=user_text)
    if brief:
        run.emit("brief", text=brief, model=bmodel)
    if notice:
        run.emit("notice", text=notice)
    steps_here = errs = 0
    sigs = []
    run_cost = 0.0
    steps = 0
    lvl = cfg.get("level", "balanced")
    work_budget = LEVEL_WORK.get(lvl, 0)
    max_steps_eff = max(cfg["max_steps"], LEVEL_STEPS.get(lvl, 0))
    t_start = time.time()
    push = {"n": 0, "idle": 0, "writes": 0, "w_at": 0}
    status = "done"
    acts = {"files": [], "cmds": [], "errors": [], "ais": []}
    last_text = ""

    free = bool(cfg["providers"][pid].get("free"))
    blocked = set()                       # (provider, tier) pairs that hit a limit during this run
    dead = set()                          # providers that ran out of credit or rejected the key during this run

    def quota_left(i):
        return quota_left_for(pid, tiers[i])

    def switch_to(j, reason):
        nonlocal ti, steps_here, errs
        up = j > ti
        run.emit("escalate", frm=ti, to=j, reason=reason, model=tiers[j]["model"], name=tiers[j]["name"], up=up)
        append_note(cfg["workspace"], "Aqua", "Handed over from %s to %s (%s)." % (tiers[ti]["model"], tiers[j]["model"], reason))
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
        if cfg.get("pool_models", True):                      # every configured model is limited: try the provider's other models with the same key
            try:
                for m in pool_models(pid, tiers):
                    if is_limited(pid + ":" + m):
                        continue
                    tiers.append({"name": "Extra", "model": m, "in": 0.0, "cache": 0.0, "out": 0.0, "rpm": 0, "rpd": 0, "extra": True})
                    switch_to(len(tiers) - 1, reason + "; trying " + m)
                    return True
            except Exception:
                pass
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
            append_note(cfg["workspace"], "Aqua", "Switched AI from %s to %s (%s). Continue from the notes and the chat history." % (old, plabel, reason))
            messages.append({"role": "user", "content": "[aqua note] Switched AI from %s to %s (%s). Review the work so far, "
                             "fix what failed, and continue the task." % (old, plabel, reason)})
            return True
        return False

    tr = {"n": 0}

    def wait_for_limits():
        """Balanced/Max: do not give up while working time is left. Sleep until the soonest short limit is back, then carry on."""
        left_ = work_budget - (time.time() - t_start)
        if work_budget <= 0 or left_ < 120 or run.stop:
            return False
        soon_ = sorted((b for b in limits_snapshot(cfg)["blocked"] if b["kind"] == "rate"), key=lambda b: b["until"])
        wait_ = (soon_[0]["until"] - time.time()) if soon_ else 60.0
        if wait_ > min(900.0, left_ - 60):
            return False
        wait_ = max(5.0, wait_ + 2)
        who_ = ", ".join(sorted({"%s %s" % (b["provider"], b["model"]) for b in limits_snapshot(cfg)["blocked"]}))[:120] or plabel
        end_ = time.time() + wait_
        while time.time() < end_:
            if run.stop:
                return False
            run.emit("status", persist=False, text="Free per-minute limit reached (%s). Back in %d s, then Aqua keeps working. Add a second free key (Groq, OpenRouter...) to avoid waiting." % (who_, max(0, int(end_ - time.time()))))
            for _ in range(10):
                if run.stop or time.time() >= end_:
                    break
                time.sleep(1.0)
        blocked.clear()
        dead.clear()
        return True

    try:
        while steps < max_steps_eff and not run.stop:
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
                if fallback(why) or next_provider(why) or wait_for_limits():
                    continue
                soon = sorted(limits_snapshot(cfg)["blocked"], key=lambda b: b["until"])
                when = (" The soonest one, %s, is back in about %s." % (soon[0]["provider"], fmt_in(soon[0]["until"] - time.time()))) if soon else ""
                run.emit("error", text="Every connected AI has used up its limit for now.%s Free limits reset at midnight (Gemini: Pacific time, OpenRouter: UTC). Connect another free provider in the AI menu to keep going." % when)
                status = "limit"
                break
            run.who = "%s / %s" % (plabel, tiers[ti]["model"])
            if run.who not in acts["ais"]:
                acts["ais"].append(run.who)
            run.emit("model", tier=ti, name=tiers[ti]["name"], model=tiers[ti]["model"], tiers=len(tiers), provider=plabel)
            left_now = work_budget - (time.time() - t_start)
            run.emit("status", persist=False, text="%s - %s is working%s" % (plabel, tiers[ti]["model"], (" (%s: about %d min of working time left)" % ({"balanced": "Balanced", "high": "Max"}.get(lvl, lvl), int(left_now // 60))) if left_now > 60 else ""))
            try:
                call_t0 = time.time()
                resp = call_llm(run, cfg, pid, tiers, ti, messages)
            except LLMError as e:
                if tiers[ti].get("extra") and e.status in (400, 404, 415, 422) and fallback("%s cannot be used here" % tiers[ti]["model"]):
                    continue
                if e.status == 401 and next_provider("%s rejected the key" % plabel):
                    continue
                if is_limit_error(e):
                    m = tiers[ti]["model"]
                    if e.status in (429, 503) and not out_of_credit(e):
                        until = note_limit(pid, plabel, m, e.msg)
                        run.emit("limit", provider=plabel, model=m, until=until, kind=LIMIT_UNTIL[pid + ":" + m]["kind"])
                    if out_of_credit(e):
                        if next_provider("%s is out of credit" % plabel):
                            continue
                    else:
                        why = ("rate limited on %s" if e.status == 429 else "%s is overloaded" if e.status == 503 else "%s is not available") % m
                        if fallback(why) or next_provider(why) or wait_for_limits():
                            continue
                    e.msg += " (no other connected AI could take over. Add another free provider in the AI menu, or wait for the limit to reset.)"
                if work_budget > 0 and not run.stop and (work_budget - (time.time() - t_start)) > 120 and tr["n"] < 12 and (e.status in (0, 408, 500, 502, 504) or "timed out" in (e.msg or "").lower() or "connection" in (e.msg or "").lower()):
                    tr["n"] += 1
                    run.emit("status", persist=False, text="Network or server hiccup (%s). Retrying in 20 s, the working time is not over" % (e.msg or e.status)[:80])
                    for _ in range(40):
                        if run.stop:
                            break
                        time.sleep(0.5)
                    continue
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
                last_text = am["content"].strip()
                run.emit("text", text=am["content"], tier=ti, model=tiers[ti]["model"], name=tiers[ti]["name"], cost=cost, provider=plabel)
            if not calls:
                # safety net: never finish while the page the AI built still crashes in the browser
                pe = getattr(run, "page_errors", None)
                if pe and getattr(run, "gate_count", 0) < 2 and not run.stop:
                    run.gate_count = getattr(run, "gate_count", 0) + 1
                    run.emit("status", persist=False, text="Aqua found errors in the page, asking the AI to fix them")
                    messages.append({"role": "user", "content": "[aqua check] You said you are done, but the page still fails in the browser:\n- " + "\n- ".join(pe[:6]) +
                                     "\nThis is the real error. Find the cause with search/read_file and fix it (do not change unrelated things), then call look_page to confirm before you finish."})
                    continue
                if brief and cfg.get("review_gate", True) and getattr(run, "review_n", 0) < 2 and acts["files"] and not run.stop:
                    run.review_n = getattr(run, "review_n", 0) + 1
                    run.emit("status", persist=False, text="A stronger model is reviewing the result against the brief")
                    issues = review_work(run, cfg, pid, tiers, top, brief, cfg["workspace"], last_text)
                    run.emit("review", ok=not issues, issues=issues)
                    if issues:
                        messages.append({"role": "user", "content": "[aqua review] A stronger reviewer checked your result against the brief and found real problems:\n- " + "\n- ".join(issues) +
                                         "\nFix these exactly with real edits (edit_file), keep everything that works, then verify with look_page."})
                        steps_here = errs = 0
                        continue
                left = work_budget - (time.time() - t_start)
                if work_budget > 0 and left > 45 and not run.stop and steps < max_steps_eff - 3:
                    if push["n"] > 0:
                        push["idle"] = push["idle"] + 1 if push["writes"] == push["w_at"] else 0
                    if True:                                                # forced: keep going until the working time is over or the user presses Stop
                        if push["idle"] >= 1:
                            for _ in range(min(60, 10 * push["idle"]) * 2):  # gentle pace so free limits are not burned by instant answers
                                if run.stop:
                                    break
                                time.sleep(0.5)
                            if run.stop:
                                break
                        push["n"] += 1
                        push["w_at"] = push["writes"]
                        focus = KEEP_FOCI[(push["n"] - 1) % len(KEEP_FOCI)]
                        if push["idle"] >= 1:
                            focus = "you changed NOTHING last round, which is not acceptable. Pick the single biggest missing feature, section, level, animation or polish item and implement it now with real edits (write_file/edit_file), then look at it"
                        run.emit("keepgoing", round=push["n"], minutes_left=int(left // 60), level=lvl, focus=focus)
                        messages.append({"role": "user", "content": "[aqua keep going] The user picked the %s working level: they want the BEST result, not the fastest. About %d minutes of working time are left. "
                                         "Do not stop at good enough. Round %d focus: %s. The original request was: %s\n"
                                         "First look at the real result (look_page, look_3d, run it, run the tests), list 3 concrete weaknesses, then fix them with real edits. "
                                         "Do NOT give a final summary yet: keep making real improvements until the working time is over (a final summary is only for the very last round)." % (
                                             {"balanced": "Balanced", "high": "Max"}.get(lvl, lvl), int(left // 60), push["n"], focus, user_text.strip()[:300])})
                        steps_here = errs = 0
                        continue
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
                if name in WRITE_TOOLS and not err:
                    push["writes"] += 1
                if name in WRITE_TOOLS and not err and args.get("path") and args["path"] not in acts["files"]:
                    acts["files"].append(args["path"])
                if name in ("run_command", "start_server"):
                    acts["cmds"].append("%s (%s)" % (str(args.get("command", ""))[:70], "failed" if err else "ok"))
                if err and name != "add_note" and len(acts["errors"]) < 4:
                    acts["errors"].append("%s: %s" % (name, out.strip().split("\n")[0][:110]))
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
                run.emit("error", text="Stopped after %d steps (max_steps)." % max_steps_eff)
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
    if acts["files"] or acts["cmds"] or acts["errors"] or last_text:
        lines = ["Request: " + user_text.strip().replace("\n", " ")[:240], "Worked on by: " + " -> ".join(acts["ais"] or [plabel])]
        if acts["files"]:
            lines.append("Files written or edited: " + ", ".join(acts["files"][:12]))
        if acts["cmds"]:
            lines.append("Commands: " + "; ".join(acts["cmds"][:6]))
        if acts["errors"]:
            lines.append("Problems met: " + " | ".join(acts["errors"]))
        lines.append("Outcome: %s. %s" % (status, last_text.replace("\n", " ")[:300]))
        append_note(cfg["workspace"], "Aqua (automatic log)", "\n".join(lines))
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
        if sess is not None and sess.get("workspace") != ws_key(load_cfg()["workspace"]):
            sess = None                      # a chat from another project is never continued here
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


def nokey_enable():
    """Switch on every provider that needs no key and make the first one the active AI. Returns its id (or None)."""
    first = None
    with _lock:
        cfg = load_cfg()
        for k, v in cfg["providers"].items():
            if v.get("nokey"):
                v["enabled"] = True
                first = first or k
        if first:
            cfg["provider"] = first
            save_cfg(cfg)
    return first


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


PROBE_HTML = r"""<!doctype html><meta charset=utf-8><body style="margin:0;background:#3a3d44;overflow:hidden"><iframe id=f style="border:0;display:block;background:#fff"></iframe><pre id=rep style="font-size:1px;position:fixed;left:0;bottom:0;opacity:0"></pre>
<script>
const P=new URLSearchParams(location.search),W=+P.get('w')||1280,H=+P.get('h')||800,WAIT=Math.min(+P.get('wait')||2500,15000),f=document.getElementById('f');
f.style.width=W+'px';f.style.height=H+'px';f.src=P.get('u')||'/index.html';
f.onload=()=>setTimeout(()=>{const R=document.getElementById('rep');try{
  const d=f.contentDocument,win=f.contentWindow,de=d.documentElement,o={title:d.title,text:(d.body?d.body.innerText:'').replace(/\s+/g,' ').slice(0,700),cw:de.clientWidth,sw:de.scrollWidth,sh:de.scrollHeight,bad:[],small:0};
  d.querySelectorAll('body *').forEach(e=>{const cs=win.getComputedStyle(e);if(cs.display==='none'||cs.position==='fixed'||cs.visibility==='hidden')return;const r=e.getBoundingClientRect();if(r.width<1||r.height<1)return;
    const own=[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim().length>1);
    if(own&&e.offsetParent!==null){let a=e.parentElement;while(a&&a!==d.body&&a!==de){const ox=win.getComputedStyle(a).overflowX;if(/(auto|scroll)/.test(ox))break;
        if(/(hidden|clip)/.test(ox)){const ar=a.getBoundingClientRect();if(r.right>ar.right+3&&ar.right>0){o.bad.push('text "'+e.textContent.trim().slice(0,28)+'" in <'+e.tagName.toLowerCase()+'> is cut off at the right edge by its container (overflow hidden)')}break}a=a.parentElement}}
    if(r.right>o.cw+2||r.left<-2){let a=e.parentElement,clip=false;while(a&&a!==d.body&&a!==de){if(/(hidden|auto|scroll|clip)/.test(win.getComputedStyle(a).overflowX)){clip=true;break}a=a.parentElement}
      if(!clip)o.bad.push(e.tagName.toLowerCase()+(e.id?'#'+e.id:'')+(typeof e.className==='string'&&e.className.trim()?'.'+e.className.trim().split(/\s+/)[0]:'')+' sticks out to x='+Math.round(r.right)+' (screen is '+o.cw+' wide)')}
    if(e.childNodes.length&&[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())&&parseFloat(cs.fontSize)<11)o.small++});
  o.bad=o.bad.slice(0,8);R.textContent=JSON.stringify(o)}catch(e){R.textContent=JSON.stringify({err:String(e)})}},WAIT);
</script>"""


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
            if u.path == "/__aqua/probe":
                return self._send(200, PROBE_HTML, "text/html; charset=utf-8")
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
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
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
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
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


# --------------------------------------------------------------------------- 3D: make models, find free models, look at them
ASSETS_3D = os.path.join(ROOT, "assets", "3d")
MODEL_HOSTS = ("raw.githubusercontent.com", "github.com", "objects.githubusercontent.com", "threejs.org", "cdn.jsdelivr.net", "modelviewer.dev")
MODEL_EXT = (".glb", ".gltf", ".obj", ".stl")
_khr = {"t": 0.0, "data": []}
THREEJS_MODELS = [("Soldier", "Soldier.glb", "animated soldier character"), ("Horse", "Horse.glb", "animated horse"), ("Flamingo", "Flamingo.glb", "animated flamingo bird"),
                  ("Parrot", "Parrot.glb", "animated parrot bird"), ("Stork", "Stork.glb", "animated stork bird"), ("RobotExpressive", "RobotExpressive/RobotExpressive.glb", "animated cartoon robot"),
                  ("LittlestTokyo", "LittlestTokyo.glb", "tiny Tokyo city diorama"), ("Xbot", "Xbot.glb", "humanoid robot character")]
THREED_PROMPT = (
    "\n\n3D MODELING. You can really make 3D models and use free ready-made ones. Workflow: "
    "(1) call scaffold_3d once: it puts a 3D kit in the workspace (3d/aqua3d.py, 3d/viewer.html, 3d/example.py, 3d/README.md, models/). "
    "(2) read_file 3d/README.md and 3d/example.py first. "
    "(3) write a Python script that does `import sys; sys.path.insert(0, '3d'); from aqua3d import Scene`, builds the model from MANY well-proportioned parts "
    "(box, sphere, cylinder, cone, torus, extrude, lathe, groups) with a small colour palette, and saves models/<name>.glb (also .obj/.stl if asked); run it with run_command from the workspace root. "
    "PATHS: run_command runs in the workspace root, so a script in the root saves to 'models/x.glb' (NOT '../models'); only a script inside 3d/ run from there uses '../models/x.glb'. "
    "(4) LOOK at your work: call s.check() in the script, then look_3d on the file (4 rendered views plus a text report with floating parts and ASCII silhouettes; vision models also get the picture). Fix every problem and look again, up to 3 rounds, then call show_3d so the user sees it. "
    "SPEED: never use Blender or install anything; aqua3d is enough and builds 1000+ parts in under a second. For scenes import ready parts: `from aqua_parts import tree, pine, rock, house, tower, fence, lamp_post, car, table, chair, bench, bridge, windmill` and use loops with Scene.grid(nx, nz, dx, dz) / Scene.ring(n, radius); build a part once as its own Scene and reuse it with s.use(other, pos=...). Several models: write one script that saves them all. "
    "(5) for complex organic or realistic things (animals, people, cars, furniture sets) do NOT model them from scratch: call search_3d_models, then download_3d_model, and tell the user the licence to check. "
    "(6) for interactive 3D web pages (games, product viewers, scenes) write HTML that imports three.js from the local copy "
    "(<script type=\"importmap\">{\"imports\":{\"three\":\"./3d/vendor/three.module.min.js\",\"three/addons/\":\"./3d/vendor/addons/\"}}</script>) and show it in the Preview. "
    "Quality matters: real proportions, details, a consistent style, and tell the user honestly what you made.")
_3D_WORDS = re.compile(r"\b(3d|3-d|three\.?js|webgl|glb|gltf|stl|blender|mesh|meshes|model(?:l?ing)|sculpt|voxel|low[- ]?poly)\b", re.I)


def wants_3d(messages, ws):
    if os.path.isfile(os.path.join(ws, "3d", "aqua3d.py")):
        return True
    users = [m.get("content") or "" for m in messages if m.get("role") == "user"][-4:]
    return any(_3D_WORDS.search(u) for u in users if isinstance(u, str))


def _http_bytes(url, limit=40 * 1024 * 1024, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "aqua-code/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        host = (urlparse(r.geturl()).hostname or "").lower()
        if host not in MODEL_HOSTS and not host.endswith(".githubusercontent.com"):
            raise ToolErr("The download was redirected to a site that is not allowed: %s" % host)
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ToolErr("That file is bigger than %d MB." % (limit // (1024 * 1024)))
    return data


def tool_scaffold_3d(ws):
    if not os.path.isdir(ASSETS_3D):
        raise ToolErr("The 3D kit is missing from this Aqua install (assets/3d).")
    dst, new = os.path.join(ws, "3d"), 0
    for root, dirs, files in os.walk(ASSETS_3D):
        rel = os.path.relpath(root, ASSETS_3D)
        for f in files:
            out = os.path.join(dst, "" if rel == "." else rel, f)
            if os.path.exists(out):
                continue                                   # never overwrite the user's or the AI's edits
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(os.path.join(root, f), out)
            new += 1
    os.makedirs(os.path.join(ws, "models"), exist_ok=True)
    return ("3D kit ready (%d files added) in 3d/. Read 3d/README.md and 3d/example.py, then write a script using aqua3d and save to models/<name>.glb. "
            "Run `python 3d/example.py` to see a finished house. Show models with show_3d." % new)


def tool_show_3d(run, ws, a):
    rel = str(a.get("path") or "").replace("\\", "/").lstrip("/")
    p = safe_path(ws, rel)
    if not os.path.isfile(p) or not rel.lower().endswith(MODEL_EXT):
        raise ToolErr("No 3D file at %s (expected .glb, .gltf, .obj or .stl)." % rel)
    if not os.path.isfile(os.path.join(ws, "3d", "viewer.html")):
        tool_scaffold_3d(ws)
    if not PREVIEW_PORT:
        raise ToolErr("The preview server is not running.")
    url = "http://127.0.0.1:%d/3d/viewer.html?m=../%s" % (PREVIEW_PORT, rel)
    run.emit("preview", url=url, cmd="3D viewer", auto=False)
    return "Opened %s in the Preview pane (orbit with the mouse, wireframe and auto-rotate buttons)." % rel


def _find_chromium():
    cands = []
    if os.name == "nt":
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    else:
        for n in ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser"):
            p = shutil.which(n)
            if p:
                cands.append(p)
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser", "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    return next((c for c in cands if os.path.exists(c)), None)


def _headless(exe, url, extra, timeout=60, size="1200,900", errs=False, budget=25000):
    prof = os.path.join(DATA, "headless-profile")
    os.makedirs(prof, exist_ok=True)
    args = [exe, "--headless=new", "--enable-unsafe-swiftshader", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
            "--user-data-dir=" + prof, "--window-size=" + size, "--virtual-time-budget=%d" % budget] + (["--enable-logging=stderr", "--v=0"] if errs else []) + extra + [url]
    kw = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE if errs else subprocess.DEVNULL, "stdin": subprocess.DEVNULL, "timeout": timeout}
    if os.name == "nt":
        kw["creationflags"] = 0x00004000 | 0x08000000          # below-normal priority, no console window
    r = subprocess.run(args, **kw)
    out = r.stdout.decode("utf-8", "replace")
    return (out, r.stderr.decode("utf-8", "replace")) if errs else out


ASSETS_RECIPES = os.path.join(ROOT, "assets", "recipes")
RECIPES = {
    "shader-jelly": ("shader-jelly.html", "Full-screen WebGL ray-marched wobbling jelly / slime cube: spring wobble, refraction, subsurface glow, mouse squish, Space shockwave. No libraries. For slime, jelly, liquid, glass, blob and shader games."),
    "three-scene": ("three-scene.html", "Three.js scene (offline three.js r160): lights, shadows, orbit controls, animated knot, spheres, floor. For any 3D scene, model viewer or 3D game base. Copies the 3d/ folder too."),
    "canvas-game": ("canvas-game.html", "2D canvas action game: player, auto-fire, enemies, difficulty ramp, score, best score, game over, restart, keyboard + mouse + touch. For any arcade game."),
}


def tool_recipes(ws, a):
    name = str(a.get("name") or "").strip().lower()
    if not name:
        return "Tested starter recipes (copy one, then adapt it to the brief):\n" + "\n".join("- %s: %s" % (k, v[1]) for k, v in RECIPES.items()) + "\nCall recipes with name=<one of these> to copy it."
    if name not in RECIPES:                                    # forgiving: "slime", "3d", "game" find the right recipe
        words = [w for w in re.split(r"\W+", name) if len(w) > 2]
        hits = [k for k, v in RECIPES.items() if any(w in (k + " " + v[1]).lower() for w in words)]
        if len(hits) >= 1:
            name = hits[0]
        else:
            raise ToolErr("No recipe called %s. Available: %s" % (name, ", ".join(RECIPES)))
    rel = str(a.get("path") or "index.html").replace("\\", "/").lstrip("/")
    dst = safe_path(ws, rel)
    if os.path.exists(dst) and os.path.getsize(dst) > 0 and not a.get("overwrite"):
        raise ToolErr("%s already exists. Use another path, or overwrite=true if you are sure." % rel)
    src = os.path.join(ASSETS_RECIPES, RECIPES[name][0])
    if not os.path.isfile(src):
        raise ToolErr("The recipe file is missing from this Aqua install.")
    os.makedirs(os.path.dirname(dst) or ws, exist_ok=True)
    shutil.copy2(src, dst)
    extra = ""
    if name == "three-scene":
        tool_scaffold_3d(ws)
        extra = " The 3d/ folder (three.js) was added too."
    return "Copied recipe %s to %s (it is tested and works as is).%s Now adapt it step by step to the brief with edit_file (change the look, the rules, the content), and run look_page after each change." % (name, rel, extra)


def png_flat_share(path):
    """How much of a screenshot is ONE flat colour (a black or blank canvas shows up as ~90%+). Returns (share, hex) or None."""
    import struct, zlib
    try:
        d = open(path, "rb").read()
        pos, idat, w, h, ct = 8, b"", 0, 0, 2
        while pos < len(d):
            n, tp = struct.unpack(">I4s", d[pos:pos + 8])
            body = d[pos + 8:pos + 8 + n]
            pos += 12 + n
            if tp == b"IHDR":
                w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", body)
                if bd != 8 or il or ct not in (2, 6):
                    return None
            elif tp == b"IDAT":
                idat += body
        ch = 3 if ct == 2 else 4
        raw = zlib.decompress(idat)
        stride = w * ch
        prev = bytearray(stride)
        counts = {}
        o = 0
        for y in range(h):
            f = raw[o]
            line = bytearray(raw[o + 1:o + 1 + stride])
            o += 1 + stride
            if f == 1:
                for i in range(ch, stride):
                    line[i] = (line[i] + line[i - ch]) & 255
            elif f == 2:
                line = bytearray((x + p) & 255 for x, p in zip(line, prev))
            elif f == 3:
                for i in range(stride):
                    a_ = line[i - ch] if i >= ch else 0
                    line[i] = (line[i] + ((a_ + prev[i]) >> 1)) & 255
            elif f == 4:
                for i in range(stride):
                    a_ = line[i - ch] if i >= ch else 0
                    b_ = prev[i]
                    c_ = prev[i - ch] if i >= ch else 0
                    p_ = a_ + b_ - c_
                    pa, pb, pc = abs(p_ - a_), abs(p_ - b_), abs(p_ - c_)
                    line[i] = (line[i] + (a_ if pa <= pb and pa <= pc else b_ if pb <= pc else c_)) & 255
            prev = line
            if y % 3 == 0:
                for x in range(0, w, 3):
                    k = (line[x * ch] >> 4, line[x * ch + 1] >> 4, line[x * ch + 2] >> 4)
                    counts[k] = counts.get(k, 0) + 1
        tot = sum(counts.values()) or 1
        k, c = max(counts.items(), key=lambda kv: kv[1])
        return c / tot, "#%x%x%x" % k
    except Exception:
        return None


def _parse_console(err):
    msgs = []
    for line in err.splitlines():
        m = re.search(r'CONSOLE[^\]]*\]\s*"(.*)", source: (\S+) \((\d+)\)', line)
        if m and "rollup" not in m.group(2) and "ProtocolLaunch" not in m.group(1) and "Tracking Prevention" not in m.group(1) and "should not be used in production" not in m.group(1):
            lvl = "ERROR" if re.search(r":ERROR:|Uncaught|TypeError|ReferenceError|SyntaxError|Failed to load|404|net::", line + m.group(1)) else "log"
            msgs.append((lvl, "%s (%s:%s)" % (m.group(1)[:220], m.group(2).split("/")[-1][:40], m.group(3))))
    return msgs


_JS_EXT = (".html", ".htm", ".js", ".mjs", ".jsx", ".css")


def lost_definitions(ws, a):
    """edit_file removed a 'const x = / let x / function x' but x is still used elsewhere: the classic cause of 'it was working, now it is blank'"""
    old, new, rel = str(a.get("old") or ""), str(a.get("new") or ""), str(a.get("path") or "")
    if not rel.lower().endswith((".html", ".htm", ".js", ".mjs", ".jsx", ".ts", ".tsx")):
        return ""
    try:
        txt = open(safe_path(ws, rel), encoding="utf-8", errors="replace").read()
    except Exception:
        return ""
    lost = {}
    for m in re.finditer(r"\b(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)", old):
        n = m.group(1)
        if n in lost or re.search(r"\b(?:const|let|var|function|class)\s+%s\b" % re.escape(n), txt):
            continue                                       # still defined somewhere in the file
        uses = [i + 1 for i, line in enumerate(txt.splitlines()) if re.search(r"(?<![\w$.])%s\b" % re.escape(n), line)]
        if uses:
            lost[n] = uses[:6]
    if not lost:
        return ""
    return ("\n[aqua check] WARNING: this edit deleted the definition of a name that other code still uses, which will crash the page:\n- " +
            "\n- ".join("%s (used on line %s)" % (n, ", ".join(str(x) for x in u)) for n, u in list(lost.items())[:6]) +
            "\nPut the definition back (or update the code that uses it) now.")


_auto = {"t": 0.0}


def auto_page_check(run, ws, rel):
    """After the AI writes a web file, load the page in a hidden browser and report real errors, so it fixes the real cause instead of guessing."""
    rel = rel.replace("\\", "/").lstrip("/")
    if not PREVIEW_PORT or not rel.lower().endswith(_JS_EXT) or rel.startswith(("3d/", ".aqua/", "node_modules/")):
        return ""
    page = rel if rel.lower().endswith((".html", ".htm")) else "index.html"
    if not os.path.isfile(os.path.join(ws, page)):
        return ""
    exe = _find_chromium()
    if not exe:
        return ""
    try:
        _, err = _headless(exe, "http://127.0.0.1:%d/%s" % (PREVIEW_PORT, page), ["--dump-dom"], timeout=40, size="1280,800", errs=True, budget=3500)
    except Exception:
        return ""
    errors = [t for l, t in _parse_console(err) if l == "ERROR"]
    run.page_errors = errors
    if not errors:
        return ""
    return "\n[aqua check] I opened %s in a browser right after your change and it has %d error(s):\n- %s\nThis is the real cause of what the user sees (often a blank or black page). Fix it first." % (page, len(errors), "\n- ".join(errors[:6]))


def tool_look_page(run, ws, a):
    """The AI looks at a web page it built (the Preview): screenshot + browser console errors + visible text."""
    import html as _html
    from urllib.parse import urlparse as _up
    u = str(a.get("url") or a.get("path") or "index.html").strip()
    if not re.match(r"^https?://", u, re.I):
        if not PREVIEW_PORT:
            raise ToolErr("The preview server is not running.")
        u = "http://127.0.0.1:%d/%s" % (PREVIEW_PORT, u.replace("\\", "/").lstrip("/"))
    host = (_up(u).hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ToolErr("look_page only opens pages on this computer (localhost / 127.0.0.1), such as the Preview or a dev server you started.")
    exe = _find_chromium()
    if not exe:
        raise ToolErr("look_page needs Edge, Chrome, Chromium or Brave installed; none was found.")
    mobile = bool(a.get("mobile"))
    try:
        w = int(a.get("width") or (390 if mobile else 1280))
        h = int(a.get("height") or (844 if mobile else 800))
    except Exception:
        w, h = (390, 844) if mobile else (1280, 800)
    w, h = max(240, min(w, 2400)), max(240, min(h, 3000))
    wait = max(500, min(int(a.get("wait_ms") or 2500), 15000))
    outdir = os.path.join(ws, NOTES_DIR, "look")
    os.makedirs(outdir, exist_ok=True)
    png = os.path.join(outdir, "page.png")
    if os.path.exists(png):
        os.remove(png)
    try:
        pu = _up(u)
        on_preview = bool(PREVIEW_PORT and pu.port == PREVIEW_PORT)
        if on_preview:
            from urllib.parse import quote as _q0
            shot_u = "http://127.0.0.1:%d/__aqua/probe?u=%s&w=%d&h=%d&wait=%d&shot=1" % (PREVIEW_PORT, _q0(pu.path + ("?" + pu.query if pu.query else "")), w, h, wait)
        else:
            shot_u = u
        # a headless window is never narrower than ~500px, so phone sizes are drawn inside a frame of exactly w x h
        _, err = _headless(exe, shot_u, ["--screenshot=" + png, "--hide-scrollbars"], timeout=60, size="%d,%d" % (max(w, 500) if on_preview else w, h), errs=True, budget=wait + (1500 if on_preview else 0))
        probe = None
        if PREVIEW_PORT and pu.port == PREVIEW_PORT:
            from urllib.parse import quote as _q
            pdom = _headless(exe, "http://127.0.0.1:%d/__aqua/probe?u=%s&w=%d&h=%d&wait=%d" % (PREVIEW_PORT, _q(pu.path + ("?" + pu.query if pu.query else "")), w, h, wait),
                             ["--dump-dom"], timeout=60, size="%d,%d" % (w, h), budget=wait + 4000)
            pm = re.search(r'<pre id="rep"[^>]*>(.*?)</pre>', pdom, re.S)
            if pm:
                try:
                    probe = json.loads(_html.unescape(pm.group(1)))
                except Exception:
                    probe = None
        dom = "" if probe and not probe.get("err") else _headless(exe, u, ["--dump-dom"], timeout=60, size="%d,%d" % (w, h), budget=wait)
    except subprocess.TimeoutExpired:
        raise ToolErr("The page did not finish loading in time (an endless loop or a server that does not answer?).")
    msgs = []
    for line in err.splitlines():
        m = re.search(r'CONSOLE[^\]]*\]\s*"(.*)", source: (\S+) \((\d+)\)', line)
        if m and "rollup" not in m.group(2) and "ProtocolLaunch" not in m.group(1) and "Tracking Prevention" not in m.group(1) and "should not be used in production" not in m.group(1):      # skip the browser's own noise
            lvl = "ERROR" if re.search(r":ERROR:|Uncaught|TypeError|ReferenceError|SyntaxError|Failed to load|404|net::", line + m.group(1)) else "log"
            msgs.append((lvl, "%s (%s:%s)" % (m.group(1)[:220], m.group(2).split("/")[-1][:40], m.group(3))))
    errors = [t for l, t in msgs if l == "ERROR"]
    run.page_errors = errors
    logs = [t for l, t in msgs if l != "ERROR"]
    layout = ""
    if probe and not probe.get("err"):
        text, ttl = probe.get("text") or "", probe.get("title") or "(none)"
        bad = probe.get("bad") or []
        if probe.get("sw", 0) > probe.get("cw", 0) + 2 or bad:
            layout = "LAYOUT PROBLEM: the page is wider than the %dpx screen (content reaches %spx)%s" % (
                probe.get("cw", w), probe.get("sw"), (":\n  - " + "\n  - ".join(bad)) if bad else ".")
            layout += "\n  Fix with max-width:100%, flex-wrap, smaller fixed widths or media queries, and do not hide it with overflow-x:hidden."
        if probe.get("small"):
            layout += ("\n" if layout else "") + "Small text: %d elements use a font size under 11px." % probe["small"]
    else:
        body = re.search(r"<body[^>]*>(.*)</body>", dom, re.S | re.I)
        text = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", " ", body.group(1) if body else dom, flags=re.S | re.I))).strip()
        t_ = re.search(r"<title[^>]*>(.*?)</title>", dom, re.S | re.I)
        ttl = _html.unescape(t_.group(1)).strip() if t_ else "(none)"
    out = ["LOOK at %s (%dx%d%s)" % (u, w, h, ", phone size" if mobile else ""),
           "Title: %s" % ttl,
           layout or "Layout: nothing sticks out of the screen.",
           "Console errors (%d): %s" % (len(errors), ("\n  - " + "\n  - ".join(errors[:12])) if errors else "none"),
           "Console messages: %s" % (("\n  - " + "\n  - ".join(logs[:6])) if logs else "none"),
           "Visible text (%d chars): %s" % (len(text), text[:700] or "(the page shows no text: it may be blank or broken)")]
    if os.path.isfile(png):
        try:
            with open(png, "rb") as f:
                run.look_img = base64.b64encode(f.read()).decode()
                run.look_text = "This is a screenshot of the page from look_page. Judge it honestly: layout, overlaps, cut-off text, wrong colours, empty areas. Fix what looks wrong."
        except Exception:
            pass
        out.append("A screenshot is saved at .aqua/look/page.png." + (" It is attached below if your model can see images." if getattr(run, "look_img", None) else ""))
    else:
        out.append("No screenshot could be taken.")
    fs = png_flat_share(png) if os.path.isfile(png) else None
    if fs and fs[0] > 0.98:
        out.append("Note: the screenshot is almost all one colour (%s). That is normal for a dark game, but if you expected a visible scene, check the camera, object sizes, the render loop and the console." % fs[1])
    out.append("Fix every console error and visual problem, then look again (at most 3 rounds). Check a phone size too with mobile=true.")
    return "\n".join(out)


def tool_look_3d(run, ws, a):
    import html as _html
    rel = str(a.get("path") or "").replace("\\", "/").lstrip("/")
    p = safe_path(ws, rel)
    if not os.path.isfile(p) or not rel.lower().endswith(MODEL_EXT):
        raise ToolErr("No 3D file at %s (expected .glb, .gltf, .obj or .stl). Run your script first and check the save path." % rel)
    vw = os.path.join(ws, "3d", "viewer.html")
    kit = os.path.join(ASSETS_3D, "viewer.html")
    if not os.path.isfile(vw):
        tool_scaffold_3d(ws)
    elif os.path.isfile(kit) and "sheet" not in open(vw, encoding="utf-8", errors="replace").read():
        shutil.copy2(kit, vw)                                  # old kit: upgrade the viewer so it can render the contact sheet
    if not PREVIEW_PORT:
        raise ToolErr("The preview server is not running.")
    exe = _find_chromium()
    if not exe:
        raise ToolErr("look_3d needs Edge, Chrome, Chromium or Brave installed; none was found. Use the size and part checks in s.report() instead.")
    base = "http://127.0.0.1:%d/3d/viewer.html?m=../%s" % (PREVIEW_PORT, rel)
    outdir = os.path.join(ws, NOTES_DIR, "look")
    os.makedirs(outdir, exist_ok=True)
    png = os.path.join(outdir, os.path.splitext(os.path.basename(rel))[0] + ".png")
    if os.path.exists(png):
        os.remove(png)
    try:
        dom = _headless(exe, base + "&report=1", ["--dump-dom"])
        _headless(exe, base + "&sheet=1", ["--screenshot=" + png])
    except subprocess.TimeoutExpired:
        raise ToolErr("The headless render timed out. Try a simpler model or look at it with show_3d.")
    m = re.search(r'<pre id="rep"[^>]*>(.*?)</pre>', dom, re.S)
    report = _html.unescape(m.group(1)).strip() if m else ""
    if not report:
        raise ToolErr("Could not load %s in the renderer (is the file valid? was it saved to the right folder?)." % rel)
    out = "LOOK at %s\n%s\n" % (rel, report)
    if os.path.isfile(png):
        try:
            with open(png, "rb") as f:
                run.look_img = base64.b64encode(f.read()).decode()
                run.look_text = None
        except Exception:
            pass
        out += "\nA picture with 4 views is saved at .aqua/look/%s." % os.path.basename(png)
        out += " It is attached below if your model can see images." if getattr(run, "look_img", None) else ""
    out += ("\nHow to read this: the ASCII views are a rough silhouette (darker characters = brighter surface). Check proportions, that every part is where it should be, "
            "that nothing floats or sinks, and that the shape is recognisable. Fix problems in your script and look again (at most 3 rounds).")
    return out


def _khronos():
    if not _khr["data"] or time.time() - _khr["t"] > 3600:
        req = urllib.request.Request("https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/model-index.json", headers={"User-Agent": "aqua-code/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            _khr["data"] = json.loads(r.read().decode("utf-8"))
        _khr["t"] = time.time()
    return _khr["data"]


def tool_search_3d(a):
    q = [w for w in re.split(r"\W+", str(a.get("query") or "").lower()) if w]
    rows = []
    try:
        for m in _khronos():
            glb = (m.get("variants") or {}).get("glTF-Binary")
            if not glb:
                continue
            hay = (m.get("name", "") + " " + m.get("label", "") + " " + " ".join(m.get("tags", []))).lower()
            sc = sum(1 for w in q if w in hay)
            if sc or not q:
                rows.append((sc, "%s (%s) [%s]\n   %s  - Khronos glTF sample asset, check its licence file in the repo" % (
                    m.get("label") or m["name"], m["name"], ", ".join(m.get("tags", [])[:4]),
                    "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/%s/glTF-Binary/%s" % (m["name"], glb))))
    except Exception as e:
        rows.append((0, "(Khronos list unavailable: %s)" % e))
    for name, path, desc in THREEJS_MODELS:
        hay = (name + " " + desc).lower()
        sc = sum(1 for w in q if w in hay)
        if sc or not q:
            rows.append((sc, "%s - %s\n   https://threejs.org/examples/models/gltf/%s  - three.js example model, licence varies (check three.js)" % (name, desc, path)))
    rows.sort(key=lambda r: -r[0])
    if not rows:
        return "No free model matched. Try simple words such as chair, car, helmet, lantern, duck, robot, tree, bottle, boat. Or build it with aqua3d."
    return "Free 3D models (pick one, then call download_3d_model with its URL):\n" + "\n".join(r[1] for r in rows[:12])


def tool_download_3d(ws, a):
    url = str(a.get("url") or "").strip()
    pu = urlparse(url)
    if pu.scheme != "https" or (pu.hostname or "").lower() not in MODEL_HOSTS:
        raise ToolErr("Only https downloads from these sites are allowed: %s" % ", ".join(MODEL_HOSTS))
    name = re.sub(r"[^\w.\- ]", "_", str(a.get("name") or os.path.basename(pu.path) or "model.glb"))
    if not name.lower().endswith(MODEL_EXT):
        raise ToolErr("Only %s files can be downloaded." % ", ".join(MODEL_EXT))
    data = _http_bytes(url)
    dest = safe_path(ws, "models/" + name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    mp = os.path.join(os.path.dirname(dest), "models.json")
    try:
        with open(mp, "r", encoding="utf-8") as f:
            man = json.load(f)
    except Exception:
        man = []
    man = [m for m in man if m.get("file") != name] + [{"file": name, "source": url}]
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1)
    return "Downloaded %s (%.1f MB) to models/%s. Call show_3d to look at it. Tell the user the source and that its licence must be checked before reuse." % (name, len(data) / 1e6, name)


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
            if u.path == "/api/projects":
                return self._send(200, {"projects": list_projects()})
            if u.path == "/api/notes":
                return self._send(200, {"notes": read_notes(load_cfg()["workspace"], 60000), "path": notes_path(load_cfg()["workspace"])})
            if u.path == "/api/servers":
                return self._send(200, {"servers": servers_list()})
            if u.path == "/api/models":
                return self._send(200, list_models(q.get("provider")))
            if u.path == "/api/state":
                cfg = load_cfg()
                lst = sorted(({"id": s["id"], "title": s["title"], "created": s["created"], "cost": s.get("cost", 0.0)}
                              for s in sessions.values() if s.get("workspace") == ws_key(cfg["workspace"])), key=lambda s: -s["created"])
                active = {r.sess["id"]: r.id for r in runs.values() if not r.done}
                return self._send(200, {"config": masked_cfg(cfg), "usage": usage_snapshot(cfg), "sessions": lst, "preview_port": PREVIEW_PORT, "platform": sys.platform, "machine": platform.machine(), "level": cfg["level"], "cores": os.cpu_count() or 1, "core_cap": _level_cores(cfg["level"]),
                                        "active": active, "budget": budget_state(cfg)})
            if u.path == "/api/session":
                s = sessions.get(q.get("id"))
                if not s or s.get("workspace") != ws_key(load_cfg()["workspace"]):
                    return self._send(404, {"error": "no such chat in this project"})
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
                if "answers" in b:
                    p["answers"] = b.get("answers") if isinstance(b.get("answers"), list) else None
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
            if u.path == "/api/notes/import":
                src = os.path.join(str(b.get("path") or "").strip().strip('"'), NOTES_DIR, "NOTES.md")
                if not os.path.isfile(src):
                    return self._send(200, {"ok": False, "error": "No project notes found in that folder (looked for %s)." % src})
                with open(src, "r", encoding="utf-8", errors="replace") as f:
                    body = f.read()[-4000:]
                append_note(load_cfg()["workspace"], "Aqua (imported because the user asked)", "Notes copied from another project, %s:\n%s" % (b.get("path"), body))
                return self._send(200, {"ok": True})
            if u.path == "/api/notes/clear":
                try:
                    os.remove(notes_path(load_cfg()["workspace"]))
                except OSError:
                    pass
                return self._send(200, {"ok": True})
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
            if u.path == "/api/nokey/enable":
                first = nokey_enable()
                if not first:
                    return self._send(200, {"ok": False, "error": "No anonymous model is available."})
                return self._send(200, {"ok": True, "config": masked_cfg(load_cfg())})
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
            if u.path == "/api/project/new":
                if runs and any(not r.done for r in runs.values()):
                    return self._send(409, {"error": "Wait for the running task to finish (or press Stop) before changing project."})
                cfg = apply_config_update({"workspace": new_project(b.get("name"))})
                return self._send(200, {"config": masked_cfg(cfg), "usage": usage_snapshot(cfg), "projects": list_projects()})
            if u.path == "/api/project/switch":
                if any(not r.done for r in runs.values()):
                    return self._send(409, {"error": "Wait for the running task to finish (or press Stop) before changing project."})
                tgt = str(b.get("path") or "")
                if not any(os.path.normcase(os.path.realpath(x["path"])) == os.path.normcase(os.path.realpath(tgt)) for x in list_projects()):
                    return self._send(400, {"error": "Unknown project"})
                cfg = apply_config_update({"workspace": tgt})
                return self._send(200, {"config": masked_cfg(cfg), "usage": usage_snapshot(cfg), "projects": list_projects()})
            if u.path == "/api/session/delete":
                with _lock:
                    if b.get("all"):
                        wk = ws_key(load_cfg()["workspace"])
                        ids = [k for k, v in sessions.items() if v.get("workspace") == wk]
                    else:
                        ids = [b.get("id")] if b.get("id") else []
                    for r in list(runs.values()):
                        if r.sess.get("id") in ids and not r.done:
                            r.stop = True                       # a chat that is still working is stopped, then deleted
                    for i in ids:
                        sessions.pop(i, None)
                save_sessions()
                return self._send(200, {"ok": True, "deleted": len(ids)})
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


try:
    apply_level(load_cfg().get("level", "balanced"))
except Exception:
    pass


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
