"""Language settings must survive drafts and preserve workflow safety checks."""

import asyncio
import json
import sqlite3
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import BotCommandScopeChat
from telegram.constants import ChatType
from telegram.error import BadRequest

from ym_playlist_maker.bot import BotUI, selection_fallback_message
from ym_playlist_maker.config import BotConfig
from ym_playlist_maker.i18n import LANGUAGES, _CATALOG, translate, translate_saved_message
from ym_playlist_maker.models import Candidate, Draft, SelectionRecord, Track
from ym_playlist_maker.service import PlaylistWorkflow, UnconfirmedInsert, WorkflowError
from ym_playlist_maker.store import DraftStore


class Message:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))


def make_update(*, user_id=123, chat_type=ChatType.PRIVATE, data=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id), effective_chat=SimpleNamespace(type=chat_type),
        effective_message=Message(),
        callback_query=SimpleNamespace(data=data, answer=AsyncMock()) if data is not None else None,
    )


def make_context(args=()):
    return SimpleNamespace(
        args=list(args), application=SimpleNamespace(bot=SimpleNamespace(
            set_my_commands=AsyncMock(), set_chat_menu_button=AsyncMock(),
        )),
    )


def make_ui(store):
    return BotUI(BotConfig("dummy", 123, Path("unused")), PlaylistWorkflow(store, search=None, youtube=None))


def test_language_picker_uses_native_names_and_current_choice_without_a_draft(tmp_path):
    store = DraftStore(tmp_path / "state.sqlite3")
    ui = make_ui(store)
    update = make_update()
    context = make_context()
    asyncio.run(ui.language(update, context))
    text, options = update.effective_message.sent[0]
    assert "Current language: English" in text
    buttons = [button for row in options["reply_markup"].inline_keyboard for button in row]
    assert [button.text for button in buttons] == ["✓ English", "Русский", "Español", "Deutsch", "Français", "Português"]
    assert [button.callback_data for button in buttons] == [f"lang:{code}" for code in LANGUAGES]
    assert store.load() is None
    assert store.get_language() is None
    context.application.bot.set_my_commands.assert_not_awaited()
    store.close()


@pytest.mark.parametrize("code", LANGUAGES)
@pytest.mark.parametrize("entry", ["command", "button"])
def test_selection_saves_language_updates_menu_and_survives_cancellation_restart(tmp_path, code, entry):
    path = tmp_path / "state.sqlite3"
    store = DraftStore(path)
    store.set_language("ru" if code == "en" else "en")
    store.save(Draft("draft123", [Track("Artist", "Song", 1)]))
    before = store.load().to_dict()
    ui = make_ui(store)
    context = make_context([code.upper()] if entry == "command" else [])
    update = make_update(data=f"lang:{code}" if entry == "button" else None)
    asyncio.run(ui.language(update, context) if entry == "command" else ui.callback(update, context))

    assert store.get_language() == code
    assert store.load().to_dict() == before
    if entry == "button":
        update.callback_query.answer.assert_awaited_once()
    commands = context.application.bot.set_my_commands.await_args
    assert commands.kwargs == {"scope": BotCommandScopeChat(123)}
    assert [item.command for item in commands.args[0]] == ["start", "resume", "cancel", "edit", "language"]
    assert all(1 <= len(item.description) <= 256 for item in commands.args[0])
    assert commands.args[0][-1].description == translate(code, "Choose the interface language")
    assert LANGUAGES[code] in update.effective_message.sent[0][0]

    asyncio.run(ui.cancel(make_update(), None))
    assert store.load() is None
    assert store.get_language() == code
    # A completed playlist and subsequent draft also preserve the setting.
    store.save(Draft("finished", [], phase="done", playlist_id="PL123"))
    ui.workflow.start("Oasis — Wonderwall")
    assert store.get_language() == code
    store.close()

    reopened = DraftStore(path)
    restarted = make_ui(reopened)
    assert restarted.language_code == code
    new_context = make_context()
    asyncio.run(restarted.configure_menu(new_context.application))
    assert new_context.application.bot.set_my_commands.await_args.args[0][-1].description == commands.args[0][-1].description
    reopened.close()


@pytest.mark.parametrize("user_id,chat_type", [(456, ChatType.PRIVATE), (123, ChatType.GROUP)])
@pytest.mark.parametrize("entry", ["command", "button"])
def test_language_selection_is_owner_private_chat_only(tmp_path, user_id, chat_type, entry):
    store = DraftStore(tmp_path / "state.sqlite3")
    ui = make_ui(store)
    update = make_update(user_id=user_id, chat_type=chat_type, data="lang:ru" if entry == "button" else None)
    context = make_context(["ru"])
    asyncio.run(ui.language(update, context) if entry == "command" else ui.callback(update, context))
    assert store.get_language() is None
    assert ui.language_code == "en"
    assert not update.effective_message.sent
    context.application.bot.set_my_commands.assert_not_awaited()
    store.close()


@pytest.mark.parametrize("args,data", [
    (["unknown"], None), (["ru", "en"], None), ([], "lang:unknown"),
    ([], "lang:ru:extra"), ([], "lang:"), ([], "lang"),
])
def test_invalid_language_does_not_change_preference_or_draft(tmp_path, args, data):
    store = DraftStore(tmp_path / "state.sqlite3")
    store.set_language("ru")
    store.save(Draft("draft123", [Track("Artist", "Song", 1)], phase="writing", playlist_id="PL123"))
    before = store.load().to_dict()
    ui = make_ui(store)
    update = make_update(data=data)
    context = make_context(args)
    asyncio.run(ui.language(update, context) if data is None else ui.callback(update, context))
    assert "Этот язык не поддерживается" in update.effective_message.sent[0][0]
    assert store.get_language() == "ru"
    assert store.load().to_dict() == before
    context.application.bot.set_my_commands.assert_not_awaited()
    store.close()


def test_legacy_database_gains_preferences_without_changing_draft(tmp_path):
    path = tmp_path / "state.sqlite3"
    draft = Draft("legacy", [Track("Artist", "Song", 1)])
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE draft (slot INTEGER PRIMARY KEY CHECK(slot = 1), data TEXT NOT NULL)")
    db.execute("INSERT INTO draft VALUES(1, ?)", (json.dumps(draft.to_dict()),))
    db.commit()
    db.close()
    store = DraftStore(path)
    assert store.load().to_dict() == draft.to_dict()
    assert make_ui(store).language_code == "en"
    store.set_language("unsupported")
    assert make_ui(store).language_code == "en"
    store.set_language("fr")
    assert make_ui(store).language_code == "fr"
    assert store.load().to_dict() == draft.to_dict()
    store.close()


def test_russian_start_review_and_stale_button_keep_draft_unchanged(tmp_path):
    store = DraftStore(tmp_path / "state.sqlite3")
    candidate = Candidate("id", "Original Song", ["Original Artist"], "Original Album", "3:42")
    draft = Draft("draft123", [
        Track("Artist", "Song", 2, candidates=[candidate], selected="id", status="selected"),
        Track("Artist", "Song", 5, candidates=[candidate], selected="id", status="selected"),
        Track("Artist", "Skipped", 7, status="skipped"),
    ], phase="confirm", title="Original Playlist", review_revision=3)
    store.save(draft)
    before = store.load().to_dict()
    ui = make_ui(store)
    asyncio.run(ui.language(make_update(), make_context(["ru"])))
    update = make_update()
    asyncio.run(ui.start(update, make_context()))
    assert "Отправьте список" in update.effective_message.sent[0][0]
    assert "/language" in update.effective_message.sent[0][0]
    update = make_update()
    asyncio.run(ui.resume(update, None))
    review, options = update.effective_message.sent[-1]
    assert "Создать приватный плейлист?" in review
    assert options["reply_markup"].inline_keyboard[0][0].text == "Создать приватный плейлист"
    assert options["reply_markup"].inline_keyboard[0][0].callback_data == "d:draft123:create:3"
    details = update.effective_message.sent[0][0]
    assert "Original Playlist: выбранные записи" in details
    assert "Original Song — Original Artist · альбом: Original Album · 3:42" in details
    assert "повтор будет пропущен" in details
    assert "Пропущенные строки: 7" in details
    stale = make_update(data="d:draft123:create:2")
    asyncio.run(ui.callback(stale, None))
    assert "Эта проверка устарела" in stale.effective_message.sent[0][0]
    assert store.load().to_dict() == before
    store.close()


def test_existing_recording_button_still_works_after_language_change(tmp_path):
    store = DraftStore(tmp_path / "state.sqlite3")
    draft = Draft("draft123", [Track("Artist", "Song", 1, candidates=[
        Candidate("first", "Song (Live)", ["Artist"]), Candidate("second", "Song", ["Artist"]),
    ], search_query="Artist Song", search_revision=4)])
    store.save(draft)
    ui = make_ui(store)
    asyncio.run(ui.language(make_update(), make_context(["ru"])))
    update = make_update(data="d:draft123:pick:0:4:1")
    asyncio.run(ui.callback(update, None))
    saved = store.load()
    assert saved.tracks[0].selected == "second"
    assert saved.tracks[0].status == "selected"
    assert saved.phase == "title"
    assert update.effective_message.sent[0][0] == "Выбор завершён. Отправьте название нового плейлиста."
    store.close()


def test_menu_failure_keeps_saved_language_and_localized_confirmation(tmp_path, caplog):
    store = DraftStore(tmp_path / "state.sqlite3")
    ui = make_ui(store)
    context = make_context(["ru"])
    context.application.bot.set_my_commands.side_effect = BadRequest("private diagnostic")
    update = make_update()
    asyncio.run(ui.language(update, context))
    assert store.get_language() == "ru"
    assert "Язык сохранён: Русский" in update.effective_message.sent[0][0]
    assert "private diagnostic" not in caplog.text
    store.close()


def test_failed_preference_write_keeps_previous_language(tmp_path, monkeypatch):
    store = DraftStore(tmp_path / "state.sqlite3")
    ui = make_ui(store)

    def fail(_code):
        raise OSError("write failed")

    monkeypatch.setattr(store, "set_language", fail)
    context = make_context(["ru"])
    update = make_update()
    asyncio.run(ui.language(update, context))
    assert ui.language_code == "en"
    assert store.get_language() is None
    assert "Error:" in update.effective_message.sent[0][0]
    context.application.bot.set_my_commands.assert_not_awaited()
    store.close()


@pytest.mark.parametrize("code", ["ru", "es", "de", "fr", "pt"])
def test_localized_search_and_uncertain_insertion_preserve_safety_buttons(tmp_path, code):
    store = DraftStore(tmp_path / "state.sqlite3")
    store.set_language(code)
    track = Track("Artist", "Song", 3, candidates=[Candidate("id", "Song (Live)", ["Artist"])],
                  search_query="Artist Song", search_revision=9)
    draft = Draft("draft123", [track])
    store.save(draft)
    ui = make_ui(store)
    update = make_update()
    asyncio.run(ui.resume(update, None))
    text, options = update.effective_message.sent[0]
    assert "Artist — Song" in text
    assert "The requested version was not found" not in text
    assert options["reply_markup"].inline_keyboard[0][0].callback_data == "d:draft123:pick:0:9:0"
    assert options["reply_markup"].inline_keyboard[-1][0].text != "Skip"

    draft.phase = "writing"
    draft.playlist_id = "PL123"
    track.status = "uncertain"
    track.selected = "id"
    store.save(draft)
    before = store.load().to_dict()
    update = make_update()
    asyncio.run(ui._error(update, UnconfirmedInsert(0, 3, 2)))
    text, options = update.effective_message.sent[0]
    assert "The outcome of the previous insertion" not in text
    assert "Line 3: insertion is unconfirmed" not in text
    assert "YouTube" in text
    assert options["reply_markup"].inline_keyboard[0][0].callback_data == "d:draft123:retryinsert:0:2"
    assert store.load().to_dict() == before
    store.close()


def test_russian_validation_gemini_fallback_and_saved_error_are_localized(tmp_path):
    store = DraftStore(tmp_path / "state.sqlite3")
    store.set_language("ru")
    ui = make_ui(store)
    update = make_update()
    asyncio.run(ui._error(update, WorkflowError("The title must be between 1 and 150 characters long")))
    assert "Название должно содержать от 1 до 150 символов" in update.effective_message.sent[0][0]
    record = SelectionRecord(provider="gemini", model="test", policy="test", snapshot_hash="snapshot",
                             search_revision=1, status="failed", error_code="http_429")
    assert "бесплатной квоты" in selection_fallback_message(record, "ru")
    assert "Выберите вручную" in selection_fallback_message(record, "ru")
    draft = Draft("done", [], phase="done", playlist_id="PL123",
                  errors=["Line 3: YouTube rejected the recording (404)"])
    update = make_update()
    asyncio.run(ui._done(update, draft))
    text = update.effective_message.sent[0][0]
    assert "Готово." in text
    assert "https://music.youtube.com/playlist?list=PL123" in text
    assert "Строка 3: YouTube отклонил запись (404)" in text
    assert draft.errors == ["Line 3: YouTube rejected the recording (404)"]
    store.close()


def test_all_translations_preserve_format_fields_and_command_names():
    formatter = Formatter()
    for source, variants in _CATALOG.items():
        fields = {field for _, field, _, _ in formatter.parse(source) if field is not None}
        assert len(variants) == len(LANGUAGES) - 1, source
        for text in variants:
            assert text
            assert {field for _, field, _, _ in formatter.parse(text) if field is not None} == fields, source
            for command in ("/start", "/resume", "/cancel", "/edit", "/language"):
                if command in source:
                    assert command in text, (source, text)
            # Values containing braces must be inserted literally, without a second formatting pass.
            rendered = text.format(**{field: "value{literal}" for field in fields})
            assert not fields or "value{literal}" in rendered
    assert translate("unknown", "Draft cancelled.") == "Draft cancelled."
    assert translate_saved_message("ru", "External diagnostic {raw}") == "External diagnostic {raw}"
