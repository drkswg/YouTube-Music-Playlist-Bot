"""Parse human track lists and conservatively match music search results."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

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


ResolutionReason = Literal[
    "auto_single", "auto_equivalent_releases", "no_results", "no_confident_match",
    "version_unavailable", "unsupported_qualifier", "multiple_recordings",
    "different_credits", "duration_unknown", "different_durations", "custom_search",
]


@dataclass(frozen=True)
class ResolutionDecision:
    selected_video_id: str | None
    reason: ResolutionReason
    ordered_candidates: tuple[Candidate, ...]


# Markers are interpreted only inside a terminal qualifier or an explicit album
# construction. Ordinary song/album names containing these words stay intact.
_MARKERS = (
    ("live", r"live|concert"),
    ("acoustic", r"acoustic|unplugged"),
    ("remix", r"remix|mix"),
    ("instrumental", r"instrumental|karaoke"),
    ("radio_edit", r"radio edit|edit"),
    ("extended", r"extended"),
    ("remastered", r"remaster(?:ed)?"),
    ("rerecorded", r"re[ -]?record(?:ed|ing)"),
    ("clean", r"clean"),
    ("explicit", r"explicit"),
)
_TERMINAL = re.compile(r"^(.*?)\s*(?:\(([^()]*)\)|\[([^\[\]]*)\])\s*$")
_SUFFIX = re.compile(r"^(.*?)\s+[—–-]\s+(.+)$")
_FEATURE_CREDIT = re.compile(r"^(?:feat\.?|ft\.?|featuring)\s+(?=\S)", re.I)
_VERSION_HINT = re.compile(
    r"\b(?:version|recording|edition|demo|take|radio|mono|stereo|"
    r"slowed|sped|speed|bonus|anniversary)\b"
)


@dataclass(frozen=True)
class _Recording:
    base: str
    versions: tuple[tuple[str, str], ...] = ()
    unknown: bool = False
    explicit_studio: bool = False
    featured: tuple[str, ...] = ()
    subtitle_bases: tuple[str, ...] = ()


def _subtitle_bases(title: str) -> tuple[str, ...]:
    """Offer literal parenthetical title expansions to Gemini, never local auto."""
    bases: list[str] = []
    while (match := _TERMINAL.match(title)) and match.group(1).strip():
        qualifier = next(g for g in match.groups()[1:] if g is not None)
        if _version(qualifier) or _VERSION_HINT.search(normalized(qualifier)):
            break
        title = match.group(1).strip()
        bases.append(normalized(title))
    return tuple(bases)


def _version(qualifier: str) -> tuple[tuple[str, str], ...]:
    value = normalized(qualifier)
    found = []
    for kind, pattern in _MARKERS:
        match = re.search(r"\b(?:" + pattern + r")\b", value)
        if match:
            # Keep identifying year, venue and remix names; remove generic labels.
            detail = value[:match.start()] + " " + value[match.end():]
            detail = re.sub(r"\b(?:version|recording|edition)\b", " ", detail)
            found.append((kind, " ".join(detail.split())))
    return tuple(found)


def _title_recording(title: str) -> _Recording:
    base = title.strip()
    versions: list[tuple[str, str]] = []
    unknown = False
    explicit_studio = False
    featured: list[str] = []
    while True:
        match = _TERMINAL.match(base) or _SUFFIX.match(base)
        if not match or not match.group(1).strip():
            break
        qualifier = next(g for g in match.groups()[1:] if g is not None)
        feature = _FEATURE_CREDIT.match(qualifier.strip())
        if feature:
            # Featuring credits describe collaborators, not a recording version.
            featured.append(normalized(qualifier.strip()[feature.end():]))
            base = match.group(1).strip()
            continue
        markers = _version(qualifier)
        label = normalized(qualifier)
        if label in ("original", "original version", "studio", "studio version") or re.fullmatch(
            r"studio recording(?: from .+ performance)?", label
        ):
            explicit_studio = True
            markers = ()
        elif not markers:
            if not _VERSION_HINT.search(normalized(qualifier)):
                # A literal subtitle, e.g. "Only Girl (In the World)", stays
                # part of the song identity. Never drop arbitrary title words.
                break
            unknown = True
        versions.extend(markers)
        base = match.group(1).strip()
    # Common metadata suffix without punctuation: "Song 2011 Remaster".
    match = re.match(r"^(.*?)\s+((?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?)$", base, re.I)
    if match and match.group(1).strip():
        versions.extend(_version(match.group(2)))
        base = match.group(1).strip()
    # Do not collapse repeated kinds into a dict: doing so can discard a
    # requested venue/year. An explicit original plus another version is also
    # contradictory, whether it appears in the input or search metadata.
    if len({kind for kind, _ in versions}) != len(versions) or (explicit_studio and versions):
        unknown = True
    return _Recording(normalized(base), tuple(versions), unknown, explicit_studio,
                      tuple(sorted(featured)), _subtitle_bases(base))


def _album_versions(album: str) -> tuple[tuple[str, str], ...]:
    if re.fullmatch(r"(?:\d{4} )?remaster(?:ed)?(?: \d{4})?(?: edition)?", normalized(album)):
        return _version(album)
    parsed = _title_recording(album)
    versions = list(parsed.versions)
    value = normalized(album)
    # A bare label or an explicit "Live at ..." describes a recording. An
    # album such as "Live Through This" does not.
    if not versions and (
        value in {"live", "concert", "acoustic", "unplugged", "remix", "instrumental",
                  "karaoke", "radio edit", "extended", "remastered", "clean", "explicit"}
        or re.match(r"^(?:live|concert) (?:at|in|from|\d{4})\b", value)
        or re.fullmatch(r"(?:\d{4} )?remaster(?:ed)?(?: \d{4})?(?: edition)?", value)
    ):
        versions.extend(_version(album))
    return tuple(versions)


def _candidate_recording(candidate: Candidate) -> _Recording:
    title = _title_recording(candidate.title)
    album = _album_versions(candidate.album)
    by_kind: dict[str, str] = dict(title.versions)
    conflict = title.unknown or bool(title.explicit_studio and (title.versions or album))
    if len(by_kind) != len(title.versions):
        conflict = True
    # Different kinds in title/album, or conflicting details of one kind, are
    # ambiguous metadata. Generic title labels may inherit album details.
    if title.versions and album and set(by_kind) != {kind for kind, _ in album}:
        conflict = True
    for kind, detail in album:
        previous = by_kind.get(kind, "")
        if previous and detail and previous != detail:
            conflict = True
        by_kind[kind] = previous or detail
    if {"clean", "explicit"} <= by_kind.keys():
        conflict = True
    credits = set(title.featured) | {normalized(a) for a in candidate.artists}
    return _Recording(title.base, tuple(sorted(by_kind.items())), conflict,
                      featured=tuple(sorted(credits)), subtitle_bases=title.subtitle_bases)


def _artist_matches(artist: str, recording: _Recording, *, allow_article: bool = False) -> bool:
    def matches(name: str) -> bool:
        name = normalized(name)
        return name in recording.featured or (
            allow_article and name.removeprefix("the ") in {
                credit.removeprefix("the ") for credit in recording.featured
            }
        )

    if matches(artist):
        return True
    # Keep full band names intact when credited as such. Otherwise an explicit
    # collaboration must account for every requested artist, including guests.
    names = re.split(r"\s+(?:(?:feat\.?|ft\.?|featuring)|&)\s+", artist, flags=re.I)
    return len(names) > 1 and all(matches(name) for name in names)


def _compatible(request: _Recording, recording: _Recording) -> bool:
    wanted = dict(request.versions)
    offered = dict(recording.versions)
    return set(request.featured) <= set(recording.featured) and wanted.keys() == offered.keys() and all(
        not detail or detail == offered[kind] or f" {detail} " in f" {offered[kind]} "
        for kind, detail in wanted.items()
    )


def _duration_seconds(value: object) -> int | None:
    if not isinstance(value, str) or not re.fullmatch(r"(?:\d+:)?\d+:\d{2}", value):
        return None
    parts = [int(part) for part in value.split(":")]
    if parts[-1] >= 60 or (len(parts) == 3 and parts[1] >= 60):
        return None
    seconds = parts[-1] + 60 * parts[-2] + (3600 * parts[0] if len(parts) == 3 else 0)
    return seconds or None


def resolve_candidates(
    track: Track, candidates: Sequence[Candidate], *, is_custom: bool = False
) -> ResolutionDecision:
    """Resolve all results using exact identity and conservative version rules.

    No state is changed. Ordering is for fresh searches only: persisted button
    snapshots must never be replaced with this function's returned order.
    """
    original = tuple(candidates)
    if is_custom:
        return ResolutionDecision(None, "custom_search", original)
    if not original:
        return ResolutionDecision(None, "no_results", original)
    request = _title_recording(track.title)
    same_song: list[tuple[Candidate, _Recording]] = []
    others: list[Candidate] = []
    for candidate in original:
        recording = _candidate_recording(candidate)
        if recording.base == request.base and _artist_matches(track.artist, recording):
            same_song.append((candidate, recording))
        else:
            others.append(candidate)
    compatible = [c for c, r in same_song if not r.unknown and _compatible(request, r)]
    ordered = tuple(compatible + [c for c, _ in same_song if c not in compatible] + others)

    def decision(reason: ResolutionReason, selected: str | None = None) -> ResolutionDecision:
        return ResolutionDecision(selected, reason, ordered)

    if not same_song:
        return decision("no_confident_match")
    if request.unknown:
        return decision("unsupported_qualifier")
    if not compatible:
        if any(r.unknown for _, r in same_song):
            return decision("unsupported_qualifier")
        return decision("version_unavailable")
    if len(compatible) == 1:
        return decision("auto_single", compatible[0].video_id)
    if request.versions:
        return decision("multiple_recordings")
    credits = {r.featured for c, r in same_song if c in compatible}
    if len(credits) != 1:
        return decision("different_credits")
    durations = [_duration_seconds(c.duration) for c in compatible]
    if any(value is None for value in durations):
        return decision("duration_unknown")
    valid_durations = [value for value in durations if value is not None]
    if max(valid_durations) - min(valid_durations) > 2:
        return decision("different_durations")
    return decision("auto_equivalent_releases", compatible[0].video_id)



def eligible_candidates(track: Track, candidates: Sequence[Candidate]) -> tuple[str, ...]:
    """Allow release preferences and literal subtitle checks within version rules."""
    request = _title_recording(track.title)
    if request.unknown:
        return ()
    return tuple(dict.fromkeys(
        c.video_id for c in candidates
        if ((recording := _candidate_recording(c)).base == request.base
            or request.base in recording.subtitle_bases)
        and not recording.unknown and _compatible(request, recording)
        and _artist_matches(track.artist, recording, allow_article=True)
    ))
