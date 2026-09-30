"""Local configuration and credential paths."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def data_dir() -> Path:
    root = os.environ.get("YM_DATA_DIR")
    if root:
        return Path(root).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    return Path(local).joinpath("ym_playlist_maker") if local else Path.home() / ".ym_playlist_maker"


@dataclass(frozen=True)
class BotConfig:
    token: str
    owner_id: int
    directory: Path
    gemini_api_key: str = field(default="", repr=False)


def bot_config() -> BotConfig:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    owner = os.environ.get("TELEGRAM_OWNER_ID", "").strip()
    if not token:
        raise ValueError("Set TELEGRAM_BOT_TOKEN")
    if not owner.isdecimal() or int(owner) <= 0:
        raise ValueError("TELEGRAM_OWNER_ID must be a positive numeric ID")
    return BotConfig(token=token, owner_id=int(owner), directory=data_dir(),
                     gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip())

