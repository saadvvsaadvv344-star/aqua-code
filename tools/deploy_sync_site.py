"""One-time setup of YOUR private Aqua Code site on YOUR free Firebase project. Safe by default: it only looks.

    python tools/deploy_sync_site.py                 look: what exists, what is missing, what would change (changes nothing)
    python tools/deploy_sync_site.py --yes           do the missing steps and publish the site + the Firestore rules

What --yes does, and nothing else:
  1. turns on the Firestore API of the project and creates its (default) database if it does not exist (free plan, no card)
  2. creates the Hosting site <site> if it does not exist (a separate site: your Auric site is never touched or replaced)
  3. adds <site>.web.app to the Google sign-in's authorized domains if missing
  4. publishes sync_site/public to that site, and sync_site/firestore.rules (only your Google account may read or write)
It uses the Firebase tool you are already signed in with (tools/node/firebase.cmd login). Nothing is stored or sent anywhere else.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
AURIC = HERE.parent
CONFIGSTORE = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~")) / ".config" / "configstore" / "firebase-tools.json"


def firebase_cmd():
    for c in (AURIC / "tools" / "node" / "firebase.cmd", AURIC / "tools" / "node" / "firebase"):
        if c.exists():
            return str(c)
    return shutil.which("firebase") or ""


FB = firebase_cmd()


def run(*args, check=True, cwd=None):
    r = subprocess.run([FB, *args, "--non-interactive"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if check and r.returncode:
        raise SystemExit("firebase %s failed:\n%s" % (" ".join(args), (r.stdout + r.stderr).strip()[-1500:]))
    return r


def token():
    for _ in range(2):
        try:
            t = json.loads(CONFIGSTORE.read_text(encoding="utf-8"))["tokens"]
            if t.get("expires_at", 0) / 1000 - time.time() > 300:
                return t["access_token"]
        except (OSError, ValueError, KeyError):
            pass
        run("projects:list", check=False)                      # the tool refreshes and saves its login
    raise SystemExit("The Firebase tool is not signed in. Run:  %s login" % FB)


def owner_email(arg):
    """The Google account that may open the site: the one the Firebase tool is signed in with (or --email). It is put into the rules only at
    deploy time, so it never sits in the code (and never reaches GitHub)."""
    if arg:
        return arg.strip()
    try:
        return json.loads(CONFIGSTORE.read_text(encoding="utf-8"))["user"]["email"]
    except (OSError, ValueError, KeyError):
        raise SystemExit("Could not find the Google account of the Firebase tool. Pass it with --email you@gmail.com")


def call(method, url, project, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json", "x-goog-user-project": project})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="auric-saad")
    ap.add_argument("--site", default="aqua-code-saad")
    ap.add_argument("--location", default="eur3")
    ap.add_argument("--email", default="", help="the Google account allowed to open the site (default: the one the Firebase tool is signed in with)")
    ap.add_argument("--yes", action="store_true", help="really do it (without this, only look)")
    a = ap.parse_args()
    if not FB:
        raise SystemExit("The Firebase tool was not found (expected tools/node/firebase.cmd).")
    P, S = a.project, a.site
    mail = owner_email(a.email)
    print("Project: %s   Site: https://%s.web.app/   Only this Google account can open it: %s" % (P, S, mail))

    code, dbs = call("GET", "https://firestore.googleapis.com/v1/projects/%s/databases" % P, P)
    fs_api = code == 200
    has_db = fs_api and any(d.get("name", "").endswith("/databases/(default)") for d in dbs.get("databases", []))
    print("Firestore API on: %s | (default) database: %s" % (fs_api, has_db))

    r = run("hosting:sites:list", "--project", P, check=False)
    has_site = ("| %s " % S) in r.stdout or (" %s " % S) in r.stdout
    print("Hosting site exists: %s" % has_site)

    code, cfg = call("GET", "https://identitytoolkit.googleapis.com/admin/v2/projects/%s/config" % P, P)
    domains = cfg.get("authorizedDomains", []) if code == 200 else []
    dom_ok = "%s.web.app" % S in domains
    print("Google sign-in allows %s.web.app: %s%s" % (S, dom_ok, "" if code == 200 else "  (could not read the sign-in settings: HTTP %s)" % code))

    todo = []
    if not fs_api:
        todo.append("turn on the Firestore API")
    if not has_db:
        todo.append("create the (default) Firestore database in %s" % a.location)
    if not has_site:
        todo.append("create the Hosting site %s" % S)
    if not dom_ok:
        todo.append("allow %s.web.app for Google sign-in" % S)
    todo.append("publish the site (sync_site/public) and the Firestore rules (sync_site/firestore.rules)")
    print("\nPlan:\n  - " + "\n  - ".join(todo))
    if not a.yes:
        print("\nNothing was changed. Run again with --yes to do it.")
        return

    if not fs_api:
        code, j = call("POST", "https://serviceusage.googleapis.com/v1/projects/%s/services/firestore.googleapis.com:enable" % P, P, {})
        print("enable Firestore API ->", code)
        if code >= 300:
            raise SystemExit("Could not turn on the Firestore API automatically. Open https://console.developers.google.com/apis/api/firestore.googleapis.com/overview?project=%s and press Enable, then run this again. %s" % (P, json.dumps(j)[:300]))
        time.sleep(20)
    if not has_db:
        r = run("firestore:databases:create", "(default)", "--location", a.location, "--project", P, check=False)
        print((r.stdout + r.stderr).strip()[-400:])
        if r.returncode:
            raise SystemExit("Could not create the database automatically. In the Firebase console (Firestore Database) press Create database, choose production mode and a location, then run this again.")
    if not has_site:
        run("hosting:sites:create", S, "--project", P)
        print("created site", S)
    if not dom_ok and code == 200:
        new = domains + ["%s.web.app" % S, "%s.firebaseapp.com" % S]
        c2, j2 = call("PATCH", "https://identitytoolkit.googleapis.com/admin/v2/projects/%s/config?updateMask=authorizedDomains" % P, P, {"authorizedDomains": sorted(set(new))})
        print("authorize sign-in domain ->", c2)
        if c2 >= 300:
            print("Could not add it automatically. In the Firebase console: Authentication, Settings, Authorized domains, Add domain: %s.web.app" % S)

    tmp = Path(tempfile.mkdtemp(prefix="aqua_site_deploy_"))
    try:
        shutil.copytree(HERE / "sync_site" / "public", tmp / "public")
        r = run("apps:list", "WEB", "--project", P, check=False)
        app_id = next((w for w in r.stdout.replace("│", " ").split() if w.count(":") == 3 and ":web:" in w), "")
        if not app_id:
            raise SystemExit("No Firebase web app found in %s. Create one in the Firebase console (Project settings, Add app, Web) and run this again." % P)
        r = run("apps:sdkconfig", "WEB", app_id, "--project", P)
        conf = json.loads(r.stdout[r.stdout.index("{"):r.stdout.rindex("}") + 1])
        keep = {k: conf[k] for k in ("apiKey", "authDomain", "projectId", "appId") if k in conf}
        (tmp / "public" / "config.js").write_text("window.AQUA_CFG = %s;\n" % json.dumps(keep), encoding="utf-8")
        rules = (HERE / "sync_site" / "firestore.rules").read_text(encoding="utf-8").replace("__OWNER_EMAIL__", mail.replace("'", ""))
        (tmp / "firestore.rules").write_text(rules, encoding="utf-8")
        (tmp / ".firebaserc").write_text(json.dumps({"projects": {"default": P}}))
        (tmp / "firebase.json").write_text(json.dumps({
            "hosting": {"site": S, "public": "public", "cleanUrls": True, "ignore": ["firebase.json", "**/.*"],
                        "headers": [{"source": "**", "headers": [{"key": "X-Content-Type-Options", "value": "nosniff"}, {"key": "Referrer-Policy", "value": "no-referrer"},
                                                                 {"key": "X-Robots-Tag", "value": "noindex, nofollow"}]},
                                    {"source": "**/*.@(html|js|css)", "headers": [{"key": "Cache-Control", "value": "no-cache"}]}]},
            "firestore": {"rules": "firestore.rules"}}, indent=2))
        r = run("deploy", "--only", "hosting:%s,firestore:rules" % S, "--project", P, check=False, cwd=str(tmp))
        print((r.stdout + r.stderr).strip()[-1500:])
        if r.returncode:
            raise SystemExit("The deploy failed (see above).")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nDone. Open https://%s.web.app/ and sign in with Google. In Aqua Code: AI menu, Sync my projects, Link this computer." % S)


if __name__ == "__main__":
    main()
