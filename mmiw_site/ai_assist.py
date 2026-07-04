from __future__ import annotations
import json
import urllib.request
import urllib.error
from .settings import settings

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"

STRUCTURE_PROMPT = """A person just triggered a personal safety panic alert and typed a short note in a hurry. Turn it into a clear, calm, factual summary for the people who will receive this alert (trusted contacts, possibly family). Do not add anything not stated or clearly implied. Do not speculate about outcomes. If the note is empty or unclear, say that plainly instead of guessing.

Return ONLY plain text, 2-4 short sentences maximum. No preamble, no labels, no markdown.

Raw note: {note}"""


def structure_panic_note(raw_note: str) -> str:
    """Best-effort AI cleanup of a panic note. Returns the raw note unchanged
    if AI is not configured or the call fails for any reason — this function
    must NEVER raise, since it sits on the critical alert-sending path."""
    raw_note = (raw_note or "").strip()
    if not raw_note:
        return "No note was provided by the sender."
    if not settings.anthropic_api_key:
        return raw_note

    try:
        payload = json.dumps({
            "model": ANTHROPIC_MODEL,
            "max_tokens": 200,
            "messages": [
                {"role": "user", "content": STRUCTURE_PROMPT.format(note=raw_note)}
            ],
        }).encode("utf-8")

        req = urllib.request.Request(
            ANTHROPIC_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            blocks = data.get("content", [])
            text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            cleaned = " ".join(text_parts).strip()
            return cleaned if cleaned else raw_note
    except Exception:
        # Any failure (timeout, bad key, network) falls back to the raw note.
        # An alert with an unstructured note is infinitely better than a
        # dropped alert because a formatting call failed.
        return raw_note
