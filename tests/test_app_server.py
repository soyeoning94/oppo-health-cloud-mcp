import json
import sqlite3

from starlette.testclient import TestClient

from app_server import create_app


def make_db(path):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE cloud_data(metric TEXT, date TEXT, payload TEXT, fetched_at INTEGER, "
        "PRIMARY KEY(metric, date))"
    )
    con.execute("INSERT INTO cloud_data VALUES(?,?,?,?)", (
        "heart_rate_detail", "one",
        json.dumps({"dataCreatedTimestamp": 1793145600000, "heartRateValue": 60}), 123,
    ))
    con.commit()
    con.close()


def test_health_check_is_public_but_summary_requires_bearer_token(tmp_path):
    db = tmp_path / "cache.db"
    make_db(db)
    app = create_app(api_token="secret-token", db_path=str(db), auth_file=str(tmp_path / "auth.json"))

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/summary").status_code == 401
        response = client.get("/api/summary", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    assert response.json()["overview"]["heart_rate"]["average"] == 60


def test_mcp_endpoint_rejects_missing_token_before_protocol_handling(tmp_path):
    db = tmp_path / "cache.db"
    make_db(db)
    app = create_app(api_token="secret-token", db_path=str(db), auth_file=str(tmp_path / "auth.json"))

    with TestClient(app) as client:
        response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

    assert response.status_code == 401


def test_status_endpoint_reports_sync_history(tmp_path, monkeypatch):
    import cloud_mcp

    db = tmp_path / "cache.db"
    make_db(db)
    monkeypatch.setattr(cloud_mcp, "CACHE_DB", str(db))
    monkeypatch.setattr(cloud_mcp, "_load_auth", lambda: {"token": "x"})
    monkeypatch.setattr(cloud_mcp, "_post", lambda *args, **kwargs: {"errorCode": 0, "body": []})
    cloud_mcp.cloud_sync("heart_rate", 1)

    app = create_app(api_token="secret-token", db_path=str(db), auth_file=str(tmp_path / "auth.json"))
    with TestClient(app) as client:
        response = client.get("/api/status", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["sync_history"][0]["metric"] == "heart_rate"
    assert body["last_sync"]["heart_rate"]["error"] is None

