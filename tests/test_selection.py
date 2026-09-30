"""Offline contracts for optional release selection and review editing."""
import asyncio
import io
import json
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
from ym_playlist_maker.parser import eligible_candidates
from ym_playlist_maker.selection import GeminiSelector, SelectionDecision, SelectionError, SelectionInput
from ym_playlist_maker.service import PlaylistWorkflow, WorkflowError
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
    assert schema["properties"]["choice"]["enum"] == [f"c{i}" for i in range(8)] + ["abstain"]
    assert "video_id" not in body["contents"][0]["parts"][0]["text"]


@pytest.mark.parametrize("payload", [
    reply("id7"), reply("c99"), reply("abstain"), reply("c0", "insufficient_evidence"),
    reply(finish="MAX_TOKENS"), reply(finish="SAFETY"), {"promptFeedback": {"blockReason": "SAFETY"}},
    {"candidates": []}, {"candidates": [reply()["candidates"][0]] * 2},
    {"candidates": "bad"}, b"not json", reply(None), reply(reason=[]),
])
def test_invalid_and_blocked_provider_results_are_safe(monkeypatch, payload):
    mock_response(monkeypatch, payload)
    with pytest.raises(SelectionError, match="^invalid_response$"):
        GeminiSelector("secret").select(selection_input())


def test_abstain(monkeypatch):
    mock_response(monkeypatch, reply("abstain", "insufficient_evidence"))
    assert GeminiSelector("secret").select(selection_input()) == SelectionDecision(None, "insufficient_evidence")


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


@pytest.fixture
def workflow(tmp_path):
    store = DraftStore(tmp_path / "state.sqlite3")
    selector = SimpleNamespace(select=lambda value: SelectionDecision("id7", "preferred_release"))
    flow = PlaylistWorkflow(store, SimpleNamespace(songs=lambda query: candidates()), None, selector)
    flow.start("\nArtist — Song\nArtist — Song")
    yield flow
    store.close()


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
    draft, index = workflow.next_unresolved()
    assert index is None and draft.phase == "title"
    assert [t.selected for t in draft.tracks] == ["id7", "id7"]
    assert len(calls) == 2
    again = PlaylistWorkflow(workflow.store, workflow.search, None, workflow.selector)
    again.next_unresolved()
    assert len(calls) == 2
    assert again.active().tracks[0].selection_record.status == "selected"


@pytest.mark.parametrize("result", [SelectionDecision(None, "insufficient_evidence"), SelectionError("http_429"),
                                    SelectionDecision("live", "preferred_release"), RuntimeError("secret")])
def test_manual_fallback_persists_and_never_repeats(workflow, result):
    calls = []
    def select(value):
        calls.append(1)
        if isinstance(result, Exception):
            raise result
        return result
    workflow.selector.select = select
    draft, index = workflow.next_unresolved()
    assert index == 0 and draft.tracks[0].selected is None
    assert draft.tracks[0].selection_record.status in ("failed", "abstained")
    workflow.next_unresolved()
    assert calls == [1]
    assert "secret" not in json.dumps(workflow.active().to_dict())


@pytest.mark.parametrize("mutation", ["cancel", "new_draft", "revision", "await_query", "snapshot"])
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
            elif mutation == "await_query":
                draft.tracks[0].await_query = True
            else:
                draft.tracks[0].candidates[0].album = "Changed"
            workflow.store.save(draft)
        return SelectionDecision("id7", "preferred_release")
    workflow.selector.select = select
    with pytest.raises(WorkflowError, match="changed"):
        workflow.next_unresolved()
    draft = workflow.active()
    assert draft is None or draft.tracks[0].selected is None


def test_existing_custom_and_interrupted_snapshots_remain_manual(workflow):
    def fail(value):
        raise AssertionError("must not call")
    workflow.selector.select = fail
    draft = workflow.active()
    draft.tracks[0].search_query = "Artist Song"
    draft.tracks[0].candidates = candidates()
    workflow.store.save(draft)
    assert workflow.next_unresolved()[1] == 0
    workflow.request_search_again(0)
    workflow.search_again(0, "Artist Song")
    assert workflow.active().tracks[0].selection_record is None
    assert workflow.next_unresolved()[1] == 0


def test_edit_uses_source_line_keeps_title_and_invalidates_old_create(workflow):
    workflow.next_unresolved()
    workflow.set_title("My playlist")
    workflow.edit(2)
    draft = workflow.active()
    assert draft.title == "My playlist" and draft.review_revision == 1
    assert draft.tracks[0].search_revision == 2 and draft.tracks[0].selection_record is None
    assert draft.tracks[1].selected == "id7"
    with pytest.raises(WorkflowError, match="outdated"):
        workflow.publish(expected_review_revision=0)
    draft, index = workflow.next_unresolved()
    assert index == 0
    workflow.choose(0, 1, 2)
    assert workflow.next_unresolved()[0].phase == "confirm"


def test_config_optional_key_hidden_and_legacy_json(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "1")
    monkeypatch.setenv("GEMINI_API_KEY", " secret ")
    config = bot_config()
    assert config.gemini_api_key == "secret" and "secret" not in repr(config)
    legacy = Draft.from_dict({"draft_id": "old", "tracks": [{"artist": "A", "title": "B", "source_line": 1}]})
    assert legacy.review_revision == 0 and legacy.tracks[0].selection_record is None


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
        ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)
        await ui._show_state(SimpleNamespace(effective_message=Message()))
        assert len([t for t in sent if t.startswith("Gemini")]) == 2
        assert sent[-1] == "Selection complete. Send a title for the new playlist."
        assert workflow.active().tracks[0].selected == "id7"
    asyncio.run(run())


def test_interrupted_attempt_does_not_repeat_after_restart(workflow):
    def interrupted(value):
        raise KeyboardInterrupt
    workflow.selector.select = interrupted
    with pytest.raises(KeyboardInterrupt):
        workflow.next_unresolved()
    assert workflow.active().tracks[0].selection_record.status == "attempted"
    workflow.selector.select = lambda value: pytest.fail("repeat")
    restarted = PlaylistWorkflow(workflow.store, workflow.search, None, workflow.selector)
    assert restarted.next_unresolved()[1] == 0


def test_skip_last_edit_cannot_create_empty_playlist(workflow):
    workflow.next_unresolved()
    draft = workflow.active()
    draft.tracks[1].status = "skipped"
    workflow.store.save(draft)
    workflow.set_title("Keep title")
    workflow.edit(2)
    workflow.skip(0, 2)
    with pytest.raises(WorkflowError, match="All songs"):
        workflow.next_unresolved()
    with pytest.raises(WorkflowError):
        workflow.publish(expected_review_revision=0)


@pytest.mark.parametrize("state", ["wrong_line", "title", "writing", "playlist", "insert_history", "uncertain", "added"])
def test_edit_rejects_invalid_boundary(workflow, state):
    workflow.next_unresolved()
    workflow.set_title("Title")
    draft = workflow.active()
    if state in ("title", "writing"):
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
        workflow.edit(1 if state == "wrong_line" else 2)
    assert workflow.active().to_dict() == before


def test_edit_after_definitive_creation_429_is_retriable(workflow, monkeypatch):
    from test_workflow import FakeYouTube, api_error
    youtube = FakeYouTube()
    youtube.create_error = api_error(429, "quotaExceeded")
    workflow.youtube = youtube
    monkeypatch.setattr("ym_playlist_maker.service.sleep", lambda seconds: None)
    workflow.next_unresolved()
    workflow.set_title("Title")
    with pytest.raises(Exception):
        workflow.publish(expected_review_revision=0)
    assert workflow.active().phase == "confirm" and workflow.active().create_marker
    workflow.edit(2)
    workflow.choose(0, 0, 2)
    workflow.next_unresolved()
    assert workflow.publish(expected_review_revision=1).phase == "done"
    assert len(youtube.created) == 1


def test_ui_edit_owner_only_and_source_line(workflow):
    workflow.next_unresolved()
    workflow.set_title("Title")
    sent = []
    class Message:
        async def reply_text(self, text, **kwargs):
            sent.append((text, kwargs))
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)
    update = SimpleNamespace(effective_user=SimpleNamespace(id=456),
                             effective_chat=SimpleNamespace(type=ChatType.PRIVATE), effective_message=Message())
    asyncio.run(ui.edit(update, SimpleNamespace(args=["2"])))
    assert workflow.active().phase == "confirm" and not sent
    update.effective_user.id = 123
    asyncio.run(ui.edit(update, SimpleNamespace(args=["2"])))
    assert workflow.active().phase == "resolving" and sent


def test_ui_stale_and_legacy_create_buttons_rejected_after_edit(workflow):
    workflow.next_unresolved()
    workflow.set_title("Title")
    draft_id = workflow.active().draft_id
    workflow.edit(2)
    workflow.choose(0, 0, 2)
    workflow.next_unresolved()
    sent = []
    class Message:
        async def reply_text(self, text, **kwargs):
            sent.append(text)
    async def answer():
        pass
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)
    for suffix in ("create", "create:0"):
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE), effective_message=Message(),
            callback_query=SimpleNamespace(data=f"d:{draft_id}:{suffix}", answer=answer))
        asyncio.run(ui.callback(update, None))
        assert "outdated" in sent[-1]
    assert workflow.active().phase == "confirm"


def test_legacy_snapshot_without_query_is_not_researched(workflow):
    draft = workflow.active()
    draft.tracks[0].candidates = candidates()
    workflow.store.save(draft)
    workflow.search.songs = lambda query: pytest.fail("legacy search")
    workflow.selector.select = lambda value: pytest.fail("legacy selection")
    assert workflow.next_unresolved()[1] == 0


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


@pytest.mark.parametrize("code,detail", [("http_400", "api_key_invalid"), ("invalid_response", "max_tokens"),
                                         ("transport_error", "timeout"), ("secret", "secret")])
def test_failed_workflow_logs_and_persists_only_safe_diagnostics(workflow, caplog, code, detail):
    def fail(value):
        raise SelectionError(code, detail)
    workflow.selector.select = fail
    draft, index = workflow.next_unresolved()
    assert index == 0
    record = draft.tracks[0].selection_record
    assert record.error_code == (code if code in selection.ERROR_CODES else "selector_error")
    assert record.error_detail == (detail if detail in selection.ERROR_DETAILS else None)
    assert "Gemini selection failed" in caplog.text and "source_line=2" in caplog.text
    assert f"model={selection.MODEL}" in caplog.text
    assert "secret" not in caplog.text and "Artist" not in caplog.text and "Song" not in caplog.text
    assert "Traceback" not in caplog.text
    reloaded = workflow.active().tracks[0].selection_record
    assert reloaded == record
    caplog.clear()
    workflow.next_unresolved()
    assert "Gemini selection failed" not in caplog.text


@pytest.mark.parametrize("code,detail,status,text", [
    ("http_400", None, "failed", "rejected the request"),
    ("http_400", "api_key_invalid", "failed", "rejected the API key"),
    ("http_401", None, "failed", "authentication failed"),
    ("http_403", None, "failed", "access is denied"),
    ("http_404", None, "failed", "endpoint is unavailable"),
    ("http_429", None, "failed", "free quota"),
    ("transport_error", None, "failed", "network or connection error"),
    ("transport_error", "timeout", "failed", "connection timeout"),
    ("invalid_response", "max_tokens", "failed", "truncated"),
    ("invalid_response", None, "failed", "invalid or incomplete"),
    (None, None, "attempted", "interrupted"),
    (None, None, "abstained", "could not choose confidently"),
    ("secret", "secret", "failed", "unavailable"),
])
def test_telegram_explains_safe_failure_categories(code, detail, status, text):
    from ym_playlist_maker.bot import selection_fallback_message
    from ym_playlist_maker.models import SelectionRecord
    record = SelectionRecord("gemini", selection.MODEL, selection.POLICY, "hash", 1,
                             status=status, error_code=code, error_detail=detail)
    message = selection_fallback_message(record)
    assert text in message and "manually" in message and "secret" not in message


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
def test_old_model_pending_record_is_preserved_without_recalling(workflow, status):
    from ym_playlist_maker.models import SelectionRecord
    draft = workflow.active()
    track = draft.tracks[0]
    track.candidates = candidates()
    track.search_query = "Artist Song"
    track.search_revision = 1
    track.selection_record = SelectionRecord("gemini", "gemini-2.5-flash-lite", selection.POLICY,
                                             "legacy snapshot hash", 1, status=status)
    workflow.store.save(draft)
    workflow.selector.select = lambda value: pytest.fail("old model snapshot must remain manual")
    reloaded, index = workflow.next_unresolved()
    assert index == 0 and reloaded.tracks[0].selection_record == track.selection_record
    assert reloaded.tracks[0].search_revision == 1
