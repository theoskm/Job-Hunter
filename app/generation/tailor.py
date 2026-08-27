"""
Phase 13 — CV and cover letter generation.

*** THE API CALL ITSELF IS UNVERIFIED ***
Same honesty rule as Gmail and Sheets: no network in the build
environment, so no real Anthropic API call has ever been made from here.
Prompt construction and response parsing ARE fully tested — those are
pure functions with no network dependency. `--generate-materials` on your
own machine is the real proof.

Design principle this whole module exists to serve: never fabricate
experience. The prompt is built entirely from config/cv_context.md, with
explicit instructions to use nothing else. If that file is thin or wrong,
the output will be too — this system cannot invent what it doesn't know.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_JD_CHARS = 4000  # generous; most job descriptions are well under this


class GenerationNotConfigured(Exception):
    """Raised when there's no Anthropic API key to call."""


class CvContextMissing(Exception):
    """Raised when config/cv_context.md doesn't exist or is empty."""


@dataclass
class GeneratedMaterials:
    cv_suggestions: list[str]
    ats_keywords: list[str]
    cover_letter: str
    raw_response: str  # kept for debugging a parse that goes wrong


def load_cv_context(path: str | Path = "config/cv_context.md") -> str:
    context_path = Path(path)
    if not context_path.exists():
        raise CvContextMissing(
            f"No candidate background found at {path}. This file grounds "
            "everything generated — see config/cv_context.md.example."
        )
    text = context_path.read_text(encoding="utf-8").strip()
    if len(text) < 100:
        raise CvContextMissing(
            f"{path} exists but looks empty or near-empty — nothing "
            "substantial enough to generate honest materials from."
        )
    return text


def build_prompt(cv_context: str, job_title: str, company: str, job_description: str) -> str:
    """
    Pure function: given the candidate's real background and a job, returns
    the exact prompt text that would be sent. Fully testable without an
    API key — this is where 'never fabricate' actually gets enforced.
    """
    trimmed_jd = (job_description or "")[:MAX_JD_CHARS]
    return f"""You are helping tailor a job application. You will be given the \
candidate's ACTUAL background and a job advert. Your only source of \
information about the candidate is the background below — nothing else.

CRITICAL RULES, before anything else:
- Never invent an employer, job title, date, qualification, achievement, \
metric, or skill that is not explicitly written in the background below.
- If the job wants something the candidate's background doesn't show, \
say so as a gap. Do not paper over it or imply they have it anyway.
- Every sentence in the cover letter must be traceable to a specific line \
in the background below.
- Write in plain, direct language. No corporate filler, no "synergy", no \
exclamation marks.

=== CANDIDATE BACKGROUND (the only source of truth) ===
{cv_context}
=== END CANDIDATE BACKGROUND ===

=== JOB ===
Title: {job_title}
Company: {company}
Description: {trimmed_jd}
=== END JOB ===

Respond in exactly this format, with these exact section headers:

## CV SUGGESTIONS
3-5 bullet points, each an existing achievement from the background above, \
rephrased to speak directly to what this job wants. One bullet per line, \
starting with "- ".

## ATS KEYWORDS
5-10 keywords from the job description that the candidate can honestly \
claim based on the background above. Comma-separated, one line.

## COVER LETTER
A complete cover letter, 250-350 words, using only facts from the \
background above. Open by naming the role. Include at least one honest \
gap if one exists, framed constructively. Close asking to discuss the role."""


def parse_response(raw_text: str) -> GeneratedMaterials:
    """
    Pure function: parses the model's response into structured fields.
    Deliberately tolerant of minor formatting drift — section-header
    matching rather than strict JSON, since an LLM's exact formatting
    varies run to run even with the same prompt.
    """
    sections = re.split(r"^##\s*(CV SUGGESTIONS|ATS KEYWORDS|COVER LETTER)\s*$",
                         raw_text, flags=re.MULTILINE)
    # re.split with a capturing group returns: [before, header, body, header, body, ...]
    found: dict[str, str] = {}
    for i in range(1, len(sections) - 1, 2):
        header = sections[i].strip()
        body = sections[i + 1].strip()
        found[header] = body

    cv_block = found.get("CV SUGGESTIONS", "")
    cv_suggestions = [
        line.lstrip("-•").strip()
        for line in cv_block.splitlines()
        if line.strip().startswith(("-", "•"))
    ]

    keywords_block = found.get("ATS KEYWORDS", "")
    ats_keywords = [k.strip() for k in keywords_block.replace("\n", ",").split(",") if k.strip()]

    cover_letter = found.get("COVER LETTER", "").strip()

    if not cv_suggestions and not cover_letter:
        # Parsing failed to find the expected structure at all — better to
        # surface that clearly than silently return something empty and
        # have it look like a successful, if thin, generation.
        raise ValueError(
            "Could not find the expected section headers in the response. "
            "Raw response preserved for debugging."
        )

    return GeneratedMaterials(
        cv_suggestions=cv_suggestions,
        ats_keywords=ats_keywords,
        cover_letter=cover_letter,
        raw_response=raw_text,
    )


def generate_materials(
    api_key: str,
    cv_context: str,
    job_title: str,
    company: str,
    job_description: str,
    model: str = DEFAULT_MODEL,
) -> GeneratedMaterials:
    """
    The actual API call. UNVERIFIED — see module docstring. Everything
    above this function is tested; this function is a thin wrapper around
    the Anthropic SDK that has never been exercised against the real API
    from this environment.
    """
    if not api_key:
        raise GenerationNotConfigured(
            "ANTHROPIC_API_KEY is not set in .env. Get one at "
            "console.anthropic.com — see USER_GUIDE.md, 'Generating "
            "application materials'."
        )

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise GenerationNotConfigured(
            "The anthropic package is not installed — run: "
            "pip3 install -r requirements.txt"
        ) from exc

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(cv_context, job_title, company, job_description)

    response = client.messages.create(
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    return parse_response(raw_text)


def materials_to_row(materials: GeneratedMaterials) -> dict[str, str]:
    """Shapes generated materials for the database's TEXT/JSON columns."""
    return {
        "cv_suggestions": json.dumps(materials.cv_suggestions),
        "ats_keywords": json.dumps(materials.ats_keywords),
        "cover_letter_draft": materials.cover_letter,
    }
