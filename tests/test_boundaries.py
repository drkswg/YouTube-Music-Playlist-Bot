import asyncio
from pathlib import Path
from types import SimpleNamespace

from telegram.constants import ChatType

from ym_playlist_maker.bot import BotUI
from ym_playlist_maker.config import BotConfig
from ym_playlist_maker.models import Candidate, Draft, Track
from ym_playlist_maker.service import UnconfirmedInsert
from ym_playlist_maker.youtube import BeforeInsertError, MissingCredentials, YouTubePlaylists


class FakeCollection:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        return self

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return self

    def execute(self):
        return self.response


class FakeService:
    def __init__(self):
        self.playlist_collection = FakeCollection({"id": "PL123"})
        self.item_collection = FakeCollection({"id": "item123"})

    def playlists(self):
        return self.playlist_collection

    def playlistItems(self):
        return self.item_collection


def test_youtube_write_bodies_are_private_and_use_video_ids():
    adapter = YouTubePlaylists(Path("unused"))
    fake = FakeService()
    adapter._service = lambda: fake
    assert adapter.create_private("Title", "marker") == "PL123"
    assert fake.playlist_collection.calls[0] == (
        "insert", {"part": "snippet,status", "body": {
            "snippet": {"title": "Title", "description": "marker"},
            "status": {"privacyStatus": "private"},
        }}
    )
    adapter.add("PL123", "video123")
    assert fake.item_collection.calls[0][1]["body"]["snippet"]["resourceId"] == {
        "kind": "youtube#video", "videoId": "video123"
    }


def test_youtube_preparation_failure_is_marked_not_dispatched():
    adapter = YouTubePlaylists(Path("unused"))
    adapter._service = lambda: (_ for _ in ()).throw(MissingCredentials("expired"))
    try:
        adapter.add("PL123", "video123")
    except BeforeInsertError as exc:
        assert isinstance(exc.__cause__, MissingCredentials)
    else:
        raise AssertionError("Expected a pre-dispatch error")


def test_ui_rejects_other_user_and_group_chat():
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow=None)
    own_private = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
    )
    other = SimpleNamespace(
        effective_user=SimpleNamespace(id=456),
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
    )
    group = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(type=ChatType.GROUP),
    )
    assert ui.allowed(own_private)
    assert not ui.allowed(other)
    assert not ui.allowed(group)


class CollectMessage:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))


def test_candidate_message_shows_album_and_duration():
    track = Track("Artist", "Song", 1, candidates=[Candidate("id", "Song", ["Artist"], "Album X", "3:42")])
    track.search_query = "Artist Song"
    draft = Draft("draft123", [track])
    workflow = SimpleNamespace(next_unresolved=lambda: (draft, 0))
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)
    message = CollectMessage()
    asyncio.run(ui._show_state(SimpleNamespace(effective_message=message)))
    assert "Album X" in message.sent[0][0]
    assert "3:42" in message.sent[0][0]


def test_confirmation_lists_every_selection_in_telegram_safe_chunks():
    tracks = []
    for i in range(70):
        candidate = Candidate(f"id{i}", "Long Song Title " * 5, ["Artist"], "Album", "4:12")
        tracks.append(Track("Artist", "Song", i + 1, candidates=[candidate], selected=candidate.video_id, status="selected"))
    draft = Draft("draft123", tracks, phase="confirm", title="Many songs")
    workflow = SimpleNamespace(next_unresolved=lambda: (draft, None))
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)
    message = CollectMessage()
    asyncio.run(ui._show_state(SimpleNamespace(effective_message=message)))
    assert len(message.sent) > 2
    assert all(len(text) <= 3500 for text, _ in message.sent)
    assert "1. Long Song Title" in "\n".join(text for text, _ in message.sent)
    assert "70. Long Song Title" in "\n".join(text for text, _ in message.sent)
    assert message.sent[-1][1]["reply_markup"]


def test_unconfirmed_insert_shows_explicit_retry_button():
    draft = Draft("draft123", [Track("A", "B", 1, status="uncertain", selected="id")], phase="writing", playlist_id="PL123")
    workflow = SimpleNamespace(active=lambda: draft)
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)
    message = CollectMessage()
    asyncio.run(ui._error(SimpleNamespace(effective_message=message), UnconfirmedInsert(0, 1, 2)))
    assert "make sure" in message.sent[0][0]
    button = message.sent[0][1]["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data == "d:draft123:retryinsert:0:2"
