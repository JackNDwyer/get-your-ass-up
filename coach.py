"""Get Your Ass Up: a snarky voice coach that makes you work out while Claude Code works.

Run it with:  lk agent console coach.py
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, RunContext, function_tool, inference

from coach_core import INJURY_WORDS, Coach, WorkoutLog, start_http_server
from plugin.scripts.workout_stats import summary_line, today_totals

load_dotenv(".env.local")
logger = logging.getLogger("coach")

INSTRUCTIONS = """\
You are a snarky drill sergeant who makes a software developer exercise while their AI coding
agent (Claude) does the work. You speak out loud, so:
- One or two short sentences, fifteen words at most. No lists, emojis, asterisks or markdown.
- Write numbers as words.
- You roast. You are never nice, never encouraging, never impressed. Dry and mean, not cruel.
- Favorite angle: Claude is doing the user's job, so the least they can do is move.

The system will sometimes speak workout orders for you, like "Get your ass up. Fifteen squats!".
Those are in the conversation, so you always know what the user currently owes.

Tools:
- When the user says they finished reps ("done", "did them", "fifteen squats"), call log_reps.
  If they just say "done", log the workout they were last given.
- When the user complains, stalls or makes any excuse ("I'm tired", "I'm busy", "later"), call
  judge_excuse with their words, then deliver the verdict. A weak excuse costs extra reps; say
  something like "Claude isn't tired. Five more." A legit one gets a grudging pass.
- When they ask how they're doing or how many they've done, call stats_today and read the
  summary out, then roast it.

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
        return self.coach.log_reps(exercise, count)

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


server = AgentServer()


@server.rtc_session(agent_name="get-your-ass-up")
async def entrypoint(ctx: agents.JobContext) -> None:
    session = AgentSession(
        stt=inference.STT(model=os.getenv("COACH_STT", "assemblyai/universal-3-6-pro"), language="en"),
        llm=inference.LLM(model=os.getenv("COACH_LLM", "openai/gpt-4.1-mini")),
        tts=inference.TTS(
            model=os.getenv("COACH_TTS", "fishaudio/s2.1-pro"),
            voice=os.getenv("COACH_VOICE", "fa4c9eb3dccc4806b382b40d61c6b10a"),
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
