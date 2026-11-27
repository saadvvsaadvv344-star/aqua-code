"""Self-test for Aqua Sync: two simulated computers (PC and Mac) talk to a fake Firestore that speaks the same REST protocol.
Nothing leaves this computer.   python tools/sync_selftest.py
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sync  # noqa: E402

DOCS, CLOCK = {}, [0]


class Fake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/token":
            return self._send(200, {"id_token": "fake-id", "expires_in": "3600", "refresh_token": "fake-refresh"})
        if self.headers.get("Authorization") != "Bearer fake-id":
            return self._send(401, {"error": {"status": "UNAUTHENTICATED"}})
        if u.path.endswith(":runQuery"):
            q = json.loads(self._body())["structuredQuery"]
            coll = q["from"][0]["collectionId"]
            ff = q["where"]["fieldFilter"]
            field, since = ff["field"]["fieldPath"], int(ff["value"]["integerValue"])
            rows = [(k, v) for k, v in DOCS.items() if k.startswith(coll + "/") and int(v["fields"].get(field, {}).get("integerValue", 0)) > since]
            rows.sort(key=lambda kv: int(kv[1]["fields"][field]["integerValue"]))
            out = [{"document": {"name": "projects/p/databases/(default)/documents/" + k, "fields": v["fields"], "updateTime": v["t"]}} for k, v in rows[:q.get("limit", 100)]]
            return self._send(200, out or [{"readTime": "x"}])
        self._send(404, {})

    def _key(self):
        u = urllib.parse.urlparse(self.path)
        m = re.search(r"/documents/(.+)$", urllib.parse.unquote(u.path))
        return m.group(1), urllib.parse.parse_qsl(u.query)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer fake-id":
            return self._send(401, {})
        k, _ = self._key()
        d = DOCS.get(k)
        if not d:
            return self._send(404, {"error": {"status": "NOT_FOUND"}})
        self._send(200, {"name": "projects/p/databases/(default)/documents/" + k, "fields": d["fields"], "updateTime": d["t"]})

    def do_PATCH(self):
        if self.headers.get("Authorization") != "Bearer fake-id":
            return self._send(401, {})
        k, q = self._key()
        body = json.loads(self._body())
        cur = DOCS.get(k)
        for a, b in q:
            if a == "currentDocument.exists" and b == "false" and cur:
                return self._send(409, {"error": {"status": "ALREADY_EXISTS"}})
            if a == "currentDocument.updateTime" and (not cur or cur["t"] != b):
                return self._send(400, {"error": {"status": "FAILED_PRECONDITION"}})
        mask = [b for a, b in q if a == "updateMask.fieldPaths"]
        fields = dict(cur["fields"]) if cur else {}
        for f in (mask or body["fields"]):
            fields[f] = body["fields"][f]
        CLOCK[0] += 1
        DOCS[k] = {"fields": fields, "t": "t%020d" % CLOCK[0]}
        self._send(200, {"name": k})

    def do_DELETE(self):
        k, _ = self._key()
        DOCS.pop(k, None)
        self._send(200, {})


class Chats:
    def __init__(self):
        self.d, self.proj, self.busy_ids = {}, {}, set()

    def ids(self): return list(self.d)
    def get(self, sid): return self.d.get(sid)
    def put(self, sid, obj, project): self.d[sid] = obj; self.proj[sid] = project
    def remove(self, sid): self.d.pop(sid, None)
    def busy(self, sid): return sid in self.busy_ids
    def project_of(self, sid): return self.proj.get(sid)


class Settings:
    def __init__(self): self.s, self.k = {"level": "balanced"}, {}
    def get_settings(self): return dict(self.s)
    def put_settings(self, d): self.s = dict(d)
    def get_keys(self): return dict(self.k)
    def put_keys(self, d, merge=False):
        if merge: self.k.update(d)
        else: self.k = dict(d)


class Device:
    def __init__(self, base, name, passphrase=""):
        self.name = name
        self.dir = os.path.join(base, name + "_data")
        self.projects = os.path.join(base, name + "_projects")
        os.makedirs(self.dir, exist_ok=True)
        os.makedirs(self.projects, exist_ok=True)
        self.chats, self.settings = Chats(), Settings()
        self.pw = passphrase
        self.fs = sync.Firestore("p", "key", "refresh", base="http://127.0.0.1:%d/v1" % PORT, token_url="http://127.0.0.1:%d/token" % PORT)
        self.s = sync.Syncer(self.dir, self.fs, {"id": name + "0001", "name": name, "os": name}, self.roots, self.projects, self.chats, self.settings,
                             passphrase=lambda: self.pw)

    def roots(self):
        return {d: os.path.join(self.projects, d) for d in os.listdir(self.projects) if os.path.isdir(os.path.join(self.projects, d))}

    def write(self, proj, rel, data, age=10):
        p = os.path.join(self.projects, proj, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode())
        t = time.time() - age
        os.utime(p, (t, t))

    def read(self, proj, rel):
        with open(os.path.join(self.projects, proj, *rel.split("/")), "rb") as f:
            return f.read()

    def has(self, proj, rel):
        return os.path.isfile(os.path.join(self.projects, proj, *rel.split("/")))

    def cycle(self, n=1):
        r = None
        for _ in range(n):
            r = self.s.cycle()
        return r


PORT = 0
OK = []


def check(name, cond, extra=""):
    OK.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name + (("  " + str(extra)) if extra and not cond else ""))


def main():
    global PORT
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = tempfile.mkdtemp(prefix="aqua_sync_test_")
    try:
        pc, mac = Device(base, "pc", "my secret passphrase"), Device(base, "mac", "my secret passphrase")
        big = os.urandom(1_500_000)                                          # incompressible: needs several Firestore documents
        pc.write("Slime game", "index.html", "<h1>slime</h1>\n")
        pc.write("Slime game", "js/game.js", "console.log('hi');\n" * 200)
        pc.write("Slime game", "models/blob.bin", big)
        pc.write("Slime game", "notes/ü ñ file.txt", "unicode name\n")
        pc.write("Slime game", "huge.bin", b"x" * (sync.MAX_FILE + 10))      # over the limit: stays local
        pc.write("Slime game", "node_modules/pkg/a.js", "skipped\n")
        r = pc.cycle()
        check("PC uploads a project (4 files, settings, keys)", r["up"] == 6 and not r["last_error"], r)
        check("big file was split into several documents", sum(1 for k in DOCS if k.startswith("aqua_chunks/")) >= 2)
        mac.cycle()
        check("Mac gets the project", mac.has("Slime game", "index.html") and mac.read("Slime game", "js/game.js") == pc.read("Slime game", "js/game.js"))
        check("big binary identical", mac.read("Slime game", "models/blob.bin") == big)
        check("unicode file name", mac.read("Slime game", "notes/ü ñ file.txt") == b"unicode name\n")
        check("huge and node_modules not synced", not mac.has("Slime game", "huge.bin") and not mac.has("Slime game", "node_modules/pkg/a.js"))
        check("Mac keeps the file's modified time", abs(os.path.getmtime(os.path.join(mac.projects, "Slime game", "index.html")) - os.path.getmtime(os.path.join(pc.projects, "Slime game", "index.html"))) < 2)
        r = pc.cycle(); r2 = mac.cycle()
        check("a quiet cycle changes nothing (no loop)", r["up"] == 0 and r["down"] == 0 and r2["up"] == 0 and r2["down"] == 0, (r, r2))

        mac.write("Slime game", "index.html", "<h1>slime from the Mac</h1>\n")
        mac.cycle(); pc.cycle()
        check("Mac edit reaches the PC", pc.read("Slime game", "index.html") == b"<h1>slime from the Mac</h1>\n")
        mac.write("Slime game", "new/created_on_mac.txt", "hello\n")
        mac.cycle(); pc.cycle()
        check("a new file on the Mac reaches the PC", pc.has("Slime game", "new/created_on_mac.txt"))

        os.remove(os.path.join(pc.projects, "Slime game", "js", "game.js"))
        pc.cycle(); mac.cycle()
        trash = os.path.join(mac.dir, "sync_trash")
        check("delete on the PC removes it on the Mac (into trash, not lost)", not mac.has("Slime game", "js/game.js") and os.path.isdir(trash) and any(f.endswith("game.js") for _, _, fs in os.walk(trash) for f in fs))

        # conflict: both edit the same file before either syncs
        pc.write("Slime game", "index.html", "PC version\n", age=9)
        mac.write("Slime game", "index.html", "Mac version\n", age=4)          # newer
        pc.cycle(); mac.cycle(); pc.cycle(); mac.cycle()
        a, b = pc.read("Slime game", "index.html"), mac.read("Slime game", "index.html")
        conf = [f for f in os.listdir(os.path.join(mac.projects, "Slime game")) if ".conflict-" in f] + [f for f in os.listdir(os.path.join(pc.projects, "Slime game")) if ".conflict-" in f]
        check("conflict: both computers end identical, the newer edit wins", a == b == b"Mac version\n", (a, b))
        check("conflict: the losing version is kept, not lost", any(open(os.path.join(d, f), "rb").read() == b"PC version\n" for d in (os.path.join(mac.projects, "Slime game"), os.path.join(pc.projects, "Slime game")) for f in os.listdir(d) if ".conflict-" in f), conf)

        # safety: a poisoned document can never write outside the project
        evil = hashlib.sha1(b"evil").hexdigest()[:40]
        DOCS["aqua_files/" + evil] = {"fields": {"p": {"stringValue": "Slime game"}, "f": {"stringValue": "../../escaped.txt"}, "h": {"stringValue": hashlib.sha256(b"x").hexdigest()},
                                                 "r": {"integerValue": "5"}, "u": {"integerValue": str(sync.now_ms() + 5000)}, "d": {"booleanValue": False}, "n": {"integerValue": "1"},
                                                 "z": {"stringValue": "eJzLAAAAywDL"}, "m": {"integerValue": "0"}}, "t": "t99999999999999999999"}
        mac.cycle()
        check("a path with .. is refused and nothing escapes", not os.path.exists(os.path.join(base, "mac_projects", "escaped.txt")) and not os.path.exists(os.path.join(base, "escaped.txt")))
        del DOCS["aqua_files/" + evil]

        # safety: a vanished folder must not wipe the cloud copy
        for rel in ("index.html", "models/blob.bin", "notes/ü ñ file.txt", "new/created_on_mac.txt"):
            os.remove(os.path.join(pc.projects, "Slime game", *rel.split("/")))
        for f in list(os.listdir(os.path.join(pc.projects, "Slime game"))):
            p = os.path.join(pc.projects, "Slime game", f)
            if os.path.isfile(p): os.remove(p)
        for i in range(14):
            pc.write("Wipe me", "f%d.txt" % i, "x%d" % i)
        pc.cycle(); mac.cycle()
        before = sum(1 for k in DOCS if k.startswith("aqua_files/") and DOCS[k]["fields"].get("d", {}).get("booleanValue") is False)
        shutil.rmtree(os.path.join(pc.projects, "Wipe me")); os.makedirs(os.path.join(pc.projects, "Wipe me"))
        r = pc.cycle()
        after = sum(1 for k in DOCS if k.startswith("aqua_files/") and DOCS[k]["fields"].get("d", {}).get("booleanValue") is False)
        check("emptied folder does not delete the cloud copy", after == before and "missing" in r["warn"], (before, after, r))

        # chats
        pc.chats.d["abc"] = {"title": "My chat", "messages": [{"role": "user", "content": "hi"}], "events": []}; pc.chats.proj["abc"] = "Slime game"
        pc.cycle(); mac.cycle()
        check("chat reaches the Mac with its project", mac.chats.d.get("abc", {}).get("title") == "My chat" and mac.chats.proj.get("abc") == "Slime game")
        pc.chats.busy_ids.add("abc"); pc.chats.d["abc"]["messages"].append({"role": "assistant", "content": "working"})
        pc.cycle(); mac.cycle()
        check("a chat that is running is not uploaded half-way", len(mac.chats.d["abc"]["messages"]) == 1)
        pc.chats.busy_ids.clear(); pc.cycle(); mac.cycle()
        check("finished chat arrives", len(mac.chats.d["abc"]["messages"]) == 2)
        del mac.chats.d["abc"]; mac.cycle(); pc.cycle()
        check("deleting a chat on the Mac deletes it on the PC", "abc" not in pc.chats.d)

        # settings and keys
        pc.settings.s = {"level": "high", "approval": "auto"}
        pc.settings.k = {"gemini": "AIzaSy-SECRET-KEY-123456789", "openrouter": "sk-or-v1-ANOTHER-SECRET"}
        pc.cycle(); mac.cycle()
        check("settings reach the Mac", mac.settings.s.get("level") == "high")
        check("API keys reach the Mac (with the passphrase)", mac.settings.k == pc.settings.k, mac.settings.k)
        blob = json.dumps(DOCS)
        check("no key and no plaintext in the cloud", "SECRET-KEY" not in blob and "ANOTHER-SECRET" not in blob and "AIzaSy" not in blob)
        stranger = Device(base, "stranger", "wrong passphrase"); stranger.cycle()
        check("a wrong passphrase gets no keys", stranger.settings.k == {} and "passphrase" in stranger.s.info["last_error"].lower() or stranger.settings.k == {}, stranger.s.info)
        del pc.settings.k["openrouter"]; pc.cycle(); mac.cycle()
        check("removing a key on the PC removes it on the Mac", "openrouter" not in mac.settings.k)
        nopw = Device(base, "nopw", ""); nopw.cycle()
        check("without a passphrase keys are never uploaded or read", nopw.settings.k == {})

        # a computer that joins with keys of its own keeps them and shares the union
        late = Device(base, "late", "my secret passphrase"); late.settings.k = {"groq": "gsk_LATE_ONLY_KEY"}
        late.cycle(); pc.cycle()
        check("joining computer keeps its own key and adopts the others", late.settings.k.get("groq") == "gsk_LATE_ONLY_KEY" and "gemini" in late.settings.k, late.settings.k)
        check("the PC receives the late computer's key too", pc.settings.k.get("groq") == "gsk_LATE_ONLY_KEY" and "gemini" in pc.settings.k, pc.settings.k)

        # encryption primitives
        box = sync.seal("pw", b"hello key")
        check("seal/unseal round trip", sync.unseal("pw", box) == b"hello key")
        try:
            sync.unseal("pw", dict(box, ct=box["ct"][:-4] + "AAAA")); tamper = False
        except ValueError:
            tamper = True
        check("tampered data is rejected", tamper)

        # a fresh third computer gets everything
        third = Device(base, "third", "my secret passphrase"); third.cycle()
        check("a new computer pulls the whole project", third.has("Wipe me", "f3.txt") and third.settings.s.get("level") == "high")
    finally:
        srv.shutdown()
        shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d checks passed" % (sum(OK), len(OK)))
    return 0 if all(OK) else 1


if __name__ == "__main__":
    sys.exit(main())
