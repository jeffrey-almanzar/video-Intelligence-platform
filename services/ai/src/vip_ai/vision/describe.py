"""One structured Qwen3-VL call per selected frame."""

import base64
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from vip_ai.llm.client import LLMRequest

PROMPT_VERSION = "vision-v1"


class VisualType(StrEnum):
    slide = "slide"
    code = "code"
    diagram = "diagram"
    demo = "demo"
    chart = "chart"
    person = "person"
    other = "other"


class FrameDescription(BaseModel):
    visual_type: VisualType
    description: str = Field(
        max_length=400, description="1-2 sentences: what is shown, specifically"
    )
    ocr_text: str = Field(
        max_length=600, description="Legible text on screen, verbatim; empty if none"
    )


SYSTEM = (
    "You describe frames from a recorded talk for a search index. Be specific and factual: name what "
    "is shown (a slide's title and bullet points, a photo's subject, a chart's topic, code's language). "
    "Copy legible on-screen text exactly into ocr_text. Don't guess at unreadable text. Answer with JSON."
)


def describe_request(image: Path, model: str, context: str) -> LLMRequest[FrameDescription]:
    b64 = base64.b64encode(image.read_bytes()).decode()
    prompt = f"What the speaker is saying around this moment: {context[:600] or '(no speech)'}\n\nDescribe the frame."
    return LLMRequest(operation="describe_frame", model=model, system=SYSTEM, prompt=prompt,
                      schema=FrameDescription, prompt_version=PROMPT_VERSION, num_ctx=8192,
                      max_output_tokens=2048, images=(b64,))  # fmt: skip
