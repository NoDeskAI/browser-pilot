import json

from app import main


def test_bootstrap_response_sanitizes_blocked_state(monkeypatch):
    monkeypatch.setattr(
        main.db,
        "get_bootstrap_state",
        lambda: {
            "status": "incompatible_schema",
            "currentRevision": "future",
            "targetRevision": "current",
            "pendingRevisions": ["0010"],
            "error": "Database schema revision 'future' is not known",
            "attempt": 2,
            "updatedAt": "2026-06-03T00:00:00+00:00",
        },
    )

    response = main._bootstrap_response(503)
    body = json.loads(response.body)

    assert body == {
        "status": "service_unavailable",
        "database": {
            "status": "service_unavailable",
            "attempt": 2,
            "updatedAt": "2026-06-03T00:00:00+00:00",
        },
    }


def test_bootstrap_response_keeps_ready_status_public(monkeypatch):
    monkeypatch.setattr(
        main.db,
        "get_bootstrap_state",
        lambda: {
            "status": "ready",
            "currentRevision": "0009",
            "targetRevision": "0009",
            "pendingRevisions": [],
            "error": "",
            "attempt": 1,
            "updatedAt": "2026-06-03T00:00:00+00:00",
        },
    )

    response = main._bootstrap_response(200)
    body = json.loads(response.body)

    assert body == {
        "status": "ok",
        "database": {
            "status": "ready",
            "attempt": 1,
            "updatedAt": "2026-06-03T00:00:00+00:00",
        },
    }
