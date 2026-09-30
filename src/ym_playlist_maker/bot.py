"""Telegram long-polling UI for the single-owner workflow."""

import asyncio
import logging

from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .config import BotConfig
from .models import Draft, SelectionRecord, selected_candidate
from .parser import resolve_candidates
from .service import PlaylistWorkflow, UnconfirmedInsert, WorkflowError

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


def selection_fallback_message(record: SelectionRecord) -> str:
    """Describe only safe categories, never provider messages or arbitrary codes."""
    if record.status == "attempted":
        return "Automatic selection was interrupted. The saved search will not be sent again. Please choose manually."
    if record.status == "abstained":
        return "Gemini could not choose confidently. Please choose manually."
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
    return (text or "Automatic selection is unavailable.") + " Please choose manually."


class BotUI:
    def __init__(self, config: BotConfig, workflow: PlaylistWorkflow) -> None:
        self.config = config
        self.workflow = workflow
        self.lock = asyncio.Lock()

    async def configure_menu(self, application: Application) -> None:
        try:
            await application.bot.set_my_commands(
                [
                    BotCommand("start", "How to send a song list"),
                    BotCommand("resume", "Continue the saved draft"),
                    BotCommand("cancel", "Cancel the draft before creation"),
                    BotCommand("edit", "Change a recording: /edit <source line>"),
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
            await self.configure_menu(context.application)
            await update.effective_message.reply_text(
                "Send a list with one song per line, for example Oasis — Wonderwall. "
                "Commands: /resume, /cancel, /edit <source line>. Use the Menu button to see all commands."
            )

    async def cancel(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return
        async with self.lock:
            try:
                await asyncio.to_thread(self.workflow.cancel)
                await update.effective_message.reply_text("Draft cancelled.")
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
                    await update.effective_message.reply_text("No draft. Send a list of songs.")
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
                        await update.effective_message.reply_text("First select a version using a button or use /cancel.")
                elif draft and draft.phase in ("confirm", "creating", "writing"):
                    await update.effective_message.reply_text("Use the confirmation button or /resume.")
                else:
                    _, invalid = await asyncio.to_thread(self.workflow.start, update.effective_message.text)
                    if invalid:
                        await update.effective_message.reply_text(
                            "Correct the lines that do not follow the Artist — Song format: " + ", ".join(map(str, invalid))
                        )
                    else:
                        await self._show_state(update)
            except Exception as exc:
                await self._error(update, exc)

    async def callback(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        if not self.allowed(update):
            return
        async with self.lock:
            try:
                parts = (query.data or "").split(":")
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
                    await update.effective_message.reply_text("Send a new search query for this song.")
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
                    await update.effective_message.reply_text("Draft cancelled.")
                    return
                else:
                    raise WorkflowError("Invalid button")
                await self._show_state(update)
            except (ValueError, IndexError):
                await update.effective_message.reply_text("Invalid button; use /resume.")
            except Exception as exc:
                await self._error(update, exc)

    async def _publish(self, update: Update, *, expected_review_revision: int | None = None) -> Draft:
        loop = asyncio.get_running_loop()

        def on_playlist_pending() -> None:
            future = None
            notification = None
            try:
                notification = update.effective_message.reply_text("The playlist is still being created. Please wait.")
                future = asyncio.run_coroutine_threadsafe(notification, loop)
                future.result(timeout=10)
            except Exception:
                if future is not None:
                    future.cancel()
                elif notification is not None:
                    notification.close()
                log.warning("Could not send playlist creation progress", exc_info=True)

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
                    notification = update.effective_message.reply_text(
                        f"Gemini is choosing a recording for source line {line} ({total} songs). Please wait."
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
                await update.effective_message.reply_text(
                    f"{index + 1}/{len(draft.tracks)}: waiting for a new search query for {track.artist} — {track.title}."
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
            rows.append([InlineKeyboardButton("Try another search", callback_data=f"{base}retry:{index}:{track.search_revision}")])
            rows.append([InlineKeyboardButton("Skip", callback_data=f"{base}skip:{index}:{track.search_revision}")])
            text = f"{index + 1}/{len(draft.tracks)}: {track.artist} — {track.title}"
            # Explain the persisted snapshot without changing its order, revision
            # or pending status. Canonical custom queries cannot be distinguished
            # in old JSON, so an automatic decision receives a neutral prompt.
            decision = resolve_candidates(
                track, track.candidates,
                is_custom=track.search_query != f"{track.artist} {track.title}",
            )
            text += "\n" + _RESOLUTION_MESSAGES.get(decision.reason, "Please confirm a recording from the saved search results.")
            if track.selection_record:
                text += "\n" + selection_fallback_message(track.selection_record)
            if track.candidates:
                for i, candidate in enumerate(track.candidates[:5], start=1):
                    album = f" · album: {candidate.album}" if candidate.album else ""
                    duration = f" · {candidate.duration}" if candidate.duration else ""
                    text += f"\n{i}. {candidate.title} — {', '.join(candidate.artists)}{album}{duration}"[:600]
                if len(track.candidates) > 5:
                    text += f"\nShowing 5 of {len(track.candidates)} results; {len(track.candidates) - 5} more are hidden. Try another search to narrow the results."
            await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))
        elif draft.phase == "title":
            await update.effective_message.reply_text("Selection complete. Send a title for the new playlist.")
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
                    detail += f" · album: {candidate.album}"
                if candidate.duration:
                    detail += f" · {candidate.duration}"
                if candidate.video_id in seen:
                    detail += " · duplicate will be skipped"
                seen.add(candidate.video_id)
                lines.append(detail[:700])
            if skipped:
                lines.append("Skipped lines: " + ", ".join(map(str, skipped)))
            await self._send_lines(update, [f"{draft.title}: selected recordings"] + lines)
            base = f"d:{draft.draft_id}:"
            rows = [[
                InlineKeyboardButton("Create private playlist", callback_data=base + f"create:{draft.review_revision}"),
                InlineKeyboardButton("Cancel", callback_data=base + "cancel"),
            ]]
            summary = f"Reviewed {len(selected)} selected entries. Use /edit <source line> to change a recording. Create a private playlist?"
            await update.effective_message.reply_text(summary, reply_markup=InlineKeyboardMarkup(rows))
        elif draft.phase == "done":
            await self._done(update, draft)

    async def _done(self, update: Update, draft) -> None:
        if draft.phase != "done":
            await update.effective_message.reply_text("Writing is not complete yet. Use /resume.")
            return
        added = sum(t.status == "added" for t in draft.tracks)
        message = f"Done: {added} songs. https://music.youtube.com/playlist?list={draft.playlist_id}"
        if draft.errors:
            message += "\n" + "\n".join(draft.errors)
        await update.effective_message.reply_text(message[:4000])

    async def _send_lines(self, update: Update, lines: list[str]) -> None:
        chunk = ""
        for line in lines:
            line = line[:3000]
            if chunk and len(chunk) + len(line) + 1 > 3500:
                await update.effective_message.reply_text(chunk)
                chunk = ""
            chunk += ("\n" if chunk else "") + line
        if chunk:
            await update.effective_message.reply_text(chunk)

    async def _error(self, update: Update, exc: Exception) -> None:
        log.exception("Workflow failed", exc_info=exc)
        detail = str(exc) if isinstance(exc, WorkflowError) else f"{type(exc).__name__}: {exc}"
        if isinstance(exc, UnconfirmedInsert):
            draft = await asyncio.to_thread(self.workflow.active)
            if draft:
                button = InlineKeyboardButton(
                    "I checked the playlist; retry insertion",
                    callback_data=f"d:{draft.draft_id}:retryinsert:{exc.index}:{exc.revision}",
                )
                await update.effective_message.reply_text(
                    "The outcome of the previous insertion is unknown. Open the playlist and make sure "
                    "this recording is absent. Retrying may create a duplicate if YouTube shows the recording later.\n"
                    + detail,
                    reply_markup=InlineKeyboardMarkup([[button]]),
                )
                return
        await update.effective_message.reply_text(("Error: " + detail + "\nUse /resume to continue.")[:4000])


def build_application(config: BotConfig, workflow: PlaylistWorkflow) -> Application:
    ui = BotUI(config, workflow)
    app = Application.builder().token(config.token).post_init(ui.configure_menu).build()
    app.add_handler(CommandHandler("start", ui.start))
    app.add_handler(CommandHandler("cancel", ui.cancel))
    app.add_handler(CommandHandler("resume", ui.resume))
    app.add_handler(CommandHandler("edit", ui.edit))
    app.add_handler(CallbackQueryHandler(ui.callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ui.text))
    return app

