
from app import main
from app.database import Repository
from app.services.broker_handoff import BrokerHandoffManager
from fastapi.testclient import TestClient
from test_workflows import manual


def test_manual_api_immutable_revision_and_no_rate_collection(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "api.sqlite3")
    repo.initialize()
    monkeypatch.setattr(main, "repository", repo)
    with TestClient(main.app) as client:
        payload = manual().model_dump(mode="json")
        saved = client.post("/api/manual-quotes", json=payload)
        assert saved.status_code == 200
        quote = saved.json()
        handoff = client.post(
            "/api/quote-handoffs/tql/prepare", json={"shipment_id": quote["id"], "source_kind": "manual"}
        )
        assert handoff.status_code == 200, handoff.text
        session = main.broker_handoffs._get(handoff.json()["request_id"])
        payload["pallets"][0]["weight"] = 999
        assert client.put(f"/api/manual-quotes/{quote['id']}", json=payload).json()["revision"] == 2
        captured = client.post(
            f"/api/quote-handoffs/{session.request_id}/events?token={session.token}",
            headers={"Origin": main.BUILD2QUOTE_EXTENSION_ORIGIN},
            json={"state": "rates_captured", "message": "Test only", "rates": [{"broker": "tql", "price": 100}]},
        )
        assert captured.status_code == 422
        history = client.get(f"/api/quote-history/manual/{quote['id']}").json()
        assert history[0]["revision"] == "1"
        assert history[0]["packet"]["total_weight"] == 248
        assert "rates" not in history[0]
        assert "broker_rates" not in quote
        with repo.connect() as db:
            assert db.execute("SELECT rates_json FROM quote_snapshots").fetchone()[0] == "[]"
        completed = client.post(
            f"/api/quote-handoffs/{session.request_id}/events?token={session.token}",
            headers={"Origin": main.BUILD2QUOTE_EXTENSION_ORIGIN},
            json={"state": "completed", "message": "Autofill only"},
        )
        assert completed.status_code == 200
        assert client.get("/api/shipments").json()[0]["source_kind"] == "manual"
        assert repo.list_shipments() == []


def test_prepare_all_returns_ordered_unopened_fifteen_minute_handoffs(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "batch.sqlite3")
    repo.initialize()
    monkeypatch.setattr(main, "repository", repo)
    with TestClient(main.app) as client:
        quote = client.post("/api/manual-quotes", json=manual().model_dump(mode="json")).json()
        response = client.post(
            "/api/quote-handoffs/prepare-all",
            json={"shipment_id": quote["id"], "source_kind": "manual"},
        )
        assert response.status_code == 200, response.text
        handoffs = response.json()["handoffs"]
        assert [value["broker"] for value in handoffs] == ["echoship", "tql", "dsv", "myunishippers", "odfl"]
        assert all("#build2quote=" in value["url"] for value in handoffs)
        for value in handoffs:
            session = main.broker_handoffs._get(value["request_id"])
            assert 895 <= (session.expires_at - session.created_at).total_seconds() <= 900
            assert session.source_revision == "1"


def test_prepare_requires_explicit_replace_for_active_broker(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "replace.sqlite3")
    repo.initialize()
    monkeypatch.setattr(main, "repository", repo)
    monkeypatch.setattr(main, "broker_handoffs", BrokerHandoffManager())
    with TestClient(main.app) as client:
        quote = client.post("/api/manual-quotes", json=manual().model_dump(mode="json")).json()
        payload = {"shipment_id": quote["id"], "source_kind": "manual"}
        first = client.post("/api/quote-handoffs/dsv/prepare", json=payload)
        assert first.status_code == 200
        conflict = client.post("/api/quote-handoffs/dsv/prepare", json=payload)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["active_brokers"] == ["dsv"]
        replacement = client.post(
            "/api/quote-handoffs/dsv/prepare",
            json={**payload, "replace_active": True},
        )
        assert replacement.status_code == 200
        assert client.get(f"/api/quote-handoffs/{first.json()['request_id']}/status").json()["state"] == "cancelled"


def test_shell_never_reuses_cached_html(tmp_path):
    # Exercise the shell's static handler directly so the test does not depend on a built frontend/dist.
    from starlette.applications import Starlette

    (tmp_path / "index.html").write_text("<!doctype html><title>Quote Helper</title>", encoding="utf-8")
    shell = Starlette()
    shell.mount("/", main.FreshFrontend(directory=tmp_path, html=True), name="frontend")
    with TestClient(shell) as client:
        first = client.get("/")
        assert "no-store" in first.headers["cache-control"]
        second = client.get("/", headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 200


