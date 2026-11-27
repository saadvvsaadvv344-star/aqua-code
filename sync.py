"""Aqua Sync: keeps your projects, chats and settings identical on every computer you link (PC, Mac) through YOUR OWN free Firebase project.

* Only Python's standard library, HTTPS to Google (securetoken + Firestore REST). No installer, no extra program, no startup entry.
* Files are compressed and stored as small Firestore documents (no Cloud Storage, so no card). Big files (over 8 MB) stay local.
* A Google sign-in on your own website links this computer; the Firestore rules let only your Google account read or write.
* Your API keys leave this computer only if you set a sync passphrase, and then only encrypted with it (scrypt + HMAC).
* Nothing is deleted for good: a file removed elsewhere is moved to data/sync_trash/, a conflict keeps both versions.
"""
import base64
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

FILES, CHUNKS, CHATS, META = "aqua_files", "aqua_chunks", "aqua_chats", "aqua_meta"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", ".gradle", ".pytest_cache", ".idea"}
SKIP_FILES = {".ds_store", "thumbs.db", "desktop.ini"}
SKIP_EXT = (".pyc", ".tmp", ".swp", ".lock")
MAX_FILE = 8 * 1024 * 1024
CHUNK_B64 = 700000                 # characters per Firestore document (the limit is about 1 MB)
OVERLAP_MS = 120000                # re-read the last two minutes on every pull: clocks differ, nothing is missed
MAX_UPLOADS_PER_CYCLE = 40         # keeps the free daily write quota safe
MAX_CHAT_BYTES = 6 * 1024 * 1024


class SyncError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


class Conflict(Exception):
    pass


# ----------------------------------------------------------------------------- encryption for the API keys (standard library only)
def _keys(passphrase, salt):
    k = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=64, maxmem=64 * 1024 * 1024)
    return k[:32], k[32:]


def _stream(enc_key, nonce, n):
    out, i = bytearray(), 0
    while len(out) < n:                                   # HMAC-SHA256 as a pseudo-random function in counter mode
        out += hmac.new(enc_key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def seal(passphrase, plaintext):
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
    ek, mk = _keys(passphrase, salt)
    ct = bytes(a ^ b for a, b in zip(plaintext, _stream(ek, nonce, len(plaintext))))
    tag = hmac.new(mk, b"aqua-sync-1" + salt + nonce + ct, hashlib.sha256).digest()
    b = lambda x: base64.b64encode(x).decode("ascii")
    return {"salt": b(salt), "nonce": b(nonce), "ct": b(ct), "tag": b(tag)}


def unseal(passphrase, box):
    d = lambda k: base64.b64decode(box[k])
    salt, nonce, ct, tag = d("salt"), d("nonce"), d("ct"), d("tag")
    ek, mk = _keys(passphrase, salt)
    if not hmac.compare_digest(tag, hmac.new(mk, b"aqua-sync-1" + salt + nonce + ct, hashlib.sha256).digest()):
        raise ValueError("Wrong passphrase, or the data was changed.")
    return bytes(a ^ b for a, b in zip(ct, _stream(ek, nonce, len(ct))))


# ----------------------------------------------------------------------------- Firestore REST
def fs_value(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: fs_value(x) for k, x in v.items()}}}
    return {"arrayValue": {"values": [fs_value(x) for x in v]}}


def fs_decode(v):
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "booleanValue" in v:
        return v["booleanValue"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "mapValue" in v:
        return {k: fs_decode(x) for k, x in (v["mapValue"].get("fields") or {}).items()}
    if "arrayValue" in v:
        return [fs_decode(x) for x in (v["arrayValue"].get("values") or [])]
    return None


class Firestore:
    def __init__(self, project, api_key, refresh_token, on_token=None, base=None, token_url=None):
        self.project, self.api_key, self.refresh_token, self.on_token = project, api_key, refresh_token, on_token
        self.base = (base or "https://firestore.googleapis.com/v1").rstrip("/")
        self.token_url = token_url or "https://securetoken.googleapis.com/v1/token"
        self._id, self._exp = None, 0.0

    def _auth(self):
        if self._id and time.time() < self._exp - 60:
            return self._id
        data = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": self.refresh_token}).encode()
        req = urllib.request.Request(self.token_url + "?key=" + urllib.parse.quote(self.api_key), data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "aqua-code-sync/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise SyncError(e.code, "Google refused the saved sign-in (%s). Link this computer again." % e.code)
        except Exception as e:
            raise SyncError(0, "Cannot reach Google: %s" % e)
        self._id = j["id_token"]
        self._exp = time.time() + int(j.get("expires_in", 3600))
        if j.get("refresh_token") and j["refresh_token"] != self.refresh_token:
            self.refresh_token = j["refresh_token"]
            if self.on_token:
                self.on_token(self.refresh_token)
        return self._id

    def _req(self, method, url, body=None):
        last = None
        for attempt in range(4):
            req = urllib.request.Request(url, method=method, data=json.dumps(body).encode("utf-8") if body is not None else None, headers={
                "Authorization": "Bearer " + self._auth(), "Content-Type": "application/json", "User-Agent": "aqua-code-sync/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=40) as r:
                    raw = r.read().decode("utf-8")
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                txt = ""
                try:
                    txt = e.read().decode("utf-8", "replace")
                except Exception:
                    pass
                if e.code == 404:
                    return None
                if e.code in (409, 412) or (e.code == 400 and re.search(r"FAILED_PRECONDITION|ALREADY_EXISTS|ABORTED", txt)):
                    raise Conflict(txt[:200])
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    last = SyncError(e.code, txt[:200])
                    continue
                raise SyncError(e.code, re.sub(r"\s+", " ", txt)[:300] or str(e))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = SyncError(0, "Network problem: %s" % getattr(e, "reason", e))
                time.sleep(1.5 * (attempt + 1))
        raise last or SyncError(0, "Network problem")

    def _doc_url(self, coll, doc):
        return "%s/projects/%s/databases/(default)/documents/%s/%s" % (self.base, self.project, coll, urllib.parse.quote(doc, safe=""))

    @staticmethod
    def _read(d):
        return {"id": d["name"].rsplit("/", 1)[-1], "f": {k: fs_decode(v) for k, v in (d.get("fields") or {}).items()}, "t": d.get("updateTime")}

    def get(self, coll, doc):
        d = self._req("GET", self._doc_url(coll, doc))
        return self._read(d) if d else None

    def set(self, coll, doc, fields, exists=None, update_time=None):
        q = [("updateMask.fieldPaths", k) for k in fields]
        if exists is False:
            q.append(("currentDocument.exists", "false"))
        if update_time:
            q.append(("currentDocument.updateTime", update_time))
        url = self._doc_url(coll, doc) + "?" + urllib.parse.urlencode(q)
        self._req("PATCH", url, {"fields": {k: fs_value(v) for k, v in fields.items()}})

    def delete(self, coll, doc):
        self._req("DELETE", self._doc_url(coll, doc))

    def query_since(self, coll, field, since, limit=200):
        body = {"structuredQuery": {"from": [{"collectionId": coll}],
                                    "where": {"fieldFilter": {"field": {"fieldPath": field}, "op": "GREATER_THAN", "value": {"integerValue": str(int(since))}}},
                                    "orderBy": [{"field": {"fieldPath": field}, "direction": "ASCENDING"}], "limit": limit}}
        res = self._req("POST", "%s/projects/%s/databases/(default)/documents:runQuery" % (self.base, self.project), body) or []
        return [self._read(r["document"]) for r in res if r.get("document")]


# ----------------------------------------------------------------------------- helpers
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def fid_of(project, rel):
    return hashlib.sha1((project + "\0" + rel).encode("utf-8")).hexdigest()[:40]


def safe_join(root, rel):
    """A path that came from the cloud can never leave the project folder."""
    if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or any(p in ("..", "") for p in rel.split("/")):
        raise ValueError("unsafe path: %r" % rel)
    full = os.path.normpath(os.path.join(root, *rel.split("/")))
    rr = os.path.normcase(os.path.realpath(root))
    if not os.path.normcase(os.path.realpath(full)).startswith(rr + os.sep):
        raise ValueError("unsafe path: %r" % rel)
    return full


def clean_project(name):
    return re.sub(r"[^\w\- ]+", "", str(name or "")).strip()[:40] or "project"


def now_ms():
    return int(time.time() * 1000)


class Syncer:
    """backend: a Firestore. roots(): {project name: local folder}. projects_root: where a project that only exists in the cloud is created.
    chats / settings: adapters supplied by the app (see server.py); both may be None."""

    def __init__(self, data_dir, backend, device, roots, projects_root, chats=None, settings=None, register_root=None, passphrase=lambda: ""):
        self.dir, self.b, self.dev, self.roots_fn, self.projects_root = data_dir, backend, device, roots, projects_root
        self.chats, self.settings, self.register_root, self.passphrase = chats, settings, register_root, passphrase
        self.state_path = os.path.join(data_dir, "sync_state.json")
        self.lock = threading.RLock()
        self.st = {"files": {}, "chats": {}, "meta": {}, "since": {"f": 0, "c": 0, "m": 0}}
        try:
            with open(self.state_path, encoding="utf-8") as f:
                self.st.update(json.load(f))
        except Exception:
            pass
        self.info = {"last_ok": 0, "last_error": "", "up": 0, "down": 0, "conflicts": 0, "warn": ""}

    # ---- state
    def _save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.st, f)
        os.replace(tmp, self.state_path)

    def _trash(self, project, rel, full):
        dst = os.path.join(self.dir, "sync_trash", time.strftime("%Y%m%d-%H%M%S"), project, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(full, dst)

    # ---- content packing
    @staticmethod
    def _pack(raw):
        b64 = base64.b64encode(zlib.compress(raw, 6)).decode("ascii")
        return [b64[i:i + CHUNK_B64] for i in range(0, len(b64), CHUNK_B64)] or [""]

    def _unpack(self, coll, fid, f):
        if f.get("n", 1) <= 1:
            b64 = f.get("z") or ""
        else:
            parts = []
            for i in range(f["n"]):
                c = self.b.get(CHUNKS, "%s_%d_%d" % (fid, f["r"], i))
                if not c:
                    raise SyncError(404, "A part of %s is missing in the cloud." % fid)
                parts.append(c["f"].get("d", ""))
            b64 = "".join(parts)
        return zlib.decompress(base64.b64decode(b64)) if b64 else b""

    def _put_doc(self, coll, fid, fields, cur):
        """Write chunks first, then the main document (guarded against a concurrent writer)."""
        chunks = fields.pop("_chunks", [""])
        fields["n"] = len(chunks)
        fields["z"] = chunks[0] if len(chunks) == 1 else ""
        if len(chunks) > 1:
            for i, c in enumerate(chunks):
                self.b.set(CHUNKS, "%s_%d_%d" % (fid, fields["r"], i), {"d": c})
        if cur:
            self.b.set(coll, fid, fields, update_time=cur["t"])
        else:
            self.b.set(coll, fid, fields, exists=False)
        if cur and cur["f"].get("n", 1) > 1:                   # best effort: free the space of the old parts
            for i in range(cur["f"]["n"]):
                try:
                    self.b.delete(CHUNKS, "%s_%d_%d" % (fid, cur["f"]["r"], i))
                except Exception:
                    pass

    # ----------------------------------------------------------------------- files: cloud -> here
    def _apply_remote_file(self, d):
        fid, f = d["id"], d["f"]
        if not f.get("p") or not f.get("f"):
            return
        st = self.st["files"].get(fid)
        if st and st.get("r", 0) >= f.get("r", 0):
            return
        proj = clean_project(f["p"])
        roots = self.roots_fn()
        root = roots.get(proj)
        if root is None:
            root = os.path.join(self.projects_root, proj)
            os.makedirs(root, exist_ok=True)
            if self.register_root:
                self.register_root(proj, root)
        full = safe_join(root, f["f"])
        exists = os.path.isfile(full)
        lh = sha256_file(full) if exists else None
        local_changed = exists and (not st or lh != st.get("h"))
        if f.get("d"):
            if exists and not local_changed:
                self._trash(proj, f["f"], full)
            self.st["files"][fid] = {"p": proj, "f": f["f"], "h": "", "m": 0, "s": 0, "r": f["r"], "deleted": True} if not (exists and local_changed) else \
                {"p": proj, "f": f["f"], "h": st.get("h", "") if st else "", "m": 0, "s": 0, "r": f["r"], "dirty": True}
            self.info["down"] += 1
            return
        raw = self._unpack(FILES, fid, f)
        if hashlib.sha256(raw).hexdigest() != f["h"]:
            raise SyncError(0, "The cloud copy of %s is damaged (checksum differs)." % f["f"])
        if exists and lh == f["h"]:                              # already identical
            s = os.stat(full)
            self.st["files"][fid] = {"p": proj, "f": f["f"], "h": lh, "m": s.st_mtime_ns, "s": s.st_size, "r": f["r"]}
            return
        if local_changed:                                        # both sides changed: the newer one wins, the other is kept next to it
            keep_remote = f.get("m", 0) / 1000.0 >= os.path.getmtime(full)
            stem, ext = os.path.splitext(full)
            copy = "%s.conflict-%s-%s%s" % (stem, (self.dev["id"] or "dev")[:4], time.strftime("%Y%m%d-%H%M%S"), ext)
            if keep_remote:
                os.replace(full, copy)
            else:
                with open(copy, "wb") as cf:
                    cf.write(raw)
                self.info["conflicts"] += 1
                self.st["files"][fid] = {"p": proj, "f": f["f"], "h": st.get("h", "") if st else "", "m": 0, "s": 0, "r": f["r"], "dirty": True}
                return
            self.info["conflicts"] += 1
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".aqua-sync.tmp"
        with open(tmp, "wb") as w:
            w.write(raw)
        os.replace(tmp, full)
        if f.get("m"):
            os.utime(full, (time.time(), f["m"] / 1000.0))
        s = os.stat(full)
        self.st["files"][fid] = {"p": proj, "f": f["f"], "h": f["h"], "m": s.st_mtime_ns, "s": s.st_size, "r": f["r"]}
        self.info["down"] += 1

    def _pull(self, coll, key, apply):
        since = max(0, self.st["since"].get(key, 0) - OVERLAP_MS)
        top = self.st["since"].get(key, 0)
        while True:
            docs = self.b.query_since(coll, "u", since, 200)
            for d in docs:
                try:
                    apply(d)
                except SyncError as e:
                    self.info["last_error"] = e.msg
                except ValueError as e:
                    self.info["last_error"] = str(e)
                top = max(top, d["f"].get("u", 0))
            if len(docs) < 200:
                break
            nxt = max(d["f"].get("u", 0) for d in docs)
            if nxt <= since:
                break
            since = nxt
        self.st["since"][key] = top

    # ----------------------------------------------------------------------- files: here -> cloud
    def _walk(self, root):
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                if fn.lower() in SKIP_FILES or fn.lower().endswith(SKIP_EXT):
                    continue
                ap = os.path.join(dp, fn)
                try:
                    s = os.stat(ap)
                except OSError:
                    continue
                if s.st_size > MAX_FILE:
                    continue
                yield os.path.relpath(ap, root).replace(os.sep, "/"), ap, s

    def _upload_file(self, proj, rel, ap, h, s, st):
        fid = fid_of(proj, rel)
        with open(ap, "rb") as fh:
            raw = fh.read()
        if hashlib.sha256(raw).hexdigest() != h:
            return False                                         # changed while we read it: next cycle
        cur = self.b.get(FILES, fid)
        rem = cur["f"].get("r", 0) if cur else 0
        base = st.get("r", 0) if st else 0
        if rem > base:                                           # someone else wrote since we last looked
            if cur["f"].get("h") == h and not cur["f"].get("d"):
                self.st["files"][fid] = {"p": proj, "f": rel, "h": h, "m": s.st_mtime_ns, "s": s.st_size, "r": rem}
                return False
            self._apply_remote_file(cur)
            return False
        fields = {"p": proj, "f": rel, "s": len(raw), "h": h, "m": int(s.st_mtime * 1000), "r": rem + 1, "u": now_ms(), "d": False, "dev": self.dev["id"], "_chunks": self._pack(raw)}
        try:
            self._put_doc(FILES, fid, fields, cur)
        except Conflict:
            return False
        self.st["files"][fid] = {"p": proj, "f": rel, "h": h, "m": s.st_mtime_ns, "s": s.st_size, "r": rem + 1}
        self.info["up"] += 1
        return True

    def _tombstone(self, fid, st):
        cur = self.b.get(FILES, fid)
        rem = cur["f"].get("r", 0) if cur else 0
        if cur and rem > st.get("r", 0):
            self._apply_remote_file(cur)
            return
        fields = {"p": st["p"], "f": st["f"], "s": 0, "h": "", "m": 0, "r": rem + 1, "u": now_ms(), "d": True, "dev": self.dev["id"], "_chunks": [""]}
        try:
            self._put_doc(FILES, fid, fields, cur)
        except Conflict:
            return
        self.st["files"][fid] = {"p": st["p"], "f": st["f"], "h": "", "m": 0, "s": 0, "r": rem + 1, "deleted": True}
        self.info["up"] += 1

    def _push_files(self):
        sent = 0
        for proj, path in self.roots_fn().items():
            if not os.path.isdir(path):
                continue
            seen = set()
            for rel, ap, s in self._walk(path):
                fid = fid_of(proj, rel)
                seen.add(fid)
                st = self.st["files"].get(fid)
                if time.time() - s.st_mtime < 1.5:
                    continue                                     # still being written
                if st and not st.get("dirty") and not st.get("deleted") and st["m"] == s.st_mtime_ns and st["s"] == s.st_size:
                    continue
                h = sha256_file(ap)
                if st and not st.get("dirty") and not st.get("deleted") and st["h"] == h:
                    st["m"], st["s"] = s.st_mtime_ns, s.st_size
                    continue
                if sent >= MAX_UPLOADS_PER_CYCLE:
                    continue
                if self._upload_file(proj, rel, ap, h, s, st):
                    sent += 1
            tracked = [(fid, st) for fid, st in self.st["files"].items() if st["p"] == proj and not st.get("deleted")]
            gone = [(fid, st) for fid, st in tracked if fid not in seen]
            if gone and len(gone) > max(10, len(tracked) // 2):    # a missing drive or a moved folder must never wipe the cloud copy
                self.info["warn"] = "%d files of '%s' are missing here. Nothing was deleted in the cloud." % (len(gone), proj)
                continue
            for fid, st in gone:
                if sent >= MAX_UPLOADS_PER_CYCLE:
                    break
                self._tombstone(fid, st)
                sent += 1

    # ----------------------------------------------------------------------- chats
    def _chat_payload(self, sid):
        obj = self.chats.get(sid)
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8") if obj is not None else None

    def _apply_remote_chat(self, d):
        sid, f = d["id"], d["f"]
        st = self.st["chats"].get(sid)
        if st and st.get("r", 0) >= f.get("r", 0):
            return
        if f.get("d"):
            if sid in self.chats.ids() and (not st or hashlib.sha256(self._chat_payload(sid) or b"").hexdigest() == st.get("h")):
                self.chats.remove(sid)
            self.st["chats"][sid] = {"h": "", "r": f["r"], "deleted": True}
            return
        raw = self._unpack(CHATS, sid, f)
        if hashlib.sha256(raw).hexdigest() != f["h"]:
            raise SyncError(0, "A chat in the cloud is damaged.")
        if sid in self.chats.ids() and self.chats.busy(sid):
            return
        local = self._chat_payload(sid)
        if local is not None and st and hashlib.sha256(local).hexdigest() != st.get("h") and hashlib.sha256(local).hexdigest() != f["h"]:
            self.st["chats"][sid] = {"h": st.get("h", ""), "r": f["r"], "dirty": True}     # local edits win and go up as the next revision
            return
        self.chats.put(sid, json.loads(raw.decode("utf-8")), f.get("p"))
        self.st["chats"][sid] = {"h": f["h"], "r": f["r"]}
        self.info["down"] += 1

    def _push_chats(self):
        ids = set(self.chats.ids())
        sent = 0
        for sid in ids:
            if self.chats.busy(sid):
                continue
            st = self.st["chats"].get(sid)
            fp = self.chats.fingerprint(sid) if hasattr(self.chats, "fingerprint") else None
            if fp is not None and st and st.get("fp") == fp and not st.get("dirty") and not st.get("deleted"):
                continue                                         # cheap check: nothing new in this chat
            raw = self._chat_payload(sid)
            if raw is None or len(raw) > MAX_CHAT_BYTES:
                continue
            h = hashlib.sha256(raw).hexdigest()
            if st and st.get("h") == h and not st.get("dirty") and not st.get("deleted"):
                if fp is not None:
                    st["fp"] = fp
                continue
            if sent >= MAX_UPLOADS_PER_CYCLE:
                break
            cur = self.b.get(CHATS, sid)
            rem = cur["f"].get("r", 0) if cur else 0
            if rem > (st.get("r", 0) if st else 0):
                if cur["f"].get("h") == h:
                    self.st["chats"][sid] = {"h": h, "r": rem}
                else:
                    self._apply_remote_chat(cur)
                continue
            fields = {"p": self.chats.project_of(sid) or "", "t": str(self.chats.get(sid).get("title", ""))[:80], "h": h, "r": rem + 1, "u": now_ms(), "d": False,
                      "dev": self.dev["id"], "_chunks": self._pack(raw)}
            try:
                self._put_doc(CHATS, sid, fields, cur)
            except Conflict:
                continue
            self.st["chats"][sid] = {"h": h, "r": rem + 1, "fp": fp}
            self.info["up"] += 1
            sent += 1
        known = [k for k, v in self.st["chats"].items() if not v.get("deleted")]
        gone = [k for k in known if k not in ids]
        if gone and len(gone) <= max(5, len(known) // 2):
            for sid in gone:
                cur = self.b.get(CHATS, sid)
                rem = cur["f"].get("r", 0) if cur else 0
                if cur and rem > self.st["chats"][sid].get("r", 0):
                    self._apply_remote_chat(cur)
                    continue
                try:
                    self._put_doc(CHATS, sid, {"p": "", "t": "", "h": "", "r": rem + 1, "u": now_ms(), "d": True, "dev": self.dev["id"], "_chunks": [""]}, cur)
                except Conflict:
                    continue
                self.st["chats"][sid] = {"h": "", "r": rem + 1, "deleted": True}

    # ----------------------------------------------------------------------- settings and keys
    def _meta_get(self, name):
        return self.b.get(META, name)

    def _apply_remote_meta(self, d):
        name, f = d["id"], d["f"]
        st = self.st["meta"].get(name)
        if name not in ("settings", "keys") or (st and st.get("r", 0) >= f.get("r", 0)) or not self.settings:
            return
        if name == "settings":
            raw = self._unpack(META, name, f)
            self.settings.put_settings(json.loads(raw.decode("utf-8")))
        else:
            pw = self.passphrase()
            if not pw or not f.get("box"):
                return
            try:
                keys = json.loads(unseal(pw, f["box"]).decode("utf-8"))
            except ValueError as e:
                self.info["last_error"] = "API keys in the cloud: %s" % e
                return
            first = st is None                               # a computer that joins with keys of its own never loses them: merge, then upload the union
            self.settings.put_keys(keys, merge=first)
            same = json.dumps(self.settings.get_keys(), sort_keys=True) == json.dumps(keys, sort_keys=True)
            self.st["meta"][name] = {"h": f.get("h", "") if same else "", "r": f["r"]}
            self.info["down"] += 1
            return
        self.st["meta"][name] = {"h": f.get("h", ""), "r": f["r"]}
        self.info["down"] += 1

    def _push_meta(self):
        if not self.settings:
            return
        items = [("settings", json.dumps(self.settings.get_settings(), sort_keys=True).encode("utf-8"))]
        pw = self.passphrase()
        if pw:
            items.append(("keys", json.dumps(self.settings.get_keys(), sort_keys=True).encode("utf-8")))
        for name, raw in items:
            h = hashlib.sha256(raw).hexdigest()
            st = self.st["meta"].get(name)
            if st and st.get("h") == h:
                continue
            cur = self._meta_get(name)
            rem = cur["f"].get("r", 0) if cur else 0
            if rem > (st.get("r", 0) if st else 0):
                if name == "settings" and cur["f"].get("h") == h:
                    self.st["meta"][name] = {"h": h, "r": rem}
                else:
                    self._apply_remote_meta(cur)
                continue
            fields = {"h": h, "r": rem + 1, "u": now_ms(), "dev": self.dev["id"]}
            if name == "settings":
                fields["_chunks"] = self._pack(raw)
                fields["box"] = {}
            else:
                fields["_chunks"] = [""]
                fields["box"] = seal(pw, raw)
            try:
                self._put_doc(META, name, fields, cur)
            except Conflict:
                continue
            self.st["meta"][name] = {"h": h, "r": rem + 1}
            self.info["up"] += 1

    def _register_device(self):
        d = self.st.setdefault("device_beat", 0)
        if time.time() - d < 600:
            return
        cur = self.b.get(META, "devices")
        devs = (cur["f"].get("list") if cur else None) or {}
        devs[self.dev["id"]] = {"name": self.dev["name"], "os": self.dev["os"], "seen": now_ms()}
        try:
            self.b.set(META, "devices", {"list": devs, "u": now_ms()}, update_time=cur["t"] if cur else None, exists=False if not cur else None)
            self.st["device_beat"] = time.time()
        except Conflict:
            pass

    # ----------------------------------------------------------------------- one full round
    def cycle(self):
        with self.lock:
            self.info.update({"up": 0, "down": 0, "conflicts": 0, "warn": "", "last_error": ""})
            try:
                self._pull(FILES, "f", self._apply_remote_file)
                self._push_files()
                if self.chats:
                    self._pull(CHATS, "c", self._apply_remote_chat)
                    self._push_chats()
                if self.settings:
                    self._pull(META, "m", self._apply_remote_meta)
                    self._push_meta()
                self._register_device()
                self.info["last_ok"] = time.time()
            except SyncError as e:
                self.info["last_error"] = e.msg
            except Conflict as e:
                self.info["last_error"] = "Busy elsewhere, trying again: %s" % e
            finally:
                self._save()
            return dict(self.info)


def device_info(data_dir):
    p = os.path.join(data_dir, "sync_device.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        d = {"id": secrets.token_hex(8), "name": platform.node() or "computer", "os": platform.system() or "?"}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
        return d
