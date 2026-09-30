"""Small serializable draft model shared by UI and write pipeline."""

from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass
class Candidate:
    video_id: str
    title: str
    artists: list[str]
    album: str = ""
    duration: str = ""

    @classmethod
    def from_dict(cls, value: dict) -> "Candidate":
        return cls(**value)


@dataclass
class Track:
    artist: str
    title: str
    source_line: int
    candidates: list[Candidate] = field(default_factory=list)
    selected: str | None = None
    status: Literal["pending", "selected", "skipped", "added", "uncertain"] = "pending"
    search_query: str = ""
    search_revision: int = 0
    insert_revision: int = 0
    await_query: bool = False

    @classmethod
    def from_dict(cls, value: dict) -> "Track":
        value = value.copy()
        value["candidates"] = [Candidate.from_dict(x) for x in value.get("candidates", [])]
        return cls(**value)


@dataclass
class Draft:
    draft_id: str
    tracks: list[Track]
    phase: Literal["resolving", "title", "confirm", "creating", "writing", "done"] = "resolving"
    title: str = ""
    playlist_id: str | None = None
    create_marker: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "Draft":
        value = value.copy()
        value["tracks"] = [Track.from_dict(x) for x in value["tracks"]]
        return cls(**value)


def selected_candidate(track: Track) -> Candidate | None:
    return next((x for x in track.candidates if x.video_id == track.selected), None)

