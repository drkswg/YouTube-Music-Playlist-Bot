"""OAuth and bot entry points for local and container execution."""

import argparse
import logging
from pathlib import Path

from .config import bot_config, data_dir
from .youtube import OAuthAuthorizationError, YouTubePlaylists, authorize


def _auth_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer between 0 and 65535") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be an integer between 0 and 65535")
    return port


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal YouTube Music playlist bot")
    commands = parser.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("auth", help="Connect a Google account locally or in a container")
    auth.add_argument("--client-secrets", required=True, type=Path, help="OAuth Desktop client JSON")
    auth.add_argument("--bind-address", default="127.0.0.1", help="Callback listening address; use 0.0.0.0 in Docker")
    auth.add_argument("--port", type=_auth_port, default=0, help="Callback port; 0 chooses a free local port")
    auth.add_argument("--no-browser", action="store_true", help="Open the printed authorization link yourself")
    commands.add_parser("bot", help="Start Telegram long polling")
    args = parser.parse_args()
    directory = data_dir()
    if args.command == "auth":
        try:
            authorize(args.client_secrets, directory / "token.json", bind_address=args.bind_address,
                      port=args.port, open_browser=not args.no_browser)
        except (OAuthAuthorizationError, OSError, ValueError) as exc:
            parser.exit(1, f"{exc}\n")
        except KeyboardInterrupt:
            parser.exit(130, "Google authorization cancelled. Run auth again when ready.\n")
        print(f"Google account connected. Token saved: {directory / 'token.json'}")
        return
    from .bot import build_application
    from .search import MusicSearch
    from .selection import GeminiSelector
    from .service import PlaylistWorkflow
    from .store import DraftStore
    from telegram import Update

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = bot_config()
    store = DraftStore(config.directory / "state.sqlite3")
    workflow = PlaylistWorkflow(store, MusicSearch(), YouTubePlaylists(config.directory / "token.json"),
                                GeminiSelector(config.gemini_api_key) if config.gemini_api_key else None)
    build_application(config, workflow).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
