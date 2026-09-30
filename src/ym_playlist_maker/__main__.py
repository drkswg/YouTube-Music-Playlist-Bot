"""Local OAuth and bot entry points."""

import argparse
import logging
from pathlib import Path

from .config import bot_config, data_dir
from .youtube import OAuthCallbackTimeout, YouTubePlaylists, authorize


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal YouTube Music playlist bot")
    commands = parser.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("auth", help="Connect a Google account locally")
    auth.add_argument("--client-secrets", required=True, type=Path, help="OAuth Desktop client JSON")
    commands.add_parser("bot", help="Start Telegram long polling")
    args = parser.parse_args()
    directory = data_dir()
    if args.command == "auth":
        try:
            authorize(args.client_secrets, directory / "token.json")
        except OAuthCallbackTimeout as exc:
            parser.exit(1, f"{exc}\n")
        print(f"Google account connected. Token saved: {directory / 'token.json'}")
        return
    from .bot import build_application
    from .search import MusicSearch
    from .service import PlaylistWorkflow
    from .store import DraftStore
    from telegram import Update

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = bot_config()
    store = DraftStore(config.directory / "state.sqlite3")
    workflow = PlaylistWorkflow(store, MusicSearch(), YouTubePlaylists(config.directory / "token.json"))
    build_application(config, workflow).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
