"""Parse human track lists and conservatively match music search results."""

import re
import unicodedata

from .models import Candidate, Track


NUMBERING = re.compile(r"^\s*(?:\d+[.)]\s*|[-*•]\s+)")
SEPARATOR = re.compile(r"\s+[—–-]\s+")


def parse_tracks(text: str) -> tuple[list[Track], list[int]]:
    tracks: list[Track] = []
    invalid: list[int] = []
    for line_number, original in enumerate(text.splitlines(), start=1):
        line = NUMBERING.sub("", original).strip()
        if not line:
            continue
        parts = SEPARATOR.split(line, maxsplit=1)
        if len(parts) != 2 or not all(part.strip() for part in parts):
            invalid.append(line_number)
            continue
        tracks.append(Track(parts[0].strip(), parts[1].strip(), line_number))
    return tracks, invalid


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in value).split())


def exact_matches(track: Track, candidates: list[Candidate]) -> list[Candidate]:
    artist = normalized(track.artist)
    title = normalized(track.title)
    return [
        c for c in candidates
        if normalized(c.title) == title
        and any(normalized(a) == artist for a in c.artists)
    ]

