# Personal YouTube Music Playlist Bot

A Telegram bot that accepts a list in `Artist — Song` format (one entry per line), searches YouTube Music, and creates a **private** playlist in your Google account. It runs on your Windows PC while the long-polling process is active, either locally or through Docker Compose. Search uses the unofficial `ytmusicapi`; playlist creation and track insertion use the official YouTube Data API.

## Setup

1. Install Python 3.12 and create a bot through `@BotFather`. Save the bot token and the owner's **numeric** Telegram user ID. The bot only accepts messages from this ID in a private chat.
2. In [Google Cloud Console](https://console.cloud.google.com/), create a project, enable **YouTube Data API v3**, configure the OAuth consent screen, and create an OAuth client of type **Desktop app**. Download the client JSON to a private location outside the repository. Add your Google account to the test users if the OAuth app is in Testing mode.
3. To run without Docker, open PowerShell in the project directory:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[test]"
   .\.venv\Scripts\python.exe -m ym_playlist_maker auth --client-secrets C:\path\to\desktop-client.json
   $env:TELEGRAM_BOT_TOKEN = "token-from-BotFather"
   $env:TELEGRAM_OWNER_ID = "your-numeric-id"
   .\.venv\Scripts\python.exe -m ym_playlist_maker bot
   ```

   The `auth` command opens your default browser and saves the Google OAuth token to `%LOCALAPPDATA%\ym_playlist_maker\token.json`. The bot also stores `state.sqlite3` there. These files contain sensitive data: restrict directory access to your Windows user and do not copy the token into the repository. To use a different directory, set `YM_DATA_DIR` before running both commands.

4. Send the bot a list, for example:

   ```text
   Oasis — Wonderwall
   Linkin Park — Numb
   Radiohead — Creep
   ```

   If the title and artist have a unique exact match, the bot selects the recording automatically. If multiple versions are available, it shows the title, artist, album, and duration (when available). Before displaying the creation button, the bot sends a list of all selected recordings and versions, split across multiple messages if necessary. `/cancel` deletes the draft before writing begins; `/resume` continues after a restart or error.

## Running with Docker Compose

Install Docker with Compose. Complete OAuth authorization once on the Windows host: it opens a browser and uses a temporary local port. From the project directory in PowerShell:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Enter your actual `TELEGRAM_BOT_TOKEN` and numeric `TELEGRAM_OWNER_ID`, then save the file. Next:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
New-Item -ItemType Directory -Force .\data | Out-Null
$env:YM_DATA_DIR = (Resolve-Path .\data).Path
.\.venv\Scripts\python.exe -m ym_playlist_maker auth --client-secrets C:\path\to\desktop-client.json
docker compose up --build -d
docker compose logs -f bot
```

After authorization, `data/token.json` is available to the container through a bind mount; `state.sqlite3` is saved in the same directory. The `data/` directory and `.env` file are excluded from Git and the image build context. Restrict access to yourself and do not publish the output of `docker compose config`, which exposes the Telegram token. No ports need to be exposed: the bot receives messages through long polling.

If the bot has already run locally, stop the local process and copy `%LOCALAPPDATA%\ym_playlist_maker\token.json` to `data/token.json` instead of running `auth` again. If there is an unfinished draft, also move `state.sqlite3` to `data/`.

For subsequent runs, use `docker compose up -d`; stop the bot with `docker compose down`. If Google authorization needs to be renewed, run the local `auth` command with the same `YM_DATA_DIR`, then run `docker compose restart bot`.

If authorization hangs after you grant consent in Google, check whether the browser opened `http://127.0.0.1:<port>/` with a completion message. The command waits up to five minutes for this local redirect; if it does not arrive, check whether the browser, VPN, proxy, or security software is blocking the address. The `auth` command requires an OAuth client JSON of type **Desktop app**. Do not share the full redirect URL with anyone: it contains a one-time authorization code.

## Verifying the Integration Before Regular Use

Try a list of 2–3 well-known songs, check the selected versions, create a playlist, and open the returned link in **YouTube Music**. Make sure the recordings are visible and that the privacy setting and track order are correct. This combined `ytmusicapi` → official API workflow is not covered by automated tests without your credentials.

If a playlist creation or track insertion request ends without a definitive response, the bot saves its state and reconciles it with YouTube on `/resume`. If the inserted track is found, it continues without adding a duplicate. If the track is absent, the bot displays the **"I checked the playlist; retry insertion"** button. Open the playlist, confirm that the recording is absent, and only then press the button: YouTube sometimes shows changes with a delay, so retrying can still create a duplicate. If the outcome of creating *the playlist itself* is unknown, the bot will not automatically create a second one; check your playlists manually and keep `state.sqlite3` for diagnostics. An authorization or network error **before the insertion request is sent** does not require this button: fix the cause and run `/resume` again. If the quota is exhausted, run `/resume` after it resets.

An OAuth app in **Testing** mode may issue a refresh token that expires after about 7 days; run `auth` again when reauthorization is needed. For ongoing personal use, configure the OAuth app's publishing status according to Google's rules. The standard daily YouTube API quota is limited; playlist creation and each track insertion consume quota separately.

## Local Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The tests do not contact Telegram or Google and do not use real secrets.
