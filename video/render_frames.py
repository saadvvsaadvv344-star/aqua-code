"""Renders video frames with Edge (headless) by seeking the deterministic scene page. Run by make_video.py in parallel shards.
Uses the graphics card (ANGLE / Direct3D 11) when possible: about 40x faster than software rendering and almost no CPU.
Frames that already exist are skipped, so an interrupted render simply continues.

    python render_frames.py <port> <first> <last_exclusive>

Environment: VIDEO_GPU=0 forces software rendering, VIDEO_FAST=1 drops the background blur (only useful without a GPU).
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
port, first, last = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
tl = json.load(open(os.path.join(HERE, "timeline.json")))
fps = tl["fps"]
frames = os.path.join(HERE, "build", "frames")
os.makedirs(frames, exist_ok=True)
GPU = os.environ.get("VIDEO_GPU", "1") == "1"
FAST = os.environ.get("VIDEO_FAST", "0") == "1"

args = ["--ignore-gpu-blocklist", "--force-device-scale-factor=1"]
if GPU:
    args += ["--use-gl=angle", "--use-angle=d3d11", "--enable-gpu-rasterization", "--enable-zero-copy", "--disable-gpu-sandbox"]
else:
    args += ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True, args=args, ignore_default_args=["--disable-gpu"] if GPU else None)
    page = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    page.goto("http://127.0.0.1:%d/scenes.html%s" % (port, "?fast=1" if FAST else ""))
    page.wait_for_function("window.READY === true", timeout=60000)
    page.evaluate("document.fonts.ready")
    for i in range(first, last):
        path = os.path.join(frames, "f%05d.jpg" % i)
        if os.path.exists(path) and os.path.getsize(path) > 20000:
            continue
        page.evaluate("t => window.seek(t)", i / fps)
        page.screenshot(path=path, type="jpeg", quality=92)
    b.close()
print("shard done", first, last)
