# Get Your Ass Up

AI coding agents leave you with thirty seconds of dead time every two minutes. Get Your Ass Up
fills it. It's a snarky voice coach that yells a workout at you whenever Claude Code has been busy
for a while, tells you to sit when Claude is done, and argues with you when you talk back.

```
Claude is working...
COACH: Get your ass up. Fifteen squats!
YOU:   I'm tired.
COACH: Claude isn't tired. Five more.
YOU:   How many have I done today?
COACH: A hundred and forty pushups and three minutes of plank. My grandma does that before breakfast.
Claude finishes.
COACH: Agent's done. You may sit.
```

It has two parts:

1. **The coach** (`coach.py`): a voice agent built on [LiveKit Agents](https://docs.livekit.io/agents/).
   It runs on your machine using your mic and speakers. It also runs a tiny HTTP server on
   `127.0.0.1:7777` so Claude Code can tell it what's happening.
2. **A Claude Code plugin** (`plugin/`): three hooks that `curl` the coach when you send a prompt,
   when Claude stops and when Claude needs you. It also adds a `/workout-stats` command.

## Run the coach

You need [uv](https://docs.astral.sh/uv/), the [LiveKit CLI](https://docs.livekit.io/home/cli/)
(`brew install livekit-cli`) and a free [LiveKit Cloud](https://cloud.livekit.io) project.
Speech-to-text, the LLM and text-to-speech all run on
[LiveKit Inference](https://docs.livekit.io/agents/models/inference/), so your LiveKit keys are
the only credentials you need.

```bash
git clone https://github.com/<you>/get-your-ass-up && cd get-your-ass-up
uv sync
lk cloud auth                # once, links the CLI to your LiveKit Cloud project
lk app env -w                # writes LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET to .env.local
lk agent console coach.py    # talk to the coach through your Mac's mic and speakers
```

Headphones help, so the coach doesn't hear itself. Press `ctrl+t` in the console to type instead of
talk, and `q` to quit. `uv run python coach.py console` also works, but LiveKit has deprecated it in
favor of `lk agent console`.

## Install the Claude Code plugin

In Claude Code:

```
/plugin marketplace add <you>/get-your-ass-up
/plugin install get-your-ass-up@get-your-ass-up
```

From a local clone, add the folder instead: `/plugin marketplace add ./get-your-ass-up`. To try it
for a single session without installing, run `claude --plugin-dir ./plugin`.

Now send Claude something that takes a while. After about 20 seconds the coach will be on you.

### The hooks

| Claude Code event  | What the plugin does                 | What the coach does                                                     |
| ------------------ | ------------------------------------ | ----------------------------------------------------------------------- |
| `UserPromptSubmit` | `POST /work-started`                 | Starts a 20 s timer. If Claude is still working when it fires, it yells a workout, then another every 60 s. |
| `Stop`             | `POST /work-finished`                | Cancels the timer. If you were given a workout: "Agent's done. You may sit." |
| `Notification`     | `POST /needs-you`                    | "Claude needs you. Back to the desk."                                   |

Each hook is a single `curl --max-time 1 ... || true`, so it never slows down or blocks Claude
Code, even when the coach isn't running.

Workouts rotate through pushups, squats, plank and lunges. Reps grow the longer Claude has been
working, up to three times the base amount.

### Talking back

You can talk to the coach at any time.

- **"Done."** or **"Did fifteen squats."** logs your reps.
- **"I'm tired."** gets judged. Weak excuses cost you extra reps. A real one ("I'm on a call") gets a pass.
- **"How many today?"** reads out your stats.

Reps are logged to `~/.get-your-ass-up/log.jsonl`. `/workout-stats` in Claude Code prints today's
totals in one line, like `Today: 140 pushups · 3 min of plank`.

## Safety

The coach is meant to be rude, not dangerous. If you say you're hurt, injured, in pain or dizzy,
it stops assigning workouts right away, drops the act and tells you to rest. It stays that way until
you tell it you're fine. That check runs on your words directly as well as through the LLM, so it
doesn't depend on the model playing along.

It's still a joke app yelling at you, not a trainer. Know your limits, warm up, skip anything that
hurts, and talk to a doctor if you have a condition that exercise could make worse.

## Configuration

Set these in `.env.local` or your shell:

| Variable       | Default                            | What it does                                |
| -------------- | ---------------------------------- | ------------------------------------------- |
| `GYAU_DELAY`   | `20`                               | Seconds of Claude working before the first workout |
| `GYAU_REPEAT`  | `60`                               | Seconds between workouts while Claude keeps working |
| `GYAU_PORT`    | `7777`                             | Port for the hooks. Set the same value where Claude Code runs. |
| `GYAU_LOG`     | `~/.get-your-ass-up/log.jsonl`     | Where reps are logged                       |
| `COACH_LLM`    | `openai/gpt-4.1-mini`              | Any [LiveKit Inference](https://docs.livekit.io/agents/models/inference/) LLM |
| `COACH_STT`    | `assemblyai/universal-3-6-pro`     | Speech-to-text model                        |
| `COACH_TTS`, `COACH_VOICE` | `fishaudio/s2.1-pro`, a default voice | Text-to-speech model and voice. Pick something angrier. |

## Development

```bash
uv run pytest
```

The tests cover the timer and event logic, excuses, the log and the HTTP endpoints. None of them
need audio or LiveKit.

| File                               | What's in it                                                  |
| ---------------------------------- | ------------------------------------------------------------- |
| `coach.py`                         | The LiveKit agent: personality, tools, session setup          |
| `coach_core.py`                    | Timers, workouts, excuses, the log, the HTTP server           |
| `plugin/hooks/hooks.json`          | The three Claude Code hooks                                   |
| `plugin/commands/workout-stats.md` | `/workout-stats`                                              |
| `plugin/scripts/workout_stats.py`  | Reads the log. Uses only the standard library, so the plugin needs nothing installed. |

## License

MIT
