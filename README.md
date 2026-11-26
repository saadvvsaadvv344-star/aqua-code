<h2 align="center"><a href="https://youtu.be/VPRA-yaupJc">Watch the video on YouTube: how Aqua Code works + free API key tutorial</a></h2>

<p align="center"><img src="assets/logo.png" width="120" alt="Aqua Code logo"></p>

# Aqua Code


**Your own coding agent. Bring your DeepSeek or Gemini key, start cheap, escalate only when needed.**

Aqua Code is a local, single-file-server coding agent with a liquid-glass interface. It reads and edits files in a workspace folder, runs commands, and shows you exactly what it costs. Made by Saad.

- **Watch the 5-minute explainer** (every feature, plus the free API key tutorial): [video/aqua-code-explainer-github.mp4](video/aqua-code-explainer-github.mp4)
- **Where to get free API keys** (every link in one place): [FREE_API_KEYS.md](FREE_API_KEYS.md)
- Every AI can also build 3D models (see "3D modeling" below).
- No dependencies: Python 3.9+ standard library only.
- Runs on `127.0.0.1`. Your key never leaves your machine except to call the provider you chose.

## Install

Pick whichever is easiest. Nothing else to download: Python 3.9 or newer is the only requirement, and the app has no packages.

| | |
| --- | --- |
| **Download button** | [Download the ZIP](https://github.com/saadvvsaadvv344-star/aqua-code/archive/refs/heads/main.zip), unzip it, then double-click `Start Aqua Code.bat` (Windows) or `Start Aqua Code.command` (Mac). Releases also has a ready-made `aqua-code.zip`. |
| **Windows (PowerShell)** | `irm https://raw.githubusercontent.com/saadvvsaadvv344-star/aqua-code/main/install.ps1 \| iex` |
| **macOS (Intel and Apple Silicon) and Linux (Terminal)** | `curl -fsSL https://raw.githubusercontent.com/saadvvsaadvv344-star/aqua-code/main/install.sh \| bash` |
| **git** | `git clone https://github.com/saadvvsaadvv344-star/aqua-code.git && cd aqua-code && python3 aqua_app.py` |

The installers put Aqua Code in `~/AquaCode` (or `%USERPROFILE%\AquaCode`), add a Desktop launcher, and keep your keys, chats and workspace when you run them again to update.

It opens as its own desktop window (Edge, Chrome, Brave or Chromium in app mode: no tabs, no address bar) and stops its server when you close the window. If no Chromium browser is installed (for example only Safari or Firefox), it opens in a normal browser tab instead. You can also run only the server: `python3 server.py`, then open http://127.0.0.1:8765.

### macOS notes (Intel and Apple Silicon)

- Install Python 3.9+ from https://www.python.org/downloads/ (or `brew install python`). The app itself needs nothing else.
- Intel Macs are fully supported in design: there is no native code, and the glass effects default to **Lite**. If it ever feels heavy, open Settings and set **Glass quality** to **Off** for a plain flat look.
- If double-clicking `Start Aqua Code.command` is blocked, run `chmod +x "Start Aqua Code.command"` once, or just run `python3 aqua_app.py` in Terminal.
- I developed and tested this on Windows 11. The macOS and Linux paths are written to the same standard-library code and checked for Python 3.9 compatibility, but I could not run them on a real Mac. Please open an issue if something misbehaves.

### Working level (how hard Aqua works on your PC)

The chat bar (next to Auto / Lite / Pro and the approval mode) has a **working-level swapper: Low, Balanced or Max**. Drag it or click a stop; it applies instantly with no restart. Settings has the same choice.

| Level | What it does |
| --- | --- |
| **Low** | Gentle. Plain look (glass off), no animations, no intro, and the Aqua server runs at low priority on a quarter of your CPU cores (4 of 16, never fewer than 2). Anything it starts (commands, dev servers) inherits that. |
| **Balanced** (default) | Soft glass, normal priority, all cores. |
| **Max** | Full liquid glass plus **live water light** over your normal background (the background itself never changes): ripples follow your cursor and clicks, with caustic light and reflections. If your PC cannot keep up, Aqua drops back to Balanced by itself. |

On Windows you can switch levels at any time. On macOS and Linux a lowered priority cannot be raised again without administrator rights, so going back up from Low needs an app restart there. Heavy jobs such as rendering the explainer video run with their own limits (see `video/README.md`).

**The level also sets how long the AI keeps working.** Low: it stops as soon as the task is done. Balanced: after it says "done" it keeps testing, adding detail and polishing for up to **30 minutes**. Max: up to **1 hour** (up to 400 steps), in rounds (test and fix, add depth, polish, code quality). It does not stop on its own: if the AI says "done" without changing anything, Aqua tells it so and makes it pick the next biggest improvement (with a short pause between tries so free limits are not wasted), waits out short rate limits and network hiccups, and only ends when the time is over, you press Stop, or every connected AI has used up its daily limit. Long runs shorten old tool output automatically so the model's memory does not overflow. Remember that free API limits still apply: a long Max run uses many requests, so connect several free keys.

### Plain mode (remove the liquid glass)

Settings, **Glass quality**: **Full** (real refraction), **Lite** (soft glass, default) or **Off** (flat panels, no blur, no animation, no effects: fastest). **Water intro animation** can be switched off separately.

## Quick start

1. Start it (see Install).
2. Paste a free API key on the first screen, or press **Try without a key**.
3. Describe what you want built.

Without any key, Aqua uses the one free model that still answers anonymously (LLM7, gpt-oss:20b). It is slow, rate limited, and your prompts are public, so add a free key as soon as you can. You can turn this fallback off in Settings.

## Free API keys

Aqua Code is set up to work on free keys. On first launch it shows a step-by-step guide per provider and an **Open the key page** button.

| Provider | Free? | Notes |
| --- | --- | --- |
| **Gemini** (Google AI Studio) | Yes, free tier, no credit card | Key at https://aistudio.google.com/apikey. Limits apply per project and per model, reset at midnight Pacific, and can change; check the rate-limit page in AI Studio. On the free tier Google may use your prompts to improve its products, so do not send secrets. |
| **DeepSeek** (official API) | No permanent free tier | Pay as you go and very cheap. New accounts sometimes get a one-time trial credit; the amount and conditions vary, so check https://platform.deepseek.com. |
| **OpenRouter** (free models) | Yes, `:free` models, no card | Key at https://openrouter.ai/keys. As of 2026-11-26 OpenRouter lists no free DeepSeek model, so Aqua picks other free models that support tools (it re-checks the list when you connect). Free models are capped (roughly 20 requests/minute and 50/day until you buy $10 of credits). |

What the app does for free keys:

- A provider marked **free-tier** counts its cost as $0 and shows a requests-used ring instead of dollars.
- It respects each model's requests-per-minute and requests-per-day limits (editable per tier).
- When a model hits its limit (including a free model whose quota is 0), it switches to another model that still has quota instead of failing, and tells you in the chat.
- If every model is used up it stops and tells you when the limits reset.

More permanent free tiers from the community lists ([mnfst/awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis), [amardeeplakshkar/awesome-free-llm-apis](https://github.com/amardeeplakshkar/awesome-free-llm-apis)) are built in as presets, each with its own key: **Groq**, **Cerebras**, **Mistral** and **NVIDIA NIM**. Limits are as those lists state them and may be out of date. When you connect, Aqua asks the provider for its model list and swaps in the closest match for any default model name that no longer exists. In Settings you can also load a provider's model list into the model boxes, and add **any other OpenAI-compatible service** (Kilo, Runcrate, OVHcloud, a local server...) as a custom provider. Check each provider's terms: several free tiers log prompts or use them for training.

Beyond those, `catalog.json` lists 19 more providers (free tiers, small community gateways, and four that need no key at all). It is re-read on every request, so adding a provider there needs no restart. The no-key ones are anonymous: they sit behind an **Enable** button and are only used as backups once you enable them.

**No restart needed for changes.** The launcher reloads `server.py` by itself when it changes (it waits until no task is running) and the page refreshes itself when `index.html` changes, so editing the app never closes your window.

Free-tier numbers in the default config are approximate. Google and OpenRouter change them, so edit them in Settings.

## Preview

The **Preview** button opens a live pane next to the chat (drag its edge to resize):

- Ask the agent for a web page and it appears automatically. The workspace folder is served on its own port (the Aqua port + 1, so previewed pages cannot reach the Aqua API) and reloads whenever the agent edits a file.
- The agent can start a dev server (`start_server`, for example `npm run dev` or `python -m http.server 8000`). It keeps running in the background, opens in the pane, and shows up as a chip you can click or stop.
- Type any address: `3000`, `localhost:5173`, or a website. Desktop, tablet and phone sizes, back, forward, reload, and open-in-browser are built in.
- Some websites refuse to be shown inside other apps. Use the open-in-browser button for those.

## Project notes (every AI remembers what happened)

Aqua keeps a shared notebook for each project at `.aqua/NOTES.md` inside your workspace (open it with the file button in the sidebar). After each task Aqua adds an automatic log (the request, which AI did it, files changed, commands run, problems, outcome), every AI can also save its own note with the `add_note` tool (what it did, what is left), and every switch between models or providers is logged. The latest notes are put in front of **every** AI on every call, so when Gemini runs out and Groq takes over, or you start a new chat, the next AI already knows what happened. You can read or clear the notes any time.

**Projects never mix.** One workspace folder is one project. Its chats, its notes and what the AIs are told all belong to that folder only; switching the workspace folder in Settings starts clean, and a chat from another project is never continued. Nothing carries over unless you ask: in the notes window, **Use notes from another project** copies that project's notes in on purpose. (File tools are limited to the workspace; shell commands the AI runs are not sandboxed, so the AI is told to stay inside the project, but treat that as a rule, not a wall.)
## It understands messy requests first

For a new task, one call of your strongest model first turns your request (typos, missing words and all) into a short clear brief (goal, look and feel, features, tech, done-when). You see it as a card, called What I understood, and the cheaper model builds exactly that instead of guessing. Turn it off in Settings (Understand first).

**Auto-prompting for every AI.** Short or vague follow-ups are expanded automatically: a complaint ("it does not work", "I see nothing") gets the debugging method added (look at the page, read the console error, fix that exact cause, prove it), and a short change request gets "edit the existing files, keep what works, then verify". Every AI also gets a build method in its instructions: plan, build in layers (a small working core first, then features, then polish), verify after each step, no placeholders. Hard tasks (shaders, 3D, physics, games) start one model tier higher instead of on the smallest model.

## Why free AIs code badly, and what Aqua does about it

Small free models fail in predictable ways: they misread vague requests, write one giant file in one go, invent code from scratch, never run what they wrote, and say done too early. Aqua adds a safety net for each: **understand first** (a strong model writes a clear brief), **recipes** (tested starter projects for shaders/slime/jelly, 3D scenes and 2D games, copied and adapted instead of invented), **build in layers** (small working core, then features), **auto page check** after every edit (real console errors go straight back to the AI), **look_page** (screenshot, console, layout), a **reviewer** (a stronger model checks the finished result against the brief and sends the AI back to fix real problems), and **keep going** at Balanced and Max.

## It switches models by itself

When a model hits its limit, Aqua does not wait: it moves to your next model, then to your other connected AIs, and then to the provider's other models with the same key (for example every Gemini flash model the key can use; each has its own free limit). It only waits when nothing is left, and it shows a countdown. The per-model fallbacks can be turned off in Settings.

## Projects (the AI only ever sees one)

Every project is its own folder with its own chats and notes. Use the **Project** box in the left sidebar: pick a project, or click **+** to start a new empty one (it is created in the `projects` folder). The AI can only read and write inside the current project's folder, shell commands that point into other folders (other projects, Aqua's own data and API keys) are blocked, and chats and notes never carry over. If the files in a folder clearly belong to a different project than your new request, the AI tells you to start a new project instead of mixing them.

## The AI asks, looks and fixes (like a good colleague)

- **It asks you questions.** If your request is vague ("make me a website"), the AI first asks 1 to 4 short questions with buttons (what kind, which style, which features) and then builds what you chose. Click "Let the AI decide" to skip. Tell it "just do it" and it will not ask.
- **It looks at the preview.** The `look_page` tool opens the page it built (desktop or phone size) in a hidden browser and returns a screenshot (models that can see images), the browser console errors, the visible text, and layout problems such as content wider than the screen. It fixes what it finds and looks again, so it is much better at fixing bugs.
- **It cannot hand you a broken page any more.** After every edit of a web file, Aqua opens the page in a hidden browser and tells the AI about real errors (for example "shieldSphere is not defined"). It also warns when an edit deletes a variable that other code still uses, and it will not let the AI say "done" while the page still crashes. When you say "it is black", the AI is told to read the console errors first instead of guessing.
- **When do limits come back?** The credit panel shows when each limit restarts ("Daily limit restarts in 4 h 43 min, at 09:00 your time"; Gemini resets at midnight Pacific time, OpenRouter at midnight UTC), and when a model hits its limit the chat tells you how long until it is back.
- **Liquid glass on/off** is the button next to the theme button in the top bar (and "Glass quality" in Settings).
- **Mac:** on first launch Aqua adds an "Aqua Code" app with the logo to `~/Applications`. Resizing the window is smooth now (plain blur while you drag, real glass when you stop) and very small windows scroll instead of cutting off the message box.

## 3D modeling (every AI can do it)

Ask for anything 3D ("make a low-poly castle", "download a duck model") and every provider gets the same 3D tools:

- `scaffold_3d` puts a kit in your project: `3d/aqua3d.py` (a pure-Python model builder: box, sphere, cylinder, cone, torus, extrude, lathe, groups, exports GLB / OBJ / STL), an offline Three.js viewer and an example.
- `show_3d` opens the model in the Preview pane (orbit, wireframe, auto-rotate, save picture).
- `search_3d_models` + `download_3d_model` fetch free ready-made models (Khronos glTF samples, three.js examples). Check each model's licence before you use it.

- `look_3d` lets the AI look at its own build: it renders 4 views (3/4, front, side, top) in a headless Edge/Chrome and returns a text report (size, floating or sunk parts, ASCII silhouettes). Models that can read images also get the picture, so even text-only models can check their work. The pictures are saved in `.aqua/look/`.
- Big or many builds are fast and need no Blender: the builder makes 1000+ parts in under a second, `aqua_parts.py` has ready trees, houses, towers, cars, furniture, bridges and a windmill, and `Scene.grid` / `Scene.ring` / `Scene.use` repeat them.

The AI does a few look-and-fix rounds by itself; open the Preview and tell it what else to change.

## How it saves credit

Every task starts on the **cheapest** model and only moves up when the cheap one struggles:

| Trigger | Result |
| --- | --- |
| 2 failing steps in a row (tool error, syntax error after a write) | next tier |
| 8 steps on one tier without finishing | next tier |
| Rate limit (HTTP 429) or daily request cap on that model | next tier (it has its own quota) |
| The model calls `request_stronger_model` | next tier |
| You press **Escalate now** | next tier |

The full conversation is handed over, so the stronger model continues instead of starting again. The next message resets to the cheap tier unless you enable "stay on the stronger model". You can also pin a tier from the composer.

Default ladders (all editable in Settings, including prices and limits):

- **DeepSeek:** `deepseek-chat` then `deepseek-reasoner`
- **Gemini:** `gemini-2.5-flash-lite`, `gemini-2.5-flash`, `gemini-2.5-pro`

## Limits and credit display

- Spend today against a daily budget, plus a total budget. The agent refuses to call the API once a budget is reached.
- Per-model requests today vs. the daily cap, and requests per minute vs. the per-minute cap. The agent waits for a free per-minute slot instead of failing.
- "Saved $X" compares your real spend with always using the top tier.
- DeepSeek: live account balance. Gemini has no balance API, so use the budget.

Prices and limits in the default config are my best knowledge at the time of writing. Check your provider's pricing page and edit them in Settings.

## What the agent can do

`list_dir`, `read_file`, `write_file`, `edit_file` (exact unique replace), `search` (regex), `run_command`, and `request_stronger_model`. Python and JSON files are syntax-checked after every write, and a failure counts toward escalation.

Approval modes: ask for every change, auto-approve edits but ask for commands (default), or full auto.

## Interface

Liquid-glass UI with dark and light themes, a water intro, per-tier chips, diffs for edits, approval cards, escalation banners and a file viewer. Settings has a **Glass quality** switch:

- **Lite** (default): soft glass, smooth everywhere.
- **Full**: real edge refraction and an animated background. Heavier on the GPU and only works in Chrome/Edge.

## Security notes (please read)

- File tools are confined to the workspace folder. **`run_command` is not sandboxed**: it runs a real shell as your user in that folder. A small denylist blocks obviously destructive commands, but it is not a security boundary. Keep the default "ask for commands" mode unless you trust the task.
- Your API keys are stored in plain text in `data/config.json`. That folder is git-ignored. Do not commit it.
- The server binds to `127.0.0.1` only and has no authentication. Do not expose it to a network.
- The model sees the contents of files it reads. Do not point the workspace at folders with secrets.

## Project layout

```
aqua_app.py   desktop launcher (server + app window)
server.py     HTTP server, agent loop, tools, usage and budget tracking
assets/       logo and icon
index.html    the whole UI (no build step)
data/         config, usage and chat history (git-ignored, created on first run)
workspace/    default folder the agent works in (git-ignored)
```

Environment variables: `AQUA_CODE_PORT` (default 8765), `AQUA_CODE_DATA` (data folder).

## Roadmap ideas

Streaming responses, more providers (any OpenAI-compatible endpoint already works by changing the endpoint in Settings), git integration, per-project workspaces.

## License

MIT. See `LICENSE`.
