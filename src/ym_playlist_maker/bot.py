"""Telegram long-polling UI for the single-owner workflow."""

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .config import BotConfig
from .models import Draft, selected_candidate
from .service import PlaylistWorkflow, UnconfirmedInsert, WorkflowError

log = logging.getLogger(__name__)


class BotUI:
    def __init__(self, config: BotConfig, workflow: PlaylistWorkflow) -> None:
        self.config = config
        self.workflow = workflow
        self.lock = asyncio.Lock()

    def allowed(self, update: Update) -> bool:
        return bool(
            update.effective_user
            and update.effective_user.id == self.config.owner_id
            and update.effective_chat
            and update.effective_chat.type == ChatType.PRIVATE
        )

    async def start(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.allowed(update):
            await update.effective_message.reply_text(
                "Send a list with one song per line, for example Oasis — Wonderwall. "
                "Commands: /resume, /cancel."
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
                elif action == "create" and len(parts) == 3:
                    draft = await self._publish(update)
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

    async def _publish(self, update: Update) -> Draft:
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

        return await asyncio.to_thread(self.workflow.publish, on_playlist_pending=on_playlist_pending)

    async def _show_state(self, update: Update) -> None:
        draft, index = await asyncio.to_thread(self.workflow.next_unresolved)
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
            if not track.candidates:
                text += "\nNo matches. Try another query or skip this song."
            else:
                for i, candidate in enumerate(track.candidates[:5], start=1):
                    album = f" · album: {candidate.album}" if candidate.album else ""
                    duration = f" · {candidate.duration}" if candidate.duration else ""
                    text += f"\n{i}. {candidate.title} — {', '.join(candidate.artists)}{album}{duration}"[:600]
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
                InlineKeyboardButton("Create private playlist", callback_data=base + "create"),
                InlineKeyboardButton("Cancel", callback_data=base + "cancel"),
            ]]
            summary = f"Reviewed {len(selected)} selected entries. Create a private playlist?"
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
    app = Application.builder().token(config.token).build()
    app.add_handler(CommandHandler("start", ui.start))
    app.add_handler(CommandHandler("cancel", ui.cancel))
    app.add_handler(CommandHandler("resume", ui.resume))
    app.add_handler(CallbackQueryHandler(ui.callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ui.text))
    return app

