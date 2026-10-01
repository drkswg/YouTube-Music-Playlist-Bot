import pytest

from ym_playlist_maker.models import Candidate, Track
from ym_playlist_maker.parser import exact_matches, parse_tracks, resolve_candidates


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


def song(video_id="studio", title="Song", album="Album", duration="3:42", artists=None):
    return Candidate(video_id, title, artists or ["Artist"], album, duration)


@pytest.mark.parametrize("label", [
    "Live", "Acoustic", "Unplugged", "DJ Example Remix", "Instrumental", "Karaoke",
    "Radio Edit", "Extended", "2011 Remastered", "Re-recorded", "Clean", "Explicit",
])
def test_studio_preferred_to_explicit_alternate_version(label):
    alternate = song("other", f"Song ({label})")
    studio = song(duration="")
    result = resolve_candidates(Track("Artist", "Song", 1), [alternate, studio])
    assert result.selected_video_id == "studio"
    assert result.ordered_candidates == (studio, alternate)
    assert resolve_candidates(Track("Artist", "Song", 1), [alternate]).reason == "version_unavailable"


@pytest.mark.parametrize("title,album", [
    ("Song (Live)", "Album"), ("Song", "Live at Wembley 1992"),
    ("Song - Live at Wembley", "Album"), ("Song 2011 Remaster", "Album"),
    ("Song", "Album (Acoustic)"),
    ("Song", "2011 Remaster"),
])
def test_explicit_version_requested_from_title_or_album(title, album):
    requested = "Song (2011 Remastered)" if "Remaster" in title + album else "Song (Acoustic)" if "Acoustic" in album else "Song (Live)"
    result = resolve_candidates(Track("Artist", requested, 1), [song("version", title, album)])
    assert result.selected_video_id == "version"


@pytest.mark.parametrize("requested,correct,incorrect", [
    ("Song (Live at Wembley 1992)", "Song (Live at Wembley 1992)", "Song (Live at Wembley 1993)"),
    ("Song (DJ One Remix)", "Song (DJ One Remix)", "Song (DJ Two Remix)"),
    ("Song (2011 Remastered)", "Song (2011 Remaster)", "Song (2020 Remaster)"),
])
def test_requested_version_details_are_preserved(requested, correct, incorrect):
    result = resolve_candidates(Track("Artist", requested, 1), [song("wrong", incorrect), song("right", correct)])
    assert result.selected_video_id == "right"
    assert resolve_candidates(Track("Artist", requested, 1), [song("wrong", incorrect)]).reason == "version_unavailable"


def test_multiple_requested_versions_always_manual_even_equal_duration():
    result = resolve_candidates(Track("Artist", "Song (Live)", 1), [
        song("one", "Song (Live at Wembley)"), song("two", "Song (Live at Paris)"),
    ])
    assert result.selected_video_id is None and result.reason == "multiple_recordings"


@pytest.mark.parametrize("title,album", [
    ("Live Forever", "Live Through This"), ("Acoustic Dreams", "Live Through This"),
    ("Remix My Life", "Live Through This"),
])
def test_version_words_in_ordinary_names_are_not_markers(title, album):
    result = resolve_candidates(Track("Artist", title, 1), [song(title=title, album=album)])
    assert result.selected_video_id == "studio"


@pytest.mark.parametrize("title,album", [
    ("Song (Anniversary Edition)", "Album"), ("Song - Demo", "Album"),
    ("Song (Live)", "Album (Acoustic)"),
    ("Song (Live at Wembley)", "Live at Paris"),
    ("Song (Clean Explicit)", "Album"),
    ("Song (Original)", "Live"),
])
def test_unknown_or_conflicting_candidate_does_not_block_compatible_recording(title, album):
    unclear = song("unclear", title, album)
    studio = song()
    result = resolve_candidates(Track("Artist", "Song", 1), [unclear, studio])
    assert result.selected_video_id == "studio" and result.reason == "auto_single"
    assert result.ordered_candidates == (studio, unclear)
    unavailable = resolve_candidates(Track("Artist", "Song", 1), [unclear])
    assert unavailable.selected_video_id is None and unavailable.reason == "unsupported_qualifier"


@pytest.mark.parametrize("credit", ["feat.", "feat", "ft.", "ft", "featuring", "FEAT."])
def test_featured_artist_metadata_is_not_a_recording_version(credit):
    studio = song(title=f"Song ({credit} Guest)")
    remix = song("remix", f"Song (Remix) ({credit} Guest)")
    result = resolve_candidates(Track("Artist", "Song", 1), [remix, studio])
    assert result.selected_video_id == "studio" and result.reason == "auto_single"
    assert resolve_candidates(Track("Artist", "Song", 1), [remix]).reason == "version_unavailable"


def test_featured_artist_named_after_a_version_is_still_a_credit():
    result = resolve_candidates(Track("Artist", "Song", 1), [song(title="Song (feat. Live)")])
    assert result.selected_video_id == "studio"


def test_featured_credits_participate_in_release_equivalence_and_requested_identity():
    first = song("first", "Song (feat. Guest One)")
    second = song("second", "Song (feat. Guest Two)")
    assert resolve_candidates(Track("Artist", "Song", 1), [first, second]).reason == "different_credits"
    result = resolve_candidates(Track("Artist", "Song (feat. Guest One)", 1), [second, first])
    assert result.selected_video_id == "first"
    metadata = song("metadata", artists=["Artist", "Guest One"])
    assert resolve_candidates(Track("Artist", "Song (ft. Guest One)", 1), [metadata]).selected_video_id == "metadata"
    assert resolve_candidates(Track("Artist", "Song", 1), [first, metadata]).reason == "auto_equivalent_releases"


@pytest.mark.parametrize("title", ["Only Girl (In the World)", "Single Ladies (Put a Ring on It)"])
def test_literal_parenthetical_title_is_preserved(title):
    wrong = song("other", title.split(" (")[0])
    studio = song(title=f"{title} (feat. Guest)")
    live = song("live", f"{title} (Live)")
    result = resolve_candidates(Track("Artist", title, 1), [wrong, live, studio])
    assert result.selected_video_id == "studio"
    assert result.ordered_candidates == (studio, live, wrong)


def test_arbitrary_subtitle_is_not_dropped_from_song_identity():
    result = resolve_candidates(Track("Artist", "Song", 1), [song(title="Song (Another Story)")])
    assert result.selected_video_id is None and result.reason == "no_confident_match"


@pytest.mark.parametrize("artist", ["David Guetta ft. Sia", "David Guetta feat. Sia", "David Guetta & Sia"])
@pytest.mark.parametrize("artists,title", [
    (["David Guetta", "Sia"], "Titanium"), (["David Guetta"], "Titanium (feat. Sia)"),
])
def test_requested_collaboration_matches_separate_artist_credits(artist, artists, title):
    correct = song("correct", title, artists=artists)
    missing_guest = song("missing_guest", "Titanium", artists=["David Guetta"])
    cover = song("cover", "Titanium", artists=["Other", "Sia"])
    result = resolve_candidates(Track(artist, "Titanium", 1), [missing_guest, cover, correct])
    assert result.selected_video_id == "correct"
    assert resolve_candidates(Track(artist, "Titanium", 1), [missing_guest, cover]).reason == "no_confident_match"


def test_band_name_with_ampersand_matches_as_a_whole():
    result = resolve_candidates(Track("Florence & The Machine", "Song", 1), [
        song(artists=["Florence & The Machine"]),
    ])
    assert result.selected_video_id == "studio"


@pytest.mark.parametrize("qualifier", ["Studio", "Studio Version", "Studio Recording",
                                      'Studio Recording From "The Voice" Performance'])
def test_explicit_studio_qualifier_does_not_block_original_recording(qualifier):
    studio = song(title=f"Song ({qualifier}) (feat. Guest)")
    result = resolve_candidates(Track("Artist", "Song", 1), [studio])
    assert result.selected_video_id == "studio"
    conflict = song(title=studio.title, album="Live")
    assert resolve_candidates(Track("Artist", "Song", 1), [conflict]).reason == "unsupported_qualifier"


@pytest.mark.parametrize("qualifier", ["Demo", "Special Version", "Radio Version", "Slowed", "Bonus Track"])
def test_unrecognized_recording_qualifier_still_requires_manual_choice(qualifier):
    title = f"Song ({qualifier})"
    result = resolve_candidates(Track("Artist", title, 1), [song(title=title)])
    assert result.selected_video_id is None and result.reason == "unsupported_qualifier"


@pytest.mark.parametrize("durations,reason", [
    (["3:40", "3:42"], "auto_equivalent_releases"),
    (["1:03:40", "1:03:42"], "auto_equivalent_releases"),
    (["3:40", "3:42", "3:44"], "different_durations"),
    (["3:40", "3:43"], "different_durations"),
    (["3:40", ""], "duration_unknown"),
    (["3:40", "3:60"], "duration_unknown"),
    (["3:40", "0:00"], "duration_unknown"),
    (["3:40", "1:60:00"], "duration_unknown"),
    (["3:40", "3:4"], "duration_unknown"),
])
def test_equivalent_releases_require_valid_global_duration_span(durations, reason):
    candidates = [song(str(i), album=f"Album {i}", duration=d) for i, d in enumerate(durations)]
    result = resolve_candidates(Track("Artist", "Song", 1), candidates)
    assert result.reason == reason
    assert result.selected_video_id == ("0" if reason.startswith("auto") else None)


def test_full_artist_credits_must_match_for_equivalent_releases():
    result = resolve_candidates(Track("Artist", "Song", 1), [song(), song("guest", artists=["Artist", "Guest"])])
    assert result.reason == "different_credits" and result.selected_video_id is None
    result = resolve_candidates(Track("Artist", "Song", 1), [
        song(artists=["Artist", "Guest"]), song("same", artists=["GUEST", "ARTIST"]),
    ])
    assert result.reason == "auto_equivalent_releases"


def test_all_ten_results_are_considered_and_relevant_results_rank_first():
    wrong = [song(str(i), artists=["Cover Artist"]) for i in range(8)]
    first = song("first")
    hidden = song("hidden", duration="4:00")
    candidates = wrong + [first, hidden]
    result = resolve_candidates(Track("Artist", "Song", 1), candidates)
    assert result.reason == "different_durations" and result.selected_video_id is None
    assert result.ordered_candidates == tuple([first, hidden] + wrong)
    assert candidates == wrong + [first, hidden]  # pure, no caller mutation
    assert resolve_candidates(Track("Artist", "Song", 1), wrong + [first]).selected_video_id == "first"


def test_unrelated_or_cover_candidates_do_not_conflict_with_exact_identity():
    cover = song("cover", "Song (Demo)", artists=["Cover Artist"])
    unrelated = song("other", "Another Song (Demo)")
    assert resolve_candidates(Track("Artist", "Song", 1), [cover, unrelated]).reason == "no_confident_match"
    assert resolve_candidates(Track("Artist", "Song", 1), [cover, unrelated, song()]).selected_video_id == "studio"
    assert resolve_candidates(Track("Artist", "Song", 1), []).reason == "no_results"


def test_custom_search_never_selects_or_reorders_candidates():
    candidates = [song("cover", artists=["Cover Artist"]), song()]
    result = resolve_candidates(Track("Artist", "Song", 1), candidates, is_custom=True)
    assert result.reason == "custom_search" and result.selected_video_id is None
    assert result.ordered_candidates == tuple(candidates)


@pytest.mark.parametrize("duration", [None, 225, {}, ["3:45"]])
def test_nonstring_duration_requires_manual_selection_without_raising(duration):
    result = resolve_candidates(Track("Artist", "Song", 1), [
        song(duration="3:45"), song("invalid", duration=duration),
    ])
    assert result.reason == "duration_unknown" and result.selected_video_id is None


@pytest.mark.parametrize("title", [
    "Song (Live) (Live at Wembley 1992)",
    "Song (Live at Wembley 1992) (Live)",
    "Song (Original) (Live)",
    "Song (Live) (Original)",
])
def test_repeated_or_contradictory_requested_qualifiers_do_not_discard_intent(title):
    candidate = song("wrong_venue", "Song (Live at Paris 1993)")
    result = resolve_candidates(Track("Artist", title, 1), [candidate])
    assert result.reason == "unsupported_qualifier" and result.selected_video_id is None



@pytest.mark.parametrize("title,album", [
    ("Song", "Album (Demo)"), ("Song", "Album (Special Version)"),
    ("Song", "Demo"), ("Song", "Special Version"),
    ("Song (Live)", "Album (Studio Version)"),
    ("Song (Live)", "Studio"), ("Song", "Album (Studio Version) (Live)"),
    ("Song (Live at Wembley)", "Album (Live at Paris)"),
])
def test_album_uncertainty_and_studio_conflicts_are_excluded(title, album):
    from ym_playlist_maker.parser import resolve_candidates, eligible_candidates
    requested = Track("Artist", "Song (Live)" if "Live" in title else "Song", 1)
    rejected = Candidate("bad", title, ["Artist"], album, "3:20")
    compatible = Candidate("good", requested.title, ["Artist"], "Ordinary Album", "3:20")
    assert resolve_candidates(requested, [rejected]).selected_video_id is None
    assert eligible_candidates(requested, [rejected]) == ()
    assert resolve_candidates(requested, [rejected, compatible]).selected_video_id == "good"
    assert eligible_candidates(requested, [rejected, compatible]) == ("good",)
