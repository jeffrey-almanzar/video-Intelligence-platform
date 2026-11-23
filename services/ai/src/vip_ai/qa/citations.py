"""Answer post-processing: citation markers are parsed and validated by code (ADR-015)."""

import re
from dataclasses import dataclass

#: `[S12]`, the excerpt-label form models copy (`[S12 | 03:04]`), and lists (`[S12, S14]`).
CITATION = re.compile(r"\[\s*(S\s*\d+(?:\s*[,;]\s*S?\s*\d+)*)\s*(?:\|[^\]]*)?\]")
_ID = re.compile(r"\d+")
INFERRED = re.compile(r"\[\s*inferred\s*\]", re.IGNORECASE)
#: The exact refusal the prompt asks for (EN/ES), so "not covered" is detected reliably.
NOT_COVERED = ("this isn't covered in the video", "esto no se trata en el video")


@dataclass(frozen=True, slots=True)
class ParsedAnswer:
    content: str
    #: Valid cited segment ids, in order of first appearance.
    cited: list[int]
    invalid: list[int]
    inferred: bool
    not_covered: bool


def parse_answer(text: str, allowed: set[int]) -> ParsedAnswer:
    cited: list[int] = []
    invalid: list[int] = []

    def keep(m: re.Match[str]) -> str:
        out = []
        for raw in _ID.findall(m.group(1)):
            idx = int(raw)
            if idx in allowed:
                if idx not in cited:
                    cited.append(idx)
                out.append(f"[S{idx}]")
            elif idx not in invalid:
                invalid.append(idx)
        # Citations to segments we never showed the model are removed.
        return "".join(out)

    content = CITATION.sub(keep, text)
    content = re.sub(r"[ \t]+([.,;:!?])", r"\1", content)
    content = re.sub(r"[ \t]{2,}", " ", content).strip()
    lowered = content.lower().lstrip("\"'“ ")
    return ParsedAnswer(
        content=content,
        cited=cited,
        invalid=invalid,
        inferred=bool(INFERRED.search(content)),
        not_covered=any(lowered.startswith(p) for p in NOT_COVERED) and not cited,
    )
