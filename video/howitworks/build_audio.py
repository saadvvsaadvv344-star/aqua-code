"""How-it-works presentation (60 fps). Step 1: narration, timeline, sound effects, music, mix. Based on the explainer pipeline.

Original docstring: Step 1 of the explainer video: narration, timeline, sound effects, music, final mix.

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
LEAD, GAP, TAIL = 2.0, 0.8, 4.5
s, e, t = [], [], LEAD
for a in voices:
    s.append(t)
    t += len(a) / SR
    e.append(t)
    t += GAP
END = e[-1] + TAIL
NL = len(LINES)


def at(i, f):
    return s[i] + f * (e[i] - s[i])


ev = {}
# every scene from 1 to 10 is driven by a plan: (fraction of its line, sound). The page reads the same times by index.
PLAN = {
    1: [(.04, "pop"), (.08, "keys0"), (.30, "keys1"), (.36, "whoosh"), (.50, "confirm"), (.58, "tick"), (.66, "tick"), (.74, "tick"), (.82, "tick")],
    2: [(.05, "pop"), (.18, "pop"), (.32, "pop"), (.50, "buzz"), (.62, "whoosh"), (.74, "confirm")],
    3: [(.05, "pop")] + [(.20 + .10 * k, "tick") for k in range(6)] + [(.90, "sparkle")],
    4: [(.05, "pop"), (.17, "pop"), (.29, "pop"), (.41, "pop"), (.58, "pop"), (.70, "pop"), (.82, "pop"), (.93, "sparkle")],
    5: [(.04, "pop"), (.18, "tick"), (.30, "buzz"), (.42, "whoosh"), (.58, "click"), (.72, "confirm"), (.88, "sparkle")],
    6: [(.04, "pop"), (.14, "pop"), (.26, "whoosh"), (.38, "tick"), (.46, "tick"), (.56, "buzz"), (.68, "whoosh"), (.82, "confirm"), (.92, "sparkle")],
    7: [(.04, "pop"), (.16, "pop"), (.26, "pop"), (.36, "pop"), (.44, "whoosh")] + [(.56 + .08 * k, "tick") for k in range(4)] + [(.92, "sparkle")],
    8: [(.04, "pop"), (.16, "none"), (.30, "buzz"), (.40, "whoosh"), (.46, "confirm"), (.60, "pop"), (.78, "pop")],
    9: [(.05, "pop"), (.16, "pop"), (.26, "pop"), (.36, "pop"), (.52, "click"), (.60, "confirm"), (.76, "pop")],
    10: [(.05, "pop"), (.35, "click"), (.42, "confirm"), (.60, "pop"), (.72, "pop"), (.84, "pop"), (.93, "sparkle")],
}
ev["p"] = {str(k): [at(k, f) for f, _ in v] for k, v in PLAN.items()}
ev["intro"] = {"drop": [0.45, 1.35], "text": 0.95}
ev["out"] = {"drop": [s[NL - 1] + 0.30, s[NL - 1] + 1.20], "text": s[NL - 1] + 0.80}
scenes = [[0 if k == 0 else s[k] - 0.55, END if k == NL - 1 else s[k + 1] - 0.15] for k in range(NL)]
timeline = {"end": END, "fps": 60, "scenes": scenes, "ev": ev,
            "lines": [{"s": s[i], "e": e[i], "text": LINES[i], "words": [{"w": w["w"], "s": s[i] + w["s"], "e": s[i] + w["s"] + w["d"]} for w in wordlists[i]]} for i in range(NL)]}
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


# intro + scene changes
for t0 in ev["intro"]["drop"]:
    place(fx_drop(), t0, 0.9)
place(fx_whoosh(1.4, True), 0.9, 0.5)
for k in range(1, NL):
    place(fx_whoosh(0.9, True), scenes[k][0], 0.7)
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
