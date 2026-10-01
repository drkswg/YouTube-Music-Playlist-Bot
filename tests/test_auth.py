import errno
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from google_auth_oauthlib.flow import WSGITimeoutError
from oauthlib.oauth2 import AccessDeniedError
from requests.exceptions import ConnectionError

from ym_playlist_maker import __main__ as cli
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
    assert call["bind_addr"] == "127.0.0.1"
    assert call["port"] == 0
    assert call["open_browser"] is True
    assert call["timeout_seconds"] > 0
    assert call["access_type"] == "offline"
    assert call["prompt"] == "consent"
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


@pytest.fixture
def desktop_client(tmp_path):
    client_file = tmp_path / "client.json"
    client_file.write_text(json.dumps({"installed": {}}), encoding="utf-8")
    return client_file


@pytest.fixture
def fake_flow(monkeypatch):
    call = {}

    def install(error=None):
        class FakeFlow:
            def run_local_server(self, **kwargs):
                call.update(kwargs)
                if error is not None:
                    raise error
                return SimpleNamespace(to_json=lambda: '{"token": "new"}')

        monkeypatch.setattr(youtube.InstalledAppFlow, "from_client_secrets_file",
                            lambda *args, **kwargs: FakeFlow())
        return call

    return install


def test_container_auth_listens_on_all_interfaces_but_redirects_to_loopback(desktop_client, tmp_path, fake_flow):
    call = fake_flow()
    token_file = tmp_path / "data" / "token.json"

    youtube.authorize(desktop_client, token_file, bind_address="0.0.0.0", port=8765, open_browser=False)

    assert call["host"] == "127.0.0.1"
    assert call["bind_addr"] == "0.0.0.0"
    assert call["port"] == 8765
    assert call["open_browser"] is False
    assert call["timeout_seconds"] == 300
    assert token_file.read_text(encoding="utf-8") == '{"token": "new"}'


@pytest.mark.parametrize("error, message", [
    (WSGITimeoutError("timeout"), "within 5 minutes"),
    (AccessDeniedError(description="sensitive-provider-detail"), "declined or rejected"),
    (ConnectionError("sensitive-provider-detail"), "network connection"),
    (OSError(errno.EADDRINUSE, "Address already in use"), "YM_AUTH_PORT"),
])
def test_failed_auth_preserves_existing_token(desktop_client, tmp_path, fake_flow, error, message):
    fake_flow(error)
    token_file = tmp_path / "token.json"
    token_file.write_text("original token", encoding="utf-8")

    with pytest.raises(youtube.OAuthAuthorizationError, match=message) as failure:
        youtube.authorize(desktop_client, token_file, bind_address="0.0.0.0", port=8080, open_browser=False)

    assert "sensitive-provider-detail" not in str(failure.value)
    assert token_file.read_text(encoding="utf-8") == "original token"


@pytest.mark.parametrize("client_contents, message", [
    ('{"web": {}}', "Desktop app"),
    ('not JSON', "valid UTF-8 JSON"),
])
def test_invalid_client_preserves_existing_token(tmp_path, client_contents, message):
    client_file = tmp_path / "client.json"
    client_file.write_text(client_contents, encoding="utf-8")
    token_file = tmp_path / "token.json"
    token_file.write_text("original token", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        youtube.authorize(client_file, token_file)

    assert token_file.read_text(encoding="utf-8") == "original token"


def test_missing_client_preserves_existing_token(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text("original token", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="OAuth client JSON not found"):
        youtube.authorize(tmp_path / "absent.json", token_file)

    assert token_file.read_text(encoding="utf-8") == "original token"


def test_auth_replaces_existing_token_after_success(desktop_client, tmp_path, fake_flow):
    fake_flow()
    token_file = tmp_path / "token.json"
    token_file.write_text("original token", encoding="utf-8")

    youtube.authorize(desktop_client, token_file)

    assert token_file.read_text(encoding="utf-8") == '{"token": "new"}'
    assert list(tmp_path.glob(".token.json.*.tmp")) == []


def test_token_replace_failure_preserves_old_token_and_cleans_temporary_file(
        monkeypatch, desktop_client, tmp_path, fake_flow):
    fake_flow()
    token_file = tmp_path / "token.json"
    token_file.write_text("original token", encoding="utf-8")

    def cannot_replace(self, target):
        assert self.read_text(encoding="utf-8") == '{"token": "new"}'
        assert target == token_file
        raise PermissionError("Cannot replace token")

    monkeypatch.setattr(Path, "replace", cannot_replace)

    with pytest.raises(PermissionError, match="Cannot replace token"):
        youtube.authorize(desktop_client, token_file)

    assert token_file.read_text(encoding="utf-8") == "original token"
    assert list(tmp_path.glob(".token.json.*.tmp")) == []


@pytest.mark.parametrize("extra_args, expected_options", [
    ([], {"bind_address": "127.0.0.1", "port": 0, "open_browser": True}),
    (["--bind-address", "0.0.0.0", "--port", "8765", "--no-browser"],
     {"bind_address": "0.0.0.0", "port": 8765, "open_browser": False}),
])
def test_auth_cli_passes_options_without_bot_configuration(monkeypatch, tmp_path, extra_args, expected_options):
    call = {}

    def authorize(client_secrets, token_path, **options):
        call.update(client_secrets=client_secrets, token_path=token_path, options=options)

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_OWNER_ID", raising=False)
    monkeypatch.setenv("YM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "authorize", authorize)
    monkeypatch.setattr(sys, "argv", ["ym_playlist_maker", "auth", "--client-secrets", "client.json", *extra_args])

    cli.main()

    assert call["client_secrets"] == Path("client.json")
    assert call["token_path"] == tmp_path / "token.json"
    assert call["options"] == expected_options


@pytest.mark.parametrize("port", ["-1", "65536", "invalid"])
def test_auth_cli_rejects_invalid_port(monkeypatch, capsys, port):
    monkeypatch.setattr(sys, "argv", ["ym_playlist_maker", "auth", "--client-secrets", "client.json", "--port", port])

    with pytest.raises(SystemExit) as failure:
        cli.main()

    assert failure.value.code == 2
    assert "between 0 and 65535" in capsys.readouterr().err


def test_container_auth_requires_fixed_nonzero_port(monkeypatch, desktop_client, tmp_path, capsys):
    monkeypatch.setenv("YM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ym_playlist_maker", "auth", "--client-secrets", str(desktop_client),
                                      "--bind-address", "0.0.0.0", "--no-browser"])

    with pytest.raises(SystemExit) as failure:
        cli.main()

    assert failure.value.code == 1
    assert "fixed nonzero --port" in capsys.readouterr().err


@pytest.mark.parametrize("error, message, exit_code", [
    (AccessDeniedError(description="sensitive-provider-detail"), "declined or rejected", 1),
    (ConnectionError("sensitive-provider-detail"), "network connection", 1),
    (WSGITimeoutError("timeout"), "within 5 minutes", 1),
    (OSError(errno.EADDRINUSE, "Address already in use"), "YM_AUTH_PORT", 1),
    (KeyboardInterrupt(), "cancelled", 130),
])
def test_auth_cli_reports_failure_without_exposing_provider_details(
        monkeypatch, desktop_client, tmp_path, fake_flow, capsys, error, message, exit_code):
    fake_flow(error)
    token_file = tmp_path / "token.json"
    token_file.write_text("original token", encoding="utf-8")
    monkeypatch.setenv("YM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ym_playlist_maker", "auth", "--client-secrets", str(desktop_client)])

    with pytest.raises(SystemExit) as failure:
        cli.main()

    output = capsys.readouterr()
    assert failure.value.code == exit_code
    assert message in output.err
    assert "Traceback" not in output.err
    assert "sensitive-provider-detail" not in output.err
    assert "Google account connected" not in output.out
    assert token_file.read_text(encoding="utf-8") == "original token"


@pytest.mark.parametrize("client_contents, message", [
    (None, "OAuth client JSON not found"),
    ('{"web": {}}', "Desktop app"),
    ('not JSON', "valid UTF-8 JSON"),
    ('{"installed": {}}', "OAuth Desktop client JSON is invalid"),
])
def test_auth_cli_reports_client_configuration_errors(monkeypatch, tmp_path, capsys, client_contents, message):
    client_file = tmp_path / "client.json"
    if client_contents is not None:
        client_file.write_text(client_contents, encoding="utf-8")
    token_file = tmp_path / "token.json"
    token_file.write_text("original token", encoding="utf-8")
    monkeypatch.setenv("YM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ym_playlist_maker", "auth", "--client-secrets", str(client_file)])

    with pytest.raises(SystemExit) as failure:
        cli.main()

    assert failure.value.code == 1
    assert message in capsys.readouterr().err
    assert token_file.read_text(encoding="utf-8") == "original token"


def test_missing_credentials_explains_container_authorization(tmp_path):
    with pytest.raises(youtube.MissingCredentials, match="docker compose -f compose.auth.yaml"):
        youtube.YouTubePlaylists(tmp_path / "absent.json").check_credentials()
