import json
from types import SimpleNamespace

import pytest
from google_auth_oauthlib.flow import WSGITimeoutError

from ym_playlist_maker import youtube


def test_auth_requires_desktop_client(tmp_path):
    client_file = tmp_path / "client.json"
    client_file.write_text(json.dumps({"web": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="Desktop app"):
        youtube.authorize(client_file, tmp_path / "token.json")


def test_auth_uses_ipv4_loopback_and_saves_token(monkeypatch, tmp_path):
    client_file = tmp_path / "client.json"
    client_file.write_text(json.dumps({"installed": {}}), encoding="utf-8")
    call = {}

    class FakeFlow:
        def run_local_server(self, **kwargs):
            call.update(kwargs)
            return SimpleNamespace(to_json=lambda: '{"token": "test"}')

    monkeypatch.setattr(youtube.InstalledAppFlow, "from_client_secrets_file", lambda *args, **kwargs: FakeFlow())
    token_file = tmp_path / "data" / "token.json"

    youtube.authorize(client_file, token_file)

    assert call["host"] == "127.0.0.1"
    assert call["timeout_seconds"] > 0
    assert token_file.read_text(encoding="utf-8") == '{"token": "test"}'


def test_auth_explains_missing_browser_callback(monkeypatch, tmp_path):
    client_file = tmp_path / "client.json"
    client_file.write_text(json.dumps({"installed": {}}), encoding="utf-8")

    class FakeFlow:
        def run_local_server(self, **kwargs):
            raise WSGITimeoutError("timed out")

    monkeypatch.setattr(youtube.InstalledAppFlow, "from_client_secrets_file", lambda *args, **kwargs: FakeFlow())
    token_file = tmp_path / "token.json"

    with pytest.raises(youtube.OAuthCallbackTimeout, match="127.0.0.1"):
        youtube.authorize(client_file, token_file)
    assert not token_file.exists()
