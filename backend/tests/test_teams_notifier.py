import httpx
import pytest

from app.teams_notifier import TeamsSendError, send_teams_card

SAMPLE_CARD = {"type": "AdaptiveCard", "version": "1.4", "body": []}


def test_send_teams_card_posts_card_under_card_key(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.teams_notifier.httpx.post", fake_post)

    send_teams_card("https://prod.example.com/workflow/xyz", SAMPLE_CARD)

    assert len(calls) == 1
    url, payload, timeout = calls[0]
    assert url == "https://prod.example.com/workflow/xyz"
    assert payload == {"card": SAMPLE_CARD}


def test_send_teams_card_raises_clear_error_on_http_failure(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(500, request=request)

    monkeypatch.setattr("app.teams_notifier.httpx.post", fake_post)

    with pytest.raises(TeamsSendError):
        send_teams_card("https://prod.example.com/workflow/xyz", SAMPLE_CARD)


def test_send_teams_card_raises_clear_error_on_connection_failure(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise httpx.ConnectError("no se pudo conectar")

    monkeypatch.setattr("app.teams_notifier.httpx.post", fake_post)

    with pytest.raises(TeamsSendError):
        send_teams_card("https://prod.example.com/workflow/xyz", SAMPLE_CARD)
