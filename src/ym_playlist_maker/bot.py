"""Telegram long-polling UI for the single-owner workflow."""

import asyncio
import logging
import re
from typing import Literal

from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Update
from telegram.constants import ChatType
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .config import BotConfig
from .i18n import LANGUAGES, translate, translate_saved_message, safe_workflow_message
from .models import Draft, selected_candidate
from .service import ALL_SKIPPED, PlaylistWorkflow, UnconfirmedInsert, UncertainWrite, WorkflowError
from .youtube import MissingCredentials, http_error_reason

log = logging.getLogger(__name__)


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
                    BotCommand("edit", self._t("Search again: /edit <source line> <query>")),
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
                    "Commands: /resume, /cancel, /edit <source line> <query>, /language. Use the Menu button to see all commands."
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
                if len(context.args) < 2 or not context.args[0].isdecimal():
                    raise WorkflowError("Use /edit <source line number> <search query> from the review")
                await asyncio.to_thread(self.workflow.edit, int(context.args[0]), " ".join(context.args[1:]))
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
                    await self._send_text(update, self._t("Songs are still being matched. Use /resume to continue or /cancel."))
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
                if action == "create":
                    if len(parts) in (3, 4):
                        raise WorkflowError("This review is outdated; use /resume")
                    if len(parts) != 5 or parts[4] not in ("private", "public"):
                        raise WorkflowError("Invalid button")
                    expected = int(parts[3])
                    if draft.phase != "confirm" or draft.review_revision != expected:
                        raise WorkflowError("This review is outdated; use /resume")
                    draft = await self._publish(update, expected_review_revision=expected, privacy=parts[4])
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
            except (ValueError, IndexError):
                await self._send_text(update, self._t("Invalid button; use /resume."))
            except Exception as exc:
                await self._error(update, exc)

    async def _publish(self, update: Update, *, expected_review_revision: int | None = None,
                       privacy: Literal["private", "public"] | None = None) -> Draft:
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
        if privacy is not None:
            kwargs["privacy"] = privacy
        return await asyncio.to_thread(self.workflow.publish, **kwargs)

    async def _show_state(self, update: Update) -> None:
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

        draft = await asyncio.to_thread(self.workflow.resolve_pending, on_selection_pending=progress)
        selected = [t for t in draft.tracks if t.status == "selected"]
        if draft.phase in ("title", "confirm") and not selected:
            # Nothing to create; keep /edit available and explain every skipped line.
            cancel = InlineKeyboardButton(self._t("Cancel"), callback_data=f"d:{draft.draft_id}:cancel")
            await self._send_text(update, "\n".join(self._skip_report(draft) + [self._t(ALL_SKIPPED)]),
                                  reply_markup=InlineKeyboardMarkup([[cancel]]))
        elif draft.phase == "title":
            await self._send_lines(update, self._skip_report(draft)
                                   + [self._t("Selection complete. Send a title for the new playlist.")])
        elif draft.phase == "confirm":
            skipped = [t.source_line for t in draft.tracks if t.status == "skipped" and t.skip_reason is None]
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
            lines.extend(self._skip_report(draft))
            await self._send_lines(update, [self._t("{title}: selected recordings", title=draft.title)] + lines)
            base = f"d:{draft.draft_id}:"
            rows = [
                [InlineKeyboardButton(self._t("Create private playlist"), callback_data=base + f"create:{draft.review_revision}:private")],
                [InlineKeyboardButton(self._t("Create public playlist"), callback_data=base + f"create:{draft.review_revision}:public")],
                [InlineKeyboardButton(self._t("Cancel"), callback_data=base + "cancel")],
            ]
            summary = self._t("Reviewed {count} selected entries. Use /edit <source line> <search query> to search again for a song. Create a private or public playlist?", count=len(selected))
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
        report = self._skip_report(draft)
        if report:
            message += "\n" + "\n".join(report)
        await self._send_text(update, message)

    def _skip_report(self, draft: Draft) -> list[str]:
        messages = {
            "no_results": "Line {line} ({artist} — {title}): no search results, skipped",
            "no_match": "Line {line} ({artist} — {title}): no matching recording found, skipped",
            "selection_failed": "Line {line} ({artist} — {title}): automatic selection failed, skipped",
        }
        return [self._t(messages[t.skip_reason], line=t.source_line, artist=t.artist, title=t.title)
                for t in draft.tracks if t.status == "skipped" and t.skip_reason in messages]

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

