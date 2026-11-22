import json
import sqlite3
from pathlib import Path

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from ym_playlist_maker.models import Candidate
from ym_playlist_maker.selection import SelectionDecision
from ym_playlist_maker.service import PlaylistWorkflow, UnconfirmedInsert, UncertainWrite, WorkflowError
from ym_playlist_maker.store import DraftStore
from ym_playlist_maker.youtube import BeforeCreateError, BeforeInsertError


def api_error(status, reason):
    return HttpError(Response({"status": str(status)}), (
        '{"error":{"errors":[{"reason":"' + reason + '"}]}}'
    ).encode())


class FakeSearch:
    def songs(self, query):
        if "Wonderwall" in query:
            return [Candidate("oasis", "Wonderwall", ["Oasis"])]
        if "Numb" in query:
            return [
                Candidate("numb1", "Numb", ["Linkin Park"], "Meteora"),
                Candidate("numb2", "Numb", ["Linkin Park"], "Collection"),
            ]
        return []


class FakeYouTube:
    def __init__(self):
        self.created = []
        self.items = []
        self.fail_create_after_commit = False
        self.fail_add_after_commit = False
        self.fail_add_before_commit = False
        self.fail_before_dispatch = False
        self.create_error = None
        self.add_error = None
        self.lag_item_reads = False
        self.item_reads = 0
        self.item_errors = []
        self.events = []

    def check_credentials(self):
        pass

    def create_playlist(self, title, marker, privacy):
        if self.create_error:
            error, self.create_error = self.create_error, None
            raise error
        self.created.append(("playlist1", title, marker, privacy))
        if self.fail_create_after_commit:
            self.fail_create_after_commit = False
            raise TimeoutError("lost response")
        return "playlist1"

    def find_created(self, marker):
        return [p[0] for p in self.created if p[2] == marker]

    def item_ids(self, _playlist_id):
        self.item_reads += 1
        self.events.append(("read", _playlist_id))
        if self.item_errors:
            raise self.item_errors.pop(0)
        return [] if self.lag_item_reads else list(self.items)

    def add(self, _playlist_id, video_id):
        if self.fail_before_dispatch:
            self.fail_before_dispatch = False
            raise BeforeInsertError("not sent")
        if self.add_error:
            error, self.add_error = self.add_error, None
            raise error
        if self.fail_add_before_commit:
            self.fail_add_before_commit = False
            raise TimeoutError("possibly not sent")
        self.items.append(video_id)
        if self.fail_add_after_commit:
            self.fail_add_after_commit = False
            raise TimeoutError("lost response")


class FakeSelector:
    def __init__(self):
        self.calls = []

    def select(self, value):
        self.calls.append(value)
        return SelectionDecision(value.candidates[0].video_id, "preferred_release")


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    store = DraftStore(tmp_path / "state.sqlite3")
    youtube = FakeYouTube()
    monkeypatch.setattr("ym_playlist_maker.service.sleep", lambda seconds: youtube.events.append(("sleep", seconds)))
    workflow = PlaylistWorkflow(store, FakeSearch(), youtube, FakeSelector())
    yield workflow, youtube, store
    store.close()


def prepare(workflow):
    draft, invalid = workflow.start("Oasis — Wonderwall\nLinkin Park — Numb\nOasis — Wonderwall")
    assert not invalid
    draft = workflow.resolve_pending()
    assert draft.phase == "title"
    return workflow.set_title("My songs")


def test_publish_skips_duplicate_video_and_is_idempotent(setup):
    workflow, youtube, _ = setup
    draft = prepare(workflow)
    assert draft.phase == "confirm"
    result = workflow.publish()
    assert result.phase == "done"
    assert youtube.items == ["oasis", "numb1"]
    assert result.tracks[2].status == "skipped"
    workflow.publish()
    assert len(youtube.created) == 1
    assert youtube.items == ["oasis", "numb1"]


@pytest.mark.parametrize("privacy", ["private", "public"])
def test_lost_create_response_reconciles_without_second_playlist(setup, tmp_path, privacy):
    workflow, youtube, store = setup
    prepare(workflow)
    youtube.fail_create_after_commit = True
    with pytest.raises(UncertainWrite):
        workflow.publish(privacy=privacy)
    saved = store.load().to_dict()
    assert saved["phase"] == "creating" and saved["privacy"] == privacy
    opposite = "public" if privacy == "private" else "private"
    calls = (youtube.created.copy(), youtube.events.copy(), youtube.item_reads)
    with pytest.raises(WorkflowError, match="review is outdated"):
        workflow.publish(privacy=opposite)
    assert store.load().to_dict() == saved
    assert (youtube.created, youtube.events, youtube.item_reads) == calls
    store.close()
    reopened = DraftStore(tmp_path / "state.sqlite3")
    try:
        recovered = PlaylistWorkflow(reopened, FakeSearch(), youtube, FakeSelector())
        result = recovered.publish()
        assert result.phase == "done" and result.privacy == privacy
        assert youtube.created == [("playlist1", result.title, result.create_marker, privacy)]
        assert youtube.items == ["oasis", "numb1"]
    finally:
        reopened.close()


@pytest.mark.parametrize("legacy", [True, False])
def test_persisted_visibility_survives_store_restart(setup, tmp_path, legacy):
    workflow, youtube, store = setup
    draft = prepare(workflow)
    path = tmp_path / "state.sqlite3"
    if not legacy:
        draft.privacy = "public"
        store.save(draft)
    store.close()
    if legacy:
        with sqlite3.connect(path) as db:
            payload = json.loads(db.execute("SELECT data FROM draft WHERE slot = 1").fetchone()[0])
            del payload["privacy"]
            db.execute("UPDATE draft SET data = ? WHERE slot = 1", (json.dumps(payload),))
    reopened = DraftStore(path)
    try:
        recovered = PlaylistWorkflow(reopened, FakeSearch(), youtube, FakeSelector())
        privacy = "private" if legacy else "public"
        assert reopened.load().privacy == privacy
        result = recovered.publish()
        assert result.phase == "done" and result.privacy == privacy
        assert youtube.created == [("playlist1", result.title, result.create_marker, privacy)]
    finally:
        reopened.close()


@pytest.mark.parametrize("privacy", ["unlisted", ""])
def test_invalid_visibility_does_not_check_credentials_or_save(setup, privacy):
    workflow, youtube, store = setup
    prepare(workflow)
    saved = store.load().to_dict()
    youtube.check_credentials = lambda: pytest.fail("Invalid privacy reached credentials")
    with pytest.raises(ValueError, match="^Playlist privacy must be private or public$"):
        workflow.publish(privacy=privacy)
    assert store.load().to_dict() == saved
    assert youtube.created == []


@pytest.mark.parametrize("phase", ["creating", "writing", "done"])
def test_explicit_visibility_cannot_change_started_playlist(setup, monkeypatch, phase):
    workflow, youtube, store = setup
    draft = prepare(workflow)
    draft.phase = phase
    draft.privacy = "public"
    draft.playlist_id = None if phase == "creating" else "playlist1"
    store.save(draft)
    before = draft.to_dict()
    def forbidden(*args, **kwargs):
        pytest.fail("Outdated review reached persistence or YouTube")
    for name in ("check_credentials", "create_playlist", "find_created", "item_ids", "add"):
        monkeypatch.setattr(youtube, name, forbidden)
    monkeypatch.setattr(store, "save", forbidden)
    with pytest.raises(WorkflowError, match="review is outdated"):
        workflow.publish(privacy="private")
    assert store.load().to_dict() == before


def test_lost_add_response_reconciles_without_second_insert(setup):
    workflow, youtube, store = setup
    prepare(workflow)
    youtube.fail_add_after_commit = True
    with pytest.raises(UncertainWrite):
        workflow.publish()
    assert workflow.active().phase == "writing"
    # A new workflow process can resume from the same durable database.
    recovered = PlaylistWorkflow(store, FakeSearch(), youtube, FakeSelector())
    result = recovered.publish()
    assert result.phase == "done"
    assert youtube.items == ["oasis", "numb1"]


def test_unknown_create_without_remote_match_never_retries(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.fail_create_after_commit = True
    with pytest.raises(UncertainWrite):
        workflow.publish()
    youtube.created.clear()
    with pytest.raises(UncertainWrite):
        workflow.publish()
    assert youtube.created == []


def test_cancel_after_creation_rejected(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.fail_create_after_commit = True
    with pytest.raises(UncertainWrite):
        workflow.publish()
    with pytest.raises(WorkflowError):
        workflow.cancel()


def test_before_dispatch_failure_can_resume_without_approval(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.fail_before_dispatch = True
    with pytest.raises(WorkflowError):
        workflow.publish()
    draft = workflow.active()
    assert draft.phase == "writing" and draft.tracks[0].status == "selected"
    assert workflow.publish().phase == "done"
    assert youtube.items == ["oasis", "numb1"]


def test_unknown_insert_absent_requires_owner_approved_retry(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.fail_add_before_commit = True
    with pytest.raises(UncertainWrite):
        workflow.publish()
    with pytest.raises(UnconfirmedInsert) as error:
        workflow.publish()
    assert error.value.index == 0
    assert youtube.items == []
    assert workflow.retry_uncertain(0, error.value.revision).phase == "done"
    assert youtube.items == ["oasis", "numb1"]
    with pytest.raises(WorkflowError):
        workflow.retry_uncertain(0, error.value.revision)


def test_old_retry_button_rejected_after_new_uncertain_attempt_with_lagged_reads(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.fail_add_before_commit = True
    with pytest.raises(UncertainWrite):
        workflow.publish()
    with pytest.raises(UnconfirmedInsert) as first:
        workflow.publish()
    old_revision = first.value.revision
    youtube.lag_item_reads = True
    youtube.fail_add_after_commit = True
    with pytest.raises(UncertainWrite):
        workflow.retry_uncertain(0, old_revision)
    assert workflow.active().tracks[0].insert_revision == old_revision + 1
    assert youtube.items == ["oasis"]
    reads_before = youtube.item_reads
    with pytest.raises(WorkflowError):
        workflow.retry_uncertain(0, old_revision)
    assert youtube.item_reads == reads_before  # stale button cannot even start reconciliation
    assert youtube.items == ["oasis"]
    youtube.lag_item_reads = False
    assert workflow.publish().phase == "done"
    assert youtube.items == ["oasis", "numb1"]


@pytest.mark.parametrize("error", [
    api_error(429, "rateLimitExceeded"), api_error(403, "forbidden"),
    BeforeCreateError("not dispatched"),
])
def test_definitive_creation_failure_retains_public_and_allows_private_retry(setup, error):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.create_error = error
    with pytest.raises((HttpError, WorkflowError)):
        workflow.publish(privacy="public")
    assert workflow.active().phase == "confirm"
    assert workflow.active().privacy == "public"
    assert youtube.created == []
    result = workflow.publish(privacy="private")
    assert result.phase == "done" and result.privacy == "private"
    assert youtube.created == [("playlist1", result.title, result.create_marker, "private")]
    assert youtube.items == ["oasis", "numb1"]


@pytest.mark.parametrize("reason,skipped", [("videoNotFound", True), ("playlistNotFound", False)])
def test_insert_404_only_skips_missing_video(setup, reason, skipped):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.add_error = api_error(404, reason)
    if skipped:
        result = workflow.publish()
        assert result.tracks[0].status == "skipped"
        assert youtube.items == ["numb1"]
    else:
        with pytest.raises(HttpError):
            workflow.publish()
        assert workflow.active().tracks[0].status == "selected"
        assert youtube.items == []


@pytest.mark.parametrize("failures", [0, 1, 2])
def test_initial_read_waits_and_retries_until_ready(setup, failures):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.item_errors = [api_error(404, "playlistNotFound") for _ in range(failures)]
    result = workflow.publish(on_playlist_pending=lambda: youtube.events.append(("pending", None)))
    assert result.phase == "done"
    assert youtube.events == [("sleep", 1), ("read", "playlist1"), ("pending", None)] * failures + [
        ("sleep", 1), ("read", "playlist1"), ("read", "playlist1")
    ]
    assert youtube.items == ["oasis", "numb1"]


def test_exhausted_read_preserves_error_and_resume_uses_same_playlist(setup):
    workflow, youtube, store = setup
    prepare(workflow)
    errors = [api_error(404, "playlistNotFound") for _ in range(3)]
    youtube.item_errors = errors.copy()
    with pytest.raises(HttpError) as error:
        workflow.publish(on_playlist_pending=lambda: youtube.events.append(("pending", None)))
    assert error.value is errors[-1]
    assert youtube.events == [("sleep", 1), ("read", "playlist1"), ("pending", None)] * 3
    assert youtube.items == []
    draft = workflow.active()
    assert draft.playlist_id == "playlist1" and draft.phase == "writing"
    assert all(track.insert_revision == 0 for track in draft.tracks)
    recovered = PlaylistWorkflow(store, FakeSearch(), youtube, FakeSelector())
    assert recovered.publish().phase == "done"
    assert len(youtube.created) == 1
    assert youtube.events[-3:] == [("sleep", 1), ("read", "playlist1"), ("read", "playlist1")]


@pytest.mark.parametrize("error", [
    api_error(404, "videoNotFound"), api_error(403, "forbidden"),
    api_error(503, "backendError"), TimeoutError("offline"),
])
def test_initial_read_other_failures_propagate_without_retry(setup, error):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.item_errors = [error]
    with pytest.raises(type(error)) as caught:
        workflow.publish(on_playlist_pending=lambda: pytest.fail("Unexpected readiness notification"))
    assert caught.value is error
    assert youtube.events == [("sleep", 1), ("read", "playlist1")]
    assert youtube.items == []


def test_lost_create_response_recovery_waits_for_readiness(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.fail_create_after_commit = True
    with pytest.raises(UncertainWrite):
        workflow.publish()
    youtube.item_errors = [api_error(404, "playlistNotFound")]
    assert workflow.publish().phase == "done"  # callback remains optional
    assert len(youtube.created) == 1
    assert youtube.events == [("sleep", 1), ("read", "playlist1")] * 2 + [("read", "playlist1")]


@pytest.mark.parametrize("status,revision", [("added", 0), ("uncertain", 0), ("selected", 1)])
def test_insertion_history_bypasses_readiness_retries(setup, status, revision):
    workflow, youtube, store = setup
    draft = prepare(workflow)
    draft.phase = "writing"
    draft.playlist_id = "playlist1"
    draft.tracks[0].status = status
    draft.tracks[0].insert_revision = revision
    store.save(draft)
    error = api_error(404, "playlistNotFound")
    youtube.item_errors = [error]
    with pytest.raises(HttpError) as caught:
        workflow.publish(on_playlist_pending=lambda: pytest.fail("Unexpected readiness notification"))
    assert caught.value is error
    assert youtube.events == [("read", "playlist1")]
    assert youtube.items == []


class StaticSearch:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    def songs(self, query):
        self.calls.append(query)
        return list(self.candidates)


def test_studio_plus_live_auto_selects_and_persists(setup):
    workflow, youtube, store = setup
    workflow.search = StaticSearch([
        Candidate("live", "Numb", ["Linkin Park"], "Live"),
        Candidate("studio", "Numb", ["Linkin Park"], "Meteora"),
    ])
    workflow.start("Linkin Park — Numb")
    draft = workflow.resolve_pending()
    assert draft.phase == "title"
    assert store.load().tracks[0].selected == "studio"
    assert [c.video_id for c in draft.tracks[0].candidates] == ["studio", "live"]
    assert draft.tracks[0].search_revision == 1
    assert youtube.items == []


def test_legacy_pending_snapshot_is_selected_from_stored_candidates_without_search(setup):
    workflow, _, store = setup
    draft, _ = workflow.start("Linkin Park — Numb")
    track = draft.tracks[0]
    track.candidates = [
        Candidate("live", "Numb (Live)", ["Linkin Park"]),
        Candidate("studio", "Numb", ["Linkin Park"]),
    ]
    track.search_query = "Linkin Park Numb"
    track.search_revision = 7
    store.save(draft)
    workflow.search = StaticSearch([])
    snapshot = workflow.resolve_pending()
    assert workflow.search.calls == []
    assert len(workflow.selector.calls) == 1
    assert snapshot.tracks[0].selected == "live" and snapshot.tracks[0].search_revision == 7


def test_fresh_search_ranks_late_relevant_results_before_selection(setup):
    workflow, _, store = setup
    covers = [Candidate(f"cover{i}", "Numb", ["Cover Artist"]) for i in range(8)]
    workflow.search = StaticSearch(covers + [
        Candidate("one", "Numb", ["Linkin Park"], duration="3:00"),
        Candidate("two", "Numb", ["Linkin Park"], duration="4:00"),
    ])
    workflow.start("Linkin Park — Numb")
    draft = workflow.resolve_pending()
    track = draft.tracks[0]
    assert len(track.candidates) == 10
    assert [c.video_id for c in store.load().tracks[0].candidates[:2]] == ["one", "two"]
    assert track.selected == "one" and draft.phase == "title"


def test_search_failure_leaves_saved_snapshot_and_revision_intact(setup):
    workflow, _, store = setup
    workflow.start("Linkin Park — Numb")
    expected = store.load().to_dict()

    class FailingSearch:
        def songs(self, query):
            raise TimeoutError("offline")

    workflow.search = FailingSearch()
    with pytest.raises(TimeoutError, match="offline"):
        workflow.resolve_pending()
    assert store.load().to_dict() == expected



def test_creation_preparation_failure_returns_to_editable_confirm(setup):
    from ym_playlist_maker.youtube import BeforeCreateError
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.create_error = BeforeCreateError("synthetic private detail")
    with pytest.raises(WorkflowError, match="creation request was not sent"):
        workflow.publish()
    assert workflow.active().phase == "confirm"
    assert youtube.created == [] and youtube.items == []
    assert workflow.edit(1, "Numb live").phase == "resolving"
    workflow.cancel()
    assert workflow.active() is None


@pytest.mark.parametrize("failure", ["read", "missing", "reordered", "extra"])
def test_final_verification_preserves_added_and_resume_does_not_insert_again(setup, failure):
    workflow, youtube, store = setup
    prepare(workflow)
    original_read = youtube.item_ids
    def read(playlist):
        if youtube.items == ["oasis", "numb1"]:
            if failure == "read":
                raise TimeoutError("synthetic remote secret")
            if failure == "missing":
                return ["oasis"]
            if failure == "reordered":
                return ["numb1", "oasis"]
            return ["oasis", "numb1", "outside"]
        return original_read(playlist)
    youtube.item_ids = read
    with pytest.raises((TimeoutError, UncertainWrite)):
        workflow.publish()
    saved = store.load()
    assert saved.phase == "writing"
    assert [t.status for t in saved.tracks] == ["added", "added", "skipped"]
    assert youtube.items == ["oasis", "numb1"]
    youtube.item_ids = original_read
    assert workflow.publish().phase == "done"
    assert youtube.items == ["oasis", "numb1"]


def test_final_verification_excludes_definitively_rejected_recording(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.add_error = api_error(404, "videoNotFound")
    result = workflow.publish()
    assert result.phase == "done"
    assert youtube.items == ["numb1"]
    assert result.tracks[0].status == "skipped"


def test_final_verification_accepts_empty_playlist_after_all_rejections(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    def reject(playlist, video):
        raise api_error(404, "videoNotFound")
    youtube.add = reject
    result = workflow.publish()
    assert result.phase == "done"
    assert all(track.status == "skipped" for track in result.tracks)
    assert youtube.items == []
