import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import BotCommandScopeChat, MenuButtonCommands
from telegram.constants import ChatType
from telegram.error import BadRequest
from telegram.ext import CommandHandler

from ym_playlist_maker.bot import BotUI, build_application
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


def test_application_startup_registers_owner_menu_for_all_command_handlers(monkeypatch):
    commands = AsyncMock()
    menu = AsyncMock()
    monkeypatch.setattr("telegram.ext.ExtBot.set_my_commands", commands)
    monkeypatch.setattr("telegram.ext.ExtBot.set_chat_menu_button", menu)
    app = build_application(BotConfig("123:dummy", 123, Path("unused")), workflow=None)

    assert app.post_init is not None
    asyncio.run(app.post_init(app))

    commands.assert_awaited_once()
    registered = commands.await_args.args[0]
    assert [command.command for command in registered] == ["start", "resume", "cancel", "edit", "language"]
    handled = {command for handler in app.handlers[0] if isinstance(handler, CommandHandler)
               for command in handler.commands}
    assert {command.command for command in registered} == handled
    assert all(command.description for command in registered)
    assert "/edit <source line>" in next(command.description for command in registered if command.command == "edit")
    assert commands.await_args.kwargs == {"scope": BotCommandScopeChat(123)}
    menu.assert_awaited_once_with(chat_id=123, menu_button=MenuButtonCommands())


@pytest.mark.parametrize("user_id,chat_type,allowed", [
    (123, ChatType.PRIVATE, True),
    (456, ChatType.PRIVATE, False),
    (123, ChatType.GROUP, False),
])
def test_start_configures_menu_only_for_owner_private_chat(user_id, chat_type, allowed):
    bot = SimpleNamespace(set_my_commands=AsyncMock(), set_chat_menu_button=AsyncMock())
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow=None)
    message = CollectMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(type=chat_type), effective_message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot=bot))
    asyncio.run(ui.start(update, context))

    if allowed:
        bot.set_my_commands.assert_awaited_once()
        assert bot.set_my_commands.await_args.kwargs["scope"] == BotCommandScopeChat(123)
        bot.set_chat_menu_button.assert_awaited_once_with(chat_id=123, menu_button=MenuButtonCommands())
        assert "Menu button" in message.sent[0][0]
    else:
        bot.set_my_commands.assert_not_awaited()
        bot.set_chat_menu_button.assert_not_awaited()
        assert not message.sent


@pytest.mark.parametrize("failed_method", ["set_my_commands", "set_chat_menu_button"])
def test_menu_failure_keeps_startup_and_start_help_available(monkeypatch, caplog, failed_method):
    commands = AsyncMock()
    menu = AsyncMock()
    methods = {"set_my_commands": commands, "set_chat_menu_button": menu}
    methods[failed_method].side_effect = BadRequest("chat not found: sensitive provider detail")
    for name, method in methods.items():
        monkeypatch.setattr(f"telegram.ext.ExtBot.{name}", method)
    app = build_application(BotConfig("123:dummy", 123, Path("unused")), workflow=None)
    message = CollectMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE), effective_message=message,
    )
    start_handler = next(handler for handler in app.handlers[0]
                         if isinstance(handler, CommandHandler) and "start" in handler.commands)

    async def run():
        await app.post_init(app)
        # Once /start creates the chat or Telegram recovers, setup is retried.
        methods[failed_method].side_effect = None
        await start_handler.callback(update, SimpleNamespace(application=app))

    asyncio.run(run())
    assert commands.await_count == 2
    assert menu.await_count == (1 if failed_method == "set_my_commands" else 2)
    assert "Send a list" in message.sent[0][0]
    assert "Could not configure command menu (BadRequest)" in caplog.text
    assert "sensitive provider detail" not in caplog.text


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
    assert "confirm a recording from the saved search results" in message.sent[0][0]


@pytest.mark.parametrize("candidates,reason", [
    ([Candidate("one", "Song", ["Artist"]), Candidate("two", "Song", ["Artist"])], "durations are missing"),
    ([Candidate("one", "Song (Live)", ["Artist"])], "requested version was not found"),
    ([Candidate("one", "Song (Demo)", ["Artist"])], "labels are unclear"),
    ([Candidate("one", "Song", ["Cover Artist"])], "title and artist do not match"),
    ([], "No matches"),
])
def test_resolution_message_explains_manual_selection(candidates, reason):
    track = Track("Artist", "Song", 1, candidates=candidates, search_query="Artist Song", search_revision=3)
    draft = Draft("draft123", [track])
    before = draft.to_dict()
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), SimpleNamespace(next_unresolved=lambda: (draft, 0)))
    message = CollectMessage()
    asyncio.run(ui._show_state(SimpleNamespace(effective_message=message)))
    assert reason in message.sent[0][0]
    assert draft.to_dict() == before


def test_hidden_results_and_button_indices_use_persisted_snapshot():
    candidates = [Candidate(f"id{i}", "Song", ["Artist"], duration=f"3:{40 + i}") for i in range(10)]
    track = Track("Artist", "Song", 1, candidates=candidates, search_query="Artist Song", search_revision=9)
    draft = Draft("draft123", [track])
    before = draft.to_dict()
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), SimpleNamespace(next_unresolved=lambda: (draft, 0)))
    message = CollectMessage()
    asyncio.run(ui._show_state(SimpleNamespace(effective_message=message)))
    text, options = message.sent[0]
    assert "different durations" in text
    assert "Showing 5 of 10 results; 5 more are hidden" in text
    assert [row[0].callback_data for row in options["reply_markup"].inline_keyboard[:5]] == [
        f"d:draft123:pick:0:9:{i}" for i in range(5)
    ]
    assert draft.to_dict() == before


def test_ui_preserves_legacy_order_when_new_rules_would_choose_studio():
    track = Track("Artist", "Song", 1, candidates=[
        Candidate("live", "Song (Live)", ["Artist"]), Candidate("studio", "Song", ["Artist"]),
    ], search_query="Artist Song", search_revision=4)
    draft = Draft("draft123", [track])
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), SimpleNamespace(next_unresolved=lambda: (draft, 0)))
    message = CollectMessage()
    asyncio.run(ui._show_state(SimpleNamespace(effective_message=message)))
    text, options = message.sent[0]
    assert "confirm a recording from the saved search results" in text
    assert text.index("1. Song (Live)") < text.index("2. Song")
    assert options["reply_markup"].inline_keyboard[0][0].callback_data == "d:draft123:pick:0:4:0"


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


@pytest.mark.parametrize("entry", ["create", "resume"])
@pytest.mark.parametrize("failed", [False, True])
def test_publish_ui_delivers_progress_on_loop_before_result(entry, failed):
    events = []
    draft = Draft("draft123", [Track("A", "B", 1, status="selected", selected="id")],
                  phase="confirm" if entry == "create" else "writing", playlist_id="PL123")

    async def run():
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()

        class Message:
            async def reply_text(self, text, **kwargs):
                assert asyncio.get_running_loop() is loop
                assert threading.get_ident() == loop_thread
                await asyncio.sleep(0)
                events.append(text)

        def publish(*, on_playlist_pending=None):
            assert threading.get_ident() != loop_thread
            for attempt in range(3 if failed else 2):
                events.append(f"read {attempt + 1}")
                on_playlist_pending()
                assert events[-1] == "The playlist is still being created. Please wait."
            if failed:
                raise RuntimeError("playlist unavailable")
            draft.phase = "done"
            draft.tracks[0].status = "added"
            return draft

        workflow = SimpleNamespace(active=lambda: draft, publish=publish)
        ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)

        async def answer():
            pass

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123), effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_message=Message(), callback_query=SimpleNamespace(data="d:draft123:create", answer=answer),
        )
        if entry == "create":
            await ui.callback(update, None)
        else:
            await ui.resume(update, None)

    asyncio.run(run())
    expected = []
    for attempt in range(3 if failed else 2):
        expected.extend([f"read {attempt + 1}", "The playlist is still being created. Please wait."])
    assert events[:-1] == expected
    if failed:
        assert events[-1] == "Error: RuntimeError: playlist unavailable\nUse /resume to continue."
    else:
        assert events[-1] == "Done: 1 songs. https://music.youtube.com/playlist?list=PL123"


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_progress_delivery_failure_does_not_replace_publish_outcome(outcome, caplog):
    draft = Draft("draft123", [], phase="done", playlist_id="PL123")
    original = RuntimeError("YouTube result")

    class Message:
        async def reply_text(self, text):
            raise OSError("Telegram unavailable")

    def publish(*, on_playlist_pending=None):
        on_playlist_pending()
        if outcome == "failure":
            raise original
        return draft

    ui = BotUI(BotConfig("dummy", 123, Path("unused")), SimpleNamespace(publish=publish))
    update = SimpleNamespace(effective_message=Message())
    if outcome == "failure":
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(ui._publish(update))
        assert caught.value is original
    else:
        assert asyncio.run(ui._publish(update)) is draft
    assert "Could not send playlist creation progress" in caplog.text


def test_progress_delivery_timeout_cancels_notification_without_failing_publish(monkeypatch, caplog):
    draft = Draft("draft123", [], phase="done", playlist_id="PL123")
    futures = []

    class TimedOutFuture:
        cancelled = False

        def result(self, timeout):
            assert timeout == 10
            raise TimeoutError("delivery timed out")

        def cancel(self):
            self.cancelled = True

    def schedule(coroutine, loop):
        coroutine.close()
        future = TimedOutFuture()
        futures.append(future)
        return future

    monkeypatch.setattr("ym_playlist_maker.bot.asyncio.run_coroutine_threadsafe", schedule)

    def publish(*, on_playlist_pending=None):
        on_playlist_pending()
        return draft

    ui = BotUI(BotConfig("dummy", 123, Path("unused")), SimpleNamespace(publish=publish))
    assert asyncio.run(ui._publish(SimpleNamespace(effective_message=CollectMessage()))) is draft
    assert futures[0].cancelled
    assert "Could not send playlist creation progress" in caplog.text
