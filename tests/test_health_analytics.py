import json
import sqlite3

from health_analytics import build_summary


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE cloud_data(metric TEXT, date TEXT, payload TEXT, fetched_at INTEGER, "
        "PRIMARY KEY(metric, date))"
    )
    con.executemany(
        "INSERT INTO cloud_data VALUES(?,?,?,?)",
        [(metric, key, json.dumps(payload), fetched_at)
         for metric, key, payload, fetched_at in rows],
    )
    con.commit()
    con.close()


def test_summary_derives_latest_daily_steps_from_detail_rows(tmp_path):
    db = tmp_path / "cache.db"
    make_db(db, [
        ("steps_detail", "a", {"startTimestamp": 1793145600000, "steps": 120,
                                "distance": 90, "calories": 5000}, 100),
        ("steps_detail", "b", {"startTimestamp": 1793149200000, "steps": 80,
                                "distance": 60, "calories": 3000}, 100),
    ])

    summary = build_summary(db)

    assert summary["overview"]["steps"]["value"] == 200
    assert summary["overview"]["steps"]["distance_km"] == 0.15
    assert summary["overview"]["steps"]["calories_kcal"] == 8.0
    assert summary["overview"]["steps"]["provenance"] == "local_derived"


def test_summary_aggregates_sleep_stages_without_treating_awake_as_sleep(tmp_path):
    db = tmp_path / "cache.db"
    start = 1_793_120_400_000
    minute = 60_000
    make_db(db, [
        ("sleep_status", "deep", {"startTimestamp": start, "endTimestamp": start + 60 * minute,
                                   "sleepState": 2}, 100),
        ("sleep_status", "light", {"startTimestamp": start + 60 * minute,
                                    "endTimestamp": start + 180 * minute, "sleepState": 4}, 100),
        ("sleep_status", "rem", {"startTimestamp": start + 180 * minute,
                                  "endTimestamp": start + 210 * minute, "sleepState": 3}, 100),
        ("sleep_status", "awake", {"startTimestamp": start + 210 * minute,
                                    "endTimestamp": start + 220 * minute, "sleepState": 1}, 100),
    ])

    sleep = build_summary(db)["overview"]["sleep"]

    assert sleep["value_minutes"] == 210
    assert sleep["stages_minutes"] == {"deep": 60, "rem": 30, "light": 120,
                                        "awake": 10, "nap": 0}
    assert sleep["provenance"] == "local_derived"


def test_summary_labels_vendor_and_local_metrics_and_reports_coverage(tmp_path):
    db = tmp_path / "cache.db"
    day = 1_793_145_600_000
    make_db(db, [
        ("heart_rate_detail", "h1", {"dataCreatedTimestamp": day, "heartRateValue": 52}, 200),
        ("heart_rate_detail", "h2", {"dataCreatedTimestamp": day + 60_000, "heartRateValue": 68}, 200),
        ("blood_oxygen_detail", "o1", {"dataCreatedTimestamp": day,
                                         "bloodOxygenSaturationValue": 96}, 200),
        ("blood_oxygen_detail", "o2", {"dataCreatedTimestamp": day + 60_000,
                                         "bloodOxygenSaturationValue": 98}, 200),
        ("hrv", "v1", {"startTimestamp": day, "heartRateVarValue": 40,
                          "source": "physical_mental_detail"}, 200),
        ("stress_detail", "s1", {"startTimestamp": day, "stressValue": 25,
                                    "source": "physical_mental_detail"}, 200),
        ("sleep_index", "i1", {"date": 20261028, "sleepRecoveryRate": 101,
                                  "avgSleepSpo2": 97, "basalSleepHeartRate": 56}, 200),
    ])

    summary = build_summary(db)

    assert summary["overview"]["heart_rate"]["average"] == 60
    assert summary["overview"]["blood_oxygen"]["average"] == 97
    assert summary["overview"]["hrv"]["provenance"] == "local_derived"
    assert summary["overview"]["sleep_index"]["provenance"] == "vendor_summary"
    assert summary["data_quality"]["metrics"]["heart_rate_detail"]["rows"] == 2
    assert summary["data_quality"]["last_fetched_at"] == 200
    assert summary["trends"]["heart_rate"][-1]["value"] == 60
