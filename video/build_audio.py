"""Step 1 of the explainer video: narration, timeline, sound effects, music, final mix.

Voice: Microsoft neural TTS through edge-tts (the most natural free voice available), post-processed to sound like a
recorded voice (EQ, compression, light room). To use your OWN voice instead, put six recordings named
own_voice/line1.wav ... line6.wav next to this file; the timeline and every scene re-time themselves to your audio.

    python build_audio.py
"""
import asyncio
import json
import math
import os
import subprocess
import wave

import edge_tts
import imageio_ffmpeg
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "build")
os.makedirs(OUT, exist_ok=True)
FF = imageio_ffmpeg.get_ffmpeg_exe()
SR = 48000
CFG = json.load(open(os.path.join(HERE, "narration.json"), encoding="utf-8"))
LINES = CFG["lines"]
OWN = os.path.join(HERE, "own_voice")
RNG = np.random.default_rng(7)


def run_ff(*args):
    subprocess.run([FF, "-y", "-loglevel", "error", *args], check=True)


def read_wav(path):
    with wave.open(path, "rb") as w:
        n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
        data = np.frombuffer(w.readframes(n), dtype=np.int16 if sw == 2 else np.int32).astype(np.float32)
        data /= 32768.0 if sw == 2 else 2147483648.0
        if ch > 1:
            data = data.reshape(-1, ch).mean(axis=1)
    return data


async def tts(i, text):
    mp3 = os.path.join(OUT, "line%d.mp3" % i)
    words = []
    c = edge_tts.Communicate(text, CFG["voice"], rate=CFG.get("rate", "-3%"), boundary="WordBoundary")
    with open(mp3, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append({"w": ch["text"], "s": ch["offset"] / 1e7, "d": ch["duration"] / 1e7})
    return mp3, words


# ---------------------------------------------------------------- voice (per line)
VOICE_FILTER = ("highpass=f=75,equalizer=f=220:t=q:w=1:g=1.2,equalizer=f=3200:t=q:w=1.1:g=1.8,"
                "acompressor=threshold=-20dB:ratio=2.6:attack=6:release=90:makeup=2,"
                "aecho=0.72:0.55:28|61:0.10|0.05,loudnorm=I=-17:TP=-2:LRA=6")
voices, wordlists = [], []
for i, text in enumerate(LINES):
    own = os.path.join(OWN, "line%d.wav" % (i + 1))
    wav = os.path.join(OUT, "line%d.wav" % i)
    if os.path.exists(own):
        run_ff("-i", own, "-ar", str(SR), "-ac", "1", "-af", "highpass=f=70,loudnorm=I=-17:TP=-2:LRA=7", wav)
        words = None
    else:
        cache = os.path.join(OUT, "line%d.json" % i)
        mp3 = os.path.join(OUT, "line%d.mp3" % i)
        words = None
        if os.path.exists(cache) and os.path.exists(mp3):
            c = json.load(open(cache, encoding="utf-8"))
            if c.get("text") == text and c.get("voice") == CFG["voice"] and c.get("rate") == CFG.get("rate"):
                words = c["words"]
        if words is None:
            for attempt in range(5):
                try:
                    mp3, words = asyncio.run(tts(i, text))
                    break
                except Exception as ex:                      # the free service sometimes returns nothing; try again
                    print("line %d attempt %d failed: %s" % (i + 1, attempt + 1, ex))
                    import time
                    time.sleep(2 + attempt * 2)
                    if attempt == 3:
                        text = text.replace("'", "’")
            else:
                raise SystemExit("could not synthesise line %d" % (i + 1))
            json.dump({"text": LINES[i], "voice": CFG["voice"], "rate": CFG.get("rate"), "words": words}, open(cache, "w", encoding="utf-8"))
        run_ff("-i", mp3, "-ar", str(SR), "-ac", "1", "-af", VOICE_FILTER, wav)
    a = read_wav(wav)
    nz = np.where(np.abs(a) > 0.012)[0]
    t0 = max(0, int(nz[0]) - int(0.03 * SR))
    t1 = min(len(a), int(nz[-1]) + int(0.12 * SR))
    a = a[t0:t1]
    off = t0 / SR
    if words is None:                                   # own recording: spread the words evenly
        toks = text.split()
        d = len(a) / SR / len(toks)
        words = [{"w": w, "s": k * d, "d": d} for k, w in enumerate(toks)]
    else:
        for w in words:
            w["s"] = max(0.0, w["s"] - off)
    voices.append(a)
    wordlists.append(words)
    print("line %d: %.2fs, %d words" % (i + 1, len(a) / SR, len(words)))

# ---------------------------------------------------------------- timeline
LEAD, GAP, TAIL = 2.2, 0.95, 5.0
s, e, t = [], [], LEAD
for a in voices:
    s.append(t)
    t += len(a) / SR
    e.append(t)
    t += GAP
END = e[-1] + TAIL


def at(i, f):
    return s[i] + f * (e[i] - s[i])


N = len(LINES)
D = [e[i] - s[i] for i in range(N)]
ev = {}
ev["s1"] = {"drop": [0.55, 1.75], "title": 0.9}
ev["s2"] = {"card": s[1] + 0.05, "type": [s[1] + 0.45, s[1] + 2.35], "saved": s[1] + 2.55,
            "pills": [at(1, 0.30) + 0.17 * k for k in range(12)], "count": [at(1, 0.60), at(1, 0.60) + 1.5]}
ty0 = s[2] + 0.5
ty1 = ty0 + 0.30 * D[2]
R = e[2] - (ty1 + 0.2) - 0.4
ev["s3"] = {"win": s[2] + 0.02, "type": [ty0, ty1], "send": ty1 + 0.2, "tag": ty1 + 0.5,
            "cards": [ty1 + 0.5 + (0.10 + 0.15 * k) * R for k in range(5)], "done": e[2] - 1.1}
ev["s4"] = {"ladder": s[3] + 0.05, "fails": [at(3, 0.30), at(3, 0.42)], "handover": at(3, 0.52), "saved": [at(3, 0.70), at(3, 0.70) + 1.3]}
ev["s5"] = {"cards": s[4] + 0.05, "fill": [at(4, 0.14), at(4, 0.36)], "limit": at(4, 0.38), "switch": at(4, 0.47), "groq": at(4, 0.52),
            "pill": at(4, 0.60), "tags": [at(4, 0.74), at(4, 0.88)]}
ev["s6"] = {"win": s[5] + 0.02, "url": [s[5] + 0.35, s[5] + 1.3], "items": [at(5, 0.30) + 0.38 * k for k in range(4)], "dev": [at(5, 0.62), at(5, 0.80)]}
# every scene from line 7 on is driven by a plan: (fraction of its line, sound). The page reads the same times by index.
PLAN = {
    6: [(.04, "pop"), (.20, "pop"), (.32, "whoosh"), (.50, "confirm"), (.62, "pop"), (.78, "pop")],
    7: [(.05, "pop"), (.30, "tick"), (.40, "tick"), (.50, "tick"), (.62, "pop"), (.82, "confirm")],
    8: [(.05, "pop")] + [(.30 + .085 * k, "tick") for k in range(6)] + [(.74, "sparkle")],
    9: [(.05, "pop"), (.20, "keys0"), (.33, "keys1"), (.36, "pop"), (.40, "none"), (.61, "click"), (.67, "confirm"), (.85, "sparkle")],
    10: [(.14 + .13 * k, "pop") for k in range(4)] + [(.58 + .07 * k, "tick") for k in range(4)] + [(.84, "sparkle")],
    11: [(.10 + .14 * k, "pop") for k in range(4)] + [(.58 + .07 * k, "click") for k in range(4)] + [(.86, "pop")],
    12: [(.05, "pop"), (.12, "none"), (.27, "click"), (.39, "click"), (.46, "keys0"), (.64, "keys1"), (.73, "click"), (.81, "confirm"), (.86, "sparkle")],
    13: [(.12 + .15 * k, "confirm") for k in range(3)] + [(.60 + .07 * k, "pop") for k in range(4)] + [(.88, "whoosh")],
    14: [(.08 + .12 * k, "pop") for k in range(6)],
    15: [(.10 + .10 * k, "tick") for k in range(8)] + [(.90, "confirm")],
    16: [(.06, "pop")] + [(.18 + .12 * k, "tick") for k in range(4)] + [(.64, "pop"), (.76, "click"), (.86, "pop"), (.92, "click")],
    17: [(.05, "pop"), (.15, "tick"), (.35, "pop"), (.50, "pop"), (.68, "tick"), (.85, "sparkle")],
    18: [(.08, "pop"), (.25, "pop"), (.35, "pop"), (.45, "pop"), (.55, "buzz"), (.62, "whoosh"), (.78, "pop"), (.90, "click")],
    19: [(.05, "pop"), (.30, "none"), (.59, "click"), (.66, "confirm"), (.82, "tick")],
    20: [(.05, "pop")] + [(.15 + .07 * k, "tick") for k in range(4)] + [(.45, "pop"), (.60, "pop"), (.75, "pop"), (.88, "pop")],
    21: [(.10, "click"), (.14, "whoosh"), (.38, "pop"), (.55, "pop"), (.70, "pop"), (.82, "pop"), (.92, "pop")],
    22: [(.10, "pop"), (.42, "pop"), (.74, "pop")],
}
ev["p"] = {str(k): [at(k, f) for f, _ in v] for k, v in PLAN.items()}
ev["out"] = {"drop": [s[N - 1] + 0.35, s[N - 1] + 1.5], "text": s[N - 1] + 0.85}
scenes = [[0 if k == 0 else s[k] - 0.5, END if k == N - 1 else s[k + 1] - 0.1] for k in range(N)]
timeline = {"end": END, "fps": 30, "scenes": scenes, "ev": ev,
            "lines": [{"s": s[i], "e": e[i], "text": LINES[i], "words": [{"w": w["w"], "s": s[i] + w["s"], "e": s[i] + w["s"] + w["d"]} for w in wordlists[i]]} for i in range(N)]}
json.dump(timeline, open(os.path.join(HERE, "timeline.json"), "w"), indent=1)
print("video length: %.1fs" % END)

# ---------------------------------------------------------------- synthesis helpers
N = int(END * SR) + SR
t_ = lambda dur: np.arange(int(dur * SR)) / SR


def env_exp(dur, decay, attack=0.004):
    t = t_(dur)
    return np.minimum(1, t / attack) * np.exp(-t * decay)


def lp(x, fc):
    a = math.exp(-2 * math.pi * fc / SR)
    out = np.empty_like(x)
    y = 0.0
    for i in range(len(x)):                       # short sounds only
        y = (1 - a) * x[i] + a * y
        out[i] = y
    return out


def smooth_noise(dur, k):
    n = RNG.standard_normal(int(dur * SR))
    ker = np.ones(k) / k
    return np.convolve(n, ker, mode="same")


def fx_drop(f0=1500, f1=420, dur=0.55):
    t = t_(dur)
    f = f1 + (f0 - f1) * np.exp(-t * 38)
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = np.sin(ph) * env_exp(dur, 9)
    x += 0.35 * np.sin(2 * ph + 0.4) * env_exp(dur, 14)
    sp = (RNG.standard_normal(len(t)) - smooth_noise(dur, 12)) * env_exp(dur, 30, 0.001) * 0.18
    ring = np.sin(2 * np.pi * 280 * t) * env_exp(dur, 6) * 0.25
    return (x * 0.7 + sp + ring) * 0.8


def fx_whoosh(dur=0.9, up=True):
    n = RNG.standard_normal(int(dur * SR))
    out = np.zeros_like(n)
    for k, w in enumerate((4, 12, 36, 110)):
        out += np.convolve(n, np.ones(w) / w, mode="same") * (w ** 0.5)
    t = t_(dur)
    e_ = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 2.2
    if not up:
        e_ = e_[::-1] ** 1 * np.sin(np.pi * np.clip(t / dur, 0, 1))
    sweep = np.linspace(0.3, 1, len(t)) if up else np.linspace(1, 0.3, len(t))
    return out * e_ * sweep * 0.16


def fx_pop(f=720):
    t = t_(0.16)
    fr = f * 0.55 + f * 0.45 * np.exp(-t * 55)
    return np.sin(2 * np.pi * np.cumsum(fr) / SR) * env_exp(0.16, 32, 0.002) * 0.55


def fx_tick(f=2400):
    t = t_(0.05)
    n = (RNG.standard_normal(len(t)) * env_exp(0.05, 140, 0.0005)) * 0.35
    return n + np.sin(2 * np.pi * f * t) * env_exp(0.05, 120, 0.0004) * 0.4


def fx_key():
    t = t_(0.06)
    return (RNG.standard_normal(len(t)) * env_exp(0.06, 160, 0.0004) * 0.25 + np.sin(2 * np.pi * 180 * t) * env_exp(0.06, 90, 0.0004) * 0.5) * 0.9


def fx_bell(f=880, dur=1.8, g=0.5):
    t = t_(dur)
    x = np.zeros(len(t))
    for ratio, amp, dec in ((1, 1, 2.2), (2.76, 0.45, 3.4), (5.4, 0.2, 5.5), (8.9, 0.08, 8)):
        x += amp * np.sin(2 * np.pi * f * ratio * t) * np.exp(-t * dec)
    return x * np.minimum(1, t / 0.002) * g * 0.5


def fx_buzz(dur=0.34):
    t = t_(dur)
    x = np.sign(np.sin(2 * np.pi * 98 * t)) * 0.5 + np.sin(2 * np.pi * 147 * t) * 0.3
    return lp(x * env_exp(dur, 6, 0.01), 900) * 0.7


def fx_riser(dur=1.3):
    n = RNG.standard_normal(int(dur * SR))
    t = t_(dur)
    out = np.zeros_like(n)
    for w in (6, 20, 60):
        out += np.convolve(n, np.ones(w) / w, mode="same") * w ** 0.5
    tone = np.sin(2 * np.pi * np.cumsum(180 + 900 * (t / dur) ** 2) / SR)
    return (out * 0.06 + tone * 0.16) * (t / dur) ** 2.2 * np.minimum(1, (dur - t) / 0.05 + 0.0)


def fx_sparkle():
    out = np.zeros(int(1.1 * SR))
    for k, f in enumerate((1318, 1568, 1976, 2349, 2637)):
        b = fx_bell(f, 0.8, 0.22)
        o = int(k * 0.06 * SR)
        out[o:o + len(b)] += b[:len(out) - o]
    return out


def fx_sweepdown(dur=1.0):
    t = t_(dur)
    f = 1100 * np.exp(-t * 2.1) + 120
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.sin(np.pi * np.clip(t / dur, 0, 1)) * 0.35


def fx_confirm():
    a, b = fx_bell(880, 1.1, 0.4), fx_bell(1320, 1.3, 0.38)
    out = np.zeros(int(1.6 * SR))
    out[:len(a)] += a
    o = int(0.11 * SR)
    out[o:o + len(b)] += b
    return out


def fx_chime_end():
    out = np.zeros(int(3.2 * SR))
    for k, f in enumerate((523.25, 659.25, 783.99, 1046.5)):
        b = fx_bell(f, 2.8, 0.34)
        o = int(k * 0.09 * SR)
        out[o:o + len(b)] += b[:len(out) - o]
    return out


def fx_click():
    t = t_(0.04)
    return (np.sin(2 * np.pi * 1500 * t) * env_exp(0.04, 160, 0.0004) * 0.5 + RNG.standard_normal(len(t)) * env_exp(0.04, 200, 0.0003) * 0.2)


# ---------------------------------------------------------------- sfx placement
mix_l = np.zeros(N, dtype=np.float32)
mix_r = np.zeros(N, dtype=np.float32)


def place(buf, t, gain=1.0, pan=0.0):
    i = int(t * SR)
    if i < 0 or i >= N:
        return
    n = min(len(buf), N - i)
    l = math.cos((pan + 1) * math.pi / 4)
    r = math.sin((pan + 1) * math.pi / 4)
    mix_l[i:i + n] += (buf[:n] * gain * l).astype(np.float32)
    mix_r[i:i + n] += (buf[:n] * gain * r).astype(np.float32)


for t0 in ev["s1"]["drop"]:
    place(fx_drop(), t0, 0.9)
for k in range(len(scenes)):
    place(fx_whoosh(0.9, True), scenes[k][0], 0.85 if k else 0.0)
place(fx_whoosh(1.4, True), 0.9, 0.5)
# scene 2
p = ev["s2"]
place(fx_pop(540), p["card"], 0.8, -0.3)
n_keys = 17
for k in range(n_keys):
    place(fx_key(), p["type"][0] + k * (p["type"][1] - p["type"][0]) / n_keys, 0.55, -0.2)
place(fx_click(), p["saved"], 0.9, -0.2)
place(fx_confirm(), p["saved"] + 0.05, 0.55, -0.2)
for k, t0 in enumerate(p["pills"]):
    place(fx_pop(560 + 38 * (k % 6)), t0, 0.55, 0.15 + 0.1 * (k % 3))
for k in range(14):
    place(fx_tick(2200 + 80 * k), p["count"][0] + k * (p["count"][1] - p["count"][0]) / 14, 0.35, 0.3)
place(fx_sparkle(), p["count"][1], 0.5, 0.3)
# scene 3
p = ev["s3"]
place(fx_pop(500), p["win"], 0.8)
for k in range(30):
    place(fx_key(), p["type"][0] + k * (p["type"][1] - p["type"][0]) / 30, 0.5)
place(fx_whoosh(0.5, True), p["send"], 0.5)
place(fx_click(), p["send"], 0.9)
for k, t0 in enumerate(p["cards"]):
    place(fx_pop(640 + 40 * k), t0, 0.6)
    if k == 2:
        place(fx_buzz(), t0 + 0.9, 0.7)
    else:
        place(fx_tick(3000), t0 + 0.8, 0.5)
place(fx_confirm(), p["done"], 0.75)
# scene 4
p = ev["s4"]
place(fx_pop(480), p["ladder"], 0.8)
for t0 in p["fails"]:
    place(fx_buzz(0.28), t0, 0.7)
place(fx_riser(1.35), p["handover"] - 1.1, 0.8)
place(fx_whoosh(0.8, True), p["handover"] - 0.1, 0.8)
place(fx_confirm(), p["handover"] + 0.15, 0.7)
for k in range(12):
    place(fx_tick(2400 + 90 * k), p["saved"][0] + k * (p["saved"][1] - p["saved"][0]) / 12, 0.35)
place(fx_sparkle(), p["saved"][1], 0.55)
# scene 5
p = ev["s5"]
place(fx_pop(520), p["cards"], 0.8)
place(fx_sweepdown(p["fill"][1] - p["fill"][0] + 0.4), p["fill"][0], 0.55)
place(fx_buzz(0.4), p["limit"], 0.8)
place(fx_whoosh(0.9, True), p["switch"], 0.9)
place(fx_confirm(), p["groq"], 0.75)
place(fx_pop(700), p["pill"], 0.7)
for t0 in p["tags"]:
    place(fx_tick(3200), t0, 0.55)
# scene 6
p = ev["s6"]
place(fx_pop(480), p["win"], 0.85)
for k in range(14):
    place(fx_key(), p["url"][0] + k * (p["url"][1] - p["url"][0]) / 14, 0.45)
place(fx_click(), p["url"][1] + 0.1, 0.8)
for k, t0 in enumerate(p["items"]):
    place(fx_pop(620 + 60 * k), t0, 0.6, 0.25)
for t0 in p["dev"]:
    place(fx_click(), t0, 0.9, 0.2)
    place(fx_whoosh(0.35, True), t0, 0.35, 0.2)
# planned scenes
for k, plan in PLAN.items():
    ts = ev["p"][str(k)]
    for idx, (f, kind) in enumerate(plan):
        t0 = ts[idx]
        pan = -0.3 + 0.1 * (idx % 7)
        if kind == "pop":
            place(fx_pop(500 + 38 * (idx % 7)), t0, 0.62, pan)
        elif kind == "tick":
            place(fx_tick(2300 + 110 * (idx % 8)), t0, 0.45, pan)
        elif kind == "click":
            place(fx_click(), t0, 0.85, pan)
        elif kind == "confirm":
            place(fx_confirm(), t0, 0.6, pan)
        elif kind == "sparkle":
            place(fx_sparkle(), t0, 0.5, pan)
        elif kind == "buzz":
            place(fx_buzz(), t0, 0.7, pan)
        elif kind == "whoosh":
            place(fx_whoosh(0.8, True), t0, 0.6, pan)
        elif kind == "keys0":
            t1 = ts[idx + 1]
            n_ = max(6, int((t1 - t0) / 0.07))
            for q in range(n_):
                place(fx_key(), t0 + q * (t1 - t0) / n_, 0.45, pan)
# outro
place(fx_drop(1300, 380, 0.7), ev["out"]["drop"][0], 1.0)
place(fx_drop(1500, 460, 0.7), ev["out"]["drop"][1], 0.7)
place(fx_chime_end(), ev["out"]["text"], 0.9)

# ---------------------------------------------------------------- music bed (ambient pad + soft pulse)
t = np.arange(N) / SR
music = np.zeros(N)
chords = [(110.0, 130.81, 164.81), (87.31, 130.81, 174.61), (130.81, 164.81, 196.0), (98.0, 146.83, 196.0)]
bar = 6.0
for b in range(int(END / bar) + 2):
    ch = chords[b % 4]
    a0, a1 = int(b * bar * SR), min(N, int((b + 1) * bar * SR + 2.5 * SR))
    if a0 >= N:
        break
    tt = t[a0:a1] - b * bar
    env = np.minimum(1, tt / 1.8) * np.exp(-np.maximum(0, tt - bar) * 1.2)
    seg = np.zeros(len(tt))
    for f in ch:
        for det, amp in ((0.997, 0.5), (1.003, 0.5), (2.0, 0.18)):
            seg += amp * np.sin(2 * np.pi * f * det * tt + 0.7 * np.sin(2 * np.pi * 0.13 * tt))
    seg += 0.5 * np.sin(2 * np.pi * (ch[0] / 2) * tt)
    music[a0:a1] += seg * env * 0.05
# gentle plucks
notes = [(0, 440.0), (1, 523.25), (2, 659.25), (3, 523.25), (4, 392.0), (5, 493.88), (6, 587.33), (7, 493.88)]
step = 0.75
for k in range(int((END - 2) / step)):
    f = notes[k % 8][1] * (1.0 if (k // 8) % 2 == 0 else 1.122)
    t0 = 1.2 + k * step
    pl = fx_bell(f, 1.1, 0.07)
    i0 = int(t0 * SR)
    n = min(len(pl), N - i0)
    if n > 0:
        music[i0:i0 + n] += pl[:n] * 0.5
music *= np.minimum(1, t / 2.5) * np.minimum(1, np.maximum(0, (END - t)) / 3.0)

# ---------------------------------------------------------------- voice track + ducking + mix
voice = np.zeros(N, dtype=np.float32)
for i, a in enumerate(voices):
    o = int(s[i] * SR)
    voice[o:o + len(a)] += a
def movavg(x, k):
    c = np.cumsum(np.insert(x.astype(np.float64), 0, 0.0))
    y = (c[k:] - c[:-k]) / k
    pad = len(x) - len(y)
    return np.concatenate([np.full(pad // 2, y[0]), y, np.full(pad - pad // 2, y[-1])])


env = movavg(np.abs(voice), int(0.06 * SR))
duck = np.clip(env / 0.03, 0, 1)
duck = movavg(duck, int(0.25 * SR))
music_gain = 1.0 - 0.62 * duck
sfx_gain = 1.0 - 0.28 * duck

left = voice * 1.0 + mix_l * sfx_gain + music * music_gain * 1.0
right = voice * 1.0 + mix_r * sfx_gain + music * music_gain * 0.96
st = np.stack([left, right], axis=1)
peak = np.abs(st).max()
st = np.tanh(st * 0.95) / np.tanh(0.95)
st *= 0.89 / max(1e-6, np.abs(st).max())
pcm = (st * 32767).astype(np.int16)
with wave.open(os.path.join(OUT, "mix.wav"), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
print("mix.wav written (peak before limiter %.2f)" % peak)
