"""Reading a match result off the site's status text.

Substring matching is a trap here.  The site announces an opponent's victory as
``"<their name> won"``, so a naive ``"won" in text`` scores every loss as a win.
The phrases it can produce are::

    You won by checkmate.            You lost by checkmate.
    You won because your opponent    You lost because your time expired.
      ran out of time.               You lost because all your pieces were captured.
    You won                          Abebe won
    Draw                             DRAW

so the subject of the verb is what decides it, not the verb alone.
"""

from __future__ import annotations

import re

WIN = "win"
LOSS = "loss"
DRAW = "draw"
UNKNOWN = "unknown"

# "you won", "you win", "you have won"
_YOU_WON = re.compile(r"\byou\b(?:\s+\w+){0,2}?\s+\b(?:won|win|wins)\b")
_YOU_LOST = re.compile(r"\byou\b(?:\s+\w+){0,2}?\s+\b(?:lost|lose|loses|resigned)\b")
_DRAW = re.compile(r"\b(?:draw|drawn|tie|tied|stalemate)\b")
# "Abebe won", "Player 2 wins" -- somebody who is not us
_SOMEONE_WON = re.compile(r"\b([\w'’\- ]{1,40}?)\s+\b(?:won|wins)\b")


def classify_result(text: str | None) -> str:
    """Return ``win``, ``loss``, ``draw`` or ``unknown`` for a status line."""
    if not text:
        return UNKNOWN
    normalised = " ".join(str(text).lower().split())

    # Whoever the sentence is about takes precedence over any bare verb.
    if _YOU_WON.search(normalised):
        return WIN
    if _YOU_LOST.search(normalised):
        return LOSS
    if _DRAW.search(normalised):
        return DRAW

    named = _SOMEONE_WON.search(normalised)
    if named and named.group(1).strip() not in ("you", "i", "we"):
        return LOSS  # someone else won, so we did not

    # Bare verbs, with no subject to disagree with.
    if re.search(r"\bwon\b", normalised):
        return WIN
    if re.search(r"\b(?:lost|lose)\b", normalised):
        return LOSS
    return UNKNOWN
