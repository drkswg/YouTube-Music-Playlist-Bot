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

from test_workflow import FakeSearch, FakeSelector, FakeYouTube, prepare


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


@pytest.mark.parametrize("privacy", ["private", "public"])
def test_youtube_write_bodies_use_chosen_privacy_and_video_ids(privacy):
    adapter = YouTubePlaylists(Path("unused"))
    fake = FakeService()
    adapter._service = lambda: fake
    assert adapter.create_playlist("Title", "marker", privacy) == "PL123"
    assert fake.playlist_collection.calls[0] == (
        "insert", {"part": "snippet,status", "body": {
            "snippet": {"title": "Title", "description": "marker"},
            "status": {"privacyStatus": privacy},
        }}
    )
    adapter.add("PL123", "video123")
    assert fake.item_collection.calls[0][1]["body"]["snippet"]["resourceId"] == {
        "kind": "youtube#video", "videoId": "video123"
    }


@pytest.mark.parametrize("privacy", ["unlisted", ""])
def test_youtube_rejects_unsupported_privacy_before_preparing_request(privacy):
    adapter = YouTubePlaylists(Path("unused"))
    adapter._service = lambda: pytest.fail("Invalid privacy reached request preparation")
    with pytest.raises(ValueError, match="^Playlist privacy must be private or public$"):
        adapter.create_playlist("Title", "marker", privacy)


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
    assert "/edit <source line> <query>" in next(command.description for command in registered if command.command == "edit")
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



def test_confirmation_lists_every_selection_in_telegram_safe_chunks():
    tracks = []
    for i in range(70):
        candidate = Candidate(f"id{i}", "Long Song Title " * 5, ["Artist"], "Album", "4:12")
        tracks.append(Track("Artist", "Song", i + 1, candidates=[candidate], selected=candidate.video_id, status="selected"))
    draft = Draft("draft123", tracks, phase="confirm", title="Many songs")
    workflow = SimpleNamespace(resolve_pending=lambda **kwargs: draft)
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)
    message = CollectMessage()
    asyncio.run(ui._show_state(SimpleNamespace(effective_message=message)))
    assert len(message.sent) > 2
    assert all(len(text) <= 3500 for text, _ in message.sent)
    assert "1. Long Song Title" in "\n".join(text for text, _ in message.sent)
    assert "70. Long Song Title" in "\n".join(text for text, _ in message.sent)
    assert message.sent[-1][1]["reply_markup"]


@pytest.fixture
def review_ui(tmp_path, monkeypatch):
    from ym_playlist_maker.service import PlaylistWorkflow
    from ym_playlist_maker.store import DraftStore

    monkeypatch.setattr("ym_playlist_maker.service.sleep", lambda _: None)
    store = DraftStore(tmp_path / "state.sqlite3")
    youtube = FakeYouTube()
    workflow = PlaylistWorkflow(store, FakeSearch(), youtube, FakeSelector())
    prepare(workflow)
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)
    yield ui, youtube, store
    store.close()


def review_update(data=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
        effective_message=CollectMessage(),
        callback_query=SimpleNamespace(data=data, answer=AsyncMock()) if data is not None else None,
    )


@pytest.mark.parametrize("privacy", ["private", "public"])
def test_review_buttons_publish_chosen_privacy_once(review_ui, privacy):
    ui, youtube, store = review_ui
    update = review_update()
    asyncio.run(ui.resume(update, None))
    rows = update.effective_message.sent[-1][1]["reply_markup"].inline_keyboard
    assert [len(row) for row in rows] == [1, 1, 1]
    buttons = {row[0].text: row[0].callback_data for row in rows}
    action = f"Create {privacy} playlist"
    callback = review_update(buttons[action])
    asyncio.run(ui.callback(callback, None))
    saved = store.load()
    assert saved.phase == "done" and saved.privacy == privacy
    assert youtube.created == [("playlist1", saved.title, saved.create_marker, privacy)]
    assert youtube.items == ["oasis", "numb1"]
    assert "Done: 2 songs. https://music.youtube.com/playlist?list=playlist1" in callback.effective_message.sent[-1][0]
    opposite = "public" if privacy == "private" else "private"
    old = review_update(buttons[f"Create {opposite} playlist"])
    calls = (youtube.created.copy(), youtube.items.copy(), youtube.item_reads)
    asyncio.run(ui.callback(old, None))
    assert "This review is outdated" in old.effective_message.sent[-1][0]
    assert store.load().to_dict() == saved.to_dict()
    assert (youtube.created, youtube.items, youtube.item_reads) == calls


@pytest.mark.parametrize("template,error", [
    ("d:{draft_id}:create", "This review is outdated"),
    ("d:{draft_id}:create:0", "This review is outdated"),
    ("d:{draft_id}:create:garbage", "This review is outdated"),
    ("d:{draft_id}:create:0:unlisted", "Invalid button"),
    ("d:{draft_id}:create:0:", "Invalid button"),
    ("d:{draft_id}:create::public", "Invalid button"),
    ("d:{draft_id}:create:invalid:private", "Invalid button"),
    ("d:{draft_id}:create:0:public:extra", "Invalid button"),
    ("d:{draft_id}", "Invalid button"),
    ("d:wrong-draft:create:0:public", "This button belongs to an old draft"),
])
def test_invalid_create_callbacks_never_mutate_draft(review_ui, template, error):
    ui, youtube, store = review_ui
    saved = store.load().to_dict()
    update = review_update(template.format(draft_id=saved["draft_id"]))
    asyncio.run(ui.callback(update, None))
    assert error in update.effective_message.sent[-1][0]
    assert store.load().to_dict() == saved
    assert youtube.created == [] and youtube.items == [] and youtube.item_reads == 0


@pytest.mark.parametrize("privacy", ["private", "public"])
def test_create_callbacks_are_stale_after_edit(review_ui, privacy):
    ui, youtube, store = review_ui
    review = review_update()
    asyncio.run(ui.resume(review, None))
    rows = review.effective_message.sent[-1][1]["reply_markup"].inline_keyboard
    callback_data = rows[privacy == "public"][0].callback_data
    edit = review_update()
    asyncio.run(ui.edit(edit, SimpleNamespace(args=["2", "Numb", "live"])))
    assert store.load().phase == "confirm"
    saved = store.load().to_dict()
    update = review_update(callback_data)
    asyncio.run(ui.callback(update, None))
    assert "This review is outdated" in update.effective_message.sent[-1][0]
    assert store.load().to_dict() == saved
    assert youtube.created == [] and youtube.items == [] and youtube.item_reads == 0


def test_unconfirmed_insert_shows_explicit_retry_button():
    draft = Draft("draft123", [Track("A", "B", 1, status="uncertain", selected="id")], phase="writing", playlist_id="PL123")
    workflow = SimpleNamespace(active=lambda: draft)
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)
    message = CollectMessage()
    asyncio.run(ui._error(SimpleNamespace(effective_message=message), UnconfirmedInsert(0, 1, 2)))
    assert "make sure" in message.sent[0][0]
    button = message.sent[0][1]["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data == "d:draft123:retryinsert:0:2"


@pytest.mark.parametrize("entry", ["private", "public", "resume"])
@pytest.mark.parametrize("failed", [False, True])
def test_publish_ui_delivers_progress_on_loop_before_result(entry, failed):
    events = []
    draft = Draft("draft123", [Track("A", "B", 1, status="selected", selected="id")],
                  phase="writing" if entry == "resume" else "confirm", playlist_id="PL123")

    async def run():
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()

        class Message:
            async def reply_text(self, text, **kwargs):
                assert asyncio.get_running_loop() is loop
                assert threading.get_ident() == loop_thread
                await asyncio.sleep(0)
                events.append(text)

        def publish(*, on_playlist_pending=None, privacy=None, expected_review_revision=None):
            assert privacy == (None if entry == "resume" else entry)
            assert expected_review_revision == (None if entry == "resume" else draft.review_revision)
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
            effective_message=Message(), callback_query=SimpleNamespace(data=f"d:draft123:create:0:{entry}", answer=answer),
        )
        if entry != "resume":
            await ui.callback(update, None)
        else:
            await ui.resume(update, None)

    asyncio.run(run())
    expected = []
    for attempt in range(3 if failed else 2):
        expected.extend([f"read {attempt + 1}", "The playlist is still being created. Please wait."])
    assert events[:-1] == expected
    if failed:
        assert events[-1] == "Error: An external service or internal operation failed. Check the bot logs for the error category.\nUse /resume to continue."
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


@pytest.mark.parametrize("boundary", ["service", "construction", "dispatch"])
def test_creation_boundary_distinguishes_prepare_from_dispatch(boundary):
    from ym_playlist_maker.youtube import BeforeCreateError
    adapter = YouTubePlaylists(Path("unused"))
    fake = FakeService()
    calls = []
    def fail():
        calls.append(boundary)
        raise TimeoutError("synthetic private detail")
    if boundary == "service":
        adapter._service = fail
    else:
        adapter._service = lambda: fake
        if boundary == "construction":
            fake.playlist_collection.insert = lambda **kwargs: fail()
        else:
            fake.playlist_collection.execute = fail
    expected = TimeoutError if boundary == "dispatch" else BeforeCreateError
    with pytest.raises(expected):
        adapter.create_playlist("Title", "marker", "private")
    assert calls == [boundary]


@pytest.mark.parametrize("exception", [
    RuntimeError("SYNTHETIC_SECRET"),
    UnconfirmedInsert(0, 1, 2),
])
def test_workflow_errors_never_print_chained_provider_secrets(caplog, exception):
    exception.__cause__ = ValueError("SYNTHETIC_SECRET")
    draft = Draft("draft123", [])
    ui = BotUI(BotConfig("SYNTHETIC_SECRET", 123, Path("unused")), SimpleNamespace(active=lambda: draft))
    message = CollectMessage()
    asyncio.run(ui._error(SimpleNamespace(effective_message=message), exception))
    assert "SYNTHETIC_SECRET" not in caplog.text
    assert "SYNTHETIC_SECRET" not in str(message.sent)
    assert "SYNTHETIC_SECRET" not in repr(ui.config)
    assert type(exception).__name__ in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_unknown_workflow_message_is_not_trusted(caplog):
    from ym_playlist_maker.service import WorkflowError
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), None)
    message = CollectMessage()
    asyncio.run(ui._error(SimpleNamespace(effective_message=message), WorkflowError("SYNTHETIC_SECRET")))
    assert "SYNTHETIC_SECRET" not in caplog.text + str(message.sent)


def test_global_error_handler_registered_and_contains_notification_failure(monkeypatch, caplog):
    from telegram import Chat, Message, User, Update
    from datetime import datetime, timezone
    app = build_application(BotConfig("123:dummy", 123, Path("unused")), None)
    assert len(app.error_handlers) == 1
    handler = next(iter(app.error_handlers))
    update = Update(1, message=Message(1, datetime.now(timezone.utc), Chat(123, "private"),
                                       from_user=User(123, "Owner", False), text="SYNTHETIC_SECRET"))
    send = AsyncMock(side_effect=BadRequest("SYNTHETIC_SECRET"))
    monkeypatch.setattr("telegram.Message.reply_text", send)
    context = SimpleNamespace(error=RuntimeError("SYNTHETIC_SECRET"))
    asyncio.run(handler(update, context))
    send.assert_awaited_once()
    assert "Could not send error notification" in caplog.text
    assert "SYNTHETIC_SECRET" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("text", ["A" * 10001, "😀" * 5000, "first\n" + "B" * 4000 + "\nlast"],
                         ids=["long-line", "astral-unicode", "multiline"])
def test_shared_sender_preserves_text_and_keyboard_on_last_fragment(text):
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), None)
    message = CollectMessage()
    keyboard = object()
    asyncio.run(ui._send_text(SimpleNamespace(effective_message=message), text, reply_markup=keyboard))
    assert "".join(part for part, _ in message.sent) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 3500 for part, _ in message.sent)
    assert all(not kwargs for _, kwargs in message.sent[:-1])
    assert message.sent[-1][1] == {"reply_markup": keyboard}


@pytest.mark.parametrize("user_id,draft_id", [(123, "current"), (456, "current"), (123, "stale")])
def test_failed_callback_ack_does_not_bypass_access_or_draft_checks(user_id, draft_id, caplog):
    cancelled = []
    draft = Draft("current", [Track("Artist", "Song", 1)])
    workflow = SimpleNamespace(active=lambda: draft, cancel=lambda: cancelled.append(True))
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), workflow)
    message = CollectMessage()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=user_id),
                             effective_chat=SimpleNamespace(type=ChatType.PRIVATE), effective_message=message,
                             callback_query=SimpleNamespace(data=f"d:{draft_id}:cancel",
                                 answer=AsyncMock(side_effect=BadRequest("SYNTHETIC_SECRET"))))
    asyncio.run(ui.callback(update, None))
    assert cancelled == ([True] if user_id == 123 and draft_id == "current" else [])
    assert "SYNTHETIC_SECRET" not in caplog.text + str(message.sent)


def _diagnostic_http_error(reason, status=403):
    import json
    from googleapiclient.errors import HttpError
    from httplib2 import Response
    return HttpError(Response({"status": str(status)}), json.dumps({
        "error": {"message": "SYNTHETIC_SECRET", "errors": [{"reason": reason, "message": "SYNTHETIC_SECRET"}]}
    }).encode(), uri="https://provider.invalid/SYNTHETIC_SECRET")


def test_quota_and_permission_errors_have_distinct_actionable_safe_diagnostics(caplog):
    draft = Draft("draft123", [], phase="confirm")
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), SimpleNamespace(active=lambda: draft))
    message = CollectMessage()
    for reason in ("quotaExceeded", "insufficientPermissions"):
        exc = _diagnostic_http_error(reason)
        exc.__cause__ = RuntimeError("SYNTHETIC_SECRET")
        asyncio.run(ui._error(SimpleNamespace(effective_message=message), exc))
    assert message.sent[0][0] != message.sent[1][0]
    assert "after the limit resets" in message.sent[0][0]
    assert "Repeat authorization" in message.sent[1][0]
    assert "error_code=youtube_quota" in caplog.text
    assert "error_code=youtube_permissions" in caplog.text
    assert "http_status=403" in caplog.text and "phase=confirm" in caplog.text
    assert "SYNTHETIC_SECRET" not in caplog.text + str(message.sent)
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("kind,category", [
    ("credentials", "google_auth"), ("refresh", "google_auth"),
    ("timeout", "network_timeout"), ("connection", "network_error"),
    ("quota", "youtube_quota"), ("permissions", "youtube_permissions"),
])
@pytest.mark.parametrize("language", ["en", "ru", "es", "de", "fr", "pt"])
def test_actionable_external_errors_are_localized_and_do_not_reveal_provider_text(kind, category, language, caplog):
    from google.auth.exceptions import RefreshError
    from ym_playlist_maker.bot import _EXTERNAL_ERROR_MESSAGES
    from ym_playlist_maker.i18n import translate
    errors = {
        "credentials": MissingCredentials("SYNTHETIC_SECRET"),
        "refresh": RefreshError("SYNTHETIC_SECRET"),
        "timeout": TimeoutError("SYNTHETIC_SECRET"),
        "connection": ConnectionError("SYNTHETIC_SECRET"),
        "quota": _diagnostic_http_error("quotaExceeded"),
        "permissions": _diagnostic_http_error("insufficientPermissions"),
    }
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), None)
    ui.language_code = language
    message = CollectMessage()
    exc = errors[kind]
    exc.__context__ = RuntimeError("SYNTHETIC_SECRET")
    asyncio.run(ui._error(SimpleNamespace(effective_message=message), exc))
    expected = translate(language, _EXTERNAL_ERROR_MESSAGES[category])
    assert expected in message.sent[0][0] and "/resume" in expected
    if language != "en":
        assert expected != _EXTERNAL_ERROR_MESSAGES[category]
    assert f"error_code={category}" in caplog.text
    assert "SYNTHETIC_SECRET" not in caplog.text + str(message.sent)
    assert all(record.exc_info is None for record in caplog.records)


def test_unknown_http_reason_body_and_draft_metadata_remain_hidden(caplog):
    draft = Draft("draft123", [], phase="SYNTHETIC_SECRET")
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), SimpleNamespace(active=lambda: draft))
    message = CollectMessage()
    exc = _diagnostic_http_error("SYNTHETIC_SECRET")
    exc.__cause__ = RuntimeError("SYNTHETIC_SECRET")
    asyncio.run(ui._error(SimpleNamespace(effective_message=message), exc))
    assert "error_code=external_error" in caplog.text
    assert "phase=None" in caplog.text
    assert "error category" in message.sent[0][0]
    assert "SYNTHETIC_SECRET" not in caplog.text + str(message.sent)


def test_uncertain_insertion_diagnostics_include_known_phase_and_source_line(caplog):
    from ym_playlist_maker.service import UncertainWrite
    draft = Draft("draft123", [Track("Artist", "Song", 17)], phase="writing")
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), SimpleNamespace(active=lambda: draft))
    message = CollectMessage()
    exc = UncertainWrite("The outcome of inserting line 17 is unknown; use /resume")
    exc.__cause__ = TimeoutError("SYNTHETIC_SECRET")
    asyncio.run(ui._error(SimpleNamespace(effective_message=message), exc))
    assert "error_code=uncertain_write phase=writing source_line=17" in caplog.text
    assert "SYNTHETIC_SECRET" not in caplog.text + str(message.sent)


def test_broken_storage_does_not_prevent_actionable_error_notification(caplog):
    def active():
        raise RuntimeError("SYNTHETIC_SECRET")
    ui = BotUI(BotConfig("dummy", 123, Path("unused")), SimpleNamespace(active=active))
    message = CollectMessage()
    asyncio.run(ui._error(SimpleNamespace(effective_message=message), MissingCredentials("SYNTHETIC_SECRET")))
    assert "Repeat authorization" in message.sent[0][0]
    assert "SYNTHETIC_SECRET" not in caplog.text + str(message.sent)
