# Aqua Code explainer video

`aqua-code-explainer.mp4` is built by code from this folder: 1080p, 30 fps, about 4.5 minutes (the finished file is attached to the project's Releases, not stored in git).

- `narration.json`: the six lines and the voice settings.
- `build_audio.py`: narration (Microsoft neural voice through `edge-tts`, then EQ, compression and a light room), the timeline, every sound effect and the music bed, mixed with the music ducking under the voice.
- `scenes.html`: all visuals, drawn by code and driven by `timeline.json` (so the picture follows the voice exactly).
- `render_frames.py` and `make_video.py`: render the frames in parallel with Edge, then encode and mux.

```
pip install edge-tts playwright imageio-ffmpeg numpy
python make_video.py
```

## Use your own voice

The narration is a synthetic (neural) voice. For a real human voice, record the six lines from `narration.json` and save them as
`own_voice/line1.wav` ... `own_voice/line6.wav`, then run `python make_video.py` again. The timeline and every scene re-time themselves
to your recordings, and the music, sound effects and captions follow.
