"""Synchronous, serialized draft workflow. The Telegram layer runs this in a worker thread."""

import logging

from collections.abc import Callable
from time import sleep
from uuid import uuid4

from googleapiclient.errors import HttpError

from .models import Draft, Track, SelectionRecord
from .parser import parse_tracks, resolve_candidates, eligible_candidates
from .selection import MODEL, POLICY, SelectionInput, SelectionError, Selector
from .store import DraftStore
from .youtube import BeforeCreateError, BeforeInsertError, http_error_reason


log = logging.getLogger(__name__)


class WorkflowError(RuntimeError):
    pass


class UncertainWrite(WorkflowError):
    pass


class UnconfirmedInsert(UncertainWrite):
    def __init__(self, index: int, line: int, revision: int) -> None:
        self.index = index
        self.revision = revision
        super().__init__(f"Line {line}: insertion is unconfirmed. Check the playlist contents before retrying")


class PlaylistWorkflow:
    def __init__(self, store: DraftStore, search, youtube, selector: Selector | None = None) -> None:
        self.store = store
        self.search = search
        self.youtube = youtube
        self.selector = selector

    def active(self) -> Draft | None:
        return self.store.load()

    def start(self, text: str) -> tuple[Draft | None, list[int]]:
        tracks, invalid = parse_tracks(text)
        if invalid:
            return None, invalid
        if not tracks:
            raise WorkflowError("The list is empty. Format: Artist — Song")
        current = self.active()
        if current and current.phase != "done":
            raise WorkflowError("A draft already exists. Use /resume or /cancel")
        draft = Draft(draft_id=uuid4().hex[:12], tracks=tracks)
        self.store.save(draft)
        return draft, []

    def next_unresolved(self, *, on_selection_pending: Callable[[int, int], None] | None = None) -> tuple[Draft, int | None]:
        draft = self._require()
        if draft.phase != "resolving":
            return draft, None
        for index in range(len(draft.tracks)):
            track = draft.tracks[index]
            if track.status != "pending":
                continue
            if (not track.search_query and not track.candidates and track.search_revision == 0
                and track.selection_record is None):
                self._search_into(draft, track, f"{track.artist} {track.title}", on_selection_pending=on_selection_pending)
                track = draft.tracks[index]
            if track.status != "pending":
                continue
            return draft, index
        if draft.title and not any(t.status == "selected" for t in draft.tracks):
            raise WorkflowError("All songs have been skipped; use /cancel")
        draft.phase = "confirm" if draft.title else "title"
        self.store.save(draft)
        return draft, None

    def search_again(self, index: int, query: str) -> Draft:
        draft = self._require()
        track = self._pending_at(draft, index, allow_waiting=True)
        query = query.strip()
        if not query or len(query) > 200:
            raise WorkflowError("Enter a search query between 1 and 200 characters long")
        self._search_into(draft, track, query, is_custom=True)
        track.await_query = False
        self.store.save(draft)
        return draft

    def request_search_again(self, index: int, revision: int | None = None) -> Draft:
        draft = self._require()
        track = self._pending_at(draft, index, revision)
        track.search_revision += 1
        track.await_query = True
        self.store.save(draft)
        return draft

    def choose(self, index: int, candidate_index: int, revision: int | None = None) -> Draft:
        draft = self._require()
        track = self._pending_at(draft, index, revision)
        if not 0 <= candidate_index < min(5, len(track.candidates)):
            raise WorkflowError("This option is outdated; use /resume")
        track.selected = track.candidates[candidate_index].video_id
        track.status = "selected"
        self.store.save(draft)
        return draft

    def skip(self, index: int, revision: int | None = None) -> Draft:
        draft = self._require()
        track = self._pending_at(draft, index, revision)
        track.status = "skipped"
        self.store.save(draft)
        return draft

    def edit(self, source_line: int) -> Draft:
        draft = self._require()
        if draft.phase != "confirm" or draft.playlist_id or any(
            t.insert_revision or t.status in ("added", "uncertain") for t in draft.tracks
        ):
            raise WorkflowError("Editing is available only before playlist creation")
        track = next((t for t in draft.tracks if t.source_line == source_line), None)
        if track is None:
            raise WorkflowError("No song on that source line")
        track.selected = None
        track.status = "pending"
        track.selection_record = None
        track.await_query = False
        # A reopened snapshot is manual, including a legacy empty search query.
        track.search_query = track.search_query or f"{track.artist} {track.title}"
        track.search_revision += 1
        draft.review_revision += 1
        draft.phase = "resolving"
        self.store.save(draft)
        return draft

    def set_title(self, title: str) -> Draft:
        draft = self._require()
        if draft.phase != "title":
            raise WorkflowError("A title is not expected at this stage")
        title = title.strip()
        if not title or len(title) > 150:
            raise WorkflowError("The title must be between 1 and 150 characters long")
        if not any(t.status == "selected" for t in draft.tracks):
            raise WorkflowError("All songs have been skipped; use /cancel")
        draft.title = title
        draft.phase = "confirm"
        self.store.save(draft)
        return draft

    def cancel(self) -> None:
        draft = self.active()
        if draft and draft.phase in ("creating", "writing"):
            raise WorkflowError("Creation has already started. Use /resume")
        self.store.clear()

    def retry_uncertain(self, index: int, revision: int) -> Draft:
        """Owner-approved retry after checking the remote playlist manually."""
        draft = self._require()
        if draft.phase != "writing" or not draft.playlist_id or not 0 <= index < len(draft.tracks):
            raise WorkflowError("Retrying the insertion is unavailable")
        track = draft.tracks[index]
        if track.status != "uncertain" or not track.selected:
            raise WorkflowError("This recording has already been processed; use /resume")
        if track.insert_revision != revision:
            raise WorkflowError("This button belongs to an old insertion attempt; use /resume")
        remote = self.youtube.item_ids(draft.playlist_id)
        expected: list[str] = []
        for item in draft.tracks:
            if item.status in ("selected", "added", "uncertain") and item.selected and item.selected not in expected:
                expected.append(item.selected)
        if remote != expected[:len(remote)]:
            raise UncertainWrite("The playlist contents were changed outside the bot; writing has stopped")
        if track.selected in remote:
            return self._write_tracks(draft)
        track.status = "selected"
        self.store.save(draft)  # Persist the owner's approval before another write attempt.
        return self._write_tracks(draft)

    def publish(self, *, on_playlist_pending: Callable[[], None] | None = None,
                expected_review_revision: int | None = None) -> Draft:
        draft = self._require()
        if expected_review_revision is not None and (
            draft.phase != "confirm" or draft.review_revision != expected_review_revision
        ):
            raise WorkflowError("This review is outdated; use /resume")
        if draft.phase == "done":
            return draft
        if draft.phase == "confirm":
            if not any(t.status == "selected" and t.selected for t in draft.tracks):
                raise WorkflowError("All songs have been skipped; use /cancel")
            # Fail before entering the uncertain creation phase when credentials are missing.
            self.youtube.check_credentials()
            draft.phase = "creating"
            draft.create_marker = f"Personal playlist bot draft {draft.draft_id}"
            self.store.save(draft)
            try:
                draft.playlist_id = self.youtube.create_private(draft.title, draft.create_marker)
            except BeforeCreateError as exc:
                draft.phase = "confirm"
                self.store.save(draft)
                raise WorkflowError("The creation request was not sent. Fix authorization or network issues and use /resume") from exc
            except HttpError as exc:
                if exc.resp.status in (400, 401, 403, 404, 429):
                    draft.phase = "confirm"
                    self.store.save(draft)
                raise
            except Exception as exc:
                raise UncertainWrite("No response was received for playlist creation. Use /resume to check") from exc
            draft.phase = "writing"
            self.store.save(draft)
        elif draft.phase == "creating":
            matches = self.youtube.find_created(draft.create_marker)
            if len(matches) != 1:
                raise UncertainWrite("The outcome of creation is unknown. Check your playlists manually; automatic retry is blocked")
            draft.playlist_id = matches[0]
            draft.phase = "writing"
            self.store.save(draft)
        if draft.phase != "writing" or not draft.playlist_id:
            raise WorkflowError("The draft is not ready for playlist creation yet")
        return self._write_tracks(draft, on_playlist_pending=on_playlist_pending)

    def _initial_item_ids(self, draft: Draft, on_playlist_pending: Callable[[], None] | None) -> list[str]:
        # Once an insertion has been attempted, preserve immediate reconciliation.
        if any(t.status in ("added", "uncertain") or t.insert_revision > 0 for t in draft.tracks):
            return self.youtube.item_ids(draft.playlist_id)
        for attempt in range(3):
            sleep(1)
            try:
                return self.youtube.item_ids(draft.playlist_id)
            except HttpError as exc:
                if exc.resp.status != 404 or http_error_reason(exc) != "playlistNotFound":
                    raise
                if on_playlist_pending is not None:
                    on_playlist_pending()
                if attempt == 2:
                    raise
        raise AssertionError("Playlist readiness attempts exhausted without a result")

    def _write_tracks(self, draft: Draft, *, on_playlist_pending: Callable[[], None] | None = None) -> Draft:
        target_ids: list[str] = []
        seen: set[str] = set()
        for track in draft.tracks:
            if track.status not in ("selected", "added", "uncertain") or not track.selected:
                continue
            if track.selected in seen:
                track.status = "skipped"
                draft.errors.append(f"Line {track.source_line}: duplicate recording skipped")
                self.store.save(draft)
                continue
            seen.add(track.selected)
            target_ids.append(track.selected)

        remote = self._initial_item_ids(draft, on_playlist_pending)
        if remote != target_ids[:len(remote)]:
            raise UncertainWrite("The playlist contents were changed outside the bot; writing has stopped")
        if any(t.status == "added" and t.selected not in remote for t in draft.tracks):
            raise UncertainWrite("A previously added recording is missing from the playlist; writing has stopped")
        for index, track in enumerate(draft.tracks):
            if track.status == "uncertain" and track.selected not in remote:
                raise UnconfirmedInsert(index, track.source_line, track.insert_revision)
        for track in draft.tracks:
            if track.selected in remote and track.status in ("selected", "uncertain"):
                track.status = "added"
        self.store.save(draft)

        for track in draft.tracks:
            if track.status != "selected" or not track.selected:
                continue
            # A process can die during execute() without running an exception handler.
            # Persist the uncertain attempt before dispatch so /resume reconciles it.
            track.insert_revision += 1
            track.status = "uncertain"
            self.store.save(draft)
            try:
                self.youtube.add(draft.playlist_id, track.selected)
            except BeforeInsertError as exc:
                track.status = "selected"
                self.store.save(draft)
                raise WorkflowError("The insertion request was not sent. Fix authorization or network issues and use /resume") from exc
            except HttpError as exc:
                if exc.resp.status in (400, 404) and http_error_reason(exc) == "videoNotFound":
                    track.status = "skipped"
                    draft.errors.append(f"Line {track.source_line}: YouTube rejected the recording ({exc.resp.status})")
                    self.store.save(draft)
                    continue
                if exc.resp.status >= 500:
                    raise UncertainWrite(f"The outcome of inserting line {track.source_line} is unknown; use /resume") from exc
                track.status = "selected"
                self.store.save(draft)
                raise
            except Exception as exc:
                raise UncertainWrite(f"The outcome of inserting line {track.source_line} is unknown; use /resume") from exc
            track.status = "added"
            self.store.save(draft)
        # Recompute after definitive rejections and duplicate skips.
        expected = [t.selected for t in draft.tracks if t.status == "added" and t.selected]
        if self.youtube.item_ids(draft.playlist_id) != expected:
            raise UncertainWrite("The playlist contents were changed outside the bot; writing has stopped")
        draft.phase = "done"
        self.store.save(draft)
        return draft

    def _search_into(self, draft: Draft, track: Track, query: str, *, is_custom: bool = False,
                     on_selection_pending: Callable[[int, int], None] | None = None) -> None:
        candidates = self.search.songs(query)
        decision = resolve_candidates(track, candidates, is_custom=is_custom)
        track.candidates = list(decision.ordered_candidates)
        track.search_query = query
        track.search_revision += 1
        track.selection_record = None
        if decision.selected_video_id is not None:
            track.selected = decision.selected_video_id
            track.status = "selected"
        self.store.save(draft)  # Persist the exact fresh snapshot before any model call.
        eligible = eligible_candidates(track, track.candidates)
        if is_custom or self.selector is None or track.status != "pending" or not eligible:
            if track.status == "pending":
                if is_custom:
                    gemini_status = "custom_search"
                elif self.selector is None:
                    gemini_status = "not_configured"
                else:
                    gemini_status = "no_eligible_candidates"
                log.info("Recording selection requires manual choice: source_line=%s rules_reason=%s "
                         "gemini_status=%s candidates=%s eligible=%s",
                         track.source_line, decision.reason, gemini_status, len(track.candidates), len(eligible))
            return
        value = SelectionInput.from_track(track, eligible, decision.reason)
        record = SelectionRecord("gemini", MODEL, POLICY, value.snapshot_hash(), track.search_revision)
        track.selection_record = record
        self.store.save(draft)  # An interrupted attempted call remains manual on resume.
        if on_selection_pending is not None:
            try:
                on_selection_pending(track.source_line, len(draft.tracks))
            except Exception:
                pass  # Telegram delivery cannot change the recording decision.
        try:
            result = self.selector.select(value)
            if (result.selected_id is not None and result.selected_id not in eligible) or (
                result.reason not in ("preferred_release", "requested_version", "insufficient_evidence")
            ) or ((result.selected_id is None) != (result.reason == "insufficient_evidence")):
                raise SelectionError("invalid_response")
        except SelectionError as exc:
            result = None
            error_code = exc.code
            error_detail = exc.detail
        except Exception:
            result = None
            error_code = "selector_error"
            error_detail = None
        current = self.active()
        index = draft.tracks.index(track)
        if current is None or current.draft_id != draft.draft_id or current.phase != "resolving":
            raise WorkflowError("The draft changed during selection; use /resume")
        latest = current.tracks[index]
        if (latest.status != "pending" or latest.await_query or latest.search_revision != record.search_revision
            or latest.selection_record != record
            or SelectionInput.from_track(latest, eligible_candidates(latest, latest.candidates), decision.reason).snapshot_hash()
               != record.snapshot_hash):
            raise WorkflowError("The search changed during selection; use /resume")
        if result is None:
            record.status = "failed"
            record.error_code = error_code
            record.error_detail = error_detail
            log.warning("Gemini selection failed: model=%s source_line=%s error_code=%s error_detail=%s",
                        MODEL, latest.source_line, error_code, error_detail or "none")
        else:
            record.status = "selected" if result.selected_id else "abstained"
            record.selected_id = result.selected_id
            record.reason = result.reason
            if result.selected_id:
                latest.selected = result.selected_id
                latest.status = "selected"
            else:
                log.info("Recording selection requires manual choice: source_line=%s rules_reason=%s "
                         "gemini_status=abstained candidates=%s eligible=%s",
                         latest.source_line, decision.reason, len(latest.candidates), len(eligible))
        latest.selection_record = record
        self.store.save(current)
        # Continue the same resolution loop using the reloaded, validated state.
        draft.__dict__.update(current.__dict__)

    def _require(self) -> Draft:
        draft = self.active()
        if not draft:
            raise WorkflowError("No draft. Send a list of songs")
        return draft

    @staticmethod
    def _pending_at(
        draft: Draft, index: int, revision: int | None = None, *, allow_waiting: bool = False
    ) -> Track:
        if draft.phase != "resolving" or not 0 <= index < len(draft.tracks) or draft.tracks[index].status != "pending":
            raise WorkflowError("This selection is outdated; use /resume")
        if revision is not None and draft.tracks[index].search_revision != revision:
            raise WorkflowError("The search results have changed; use /resume")
        if draft.tracks[index].await_query and not allow_waiting:
            raise WorkflowError("Waiting for a new search query")
        return draft.tracks[index]
