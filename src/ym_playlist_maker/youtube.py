"""Official YouTube write boundary, using a local user's OAuth credentials."""

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


class MissingCredentials(RuntimeError):
    pass


class OAuthCallbackTimeout(RuntimeError):
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


def authorize(client_secrets: Path, token_path: Path) -> None:
    if not client_secrets.is_file():
        raise FileNotFoundError(f"OAuth client JSON not found: {client_secrets}")
    client_config = json.loads(client_secrets.read_text(encoding="utf-8"))
    if not isinstance(client_config, dict) or not isinstance(client_config.get("installed"), dict):
        raise ValueError("An OAuth client JSON of type Desktop app from Google Cloud Console is required")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=[SCOPE])
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1", port=0, timeout_seconds=300,
            access_type="offline", prompt="consent",
        )
    except WSGITimeoutError as exc:
        raise OAuthCallbackTimeout(
            "No browser callback was received at the local address 127.0.0.1 within 5 minutes. "
            "Check the redirect after granting consent in Google and run auth again."
        ) from exc
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")


class YouTubePlaylists:
    def __init__(self, token_path: Path) -> None:
        self.token_path = token_path

    def _service(self):
        if not self.token_path.is_file():
            raise MissingCredentials("First run: python -m ym_playlist_maker auth --client-secrets PATH")
        credentials = Credentials.from_authorized_user_file(str(self.token_path), scopes=[SCOPE])
        if not credentials.valid:
            if not credentials.refresh_token:
                raise MissingCredentials("No refresh token; run the local auth command again")
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
