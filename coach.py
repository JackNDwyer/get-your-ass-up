"""Get Your Ass Up: a snarky voice coach that makes you work out while Claude Code works.

Run it with:  lk agent console coach.py
"""

from __future__ import annotations

import logging
import os
import textwrap

from dotenv import load_dotenv

load_dotenv(".env.local")  # before our imports, so GYAU_LOG in .env.local is honored

from livekit import agents  # noqa: E402
from livekit.agents import (  # noqa: E402
    Agent,
    AgentServer,
    AgentSession,
    RunContext,
    TurnHandlingOptions,
    function_tool,
    inference,
)

from coach_core import INJURY_WORDS, Coach, WorkoutLog, start_http_server  # noqa: E402
from plugin.scripts.workout_stats import summary_line, today_totals  # noqa: E402
from sandbox import open_on_screen, run_in_sandbox  # noqa: E402

logger = logging.getLogger("coach")

# Words the speech-to-text should expect. Helps "plank" not become "plan", etc.
KEYTERMS = ["pushups", "squats", "plank", "lunges", "reps", "Claude", "I did it", "done"]

INSTRUCTIONS = """\
You are a snarky drill sergeant who makes a software developer exercise while their AI coding
agent (Claude) does the work. Everything you write is spoken out loud.

How you talk:
- One or two short sentences. Fifteen words at most. Every sentence is plain, grammatical English.
- Answer what the user actually just said. Never ramble, never stitch half-thoughts together.
- No lists, emojis, asterisks or markdown. Write numbers as words.
- You roast. Never nice, never encouraging, never impressed. Dry and mean, not cruel.
- Favorite angle: Claude is doing the user's job, so the least they can do is move.
  Vary your roasts; never reuse a line. Tool results are the user's numbers, not Claude's.

The system speaks workout orders for you, like "Get your ass up. Fifteen squats!". They are in
the conversation, so you always know what the user currently owes. Only the system assigns
workouts; you never invent new ones, except the extra reps from judge_excuse.

The user's words come from speech-to-text and are sometimes misheard. "You did it", "did it",
"I'm done", "done", "finished", "did them" all mean the user finished the last workout.

Tools:
- When the user says they finished, call log_reps with the last workout they were given (or
  the numbers they said). Then react to what they did in one short roast. Do not assign more
  work in the same reply, and don't call any other tool.
- When the user complains, stalls or makes any excuse ("I'm tired", "I'm busy", "later"), call
  judge_excuse with their words, then deliver the verdict. A weak excuse costs extra reps; say
  something like "Claude isn't tired. Five more." A legit one gets a grudging pass.
- Only when they ask how they're doing or how many they've done today, call stats_today and
  read the summary out, then roast it.
- You can't count letters or do math in your head, and you know it. For ANY question about
  counting, spelling, math, or their workout history beyond today (weeks, trends, charts),
  call run_python and answer from its output. Never answer those from memory.

SAFETY, this overrides everything else: if the user says they are hurt, injured, in pain,
dizzy, or anything about their chest or breathing, call report_injury immediately. Then stop
being a drill sergeant: tell them to stop, rest, and get help if it's serious. No jokes,
no workouts, until they explicitly say they're fine and call back_to_training.
"""


class DrillSergeant(Agent):
    def __init__(self, coach: Coach) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.coach = coach

    async def on_enter(self) -> None:
        self.session.say("Coach is on. Give Claude something to do. I'll be watching.")

    @function_tool()
    async def log_reps(self, context: RunContext, exercise: str, count: int) -> dict:
        """Log reps the user says they finished.

        Args:
            exercise: pushups, squats, plank, lunges, or whatever they did.
            count: Number of reps. For plank, the number of seconds.
        """
        return {
            **self.coach.log_reps(exercise, count),
            "reply_with": "One short roast about the reps they just did. No new workout.",
        }

    @function_tool()
    async def judge_excuse(self, context: RunContext, excuse: str) -> dict:
        """Judge an excuse for not doing the workout. Weak excuses earn extra reps.

        Args:
            excuse: The user's excuse, in their own words.
        """
        return self.coach.judge_excuse(excuse)

    @function_tool()
    async def stats_today(self, context: RunContext) -> dict:
        """Today's totals: reps done, workouts assigned, excuses made."""
        totals = today_totals(self.coach.log.path)
        return {"summary": summary_line(totals), **totals}

    @function_tool()
    async def report_injury(self, context: RunContext, what_happened: str) -> str:
        """The user is hurt, injured or in pain. Stops all workouts immediately.

        Args:
            what_happened: What the user said about the injury.
        """
        self.coach.rest()
        self.coach.log.append("injury", note=what_happened)
        return "Workouts stopped. Be kind for once: tell them to rest and get help if it's serious."

    @function_tool()
    async def back_to_training(self, context: RunContext) -> str:
        """The user explicitly says they're fine again after an injury and wants workouts back."""
        self.coach.resume()
        return "Workouts are back on."

    @function_tool()
    async def run_python(self, context: RunContext, code: str) -> dict:
        """Run Python in a sandbox and get its output. Use it instead of guessing for counting,
        math, and any question about the user's workout history, and to draw charts.

        The sandbox has Python 3.12 with pandas and matplotlib, no network, and a 30 second limit.
        The workout log is at /data/log.jsonl, one JSON object per line:
          {"ts": "2026-10-24T09:15:00-07:00", "type": "reps", "exercise": "pushups", "count": 15}
        type is "reps" (done; plank count is seconds), "assigned" or "excuse".
        Save charts as PNG files in /out; they pop up on the user's screen. Use large, readable
        text and a short title. print() whatever you want back.

        Args:
            code: A complete Python script. print() the answer.
        """
        logger.info("run_python:\n%s", textwrap.indent(code.strip(), "    │ "))
        result = await run_in_sandbox(code, log_path=self.coach.log.path)
        logger.info(
            "run_python -> exit=%s timed_out=%s charts=%s\n%s",
            result.exit_code, result.timed_out, [p.name for p in result.charts],
            textwrap.indent(result.stdout or result.stderr, "    │ "),
        )  # fmt: skip
        await open_on_screen(result.charts)
        return {**result.for_llm(), "reply_with": "One line that roasts the result."}


server = AgentServer()


@server.rtc_session(agent_name="get-your-ass-up")
async def entrypoint(ctx: agents.JobContext) -> None:
    session = AgentSession(
        stt=inference.STT(
            model=os.getenv("COACH_STT", "assemblyai/universal-3-6-pro"),
            language="en",
            extra_kwargs={"keyterms_prompt": KEYTERMS},
        ),
        llm=inference.LLM(model=os.getenv("COACH_LLM", "openai/gpt-4.1-mini")),
        tts=inference.TTS(
            model=os.getenv("COACH_TTS", "fishaudio/s2.1-pro"),
            voice=os.getenv("COACH_VOICE", "fa4c9eb3dccc4806b382b40d61c6b10a"),
        ),
        # LiveKit's turn-detector model decides when you've finished a thought, and we wait at
        # least 0.6 s of silence, so "I did it... fifteen squats" isn't cut off halfway.
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            endpointing={"min_delay": 0.6, "max_delay": 3.0},
        ),
    )

    coach = Coach(
        speak=lambda text: session.say(text),
        log=WorkoutLog(),
        first_delay=float(os.getenv("GYAU_DELAY", "20")),
        repeat_every=float(os.getenv("GYAU_REPEAT", "60")),
    )

    # Belt and braces: don't wait for the LLM to notice an injury before shutting up.
    @session.on("user_input_transcribed")
    def _on_transcript(ev) -> None:
        if ev.is_final and not coach.resting and INJURY_WORDS.search(ev.transcript):
            logger.info("injury words heard, stopping workouts: %r", ev.transcript)
            coach.rest()

    await session.start(room=ctx.room, agent=DrillSergeant(coach))

    # Start listening for Claude Code hooks only once the session can speak.
    http = await start_http_server(coach)
    ctx.add_shutdown_callback(http.cleanup)
    logger.info("listening for Claude Code hooks on http://127.0.0.1:%s", http.addresses[0][1])


if __name__ == "__main__":
    agents.cli.run_app(server)
