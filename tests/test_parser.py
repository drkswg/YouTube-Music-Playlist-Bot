from ym_playlist_maker.models import Candidate, Track
from ym_playlist_maker.parser import exact_matches, parse_tracks


def test_parse_numbering_dashes_and_invalid_lines():
    tracks, invalid = parse_tracks("1. Кино — Группа крови\n2) Oasis - Wonderwall\nbad\n")
    assert [(t.artist, t.title, t.source_line) for t in tracks] == [
        ("Кино", "Группа крови", 1), ("Oasis", "Wonderwall", 2)
    ]
    assert invalid == [3]


def test_exact_match_requires_title_and_artist_and_unique_candidate():
    track = Track("Oasis", "Wonderwall", 1)
    correct = Candidate("id1", "Wonderwall", ["Oasis"])
    cover = Candidate("id2", "Wonderwall", ["Another Artist"])
    assert exact_matches(track, [correct, cover]) == [correct]
    assert len(exact_matches(track, [correct, Candidate("id3", "wonderwall", ["OASIS"])])) == 2

