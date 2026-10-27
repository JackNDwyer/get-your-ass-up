# Get Your Ass Up

AI coding agents leave you with thirty seconds of dead time every two minutes. Get Your Ass Up
fills it. It's a snarky voice coach that yells a workout at you whenever Claude Code has been busy
for a while, argues with you when you talk back, and tells you to sit when Claude is done. Turn on
the webcam and it can also see when you're on your phone and count your reps.

```
Claude is working...
COACH: Get your ass up. Fifteen squats! Claude's writing your tests and you can't even write a pushup.
YOU:   I'm tired.
COACH: Claude isn't tired. Five more.
YOU:   (picks up phone)
COACH: Phone down. Five more squats.
Claude finishes.
COACH: Claude's done. You got lucky. Sit.
```

Everything runs on your Mac. Speech-to-text, the LLM, text-to-speech and the vision model all go
through [LiveKit Inference](https://docs.livekit.io/agents/models/inference/), so a free LiveKit
Cloud project is the only account you need. No other API keys.

## Quickstart

You need:

- macOS (that's what it's tested on), with a mic and speakers
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- the LiveKit CLI: `brew install livekit-cli`
- a free [LiveKit Cloud](https://cloud.livekit.io) project
- [Claude Code](https://code.claude.com), and `python3` on your PATH (the hooks use it; see
  [Troubleshooting](#troubleshooting) if `python3` asks you to install developer tools)

```bash
git clone https://github.com/JackNDwyer/get-your-ass-up
cd get-your-ass-up
uv sync
lk cloud auth                # once: links the CLI to your LiveKit Cloud project
lk app env -w                # writes your LiveKit URL and keys to .env.local
lk agent console coach.py    # starts the coach. You should hear "Coach is on."
```

Then, in a second terminal, start Claude Code with the plugin:

```bash
claude --plugin-dir /path/to/get-your-ass-up/plugin
```

Give Claude something that takes a while. After about 20 seconds, the coach is on you. Talk back
whenever you like. Press `q` in the coach's terminal to quit it.

To install the plugin for good instead of passing `--plugin-dir` each time, see
[Install the plugin](#install-the-plugin).

## How it works

There are three parts. Only the first two are required.

1. **The coach** (`coach.py`): a voice agent built on [LiveKit Agents](https://docs.livekit.io/agents/).
   It runs on your machine with your mic and speakers. It also runs a small web server on
   `127.0.0.1:7777`, which only your own machine can reach, so Claude Code can tell it what's happening.
2. **A Claude Code plugin** (`plugin/`): hooks that tell the coach when you send a prompt, when
   Claude runs your tests, when Claude stops and when Claude needs you. It also adds `/workout-stats`.
3. **The webcam watcher** (`watcher/`, optional): watches you and counts your reps.

### Install the plugin

The repo is also a Claude Code plugin marketplace. In Claude Code:

```
/plugin marketplace add JackNDwyer/get-your-ass-up
/plugin install get-your-ass-up@get-your-ass-up
```

From a local clone, use the folder instead: `/plugin marketplace add ./get-your-ass-up`. Restart
Claude Code after installing, because sessions that were already open keep their old hooks.

### What the hooks do

| Claude Code event | What the coach does |
| --- | --- |
| You send a prompt (`UserPromptSubmit`) | Starts a 20 s timer. If Claude is still working when it fires, it yells a workout, and roasts you about what you asked Claude to do. |
| Claude runs tests or a build (`PostToolUse`, `PostToolUseFailure`) | Failing tests cost reps. Passing tests get a grudging line. Other commands (`ls`, `git`, `grep`) are ignored. |
| Claude finishes (`Stop`) | After a beat: "Claude's done. You got lucky. Sit." |
| Claude needs you (`Notification`) | "Claude needs you. Back to the desk." |

The hooks run `plugin/scripts/hook.py`, which uses only the Python standard library and gives up
after a second. They fail open: if the coach isn't running, or anything goes wrong, Claude Code
carries on as if they weren't there. The one exception is the optional [prompt gate](#the-prompt-gate-optional).

Workouts rotate through pushups, squats, plank and lunges. The longer Claude works, the more reps,
up to three times the base amount.

### Talking back

You can talk to the coach at any time. Wait for it to finish its sentence first; it doesn't hear
you while it's talking (see `GYAU_INTERRUPTIONS` below).

- **"Done."** or **"Did fifteen squats."** logs your reps.
- **"I'm tired."** gets judged. Weak excuses cost extra reps. A real one ("I'm on a call") gets a pass.
- **"How many today?"** reads out your stats, then roasts them.
- **"Can I do squats instead?"** swaps the workout at fixed rates, plus a 10% fee for asking.
- **"Put it on my tab."** defers it with 50% interest. Reps you do with nothing owed pay the tab
  down.
- **"How many R's in strawberry?"** or **"Chart my pushups this week vs last week."** makes the
  coach write Python and run it in the [sandbox](#the-python-sandbox-optional). Needs Docker.

It argues. It won't agree with you, and it won't apologize.

Reps go to `~/.get-your-ass-up/log.jsonl`. `/workout-stats` in Claude Code prints today's totals,
like `Today: 140 pushups · 3 min of plank`.

## The webcam (optional)

The watcher tells the coach what you're doing (at the desk, on your phone, exercising or away) and
counts squats, pushups and lunges.

- Pick up your phone while Claude works: "Phone down. Five more pushups."
- Sit there ignoring a workout: it nags every 20 seconds, and the number goes up.
- Say "done" when the camera watched you sit the whole time: it calls you out and doesn't log it.
- Hit your target on camera: "Fifteen. I counted. Sit down."

Run it in a third terminal, with the coach already running:

```bash
uv run --project watcher watcher/watch.py --list-cameras   # which cameras work, by number
uv run --project watcher watcher/watch.py --camera 0
```

- **Camera permission:** the first run makes macOS ask for camera access for your terminal app.
  Allow it, then quit the terminal completely (⌘Q) and reopen it. If you missed the prompt, go to
  System Settings → Privacy & Security → Camera.
- **Framing:** rep counting needs your whole body in view: side-on for pushups, front-on for squats.
  A laptop camera usually can't see the floor; an external webcam or an iPhone (Continuity Camera)
  angled at the floor works better.
- **Preview window:** shows the label, your pose and the rep count. Press `q` in it to quit.
- **Pose runs locally** with MediaPipe. The first run downloads a ~6 MB model to
  `~/.get-your-ass-up/models/`.
- **`--no-cloud`** labels frames from pose alone. It can't spot a phone, but no frames leave your Mac.

## The prompt gate (optional)

Start the coach with `GYAU_GATE=1 lk agent console coach.py`. Reps you owe when Claude finishes
stay owed, and Claude Code won't take your next prompt until you tell the coach you did them:

```
Coach says: 15 squats first. Tell the coach when you're done, or put #tab in your prompt to put it on your tab (with interest).
```

Ways out: put `#tab` in the prompt, quit the coach (the hook lets everything through when it's not
running), or start it without `GYAU_GATE`.

## The Python sandbox (optional)

The coach's `run_python` tool (for counting, math and charts of your history) runs code in a
throwaway Docker container with no network, a read-only filesystem, 512 MB of memory, one CPU and
a 30 second limit. It can read your workout log and write charts to a fresh folder under
`~/.get-your-ass-up/charts/`, and nothing else on your machine. It only uses the image you build
locally and never pulls one from Docker Hub. Every piece of code it runs is printed in the coach's
terminal.

```bash
brew install colima docker            # once. Colima is a light way to run Docker on a Mac
colima start
docker build -t gyau-sandbox sandbox  # once, about a minute
```

Without Docker, everything else works, and the coach just tells you the sandbox is off.

## Privacy

What leaves your machine, all through LiveKit Inference to the model providers:

- your voice (speech-to-text) and the coach's lines (text-to-speech), while the coach is running
- the conversation with the coach, plus the first 500 characters of each prompt you send Claude
  and your project's folder name, so it can roast you about them
- with the watcher running: one webcam frame every 4 seconds, scaled down to 640 px, so the
  vision model can label it. Frames aren't saved anywhere.

To keep webcam frames on your Mac, run the watcher with `--no-cloud`, or start the coach with
`GYAU_VISION=0`. Your rep log stays in `~/.get-your-ass-up/`.

## Safety

The coach is meant to be rude, not dangerous. If you say you're hurt, injured, in pain or dizzy, it
stops assigning workouts right away, drops the act and tells you to rest. It stays that way until
you say you're fine. That check runs on your words directly, not just through the LLM, so it
doesn't depend on the model playing along. The webcam, failing tests and the prompt gate all stand
down while you're resting.

It's still a joke app yelling at you, not a trainer. Know your limits, warm up, skip anything that
hurts, and talk to a doctor if you have a condition that exercise could make worse.

## Configuration

Put these in `.env.local` for the coach. The hooks and the watcher don't read `.env.local`, so if
you change `GYAU_PORT` or `GYAU_CAMERA`, set them in the shell where you run Claude Code and the
watcher too.

| Variable | Default | What it does |
| --- | --- | --- |
| `GYAU_DELAY` | `20` | Seconds of Claude working before the first workout |
| `GYAU_REPEAT` | `60` | Seconds between workouts while Claude keeps working |
| `GYAU_NAG` | `20` | With the webcam on: seconds an unpaid workout can sit untouched before the coach nags and adds reps |
| `GYAU_REACT_DELAY` | `1.5` | Seconds the coach waits before reacting to Claude finishing, tests, or the camera |
| `GYAU_INTERRUPTIONS` | `0` | `1` lets you cut the coach off mid-sentence. Only with headphones; on speakers it hears itself |
| `GYAU_GATE` | `0` | `1` turns on the prompt gate |
| `GYAU_VISION` | `1` | `0` turns off the webcam vision model |
| `GYAU_VISION_MODEL` | `openai/gpt-4.1-mini` | Vision-capable LiveKit Inference model for webcam frames |
| `GYAU_CAMERA` | `0` | Which camera the watcher opens (same as `--camera`) |
| `GYAU_PORT` | `7777` | Port the coach listens on for the hooks and the watcher |
| `GYAU_LOG` | `~/.get-your-ass-up/log.jsonl` | Where reps are logged |
| `GYAU_EXIT_WITH_TERMINAL` | `1` | The coach quits when the terminal that started it closes. `0` turns that off |
| `GYAU_SANDBOX_IMAGE` | `gyau-sandbox` | Docker image for `run_python` |
| `COACH_LLM` | `openai/gpt-4.1-mini` | Any [LiveKit Inference](https://docs.livekit.io/agents/models/inference/) LLM |
| `COACH_STT` | `assemblyai/universal-3-6-pro` | Speech-to-text model. `deepgram/nova-3` is a good alternative |
| `COACH_TTS` | `fishaudio/s2.1-pro` | Text-to-speech model |
| `COACH_VOICE` | Fish Audio's "Sawyer" | Voice ID. Any [Fish Audio](https://fish.audio) voice ID works with the default TTS model; pick something angrier |

## Troubleshooting

**"Port 7777 is already in use."** An old coach is still running in the background. Stop it with
`kill $(lsof -tiTCP:7777 -sTCP:LISTEN)`, then start the coach again. (Quit the coach with `q`
rather than closing its terminal. It also shuts itself down now when its terminal goes away, but
older copies didn't.)

**The coach never yells.** Check three things:
- Claude finished in under 20 seconds. That's on purpose. Lower `GYAU_DELAY` to test.
- Claude Code wasn't started with the plugin. Use `--plugin-dir` or install it, then start a new
  Claude Code session.
- To test the coach on its own: `curl -X POST localhost:7777/work-started`. It should yell within
  20 seconds. `curl -X POST localhost:7777/work-finished` stops it.

**No sound.** The coach's terminal shows which speakers and mic it's using at the top. Check your
Mac's sound output, and that the terminal app has microphone permission (System Settings →
Privacy & Security → Microphone).

**It keeps cutting itself off.** Leave `GYAU_INTERRUPTIONS` at `0` on speakers, or use headphones.

**The camera won't open.** Run `uv run --project watcher watcher/watch.py --list-cameras`. If no
cameras show up, it's macOS permission: see [The webcam](#the-webcam-optional). If some do, pass
one of those numbers with `--camera`.

**The watcher says it can't reach the coach.** Start the coach first. If you changed `GYAU_PORT`,
set the same value for the watcher.

**`python3` asks you to install developer tools.** On a fresh Mac, `python3` is a stub until
Apple's command line tools are installed. Install them once (`xcode-select --install`) or
`brew install python`. Until then the hooks quietly do nothing.

## Uninstall

```
/plugin uninstall get-your-ass-up@get-your-ass-up        # in Claude Code
/plugin marketplace remove get-your-ass-up
```

```bash
rm -rf ~/.get-your-ass-up        # your rep log, charts and the pose model
docker rmi gyau-sandbox          # if you built the sandbox
rm -rf /path/to/get-your-ass-up  # the clone
```

You can also take camera access away from your terminal in System Settings → Privacy & Security →
Camera.

## Development

```bash
uv run pytest
```

The tests cover the timers and workout logic, excuses, bargaining and the tab, the webcam
reactions and the rep counter, the hook script (end to end against a real local server), the HTTP
endpoints, the sandbox plumbing, and the things that bite new users: a busy port, the coach
outliving its terminal, no Docker, no camera. None of them need audio, LiveKit or the network.
One test checks that the real container has no network, and is skipped when Docker or the image
isn't available.

To film or demo the chart feature without two weeks of real workouts, seed a separate fake log
(your real log isn't touched), and rerun the seed script on the day so "this week" lines up:

```bash
uv run scripts/seed_demo_log.py      # writes ~/.get-your-ass-up/demo-log.jsonl, ending today
GYAU_LOG=~/.get-your-ass-up/demo-log.jsonl lk agent console coach.py
```

| File | What's in it |
| --- | --- |
| `coach.py` | The LiveKit agent: personality, tools, session setup |
| `coach_core.py` | Timers, workouts, excuses, bargaining, webcam reactions, the log, the local web server |
| `lifeline.py` | Shuts the coach down when its terminal goes away |
| `vision.py` | Labels a webcam frame with a vision model |
| `watcher/` | The webcam watcher (its own uv project), the rep counter and its error messages |
| `sandbox.py`, `sandbox/Dockerfile` | The locked-down container `run_python` uses |
| `scripts/seed_demo_log.py` | Fake two-week log for demos |
| `plugin/hooks/hooks.json` | The Claude Code hooks |
| `plugin/scripts/hook.py` | What the hooks run: forwards events to the coach, and the prompt gate |
| `plugin/commands/workout-stats.md`, `plugin/scripts/workout_stats.py` | `/workout-stats` (standard library only) |

## License

MIT
