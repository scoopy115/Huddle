"""User feedback on a meeting ("Speaker 2 is Shana", "the project is Plano, not Planet",
"we did not decide X"): turned into concrete, checkable corrections and applied to the
transcript before the notes are rewritten with the feedback as authoritative context.

The LLM is only asked to *translate* the feedback into speaker renames and word replacements;
applying them is deterministic (word-boundary, case-insensitive), so nothing in the
transcript changes that the user did not ask for.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import ClassVar

from pydantic import BaseModel, Field, ValidationError

from .providers.base import ProviderError
from .providers.summarize import Text, parse_json_object

REFINE_PROMPT = """A user reviewed the notes of a meeting and wrote feedback. Translate the feedback into corrections.
Respond with ONE strict JSON object only:
{"speakerRenames": [{"from": "current speaker name or label", "to": "correct name"}],
 "replacements": [{"find": "wrong word or phrase exactly as it appears in the transcript", "replace": "correct word or phrase"}],
 "context": "everything else in the feedback that the notes writer must know or fix, as short bullet points; empty string if nothing"}
Rules:
- speakerRenames only when the feedback clearly says who a speaker is. "from" must be one of the current speakers listed.
- replacements only for misheard names, products, terms or places that the feedback corrects; "find" must be text that occurs in the transcript. Never rewrite sentences.
- Put instructions about the summary, decisions or action items (missing, wrong, unwanted) in "context". Do not invent anything."""


class _Rename(BaseModel):
    from_: str = Field(alias="from")
    to: str


class _Replacement(BaseModel):
    find: str
    replace: str


class RefineOut(BaseModel):
    speakerRenames: list[_Rename] = Field(default_factory=list)
    replacements: list[_Replacement] = Field(default_factory=list)
    context: Text = ""


@dataclass
class Corrections:
    renames: list[tuple[str, str]] = field(default_factory=list)      # (from, to)
    replacements: list[tuple[str, str]] = field(default_factory=list) # (find, replace)
    context: str = ""


class _Text(HTMLParser):
    BLOCK: ClassVar[set[str]] = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "blockquote", "tr"}

    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "blockquote", "tr"):
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(raw: str | None) -> str:
    """Plain text from the editor's HTML: bullets become '- ', blocks become lines."""
    if not raw:
        return ""
    if "<" not in raw:
        return html.unescape(raw).strip()
    p = _Text()
    p.feed(raw)
    text = html.unescape("".join(p.out))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def derive_corrections(provider, feedback: str, speakers: list[str], transcript_lines: list[str],
                       sample_chars: int = 14000) -> Corrections:
    """Ask the model to turn free-form feedback into renames/replacements/context."""
    sample, n = [], 0
    for line in transcript_lines:
        if n + len(line) > sample_chars:
            break
        sample.append(line)
        n += len(line) + 1
    user = (f"Current speakers: {', '.join(speakers) or 'none'}\n\nFeedback from the user:\n{feedback}\n\n"
            f"Transcript (first part):\n" + "\n".join(sample) + "\n\nReturn the JSON now.")
    raw = provider.complete_json(REFINE_PROMPT, user, max_tokens=1500)
    try:
        out = RefineOut.model_validate(parse_json_object(raw))
    except (ValueError, ValidationError) as e:
        raise ProviderError("The AI model returned an unusable response.", detail=f"{e}\n---\n{raw[:4000]}") from e
    renames = [(r.from_.strip(), r.to.strip()) for r in out.speakerRenames if r.from_.strip() and r.to.strip()]
    repl = [(r.find.strip(), r.replace.strip()) for r in out.replacements
            if r.find.strip() and r.replace.strip() and r.find.strip().lower() != r.replace.strip().lower()]
    return Corrections(renames=renames, replacements=repl, context=out.context.strip())


def apply_replacements(text: str, replacements: list[tuple[str, str]]) -> tuple[str, int]:
    """Case-insensitive, word-boundary replacement; keeps a leading capital when the original had one.
    Returns (new_text, number_of_replacements)."""
    count = 0
    for find, repl in replacements:
        pattern = re.compile(r"(?<!\w)" + re.escape(find) + r"(?!\w)", re.IGNORECASE)

        def sub(m: re.Match, repl: str = repl) -> str:
            nonlocal count
            count += 1
            src = m.group(0)
            if src[:1].isupper() and not repl[:1].isupper():
                return repl[:1].upper() + repl[1:]
            return repl

        text = pattern.sub(sub, text)
    return text, count
