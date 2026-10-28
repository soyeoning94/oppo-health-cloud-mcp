import importlib.util
import json
import sqlite3
from pathlib import Path

import cloud_mcp


def test_fetch_versions_reports_http_error_when_gateway_returns_non_json(monkeypatch):
    monkeypatch.setattr(
        cloud_mcp,
        "_post",
        lambda *args, **kwargs: {"http_error": 28, "body": "non_json_response"},
    )

    versions, error = cloud_mcp._fetch_versions("version/path", {"token": "x"})

    assert versions is None
    assert error == "http_error:28"


def test_fetch_versioned_details_returns_empty_without_detail_requests(monkeypatch):
    monkeypatch.setattr(cloud_mcp, "_fetch_versions", lambda *args, **kwargs: ([], None))

    def unexpected_post(*args, **kwargs):
        raise AssertionError("detail endpoint must not be called when no versions exist")

    monkeypatch.setattr(cloud_mcp, "_post", unexpected_post)

    items, error = cloud_mcp._fetch_versioned_details("sleep_status", {"token": "x"}, 7)

    assert items == []
    assert error is None


def test_fetch_versioned_stats_uses_seven_day_left_open_windows(monkeypatch):
    day = 86_400_000
    versions = [1_700_000_000_000, 1_700_000_000_000 + 8 * day,
                1_700_000_000_000 + 15 * day]
    monkeypatch.setattr(cloud_mcp, "_fetch_versions", lambda *args, **kwargs: (versions, None))
    windows = []

    def fake_post(path, body, auth):
        start = body["startModifiedTimestamp"]
        end = body["endModifiedTimestamp"]
        windows.append((start, end))
        if end - start > 7 * day:
            return {"errorCode": 23103}
        return {
            "errorCode": 0,
            "body": {"dataList": [{"modifiedTimestamp": end}], "hasMore": False},
        }

    monkeypatch.setattr(cloud_mcp, "_post", fake_post)

    items, error = cloud_mcp._fetch_versioned_stats("sleep_stat", {"token": "x"}, 30)

    assert error is None
    assert [row["modifiedTimestamp"] for row in items][-1] == max(versions)
    assert windows[0][0] == min(versions) - 1
    assert windows[-1][1] == max(versions)
    assert all(end - start <= 7 * day for start, end in windows)


def test_data_directory_can_be_configured_for_server_deployment(monkeypatch, tmp_path):
    monkeypatch.setenv("OPPO_HEALTH_DATA_DIR", str(tmp_path))
    module_path = Path(cloud_mcp.__file__)
    spec = importlib.util.spec_from_file_location("cloud_mcp_isolated", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert Path(module.AUTH_FILE) == tmp_path / "auth.json"
    assert Path(module.CACHE_DB) == tmp_path / "cloud_cache.db"


def test_fetch_versioned_details_reports_http_error_from_detail_request(monkeypatch):
    monkeypatch.setattr(cloud_mcp, "_fetch_versions", lambda *args, **kwargs: ([123], None))
    monkeypatch.setattr(
        cloud_mcp,
        "_post",
        lambda *args, **kwargs: {"http_error": 28, "body": "non_json_response"},
    )

    items, error = cloud_mcp._fetch_versioned_details("sleep_status", {"token": "x"}, 7)

    assert items is None
    assert error == "http_error:28"


def test_cloud_analyze_returns_structured_provenance_summary(monkeypatch, tmp_path):
    db = tmp_path / "cache.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cloud_data(metric TEXT, date TEXT, payload TEXT, fetched_at INTEGER, PRIMARY KEY(metric,date))")
    con.execute("INSERT INTO cloud_data VALUES(?,?,?,?)", (
        "heart_rate_detail", "one",
        json.dumps({"dataCreatedTimestamp": 1793145600000, "heartRateValue": 60}), 123,
    ))
    con.commit()
    con.close()
    monkeypatch.setattr(cloud_mcp, "CACHE_DB", str(db))

    result = cloud_mcp.cloud_analyze(7)

    assert result["days"] == 7
    assert result["overview"]["heart_rate"]["average"] == 60
    assert result["overview"]["heart_rate"]["provenance"] == "local_derived"


def test_fetch_versioned_stats_uses_zero_cursor_fallback_for_legacy_endpoint(monkeypatch):
    day = 86_400_000
    versions = [1_700_000_000_000, 1_700_000_000_000 + 2 * day]
    monkeypatch.setattr(cloud_mcp, "_fetch_versions", lambda *args, **kwargs: (versions, None))
    calls = []

    def fake_post(path, body, auth):
        calls.append((body["startModifiedTimestamp"], body["endModifiedTimestamp"]))
        if body["startModifiedTimestamp"] == 0 and body["endModifiedTimestamp"] == 0:
            return {"errorCode": 0, "body": {"dataList": [{"date": 20261028}], "hasMore": False}}
        return {"errorCode": 23103}

    monkeypatch.setattr(cloud_mcp, "_post", fake_post)

    items, error = cloud_mcp._fetch_versioned_stats("stress_stat", {"token": "x"}, 7)

    assert error is None
    assert items == [{"date": 20261028, "source": "zero_cursor_fallback"}]
    assert calls[-1] == (0, 0)
    assert len(calls) < 20


def test_fetch_versioned_sport_reports_http_error(monkeypatch):
    monkeypatch.setattr(
        cloud_mcp,
        "_post",
        lambda *args, **kwargs: {"http_error": 28, "body": "non_json_response"},
    )

    items, error = cloud_mcp._fetch_versioned_sport("sport_stat", {"token": "x"}, 7)

    assert items is None
    assert error == "http_error:28"


def test_db_uses_wal_and_busy_timeout(monkeypatch, tmp_path):
    db = tmp_path / "cache.db"
    monkeypatch.setattr(cloud_mcp, "CACHE_DB", str(db))

    con = cloud_mcp._db()
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] >= 30_000
    finally:
        con.close()


def test_cloud_sync_records_successful_run(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_mcp, "CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setattr(cloud_mcp, "_load_auth", lambda: {"token": "x"})
    monkeypatch.setattr(cloud_mcp, "_post", lambda *args, **kwargs: {"errorCode": 0, "body": []})

    cloud_mcp.cloud_sync("heart_rate", 1)

    rows = cloud_mcp.sync_history(5)
    assert rows[0]["metric"] == "heart_rate"
    assert rows[0]["error"] is None
    assert rows[0]["duration_ms"] is not None


def test_cloud_sync_records_failed_run(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_mcp, "CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setattr(cloud_mcp, "_load_auth", lambda: {"token": "x"})
    monkeypatch.setattr(cloud_mcp, "_post", lambda *args, **kwargs: {"errorCode": 10101})

    result = cloud_mcp.cloud_sync("heart_rate", 1)

    assert result["error"]
    rows = cloud_mcp.sync_history(5)
    assert rows[0]["metric"] == "heart_rate"
    assert "10101" in str(rows[0]["error"])


def test_cloud_status_exposes_last_sync(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_mcp, "CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setattr(cloud_mcp, "_load_auth", lambda: {"token": "x"})
    monkeypatch.setattr(cloud_mcp, "_post", lambda *args, **kwargs: {"errorCode": 0, "body": []})

    cloud_mcp.cloud_sync("heart_rate", 1)
    status = cloud_mcp.cloud_status()

    assert status["last_sync"]["heart_rate"]["error"] is None
