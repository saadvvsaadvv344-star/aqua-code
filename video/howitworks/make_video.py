"""Builds the 'How Aqua Code works' presentation: 1920x1080 at 60 fps.

    pip install edge-tts playwright imageio-ffmpeg numpy
    python make_video.py            (uses Microsoft Edge, which Windows already has)

Steps: narration + sound design (build_audio.py) -> frames rendered from scenes.html in parallel shards (GPU, idle priority) -> encode + mux.
Flags: --skip-audio (reuse build/mix.wav and timeline.json), --skip-frames (only encode).
"""
import http.server
import json
import math
import os
import socketserver
import subprocess
import sys
import threading

import imageio_ffmpeg

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
FF = imageio_ffmpeg.get_ffmpeg_exe()
WORKERS = int(os.environ.get("VIDEO_WORKERS", "2"))   # kept low on purpose so it cannot fill the CPU
PORT = 8791


def run(*a, **k):
    print(">", " ".join(str(x) for x in a)[:120], flush=True)
    subprocess.run(list(a), check=True, **k)


if "--skip-audio" not in sys.argv:
    run(sys.executable, "build_audio.py")

tl = json.load(open("timeline.json"))
total = int(math.ceil(tl["end"] * tl["fps"]))
frames = os.path.join("build", "frames")
os.makedirs(frames, exist_ok=True)

if "--skip-frames" not in sys.argv:
    class Q(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        srv = S(("127.0.0.1", PORT), Q)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    except OSError:
        print("port %d already serving, reusing it" % PORT)
    per = math.ceil(total / WORKERS)
    flags = 0x00000040 if os.name == "nt" else 0          # idle priority: the PC stays responsive
    procs = [subprocess.Popen([sys.executable, "render_frames.py", str(PORT), str(k * per), str(min(total, (k + 1) * per))], creationflags=flags) for k in range(WORKERS)]
    for p in procs:
        p.wait()
        if p.returncode:
            raise SystemExit("a render shard failed")

out = os.path.join(HERE, "aqua-code-how-it-works-1080p60.mp4")
run(FF, "-y", "-loglevel", "error", "-framerate", str(tl["fps"]), "-i", os.path.join(frames, "f%05d.jpg"), "-i", os.path.join("build", "mix.wav"),
    "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2", "-r", str(tl["fps"]),
    "-af", "loudnorm=I=-16:TP=-1.5:LRA=9", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", "-shortest", out)
print("done:", out, "(%.1f MB)" % (os.path.getsize(out) / 1e6))
