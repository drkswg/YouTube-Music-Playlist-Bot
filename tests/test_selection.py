"""Offline contracts for Gemini release selection and review editing."""
import asyncio
import io
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from telegram.constants import ChatType

from ym_playlist_maker import selection
from ym_playlist_maker.bot import BotUI
from ym_playlist_maker.config import BotConfig, bot_config
from ym_playlist_maker.models import Candidate, Draft, Track
from ym_playlist_maker.parser import eligible_candidates, resolve_candidates
from ym_playlist_maker.selection import GeminiSelector, SelectionDecision, SelectionError, SelectionInput
from ym_playlist_maker.service import SELECTION_RETRY_DELAY, PlaylistWorkflow, WorkflowError
from ym_playlist_maker.store import DraftStore


def candidates():
    return [Candidate(f"id{i}", "Song", ["Artist"], f"Album {i}", f"3:{40+i}") for i in range(8)]


def selection_input():
    track = Track("Artist", "Song", 1, candidates=candidates())
    return SelectionInput.from_track(track, eligible_candidates(track, track.candidates), "different_durations")


def reply(choice="c7", reason="preferred_release", finish="STOP"):
    return {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": json.dumps({"choice": choice, "reason": reason})}]}}]}


def mock_response(monkeypatch, payload):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    calls = []
    def open_request(request, *, timeout):
        calls.append((request, timeout))
        return io.BytesIO(raw)
    monkeypatch.setattr(selection, "urlopen", open_request)
    return calls


def test_adapter_selects_hidden_candidate_using_only_local_labels(monkeypatch):
    calls = mock_response(monkeypatch, reply())
    assert GeminiSelector("secret").select(selection_input()).selected_id == "id7"
    request, timeout = calls[0]
    assert timeout == 10 and request.full_url == selection.ENDPOINT
    assert request.get_header("X-goog-api-key") == "secret"
    assert "secret" not in request.full_url
    body = json.loads(request.data)
    assert "tools" not in body
    schema = body["generationConfig"]["responseSchema"]
    assert schema["properties"]["choice"]["enum"] == [f"c{i}" for i in range(8)] + ["none"]
    sent = request.data.decode()
    assert "secret" not in sent and not any(c.video_id in sent for c in candidates())


@pytest.mark.parametrize("payload", [
    reply("id7"), reply("c99"), reply("abstain"), reply("c0", "insufficient_evidence"),
    reply("none", "preferred_release"), reply("c0", "no_match"),
    reply(finish="MAX_TOKENS"), reply(finish="SAFETY"), {"promptFeedback": {"blockReason": "SAFETY"}},
    {"candidates": []}, {"candidates": [reply()["candidates"][0]] * 2},
    {"candidates": "bad"}, b"not json", reply(None), reply(reason=[]),
])
def test_invalid_and_blocked_provider_results_are_safe(monkeypatch, payload):
    mock_response(monkeypatch, payload)
    with pytest.raises(SelectionError, match="^invalid_response$"):
        GeminiSelector("secret").select(selection_input())


def test_no_match_decision(monkeypatch):
    mock_response(monkeypatch, reply("none", "no_match"))
    assert GeminiSelector("secret").select(selection_input()) == SelectionDecision(None, "no_match")


def test_non_eligible_candidate_can_be_chosen_and_schema_lists_all_labels(monkeypatch):
    track = Track("Artist", "Song", 1, candidates=candidates())
    value = SelectionInput.from_track(track, ("id1",), "no_confident_match")
    calls = mock_response(monkeypatch, reply("c0", "closest_version"))
    assert GeminiSelector("secret").select(value) == SelectionDecision("id0", "closest_version")
    body = json.loads(calls[0][0].data)
    schema = body["generationConfig"]["responseSchema"]["properties"]
    assert schema["choice"]["enum"] == [f"c{i}" for i in range(8)] + ["none"]
    assert schema["reason"]["enum"] == ["preferred_release", "requested_version", "closest_version", "no_match"]
    rows = json.loads(body["contents"][0]["parts"][0]["text"])["candidates"]
    assert [row["eligible"] for row in rows] == [False, True] + [False] * 6


@pytest.mark.parametrize("error,code", [
    (HTTPError(selection.ENDPOINT, 403, "secret body", {}, io.BytesIO(b"secret")), "http_403"),
    (HTTPError(selection.ENDPOINT, 429, "secret body", {}, io.BytesIO(b"secret")), "http_429"),
    (TimeoutError("secret"), "transport_error"), (URLError("secret"), "transport_error"),
])
def test_provider_failure_never_exposes_secret_or_retries(monkeypatch, error, code):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise error
    monkeypatch.setattr(selection, "urlopen", fail)
    with pytest.raises(SelectionError) as caught:
        GeminiSelector("secret").select(selection_input())
    assert str(caught.value) == code and caught.value.__cause__ is None
    assert calls == [1]


def test_response_and_request_size_bounds(monkeypatch):
    calls = mock_response(monkeypatch, b"x" * (selection.MAX_BYTES+1))
    with pytest.raises(SelectionError, match="response_too_large"):
        GeminiSelector("secret").select(selection_input())
    value = SelectionInput("Artist", "x" * selection.MAX_BYTES, (), (), "no_results")
    with pytest.raises(SelectionError, match="request_too_large"):
        GeminiSelector("secret").select(value)
    assert len(calls) == 1


def test_eligibility_excludes_unknown_and_incompatible_individually():
    cs = [Candidate("good", "Song", ["Artist", "Guest"]), Candidate("unknown", "Song (Demo)", ["Artist"]),
          Candidate("live", "Song (Live)", ["Artist"]), Candidate("cover", "Song", ["Other"])]
    assert eligible_candidates(Track("Artist", "Song", 1), cs) == ("good",)
    assert not eligible_candidates(Track("Artist", "Song (Demo)", 1), cs)
    for version in ("Live at Wembley 1999", "2011 Remaster", "Acoustic", "Clean", "Explicit", "Remix DJ X"):
        request = Track("Artist", f"Song ({version})", 1)
        assert eligible_candidates(request, [Candidate("yes", request.title, ["Artist"]), cs[0]]) == ("yes",)


def test_eligibility_accepts_featured_and_literal_titles_without_accepting_other_versions():
    request = Track("Rihanna", "Only Girl (In the World)", 1)
    cs = [Candidate("studio", "Only Girl (In The World) (feat. Guest)", ["Rihanna"]),
          Candidate("remix", "Only Girl (In The World) (Remix) (feat. Guest)", ["Rihanna"]),
          Candidate("other", "Only Girl", ["Rihanna"])]
    assert eligible_candidates(request, cs) == ("studio",)
    requested_guest = Track("Artist", "Song (feat. Guest One)", 1)
    assert eligible_candidates(requested_guest, [
        Candidate("right", "Song (ft. Guest One)", ["Artist"]),
        Candidate("wrong", "Song (feat. Guest Two)", ["Artist"]),
    ]) == ("right",)


def test_gemini_can_check_abbreviated_titles_without_bypassing_version_or_artist_rules():
    request = Track("Artist", "Song", 1)
    cs = [Candidate("subtitle", "Song (Another Story) (feat. Guest)", ["Artist"]),
          Candidate("demo", "Song (Demo)", ["Artist"]),
          Candidate("live", "Song (Another Story) (Live)", ["Artist"]),
          Candidate("remix", "Song (Another Story) (Remix)", ["Artist"]),
          Candidate("other", "Song (Another Story)", ["Other"]),
          Candidate("different", "Different Song (Another Story)", ["Artist"])]
    assert eligible_candidates(request, cs) == ("subtitle",)
    specific = Track("Artist", "Song (Another Story)", 1)
    assert not eligible_candidates(specific, [Candidate("short", "Song", ["Artist"])])


def test_multiple_literal_subtitles_can_be_checked_by_gemini():
    request = Track("Shakira", "Waka Waka", 1)
    full = 'Waka Waka (This Time for Africa) [The Official 2010 FIFA World Cup (TM) Song] (feat. Freshlyground)'
    assert eligible_candidates(request, [Candidate("studio", full, ["Shakira"])]) == ("studio",)


@pytest.mark.parametrize("requested,credited", [
    ("Black Eyed Peas", "The Black Eyed Peas"), ("The Black Eyed Peas", "Black Eyed Peas"),
])
def test_leading_artist_article_is_checked_by_gemini_without_local_guess(requested, credited):
    request = Track(requested, "Song", 1)
    cs = [Candidate("correct", "Song", [credited]),
          Candidate("cover", "Song", ["Other"]),
          Candidate("live", "Song (Live)", [credited])]
    assert resolve_candidates(request, cs).selected_video_id is None
    assert eligible_candidates(request, cs) == ("correct",)


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    store = DraftStore(tmp_path / "state.sqlite3")
    selector = SimpleNamespace(select=lambda value: SelectionDecision("id7", "preferred_release"))
    queries = []
    def songs(query):
        queries.append(query)
        return candidates()
    flow = PlaylistWorkflow(store, SimpleNamespace(songs=songs), None, selector)
    flow.search_queries = queries
    flow.delays = []
    monkeypatch.setattr("ym_playlist_maker.service.sleep", flow.delays.append)
    flow.start("\nArtist — Song\nArtist — Song")
    yield flow
    store.close()


def test_clean_studio_match_is_still_chosen_by_gemini(workflow):
    workflow.search.songs = lambda query: [
        Candidate("remix", "Song (Remix) (feat. Guest)", ["Artist"]),
        Candidate("studio", "Song (feat. Guest)", ["Artist"]),
        Candidate("unclear", "Song (Special Version)", ["Artist"]),
    ]
    calls = []
    def select(value):
        calls.append(value)
        return SelectionDecision("studio", "preferred_release")
    workflow.selector.select = select
    draft = workflow.resolve_pending()
    assert draft.phase == "title" and len(calls) == 2
    assert calls[0].eligible_ids == ("studio",)
    assert all(t.selected == "studio" and t.selection_record.status == "selected" for t in draft.tracks)


def test_ambiguous_featured_releases_reach_gemini_with_alternates_excluded(workflow):
    workflow.search.songs = lambda query: [
        Candidate("unknown", "Song (Demo)", ["Artist"]),
        Candidate("first", "Song (feat. Guest)", ["Artist"], "Album", "3:42"),
        Candidate("second", "Song (feat. Guest)", ["Artist"], "Compilation", "3:48"),
        Candidate("live", "Song (Live) (feat. Guest)", ["Artist"]),
    ]
    calls = []
    def select(value):
        calls.append(value)
        assert value.eligible_ids == ("first", "second")
        assert value.rules_reason == "different_durations"
        return SelectionDecision("first", "preferred_release")
    workflow.selector.select = select
    draft = workflow.resolve_pending()
    assert len(calls) == 2
    assert all(t.selected == "first" and t.selection_record.status == "selected" for t in draft.tracks)


def test_abbreviated_title_is_decided_by_gemini_and_not_guessed_locally(workflow):
    workflow.search.songs = lambda query: [
        Candidate("subtitle", "Song (Another Story)", ["Artist"]),
        Candidate("live", "Song (Live)", ["Artist"]),
    ]
    calls = []
    def select(value):
        calls.append(value)
        assert value.eligible_ids == ("subtitle",)
        return SelectionDecision("subtitle", "preferred_release")
    workflow.selector.select = select
    draft = workflow.resolve_pending()
    assert len(calls) == 2
    assert all(t.selection_record.status == "selected" for t in draft.tracks)


@pytest.mark.parametrize("title,reason", [
    ("Song (Demo)", "unsupported_qualifier"), ("Song (Live)", "version_unavailable"),
    ("Different Song", "no_confident_match"),
])
def test_no_eligible_results_still_reach_gemini_with_local_hint(workflow, title, reason):
    workflow.search.songs = lambda query: [Candidate("id", title, ["Artist"])]
    seen = []
    def select(value):
        seen.append(value)
        return SelectionDecision("id", "closest_version")
    workflow.selector.select = select
    draft = workflow.resolve_pending()
    assert seen[0].eligible_ids == () and seen[0].rules_reason == reason
    assert draft.tracks[0].selected == "id" and draft.tracks[0].selection_record.reason == "closest_version"


def test_model_no_match_logs_skip_without_provider_failure(workflow, caplog):
    caplog.set_level(logging.INFO, logger="ym_playlist_maker.service")
    workflow.selector.select = lambda value: SelectionDecision(None, "no_match")
    draft = workflow.resolve_pending()
    assert draft.tracks[0].selection_record.status == "no_match" and draft.tracks[0].status == "skipped"
    assert "Recording skipped: source_line=2 skip_reason=no_match" in caplog.text
    assert "Gemini selection failed" not in caplog.text


def test_attempt_is_persisted_before_dispatch_and_selected_results_survive_restart(workflow):
    calls = []
    def select(value):
        draft = workflow.active()
        track = next(t for t in draft.tracks if t.status == "pending")
        assert track.search_query and track.search_revision == 1
        assert track.selection_record.status == "attempted"
        assert track.selection_record.snapshot_hash == value.snapshot_hash()
        calls.append(value)
        return SelectionDecision("id7", "preferred_release")
    workflow.selector.select = select
    draft = workflow.resolve_pending()
    assert draft.phase == "title"
    assert [t.selected for t in draft.tracks] == ["id7", "id7"]
    assert len(calls) == 2
    again = PlaylistWorkflow(workflow.store, workflow.search, None, workflow.selector)
    again.resolve_pending()
    assert len(calls) == 2
    assert again.active().tracks[0].selection_record.status == "selected"


@pytest.mark.parametrize("result,reason,record_status,expected_calls", [
    (SelectionDecision(None, "no_match"), "no_match", "no_match", 1),
    (SelectionError("http_429"), "selection_failed", "failed", 3),
    (SelectionError("transport_error", "timeout"), "selection_failed", "failed", 3),
    (SelectionDecision("live", "preferred_release"), "selection_failed", "failed", 3),
    (SelectionDecision("id1", "no_match"), "selection_failed", "failed", 3),
    (RuntimeError("secret"), "selection_failed", "failed", 3),
    (SelectionError("http_404"), "selection_failed", "failed", 1),
    (SelectionError("http_400", "invalid_argument"), "selection_failed", "failed", 1),
    (SelectionError("request_too_large"), "selection_failed", "failed", 1),
    (SelectionError("redirect_blocked"), "selection_failed", "failed", 1),
])
def test_skipped_outcomes_persist_and_never_repeat(workflow, result, reason, record_status, expected_calls):
    calls = []
    def select(value):
        calls.append(1)
        if isinstance(result, Exception):
            raise result
        return result
    workflow.selector.select = select
    draft = workflow.resolve_pending()
    assert draft.tracks[0].selected is None and draft.tracks[0].status == "skipped"
    assert draft.tracks[0].skip_reason == reason
    assert draft.tracks[0].selection_record.status == record_status
    workflow.resolve_pending()
    assert len(calls) == expected_calls * 2  # both source lines are processed once
    # Only transient failures are retried, with a fixed pause before each retry.
    assert workflow.delays == [SELECTION_RETRY_DELAY] * (expected_calls - 1) * 2
    assert "secret" not in json.dumps(workflow.active().to_dict())


@pytest.mark.parametrize("failures", [1, 2])
def test_transient_failure_then_success_selects_recording(workflow, failures):
    calls = []
    def select(value):
        calls.append(1)
        if len(calls) <= failures:
            raise SelectionError("http_429")
        return SelectionDecision("id7", "preferred_release")
    workflow.selector.select = select
    draft = workflow.resolve_pending()
    assert [t.status for t in draft.tracks] == ["selected", "selected"]
    assert draft.tracks[0].selection_record.status == "selected" and draft.tracks[0].skip_reason is None
    assert len(calls) == failures + 2  # the second line succeeds on its first attempt
    assert workflow.delays == [SELECTION_RETRY_DELAY] * failures


@pytest.mark.parametrize("code,detail", [("http_400", "api_key_invalid"), ("http_401", None),
                                         ("http_403", "permission_denied"), ("http_403", None)])
def test_rejected_api_key_stops_run_and_resume_reselects(workflow, code, detail):
    calls = []
    def reject(value):
        calls.append(1)
        raise SelectionError(code, detail)
    workflow.selector.select = reject
    with pytest.raises(WorkflowError, match="GEMINI_API_KEY"):
        workflow.resolve_pending()
    draft = workflow.active()
    assert len(calls) == 1 and workflow.delays == []
    assert draft.phase == "resolving" and [t.status for t in draft.tracks] == ["pending", "pending"]
    assert draft.tracks[0].selection_record.status == "attempted"
    workflow.selector.select = lambda value: SelectionDecision("id7", "preferred_release")
    draft = workflow.resolve_pending()
    assert draft.phase == "title" and [t.selected for t in draft.tracks] == ["id7", "id7"]
    assert workflow.search_queries == ["Artist Song", "Artist Song"]  # stored candidates are reused


@pytest.mark.parametrize("mutation", ["cancel", "new_draft", "revision", "snapshot"])
def test_stale_provider_result_cannot_overwrite_state(workflow, mutation):
    def select(value):
        if mutation == "cancel":
            workflow.cancel()
        elif mutation == "new_draft":
            workflow.cancel()
            workflow.start("Other — Song")
        else:
            draft = workflow.active()
            if mutation == "revision":
                draft.tracks[0].search_revision += 1
            else:
                draft.tracks[0].candidates[0].album = "Changed"
            workflow.store.save(draft)
        return SelectionDecision("id7", "preferred_release")
    workflow.selector.select = select
    with pytest.raises(WorkflowError, match="changed"):
        workflow.resolve_pending()
    draft = workflow.active()
    assert draft is None or draft.tracks[0].selected is None



def test_edit_uses_source_line_keeps_title_and_invalidates_old_create(workflow):
    workflow.resolve_pending()
    workflow.set_title("My playlist")
    workflow.edit(2, "Artist Song live")
    draft = workflow.active()
    assert draft.title == "My playlist" and draft.review_revision == 1 and draft.phase == "resolving"
    assert draft.tracks[0].search_revision == 2 and draft.tracks[0].selection_record is None
    assert draft.tracks[0].candidates == [] and draft.tracks[0].search_query == "Artist Song live"
    assert draft.tracks[1].selected == "id7"
    with pytest.raises(WorkflowError, match="outdated"):
        workflow.publish(expected_review_revision=0)
    draft = workflow.resolve_pending()
    assert draft.phase == "confirm" and draft.tracks[0].selected == "id7"
    assert workflow.search_queries[-1] == "Artist Song live"


def test_config_trims_and_hides_key_and_loads_legacy_json(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "1")
    monkeypatch.setenv("GEMINI_API_KEY", " secret ")
    config = bot_config()
    assert config.gemini_api_key == "secret" and "secret" not in repr(config)
    legacy = Draft.from_dict({"draft_id": "old", "tracks": [{"artist": "A", "title": "B", "source_line": 1}]})
    assert legacy.review_revision == 0 and legacy.tracks[0].selection_record is None


@pytest.mark.parametrize("value", [None, "", "   "])
def test_config_requires_gemini_key(monkeypatch, value):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "1")
    if value is None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("GEMINI_API_KEY", value)
    with pytest.raises(ValueError, match="Set GEMINI_API_KEY"):
        bot_config()


def test_legacy_manual_flow_draft_loads_and_drops_await_query():
    legacy = Draft.from_dict({"draft_id": "old", "phase": "resolving", "tracks": [
        {"artist": "A", "title": "B", "source_line": 1, "status": "pending", "await_query": True,
         "selection_record": {"provider": "gemini", "model": "m", "policy": "release-selection-v2",
                              "snapshot_hash": "h", "search_revision": 1, "status": "abstained"}},
    ]})
    track = legacy.tracks[0]
    assert track.status == "pending" and track.selection_record.status == "abstained"
    assert "await_query" not in json.dumps(legacy.to_dict())


def test_selection_ui_progress_runs_on_loop_and_delivery_failure_does_not_affect_choice(workflow):
    async def run():
        loop_thread = threading.get_ident()
        sent = []
        class Message:
            async def reply_text(self, text, **kwargs):
                assert threading.get_ident() == loop_thread
                sent.append(text)
                if text.startswith("Gemini"):
                    raise OSError("delivery failed")
        ui = BotUI(BotConfig("dummy", 123, Path("unused"), "gemini"), workflow)
        await ui._show_state(SimpleNamespace(effective_message=Message()))
        assert len([t for t in sent if t.startswith("Gemini")]) == 2
        assert sent[-1] == "Selection complete. Send a title for the new playlist."
        assert workflow.active().tracks[0].selected == "id7"
    asyncio.run(run())


def test_all_skipped_after_edit_stays_editable_and_recovers(workflow):
    workflow.resolve_pending()
    draft = workflow.active()
    draft.tracks[1].status = "skipped"
    workflow.store.save(draft)
    workflow.set_title("Keep title")
    workflow.edit(2, "typo")
    workflow.selector.select = lambda value: SelectionDecision(None, "no_match")
    draft = workflow.resolve_pending()
    assert draft.phase == "confirm" and draft.tracks[0].skip_reason == "no_match"
    with pytest.raises(WorkflowError, match="All songs"):
        workflow.publish(expected_review_revision=draft.review_revision)
    workflow.selector.select = lambda value: SelectionDecision("id7", "preferred_release")
    workflow.edit(2, "fixed query")
    draft = workflow.resolve_pending()
    assert draft.phase == "confirm" and draft.title == "Keep title"
    assert draft.tracks[0].selected == "id7" and draft.tracks[0].skip_reason is None


def test_all_skipped_first_pass_allows_edit_before_title(workflow):
    workflow.selector.select = lambda value: SelectionDecision(None, "no_match")
    draft = workflow.resolve_pending()
    assert draft.phase == "title" and {t.skip_reason for t in draft.tracks} == {"no_match"}
    with pytest.raises(WorkflowError, match="All songs"):
        workflow.set_title("Title")
    workflow.selector.select = lambda value: SelectionDecision("id7", "preferred_release")
    workflow.edit(3, "Artist Song studio")
    draft = workflow.resolve_pending()
    assert draft.phase == "title" and draft.tracks[1].selected == "id7"
    assert workflow.set_title("Title").phase == "confirm"


@pytest.mark.parametrize("state", ["wrong_line", "resolving", "writing", "playlist", "insert_history", "uncertain", "added"])
def test_edit_rejects_invalid_boundary(workflow, state):
    workflow.resolve_pending()
    workflow.set_title("Title")
    draft = workflow.active()
    if state in ("resolving", "writing"):
        draft.phase = state
    elif state == "playlist":
        draft.playlist_id = "PL"
    elif state == "insert_history":
        draft.tracks[0].insert_revision = 1
    elif state in ("uncertain", "added"):
        draft.tracks[0].status = state
    workflow.store.save(draft)
    before = workflow.active().to_dict()
    with pytest.raises(WorkflowError):
        workflow.edit(1 if state == "wrong_line" else 2, "query")
    assert workflow.active().to_dict() == before


@pytest.mark.parametrize("query", ["", "   ", "x" * 201])
def test_edit_rejects_invalid_query_without_changing_state(workflow, query):
    workflow.resolve_pending()
    workflow.set_title("Title")
    before = workflow.active().to_dict()
    with pytest.raises(WorkflowError, match="between 1 and 200"):
        workflow.edit(2, query)
    assert workflow.active().to_dict() == before


def gaga_results(query):
    return [
        Candidate("cover", "Poker Face (Made Popular By Lady Gaga) [Vocal Version]", ["Party Tyme Karaoke"]),
        Candidate("original", "Poker Face", ["Lady Gaga"], "The Fame", "3:58"),
        Candidate("other", "Bad Romance", ["Lady Gaga"], "The Fame Monster", "4:55"),
    ]


def test_reversed_song_and_artist_is_corrected_before_gemini(workflow):
    workflow.cancel()
    workflow.start("Poker Face — Lady Gaga")
    workflow.search.songs = gaga_results
    seen = []
    def select(value):
        seen.append(value)
        return SelectionDecision("original", "preferred_release")
    workflow.selector.select = select
    draft = workflow.resolve_pending()
    track = draft.tracks[0]
    assert (seen[0].artist, seen[0].title, seen[0].eligible_ids) == ("Lady Gaga", "Poker Face", ("original",))
    assert (track.artist, track.title, track.order_swapped, track.selected) == ("Lady Gaga", "Poker Face", True, "original")
    reloaded = workflow.active().tracks[0]
    assert reloaded.order_swapped and reloaded.search_query == "Poker Face Lady Gaga"


def test_correct_order_and_unmatched_lines_are_not_swapped(workflow):
    workflow.cancel()
    workflow.start("Lady Gaga — Poker Face\nNobody — Lady Gaga")
    workflow.search.songs = gaga_results
    seen = []
    def select(value):
        seen.append((value.artist, value.title))
        return SelectionDecision(None, "no_match") if value.artist == "Nobody" else SelectionDecision("original", "preferred_release")
    workflow.selector.select = select
    draft = workflow.resolve_pending()
    assert seen == [("Lady Gaga", "Poker Face"), ("Nobody", "Lady Gaga")]
    assert [t.order_swapped for t in draft.tracks] == [False, False]


def test_edit_with_correct_order_fixes_reversed_line(workflow):
    workflow.cancel()
    workflow.start("Poker Face — Lady Gaga")
    workflow.search.songs = lambda query: []
    assert workflow.resolve_pending().tracks[0].skip_reason == "no_results"
    workflow.search.songs = gaga_results
    workflow.selector.select = lambda value: SelectionDecision("original", "preferred_release")
    workflow.edit(1, "Lady Gaga Poker Face")
    draft = workflow.resolve_pending()
    assert draft.tracks[0].order_swapped and draft.tracks[0].artist == "Lady Gaga"
    assert draft.tracks[0].selected == "original"


def test_resumed_reversed_line_is_not_treated_as_custom_search(workflow):
    workflow.cancel()
    workflow.start("Poker Face — Lady Gaga")
    workflow.search.songs = gaga_results
    reasons = []
    def reject(value):
        reasons.append(value.rules_reason)
        raise SelectionError("http_401")
    workflow.selector.select = reject
    with pytest.raises(WorkflowError):
        workflow.resolve_pending()
    def select(value):
        reasons.append(value.rules_reason)
        return SelectionDecision("original", "preferred_release")
    workflow.selector.select = select
    assert workflow.resolve_pending().tracks[0].selected == "original"
    assert reasons[0] == reasons[1] != "custom_search"


def test_search_without_results_is_skipped_without_gemini(workflow):
    workflow.search.songs = lambda query: []
    workflow.selector.select = lambda value: pytest.fail("no candidates must not reach Gemini")
    draft = workflow.resolve_pending()
    assert draft.phase == "title"
    assert all(t.status == "skipped" and t.skip_reason == "no_results" and t.selection_record is None
               for t in draft.tracks)


def test_edit_after_definitive_creation_429_is_retriable(workflow, monkeypatch):
    from test_workflow import FakeYouTube, api_error
    youtube = FakeYouTube()
    youtube.create_error = api_error(429, "quotaExceeded")
    workflow.youtube = youtube
    monkeypatch.setattr("ym_playlist_maker.service.sleep", lambda seconds: None)
    workflow.resolve_pending()
    workflow.set_title("Title")
    with pytest.raises(Exception):
        workflow.publish(expected_review_revision=0)
    assert workflow.active().phase == "confirm" and workflow.active().create_marker
    workflow.edit(2, "Artist Song live")
    workflow.resolve_pending()
    assert workflow.publish(expected_review_revision=1).phase == "done"
    assert len(youtube.created) == 1


def test_ui_edit_owner_only_and_source_line(workflow):
    workflow.resolve_pending()
    workflow.set_title("Title")
    sent = []
    class Message:
        async def reply_text(self, text, **kwargs):
            sent.append((text, kwargs))
    ui = BotUI(BotConfig("dummy", 123, Path("unused"), "gemini"), workflow)
    update = SimpleNamespace(effective_user=SimpleNamespace(id=456),
                             effective_chat=SimpleNamespace(type=ChatType.PRIVATE), effective_message=Message())
    asyncio.run(ui.edit(update, SimpleNamespace(args=["2", "Artist", "live"])))
    assert workflow.active().review_revision == 0 and not sent
    update.effective_user.id = 123
    asyncio.run(ui.edit(update, SimpleNamespace(args=["2"])))
    assert workflow.active().review_revision == 0 and "Use /edit" in sent[-1][0]
    asyncio.run(ui.edit(update, SimpleNamespace(args=["2", "Artist", "live"])))
    assert workflow.active().review_revision == 1 and workflow.active().phase == "confirm"
    assert workflow.search_queries[-1] == "Artist live"


def test_ui_stale_and_legacy_create_buttons_rejected_after_edit(workflow):
    workflow.resolve_pending()
    workflow.set_title("Title")
    draft_id = workflow.active().draft_id
    workflow.edit(2, "Artist Song live")
    workflow.resolve_pending()
    sent = []
    class Message:
        async def reply_text(self, text, **kwargs):
            sent.append(text)
    async def answer():
        pass
    ui = BotUI(BotConfig("dummy", 123, Path("unused"), "gemini"), workflow)
    for suffix in ("create", "create:0"):
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE), effective_message=Message(),
            callback_query=SimpleNamespace(data=f"d:{draft_id}:{suffix}", answer=answer))
        asyncio.run(ui.callback(update, None))
        assert "outdated" in sent[-1]
    assert workflow.active().phase == "confirm"



def test_explicit_provider_block_with_completed_content_is_rejected(monkeypatch):
    payload = reply()
    payload["promptFeedback"] = {"blockReason": "SAFETY"}
    mock_response(monkeypatch, payload)
    with pytest.raises(SelectionError, match="invalid_response"):
        GeminiSelector("secret").select(selection_input())


@pytest.mark.parametrize("status,reason,expected", [
    (400, "API_KEY_INVALID", "api_key_invalid"),
    (403, "SERVICE_DISABLED", "service_disabled"),
    (429, "RESOURCE_EXHAUSTED", "resource_exhausted"),
    (400, "secret provider reason", "invalid_argument"),
])
def test_http_diagnostics_copy_only_allowlisted_symbols(monkeypatch, status, reason, expected):
    body = {"error": {"message": "secret key and user song", "status": "INVALID_ARGUMENT",
                      "details": [{"reason": reason, "metadata": {"key": "secret"}}]}}
    error = HTTPError(selection.ENDPOINT, status, "secret", {}, io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(selection, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(SelectionError) as caught:
        GeminiSelector("secret").select(selection_input())
    assert caught.value.code == f"http_{status}"
    assert caught.value.detail == expected
    assert "secret" not in str(caught.value)


def test_http_error_body_read_is_bounded_and_closed(monkeypatch):
    class Body(io.BytesIO):
        read_sizes = []
        def read(self, size=-1):
            self.read_sizes.append(size)
            return super().read(size)
    body = Body(b"x" * (selection.MAX_ERROR_BYTES + 100))
    error = HTTPError(selection.ENDPOINT, 400, "secret", {}, body)
    monkeypatch.setattr(selection, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(SelectionError) as caught:
        GeminiSelector("secret").select(selection_input())
    assert caught.value.detail is None
    assert body.read_sizes == [selection.MAX_ERROR_BYTES + 1] and body.closed


@pytest.mark.parametrize("payload,category", [
    (reply(finish="MAX_TOKENS"), "max_tokens"), (reply(finish="SAFETY"), "safety"),
    (b"not json", "invalid_json"), (reply("c999"), "invalid_choice"),
    ({"candidates": []}, "invalid_shape"),
])
def test_response_failure_categories_are_safe(monkeypatch, payload, category):
    mock_response(monkeypatch, payload)
    with pytest.raises(SelectionError) as caught:
        GeminiSelector("secret").select(selection_input())
    assert caught.value.code == "invalid_response" and caught.value.detail == category


@pytest.mark.parametrize("code,detail", [("http_400", "invalid_argument"), ("invalid_response", "max_tokens"),
                                         ("transport_error", "timeout"), ("secret", "secret")])
def test_failed_workflow_logs_and_persists_only_safe_diagnostics(workflow, caplog, code, detail):
    def fail(value):
        raise SelectionError(code, detail)
    workflow.selector.select = fail
    draft = workflow.resolve_pending()
    track = draft.tracks[0]
    assert track.status == "skipped" and track.skip_reason == "selection_failed"
    record = track.selection_record
    assert record.status == "failed"
    assert record.error_code == (code if code in selection.ERROR_CODES else "selector_error")
    assert record.error_detail == (detail if detail in selection.ERROR_DETAILS else None)
    assert "Gemini selection failed" in caplog.text and "source_line=2" in caplog.text
    assert f"model={selection.MODEL}" in caplog.text
    assert "secret" not in caplog.text and "Artist" not in caplog.text and "Song" not in caplog.text
    assert "Traceback" not in caplog.text
    reloaded = workflow.active().tracks[0].selection_record
    assert reloaded == record
    caplog.clear()
    workflow.resolve_pending()
    assert "Gemini selection failed" not in caplog.text




def test_old_selection_record_loads_without_diagnostic_detail():
    from ym_playlist_maker.models import SelectionRecord
    track = Track("A", "B", 1, selection_record=SelectionRecord("gemini", selection.MODEL, selection.POLICY,
                                                               "hash", 1, status="failed", error_code="http_429"))
    saved = Draft("old", [track]).to_dict()
    del saved["tracks"][0]["selection_record"]["error_detail"]
    loaded = Draft.from_dict(saved)
    assert loaded.tracks[0].selection_record.error_code == "http_429"
    assert loaded.tracks[0].selection_record.error_detail is None


def test_gemini35_request_uses_fixed_minimal_thinking_config(monkeypatch):
    calls = mock_response(monkeypatch, reply())
    GeminiSelector("secret").select(selection_input())
    request = calls[0][0]
    assert "models/gemini-3.5-flash-lite:generateContent" in request.full_url
    config = json.loads(request.data)["generationConfig"]
    assert config["maxOutputTokens"] == 512
    assert config["thinkingConfig"] == {"thinkingLevel": "MINIMAL", "includeThoughts": False}
    assert "temperature" not in config and "candidateCount" not in config
    assert config["responseMimeType"] == "application/json"


@pytest.mark.parametrize("prefix,metadata", [
    ([], {"thoughtSignature": "opaque signature"}),
    ([{"text": json.dumps({"choice": "c0", "reason": "preferred_release"}), "thought": True}], {}),
    ([{"thoughtSignature": "opaque signature"}], {}),
    ([{"text": "analysis only", "thought": True, "thoughtSignature": "opaque"}], {"thought": False}),
])
def test_gemini35_text_and_thought_metadata_preserve_final_decision(monkeypatch, prefix, metadata):
    payload = reply()
    final = payload["candidates"][0]["content"]["parts"][0]
    final.update(metadata)
    payload["candidates"][0]["content"]["parts"] = prefix + [final]
    mock_response(monkeypatch, payload)
    assert GeminiSelector("secret").select(selection_input()) == SelectionDecision("id7", "preferred_release")


@pytest.mark.parametrize("parts", [
    [{"text": '{"choice":"c0","reason":"preferred_release"}', "thought": True}],
    [{"thoughtSignature": "opaque"}], [{"text": "", "thoughtSignature": "opaque"}],
    [{"text": "   "}], [{"thought": True}],
    [{"text": "{}", "thoughtSignature": 123}], [{"text": "{}", "thought": "false"}],
    [{"text": "{}", "thought": 0}], [{"text": 123}],
    [{"text": "{}", "functionCall": {"name": "x"}}],
    [{"text": "{}", "inlineData": {"data": "opaque"}}],
    [{"text": "{}", "unknownMetadata": "opaque"}],
])
def test_gemini35_metadata_without_valid_final_text_is_rejected(monkeypatch, parts):
    payload = reply()
    payload["candidates"][0]["content"]["parts"] = parts
    mock_response(monkeypatch, payload)
    with pytest.raises(SelectionError) as caught:
        GeminiSelector("secret").select(selection_input())
    assert caught.value.code == "invalid_response" and caught.value.detail == "invalid_shape"


def test_gemini35_final_json_contract_remains_exact(monkeypatch):
    payload = reply()
    payload["candidates"][0]["content"]["parts"] = [{
        "text": json.dumps({"choice": "c7", "reason": "preferred_release", "explanation": "extra"}),
        "thoughtSignature": "opaque"}]
    mock_response(monkeypatch, payload)
    with pytest.raises(SelectionError) as caught:
        GeminiSelector("secret").select(selection_input())
    assert caught.value.detail == "invalid_fields"


@pytest.mark.parametrize("status", ["attempted", "abstained", "failed"])
def test_old_model_pending_record_is_reselected_from_stored_candidates(workflow, status):
    from ym_playlist_maker.models import SelectionRecord
    draft = workflow.active()
    for track in draft.tracks:
        track.candidates = candidates()
        track.search_query = "Artist Song"
        track.search_revision = 1
        track.selection_record = SelectionRecord("gemini", "gemini-2.5-flash-lite", "release-selection-v2",
                                                 "legacy snapshot hash", 1, status=status)
    workflow.store.save(draft)
    workflow.search.songs = lambda query: pytest.fail("stored candidates must not be searched again")
    reloaded = workflow.resolve_pending()
    assert all(t.selected == "id7" and t.selection_record.status == "selected" and t.search_revision == 1
               for t in reloaded.tracks)
    record = reloaded.tracks[0].selection_record
    assert (record.model, record.policy) == (selection.MODEL, selection.POLICY)
    assert record.snapshot_hash != "legacy snapshot hash"
