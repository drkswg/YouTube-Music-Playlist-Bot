"""Official YouTube write boundary, using a local user's OAuth credentials."""

import errno
import json
import tempfile
import webbrowser
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


class MissingCredentials(RuntimeError):
    pass


class OAuthAuthorizationError(RuntimeError):
    """An actionable authorization failure, without sensitive provider details."""


class OAuthCallbackTimeout(OAuthAuthorizationError):
    """The browser did not return to the local OAuth callback server."""


class BeforeInsertError(RuntimeError):
    """The playlist item request was not dispatched to YouTube."""


def http_error_reason(error: HttpError) -> str | None:
    """Read the structured YouTube error reason without relying on message text."""
    for detail in getattr(error, "error_details", []):
        if isinstance(detail, dict) and detail.get("reason"):
            return str(detail["reason"])
    try:
        content = json.loads(error.content)
        for detail in content.get("error", {}).get("errors", []):
            if detail.get("reason"):
                return str(detail["reason"])
    except (TypeError, ValueError, AttributeError):
        pass
    return None


def authorize(client_secrets: Path, token_path: Path, *, bind_address: str = "127.0.0.1",
              port: int = 0, open_browser: bool = True) -> None:
    if not 0 <= port <= 65535:
        raise ValueError("OAuth callback port must be between 0 and 65535")
    if bind_address != "127.0.0.1" and port == 0:
        raise ValueError("Set a fixed nonzero --port when using a different --bind-address")
    if not client_secrets.is_file():
        raise FileNotFoundError(f"OAuth client JSON not found: {client_secrets}")
    try:
        client_config = json.loads(client_secrets.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError("OAuth client file must contain valid UTF-8 JSON") from exc
    if not isinstance(client_config, dict) or not isinstance(client_config.get("installed"), dict):
        raise ValueError("An OAuth client JSON of type Desktop app from Google Cloud Console is required")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=[SCOPE])
    except (ValueError, KeyError) as exc:
        raise ValueError("OAuth Desktop client JSON is invalid; download it again from Google Cloud Console") from exc
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1", bind_addr=bind_address, port=port,
            open_browser=open_browser, timeout_seconds=300,
            access_type="offline", prompt="consent",
        )
    except WSGITimeoutError as exc:
        raise OAuthCallbackTimeout(
            "No browser callback was received at the local address 127.0.0.1 within 5 minutes. "
            "Check the redirect after granting consent in Google and run auth again."
        ) from exc
    except OAuth2Error as exc:
        raise OAuthAuthorizationError(
            "Google authorization was declined or rejected. Check the OAuth Desktop client, "
            "grant the requested access, and run auth again."
        ) from exc
    except RequestException as exc:
        raise OAuthAuthorizationError(
            "Could not complete Google authorization. Check your network connection and run auth again."
        ) from exc
    except webbrowser.Error as exc:
        raise OAuthAuthorizationError(
            "Could not open a browser. Run auth with --no-browser and open the printed link yourself."
        ) from exc
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(exc, "winerror", None) in (10048, 10013):
            raise OAuthAuthorizationError(
                f"Cannot listen for the OAuth callback on {bind_address}:{port}. "
                "The port is busy or access is denied. Choose another --port "
                "(YM_AUTH_PORT for Docker) and run auth again."
            ) from exc
        raise OAuthAuthorizationError("Could not start or complete the OAuth callback. Run auth again.") from exc
    _save_credentials(credentials, token_path)


def _save_credentials(credentials: Credentials, token_path: Path) -> None:
    """Replace a token only after the complete new credentials are on disk."""
    serialized = credentials.to_json()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=token_path.parent,
                                         prefix=f".{token_path.name}.", suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(serialized)
        temporary_path.replace(token_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class YouTubePlaylists:
    def __init__(self, token_path: Path) -> None:
        self.token_path = token_path

    def _service(self):
        if not self.token_path.is_file():
            raise MissingCredentials(
                "Google authorization is required. Follow the README Docker authorization instructions: "
                "docker compose -f compose.auth.yaml run --rm --build --service-ports auth. "
                "For local Python runs, use auth --client-secrets PATH."
            )
        credentials = Credentials.from_authorized_user_file(str(self.token_path), scopes=[SCOPE])
        if not credentials.valid:
            if not credentials.refresh_token:
                raise MissingCredentials(
                    "No refresh token; repeat Google authorization using the README Docker "
                    "or local Python instructions."
                )
            credentials.refresh(Request())
            self.token_path.write_text(credentials.to_json(), encoding="utf-8")
        return build("youtube", "v3", credentials=credentials, cache_discovery=False)

    def check_credentials(self) -> None:
        self._service()

    def create_private(self, title: str, marker: str) -> str:
        response = self._service().playlists().insert(
            part="snippet,status",
            body={"snippet": {"title": title, "description": marker}, "status": {"privacyStatus": "private"}},
        ).execute()
        return response["id"]

    def find_created(self, marker: str) -> list[str]:
        service = self._service()
        found: list[str] = []
        page = None
        while True:
            response = service.playlists().list(
                part="snippet", mine=True, maxResults=50, pageToken=page
            ).execute()
            found.extend(x["id"] for x in response.get("items", []) if x.get("snippet", {}).get("description") == marker)
            page = response.get("nextPageToken")
            if not page:
                return found

    def item_ids(self, playlist_id: str) -> list[str]:
        service = self._service()
        found: list[str] = []
        page = None
        while True:
            response = service.playlistItems().list(
                part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=page
            ).execute()
            found.extend(x["contentDetails"]["videoId"] for x in response.get("items", []))
            page = response.get("nextPageToken")
            if not page:
                return found

    def add(self, playlist_id: str, video_id: str) -> None:
        try:
            request = self._service().playlistItems().insert(
                part="snippet",
                body={"snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }},
            )
        except Exception as exc:
            raise BeforeInsertError("Could not prepare the insertion request; the request was not sent") from exc
        request.execute()
