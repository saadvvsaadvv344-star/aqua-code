"""Aqua Code desktop launcher.

Starts the local server and opens it in its own app window (Edge or Chrome in --app mode:
no tabs, no address bar). When the window is closed, the server shuts down.

    pythonw aqua_app.py        (no console window)
    python  aqua_app.py
"""
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import importlib

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import server  # noqa: E402

PORT = server.PORT
URL = "http://127.0.0.1:%d" % PORT
LOG = os.path.join(server.DATA, "aqua.log")


def log(*a):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass


def port_open(p):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", p)) == 0


def is_aqua_running():
    try:
        with urllib.request.urlopen(URL + "/api/ping", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def find_browser():
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
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    return next((c for c in cands if os.path.exists(c)), None)


def open_window():
    exe = find_browser()
    if not exe:
        webbrowser.open(URL)
        return None
    prof = os.path.join(server.DATA, "window-profile")
    os.makedirs(prof, exist_ok=True)
    args = [exe, "--app=" + URL, "--user-data-dir=" + prof, "--window-size=1400,900",
            "--no-first-run", "--no-default-browser-check", "--disable-features=Translate,msEdgeShopping"]
    return subprocess.Popen(args)


def source_ok(path):
    try:
        with open(path, encoding="utf-8") as f:
            compile(f.read(), path, "exec")
        return True
    except Exception as e:
        log("server.py has an error, keeping the running version:", repr(e))
        return False


def hot_reload(srv):
    """Swap in the new server.py without closing the app window."""
    keep = (dict(server.SERVERS), server.PREVIEW_PORT, server.LAST_PING)
    srv.shutdown()
    srv.server_close()
    try:
        import sync as _sync
        importlib.reload(_sync)                    # the sync engine is a separate file: pick up its changes too
    except Exception as e:
        log("sync.py could not be reloaded:", repr(e))
    importlib.reload(server)
    server.SERVERS.update(keep[0])
    server.PREVIEW_PORT, server.LAST_PING = keep[1], keep[2]
    server.sync_start()
    new = server.ThreadingHTTPServer(("127.0.0.1", PORT), server.H)
    new.daemon_threads = True
    threading.Thread(target=new.serve_forever, daemon=True).start()
    log("server.py reloaded without restarting the window")
    return new


def ensure_mac_app():
    """macOS: make a real 'Aqua Code' app (with the logo) in ~/Applications, so it shows up in Launchpad, Spotlight and the Dock."""
    if sys.platform != "darwin":
        return
    try:
        app = os.path.join(os.path.expanduser("~"), "Applications", "Aqua Code.app")
        exe = os.path.join(app, "Contents", "MacOS", "AquaCode")
        script = '#!/bin/bash\ncd "%s"\nexec "%s" aqua_app.py\n' % (ROOT, sys.executable)
        if os.path.isfile(exe) and open(exe).read() == script:
            return
        os.makedirs(os.path.dirname(exe), exist_ok=True)
        os.makedirs(os.path.join(app, "Contents", "Resources"), exist_ok=True)
        with open(exe, "w") as f:
            f.write(script)
        os.chmod(exe, 0o755)
        icns = os.path.join(ROOT, "assets", "aqua.icns")
        if os.path.isfile(icns):
            shutil.copy2(icns, os.path.join(app, "Contents", "Resources", "aqua.icns"))
        with open(os.path.join(app, "Contents", "Info.plist"), "w") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict><key>CFBundleName</key><string>Aqua Code</string><key>CFBundleDisplayName</key><string>Aqua Code</string>'
                    '<key>CFBundleIdentifier</key><string>com.aquacode.app</string><key>CFBundleExecutable</key><string>AquaCode</string>'
                    '<key>CFBundleIconFile</key><string>aqua</string><key>CFBundlePackageType</key><string>APPL</string>'
                    '<key>CFBundleShortVersionString</key><string>1.1</string><key>LSMinimumSystemVersion</key><string>10.13</string></dict></plist>\n')
        subprocess.call(["touch", app])
        log("created", app)
    except Exception as e:
        log("mac app bundle failed", e)


def main():
    ensure_mac_app()
    if is_aqua_running():            # already running: just open another window
        log("server already running")
        open_window()
        return
    if port_open(PORT):
        log("port busy by something else")
        return
    srv = server.ThreadingHTTPServer(("127.0.0.1", PORT), server.H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    server.start_preview_server()
    server.sync_start()
    log("server up", URL)
    server.LAST_PING = time.time()
    t_start = time.time()
    proc = open_window()
    spath = os.path.join(ROOT, "server.py")
    smt = os.path.getmtime(spath)
    # exit when the window is gone: heartbeat from the page stops (or the window process ends)
    while True:
        time.sleep(1.5)
        busy = any(not r.done for r in server.runs.values())
        silent = time.time() - server.LAST_PING
        gone = proc is not None and proc.poll() is not None and time.time() - t_start > 8
        if not busy and (silent > 150 or (gone and silent > 12)):
            break
        try:
            mt = os.path.getmtime(spath)
        except OSError:
            mt = smt
        if mt != smt and not busy:
            time.sleep(0.5)
            smt = os.path.getmtime(spath)
            if source_ok(spath):
                try:
                    srv = hot_reload(srv)
                except Exception as e:
                    log("hot reload failed:", repr(e))
    log("window closed, shutting down")
    srv.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # pythonw has no console, so keep a log
        log("crash:", repr(e))
        raise
