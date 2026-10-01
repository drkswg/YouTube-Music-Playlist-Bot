"""Telegram long-polling UI for the single-owner workflow."""

import asyncio
import logging
import re

from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Update
from telegram.constants import ChatType
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .config import BotConfig
from .i18n import LANGUAGES, translate, translate_saved_message, safe_workflow_message
from .models import Draft, SelectionRecord, selected_candidate
from .parser import resolve_candidates
from .service import PlaylistWorkflow, UnconfirmedInsert, UncertainWrite, WorkflowError
from .youtube import MissingCredentials, http_error_reason

log = logging.getLogger(__name__)


_RESOLUTION_MESSAGES = {
    "no_results": "No matches. Try another query or skip this song.",
    "no_confident_match": "The title and artist do not match confidently. Please choose a recording.",
    "version_unavailable": "The requested version was not found (a studio recording is preferred by default). Please choose a version or try another search.",
    "unsupported_qualifier": "Some version labels are unclear or conflicting. Please check the recordings.",
    "multiple_recordings": "Several recordings match the requested version. Please choose one.",
    "different_credits": "Matching recordings have different artist credits. Please choose one.",
    "duration_unknown": "Several releases match, but their durations are missing or invalid. Please choose a recording.",
    "different_durations": "Matching releases have different durations and may be different recordings. Please choose one.",
    "custom_search": "Please choose a recording from your search results.",
}


def selection_fallback_message(record: SelectionRecord, language: str = "en") -> str:
    """Describe only safe categories, never provider messages or arbitrary codes."""
    if record.status == "attempted":
        return translate(language, "Automatic selection was interrupted. The saved search will not be sent again. Please choose manually.")
    if record.status == "abstained":
        return translate(language, "Gemini could not choose confidently. Please choose manually.")
    details = {
        "api_key_invalid": "Gemini rejected the API key. Check GEMINI_API_KEY.",
        "service_disabled": "Gemini API is disabled for this project. Check the project configuration.",
        "api_key_service_blocked": "The API key does not allow Gemini. Check its API restrictions.",
        "api_key_ip_address_blocked": "The API key blocks this connection. Check its IP restrictions.",
        "timeout": "Gemini did not respond within the connection timeout.",
        "max_tokens": "Gemini's response was truncated before completing a decision.",
        "safety": "Gemini blocked this decision through its safety checks.",
        "blocked": "Gemini blocked this decision through its content checks.",
    }
    categories = {
        "http_400": "Gemini rejected the request. Check the API key and project configuration.",
        "http_401": "Gemini authentication failed. Check GEMINI_API_KEY.",
        "http_403": "Gemini access is denied. Check project access, API restrictions and regional availability.",
        "http_404": "The configured Gemini model or endpoint is unavailable for this project.",
        "http_429": "Gemini's free quota or request limit has been reached. Check AI Studio limits.",
        "transport_error": "Gemini could not be reached due to a network or connection error.",
        "invalid_response": "Gemini returned an invalid or incomplete decision.",
        "request_too_large": "The search metadata exceeds the automatic selection request limit.",
        "response_too_large": "Gemini's response exceeds the automatic selection response limit.",
    }
    text = details.get(record.error_detail) or categories.get(record.error_code)
    return translate(language, text or "Automatic selection is unavailable.") + " " + translate(language, "Please choose manually.")


_EXTERNAL_ERROR_MESSAGES = {
    "youtube_quota": "YouTube quota or request limits have been reached. Check Google Cloud quotas and use /resume after the limit resets.",
    "youtube_permissions": "Google authorization lacks the required YouTube permissions. Repeat authorization using the README instructions, then use /resume.",
    "google_auth": "Google authorization is missing or expired. Repeat authorization using the README instructions, then use /resume.",
    "network_timeout": "An external service timed out. Check your connection and use /resume to reconcile the saved state.",
    "network_error": "An external service could not be reached. Check your connection and use /resume to reconcile the saved state.",
    "external_error": "An external service or internal operation failed. Check the bot logs for the error category.",
}
_YOUTUBE_ERROR_CATEGORIES = {
    "quotaExceeded": "youtube_quota",
    "dailyLimitExceeded": "youtube_quota",
    "rateLimitExceeded": "youtube_quota",
    "userRateLimitExceeded": "youtube_quota",
    "insufficientPermissions": "youtube_permissions",
    "authError": "google_auth",
    "authorizationRequired": "google_auth",
}


def _external_error_category(exc: BaseException | None) -> str:
    """Allowlist provider categories; never return provider text or raw reasons."""
    if isinstance(exc, HttpError):
        category = _YOUTUBE_ERROR_CATEGORIES.get(http_error_reason(exc))
        if category:
            return category
        if exc.resp.status == 401:
            return "google_auth"
        if exc.resp.status == 429:
            return "youtube_quota"
    if isinstance(exc, (MissingCredentials, RefreshError)):
        return "google_auth"
    if isinstance(exc, (TimeoutError, RequestsTimeout, TimedOut)):
        return "network_timeout"
    if (isinstance(exc, (ConnectionError, RequestsConnectionError, TransportError))
            or isinstance(exc, NetworkError) and not isinstance(exc, BadRequest)):
        return "network_error"
    return "external_error"


class BotUI:
    def __init__(self, config: BotConfig, workflow: PlaylistWorkflow) -> None:
        self.config = config
        self.workflow = workflow
        self.lock = asyncio.Lock()
        # Production workflows always have a store; lightweight UI fakes need not.
        self.store = getattr(workflow, "store", None)
        saved_language = self.store.get_language() if self.store is not None else None
        self.language_code = saved_language if saved_language in LANGUAGES else "en"

    def _t(self, text: str, **values) -> str:
        return translate(self.language_code, text, **values)

    async def configure_menu(self, application: Application) -> None:
        try:
            await application.bot.set_my_commands(
                [
                    BotCommand("start", self._t("How to send a song list")),
                    BotCommand("resume", self._t("Continue the saved draft")),
                    BotCommand("cancel", self._t("Cancel the draft before creation")),
                    BotCommand("edit", self._t("Change a recording: /edit <source line>")),
                    BotCommand("language", self._t("Choose the interface language")),
                ],
                scope=BotCommandScopeChat(self.config.owner_id),
            )
            await application.bot.set_chat_menu_button(
                chat_id=self.config.owner_id, menu_button=MenuButtonCommands(),
            )
        except TelegramError as exc:
            # The owner's chat may not exist until their first /start.
            log.warning("Could not configure command menu (%s); use /start to retry", type(exc).__name__)

    def allowed(self, update: Update) -> bool:
        return bool(
            update.effective_user
            and update.effective_user.id == self.config.owner_id
            and update.effective_chat
            and update.effective_chat.type == ChatType.PRIVATE
        )

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.allowed(update):
            async with self.lock:
                await self.configure_menu(context.application)
                await self._send_text(update, self._t(
                    "Send a list with one song per line, for example Oasis — Wonderwall. "
                    "Commands: /resume, /cancel, /edit <source line>, /language. Use the Menu button to see all commands."
                ))

    async def language(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return
        async with self.lock:
            try:
                if context.args:
                    if len(context.args) == 1 and context.args[0].lower() in LANGUAGES:
                        await self._set_language(update, context, context.args[0].lower())
                    else:
                        await self._show_languages(update, invalid=True)
                else:
                    await self._show_languages(update)
            except Exception as exc:
                await self._error(update, exc)

    async def _show_languages(self, update: Update, *, invalid: bool = False) -> None:
        buttons = [
            InlineKeyboardButton(
                ("✓ " if code == self.language_code else "") + name, callback_data=f"lang:{code}",
            )
            for code, name in LANGUAGES.items()
        ]
        text = self._t("Choose a language. Current language: {language}.", language=LANGUAGES[self.language_code])
        if invalid:
            text = self._t("Unsupported language. Use /language or /language en, ru, es, de, fr, pt.") + "\n" + text
        await self._send_text(update,
            text, reply_markup=InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)]),
        )

    async def _set_language(self, update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
        if self.store is not None:
            await asyncio.to_thread(self.store.set_language, code)
        self.language_code = code
        await self.configure_menu(context.application)
        await self._send_text(update, self._t(
            "Language saved: {language}. Use /resume to continue a saved draft or send a song list.",
            language=LANGUAGES[code],
        ))

    async def cancel(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return
        async with self.lock:
            try:
                await asyncio.to_thread(self.workflow.cancel)
                await self._send_text(update, self._t("Draft cancelled."))
            except Exception as exc:
                await self._error(update, exc)

    async def edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return
        async with self.lock:
            try:
                if len(context.args) != 1 or not context.args[0].isdecimal():
                    raise WorkflowError("Use /edit <source line number> from the review")
                await asyncio.to_thread(self.workflow.edit, int(context.args[0]))
                await self._show_state(update)
            except Exception as exc:
                await self._error(update, exc)

    async def resume(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return
        async with self.lock:
            try:
                draft = await asyncio.to_thread(self.workflow.active)
                if not draft:
                    await self._send_text(update, self._t("No draft. Send a list of songs."))
                elif draft.phase in ("creating", "writing"):
                    draft = await self._publish(update)
                    await self._done(update, draft)
                else:
                    await self._show_state(update)
            except Exception as exc:
                await self._error(update, exc)

    async def text(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update) or not update.effective_message.text:
            return
        async with self.lock:
            try:
                draft = await asyncio.to_thread(self.workflow.active)
                if draft and draft.phase == "title":
                    await asyncio.to_thread(self.workflow.set_title, update.effective_message.text)
                    await self._show_state(update)
                elif draft and draft.phase == "resolving":
                    pending = next(((i, t) for i, t in enumerate(draft.tracks) if t.status == "pending"), None)
                    if pending and pending[1].await_query:
                        await asyncio.to_thread(self.workflow.search_again, pending[0], update.effective_message.text)
                        await self._show_state(update)
                    else:
                        await self._send_text(update, self._t("First select a version using a button or use /cancel."))
                elif draft and draft.phase in ("confirm", "creating", "writing"):
                    await self._send_text(update, self._t("Use the confirmation button or /resume."))
                else:
                    _, invalid = await asyncio.to_thread(self.workflow.start, update.effective_message.text)
                    if invalid:
                        await self._send_text(update,
                            self._t("Correct the lines that do not follow the Artist — Song format: {lines}", lines=", ".join(map(str, invalid)))
                        )
                    else:
                        await self._show_state(update)
            except Exception as exc:
                await self._error(update, exc)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        try:
            await query.answer()
        except TelegramError as exc:
            log.warning("Callback acknowledgement failed: error_type=%s", type(exc).__name__)
        if not self.allowed(update):
            return
        async with self.lock:
            try:
                parts = (query.data or "").split(":")
                if parts[0] == "lang":
                    if len(parts) == 2 and parts[1] in LANGUAGES:
                        await self._set_language(update, context, parts[1])
                    else:
                        await self._show_languages(update, invalid=True)
                    return
                if len(parts) < 3 or parts[0] != "d":
                    raise WorkflowError("Invalid button")
                draft = await asyncio.to_thread(self.workflow.active)
                if not draft or draft.draft_id != parts[1]:
                    raise WorkflowError("This button belongs to an old draft")
                action = parts[2]
                if action == "pick" and len(parts) == 6:
                    await asyncio.to_thread(self.workflow.choose, int(parts[3]), int(parts[5]), int(parts[4]))
                elif action == "skip" and len(parts) == 5:
                    await asyncio.to_thread(self.workflow.skip, int(parts[3]), int(parts[4]))
                elif action == "retry" and len(parts) == 5:
                    await asyncio.to_thread(self.workflow.request_search_again, int(parts[3]), int(parts[4]))
                    await self._send_text(update, self._t("Send a new search query for this song."))
                    return
                elif action == "create" and len(parts) in (3, 4):
                    expected = int(parts[3]) if len(parts) == 4 else 0
                    if draft.phase != "confirm" or draft.review_revision != expected:
                        raise WorkflowError("This review is outdated; use /resume")
                    draft = await self._publish(update, expected_review_revision=expected if len(parts) == 4 else None)
                    await self._done(update, draft)
                    return
                elif action == "retryinsert" and len(parts) == 5:
                    draft = await asyncio.to_thread(self.workflow.retry_uncertain, int(parts[3]), int(parts[4]))
                    await self._done(update, draft)
                    return
                elif action == "cancel" and len(parts) == 3:
                    await asyncio.to_thread(self.workflow.cancel)
                    await self._send_text(update, self._t("Draft cancelled."))
                    return
                else:
                    raise WorkflowError("Invalid button")
                await self._show_state(update)
            except (ValueError, IndexError):
                await self._send_text(update, self._t("Invalid button; use /resume."))
            except Exception as exc:
                await self._error(update, exc)

    async def _publish(self, update: Update, *, expected_review_revision: int | None = None) -> Draft:
        loop = asyncio.get_running_loop()

        def on_playlist_pending() -> None:
            future = None
            notification = None
            try:
                notification = self._send_text(update, self._t("The playlist is still being created. Please wait."))
                future = asyncio.run_coroutine_threadsafe(notification, loop)
                future.result(timeout=10)
            except Exception:
                if future is not None:
                    future.cancel()
                elif notification is not None:
                    notification.close()
                log.warning("Could not send playlist creation progress")

        kwargs = {"on_playlist_pending": on_playlist_pending}
        if expected_review_revision is not None:
            kwargs["expected_review_revision"] = expected_review_revision
        return await asyncio.to_thread(self.workflow.publish, **kwargs)

    async def _show_state(self, update: Update) -> None:
        kwargs = {}
        if getattr(self.workflow, "selector", None) is not None:
            loop = asyncio.get_running_loop()

            def progress(line: int, total: int) -> None:
                notification = None
                future = None
                try:
                    notification = self._send_text(update,
                        self._t("Gemini is choosing a recording for source line {line} ({total} songs). Please wait.", line=line, total=total)
                    )
                    future = asyncio.run_coroutine_threadsafe(notification, loop)
                    future.result(timeout=10)
                except Exception:
                    if future is not None:
                        future.cancel()
                    elif notification is not None:
                        notification.close()
                    log.warning("Could not send recording selection progress")

            kwargs["on_selection_pending"] = progress
        draft, index = await asyncio.to_thread(self.workflow.next_unresolved, **kwargs)
        if draft.phase == "resolving" and index is not None:
            track = draft.tracks[index]
            if track.await_query:
                await self._send_text(update,
                    self._t("{index}/{total}: waiting for a new search query for {artist} — {title}.",
                            index=index + 1, total=len(draft.tracks), artist=track.artist, title=track.title)
                )
                return
            base = f"d:{draft.draft_id}:"
            rows = [
                [InlineKeyboardButton(
                    f"{i + 1}. {candidate.title} — {', '.join(candidate.artists)}"[:64],
                    callback_data=f"{base}pick:{index}:{track.search_revision}:{i}",
                )]
                for i, candidate in enumerate(track.candidates[:5])
            ]
            rows.append([InlineKeyboardButton(self._t("Try another search"), callback_data=f"{base}retry:{index}:{track.search_revision}")])
            rows.append([InlineKeyboardButton(self._t("Skip"), callback_data=f"{base}skip:{index}:{track.search_revision}")])
            text = f"{index + 1}/{len(draft.tracks)}: {track.artist} — {track.title}"
            # Explain the persisted snapshot without changing its order, revision
            # or pending status. Canonical custom queries cannot be distinguished
            # in old JSON, so an automatic decision receives a neutral prompt.
            decision = resolve_candidates(
                track, track.candidates,
                is_custom=track.search_query != f"{track.artist} {track.title}",
            )
            text += "\n" + self._t(_RESOLUTION_MESSAGES.get(decision.reason, "Please confirm a recording from the saved search results."))
            if track.selection_record:
                text += "\n" + selection_fallback_message(track.selection_record, self.language_code)
            if track.candidates:
                for i, candidate in enumerate(track.candidates[:5], start=1):
                    album = self._t(" · album: {album}", album=candidate.album) if candidate.album else ""
                    duration = f" · {candidate.duration}" if candidate.duration else ""
                    text += f"\n{i}. {candidate.title} — {', '.join(candidate.artists)}{album}{duration}"
                if len(track.candidates) > 5:
                    text += "\n" + self._t("Showing 5 of {total} results; {hidden} more are hidden. Try another search to narrow the results.", total=len(track.candidates), hidden=len(track.candidates) - 5)
            await self._send_text(update, text, reply_markup=InlineKeyboardMarkup(rows))
        elif draft.phase == "title":
            await self._send_text(update, self._t("Selection complete. Send a title for the new playlist."))
        elif draft.phase == "confirm":
            selected = [t for t in draft.tracks if t.status == "selected"]
            skipped = [t.source_line for t in draft.tracks if t.status == "skipped"]
            lines: list[str] = []
            seen: set[str] = set()
            for track in selected:
                candidate = selected_candidate(track)
                if candidate is None:
                    continue
                detail = f"{track.source_line}. {candidate.title} — {', '.join(candidate.artists)}"
                if candidate.album:
                    detail += self._t(" · album: {album}", album=candidate.album)
                if candidate.duration:
                    detail += f" · {candidate.duration}"
                if candidate.video_id in seen:
                    detail += self._t(" · duplicate will be skipped")
                seen.add(candidate.video_id)
                lines.append(detail)
            if skipped:
                lines.append(self._t("Skipped lines: {lines}", lines=", ".join(map(str, skipped))))
            await self._send_lines(update, [self._t("{title}: selected recordings", title=draft.title)] + lines)
            base = f"d:{draft.draft_id}:"
            rows = [[
                InlineKeyboardButton(self._t("Create private playlist"), callback_data=base + f"create:{draft.review_revision}"),
                InlineKeyboardButton(self._t("Cancel"), callback_data=base + "cancel"),
            ]]
            summary = self._t("Reviewed {count} selected entries. Use /edit <source line> to change a recording. Create a private playlist?", count=len(selected))
            await self._send_text(update, summary, reply_markup=InlineKeyboardMarkup(rows))
        elif draft.phase == "done":
            await self._done(update, draft)

    async def _done(self, update: Update, draft) -> None:
        if draft.phase != "done":
            await self._send_text(update, self._t("Writing is not complete yet. Use /resume."))
            return
        added = sum(t.status == "added" for t in draft.tracks)
        message = self._t("Done: {added} songs. {url}", added=added, url=f"https://music.youtube.com/playlist?list={draft.playlist_id}")
        if draft.errors:
            message += "\n" + "\n".join((translate_saved_message(self.language_code, error) if safe_workflow_message(error)
                                      else self._t("An external service or internal operation failed. Check the bot logs for the error category."))
                                     for error in draft.errors)
        await self._send_text(update, message)

    async def _send_lines(self, update: Update, lines: list[str]) -> None:
        await self._send_text(update, "\n".join(lines))

    async def _send_text(self, update: Update, text: str, *, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        # Bound conservatively in UTF-16 units; astral characters count as two.
        chunks: list[str] = []
        remaining = text
        while remaining:
            units = 0
            end = 0
            for char in remaining:
                width = 2 if ord(char) > 0xFFFF else 1
                if units + width > 3500:
                    break
                units += width
                end += 1
            split = end
            if end < len(remaining):
                newline = remaining.rfind("\n", 0, end)
                if newline > 0:
                    split = newline + 1
            chunks.append(remaining[:split])
            remaining = remaining[split:]
        for index, chunk in enumerate(chunks):
            kwargs = {"reply_markup": reply_markup} if reply_markup is not None and index == len(chunks) - 1 else {}
            await update.effective_message.reply_text(chunk, **kwargs)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Never log provider text, updates, tracebacks or chained exceptions.
        exc = context.error
        log.error("Unhandled bot error: error_type=%s", type(exc).__name__)
        if isinstance(update, Update) and self.allowed(update) and update.effective_message:
            try:
                await self._error(update, exc)
            except Exception as send_exc:
                log.warning("Could not send error notification: error_type=%s", type(send_exc).__name__)

    async def _error(self, update: Update, exc: BaseException | None) -> None:
        status = getattr(getattr(exc, "resp", None), "status", None)
        status = status if isinstance(status, int) and 100 <= status <= 599 else None
        category = _external_error_category(exc)
        draft = None
        try:
            if self.workflow is not None:
                draft = await asyncio.to_thread(self.workflow.active)
        except Exception:
            pass  # Broken storage must not prevent a safe error notification.
        phase = draft.phase if draft and draft.phase in ("resolving", "title", "confirm", "creating", "writing", "done") else None
        source_line = None
        if isinstance(exc, UnconfirmedInsert) and draft and 0 <= exc.index < len(draft.tracks):
            source_line = draft.tracks[exc.index].source_line
        if not isinstance(source_line, int) or source_line <= 0:
            source_line = None
        known_workflow = isinstance(exc, WorkflowError) and safe_workflow_message(str(exc))
        if known_workflow:
            if source_line is None and (match := re.search(r"(?:Line|line) (\d+)", str(exc))):
                source_line = int(match.group(1))
            category = ("unconfirmed_insert" if isinstance(exc, UnconfirmedInsert)
                        else "uncertain_write" if isinstance(exc, UncertainWrite) else "workflow_error")
        log.error("Workflow failed: error_type=%s http_status=%s error_code=%s phase=%s source_line=%s",
                  type(exc).__name__, status, category, phase, source_line)
        detail = (translate_saved_message(self.language_code, str(exc)) if known_workflow
                  else self._t(_EXTERNAL_ERROR_MESSAGES[category]))
        if isinstance(exc, UnconfirmedInsert):
            if draft:
                button = InlineKeyboardButton(
                    self._t("I checked the playlist; retry insertion"),
                    callback_data=f"d:{draft.draft_id}:retryinsert:{exc.index}:{exc.revision}",
                )
                await self._send_text(update,
                    self._t("The outcome of the previous insertion is unknown. Open the playlist and make sure "
                    "this recording is absent. Retrying may create a duplicate if YouTube shows the recording later.\n"
                    ) + detail,
                    reply_markup=InlineKeyboardMarkup([[button]]),
                )
                return
        await self._send_text(update, self._t("Error: {detail}\nUse /resume to continue.", detail=detail))


def build_application(config: BotConfig, workflow: PlaylistWorkflow) -> Application:
    ui = BotUI(config, workflow)
    app = Application.builder().token(config.token).post_init(ui.configure_menu).build()
    app.add_error_handler(ui.error_handler)
    app.add_handler(CommandHandler("start", ui.start))
    app.add_handler(CommandHandler("cancel", ui.cancel))
    app.add_handler(CommandHandler("resume", ui.resume))
    app.add_handler(CommandHandler("edit", ui.edit))
    app.add_handler(CommandHandler("language", ui.language))
    app.add_handler(CallbackQueryHandler(ui.callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ui.text))
    return app

