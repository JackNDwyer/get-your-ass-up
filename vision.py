"""Look at a webcam frame and say what the user is doing: desk, phone, exercising or away.

Uses a vision-capable LLM through LiveKit Inference, so it needs no keys beyond the LiveKit ones
the coach already has. Frames go to the model provider; nothing is stored.
"""

from __future__ import annotations

import base64
import os

from livekit.agents import inference, llm

LABELS = ("desk", "phone", "exercising", "away")

PROMPT = """\
This is one webcam frame from a developer's workspace. Say what the person is doing, as exactly
one word:
- phone: holding, scrolling or looking at a phone (check their hands)
- exercising: pushups, squats, plank, lunges, or down on the floor
- desk: sitting or standing at the desk, not on a phone
- away: nobody in the frame at all (if any part of a person is visible, pick another word)
Reply with the one word only."""


def parse_label(text: str) -> str | None:
    """The model's reply, reduced to one of LABELS, or None if it said something else."""
    words = text.strip().lower().replace(".", " ").split()
    for word in words:
        if word in LABELS:
            return word
    return None


class Vision:
    def __init__(self, model: str | None = None):
        self._llm = inference.LLM(model=model or os.getenv("GYAU_VISION_MODEL", "openai/gpt-4.1-mini"))

    async def classify(self, jpeg: bytes) -> str | None:
        ctx = llm.ChatContext()
        image = llm.ImageContent(
            image=f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}",
            inference_detail="low",  # one 512 px tile: cheap and fast, plenty to spot a phone
        )
        ctx.add_message(role="user", content=[PROMPT, image])
        text = ""
        async with self._llm.chat(chat_ctx=ctx) as stream:
            async for chunk in stream:
                if chunk.delta and chunk.delta.content:
                    text += chunk.delta.content
        return parse_label(text)

    async def aclose(self) -> None:
        await self._llm.aclose()
