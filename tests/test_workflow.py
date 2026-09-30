from pathlib import Path

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from ym_playlist_maker.models import Candidate
from ym_playlist_maker.service import PlaylistWorkflow, UnconfirmedInsert, UncertainWrite, WorkflowError
from ym_playlist_maker.store import DraftStore
from ym_playlist_maker.youtube import BeforeInsertError


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
                Candidate("numb1", "Numb", ["Linkin Park"], "Original"),
                Candidate("numb2", "Numb", ["Linkin Park"], "Live"),
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

    def check_credentials(self):
        pass

    def create_private(self, title, marker):
        if self.create_error:
            error, self.create_error = self.create_error, None
            raise error
        self.created.append(("playlist1", title, marker))
        if self.fail_create_after_commit:
            self.fail_create_after_commit = False
            raise TimeoutError("lost response")
        return "playlist1"

    def find_created(self, marker):
        return [p[0] for p in self.created if p[2] == marker]

    def item_ids(self, _playlist_id):
        self.item_reads += 1
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


@pytest.fixture
def setup(tmp_path: Path):
    store = DraftStore(tmp_path / "state.sqlite3")
    youtube = FakeYouTube()
    workflow = PlaylistWorkflow(store, FakeSearch(), youtube)
    yield workflow, youtube, store
    store.close()


def prepare(workflow):
    draft, invalid = workflow.start("Oasis — Wonderwall\nLinkin Park — Numb\nOasis — Wonderwall")
    assert not invalid
    draft, index = workflow.next_unresolved()
    assert index == 1  # first track was uniquely matched
    workflow.choose(index, 0)
    draft, index = workflow.next_unresolved()
    assert index is None  # third track was auto matched
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


def test_lost_create_response_reconciles_without_second_playlist(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.fail_create_after_commit = True
    with pytest.raises(UncertainWrite):
        workflow.publish()
    assert workflow.active().phase == "creating"
    result = workflow.publish()
    assert result.phase == "done"
    assert len(youtube.created) == 1


def test_lost_add_response_reconciles_without_second_insert(setup):
    workflow, youtube, store = setup
    prepare(workflow)
    youtube.fail_add_after_commit = True
    with pytest.raises(UncertainWrite):
        workflow.publish()
    assert workflow.active().phase == "writing"
    # A new workflow process can resume from the same durable database.
    recovered = PlaylistWorkflow(store, FakeSearch(), youtube)
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


def test_stale_choice_and_cancel_after_creation_rejected(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    with pytest.raises(WorkflowError):
        workflow.choose(1, 0)
    youtube.fail_create_after_commit = True
    with pytest.raises(UncertainWrite):
        workflow.publish()
    with pytest.raises(WorkflowError):
        workflow.cancel()


def test_old_search_buttons_cannot_pick_new_results(setup):
    workflow, _, _ = setup
    workflow.start("Linkin Park — Numb")
    draft, index = workflow.next_unresolved()
    revision = draft.tracks[index].search_revision
    workflow.search_again(index, "Numb live")
    with pytest.raises(WorkflowError):
        workflow.choose(index, 0, revision)


def test_buttons_invalid_while_waiting_for_new_query(setup):
    workflow, _, _ = setup
    workflow.start("Linkin Park — Numb")
    draft, index = workflow.next_unresolved()
    revision = draft.tracks[index].search_revision
    workflow.request_search_again(index, revision)
    with pytest.raises(WorkflowError):
        workflow.choose(index, 0, revision)
    with pytest.raises(WorkflowError):
        workflow.skip(index)
    assert workflow.active().tracks[index].await_query


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


def test_create_429_returns_to_confirm_for_retry(setup):
    workflow, youtube, _ = setup
    prepare(workflow)
    youtube.create_error = api_error(429, "rateLimitExceeded")
    with pytest.raises(HttpError):
        workflow.publish()
    assert workflow.active().phase == "confirm"
    assert workflow.publish().phase == "done"


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

