"""Data-quality-aware summaries for the OPPO Health SQLite cache.

Every returned metric labels its provenance:
- raw: direct cloud record
- vendor_summary: aggregate/score produced by OPPO
- local_derived: calculation performed by this service
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

BEIJING = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")


def _date_from_payload(payload: dict) -> str | None:
    raw_date = payload.get("date")
    if raw_date is not None:
        value = str(raw_date)
        if len(value) == 8 and value.isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        if len(value) == 10 and value[4] == "-":
            return value
    for key in ("dataCreatedTimestamp", "startTimestamp", "measurementTime",
                "modifiedTimestamp", "modifiedTime"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            timestamp = int(value)
            if timestamp < 100_000_000_000:
                timestamp *= 1000
            return dt.datetime.fromtimestamp(timestamp / 1000, BEIJING).date().isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            continue
    return None


def _load_rows(db_path: str | Path) -> list[dict]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    rows = []
    try:
        for row in con.execute("SELECT metric, date, payload, fetched_at FROM cloud_data"):
            payload = json.loads(row["payload"])
            rows.append({"metric": row["metric"], "cache_key": row["date"],
                         "payload": payload, "fetched_at": row["fetched_at"],
                         "date": _date_from_payload(payload)})
    finally:
        con.close()
    return rows


def _latest_steps(rows: list[dict]) -> dict:
    daily: dict[str, dict[str, float]] = defaultdict(lambda: {
        "steps": 0.0, "distance": 0.0, "calories": 0.0, "duration": 0.0,
    })
    for row in rows:
        if row["metric"] != "steps_detail" or not row["date"]:
            continue
        payload = row["payload"]
        bucket = daily[row["date"]]
        bucket["steps"] += float(payload.get("steps") or 0)
        bucket["distance"] += float(payload.get("distance") or payload.get("totalDistance") or 0)
        bucket["calories"] += float(payload.get("calories") or payload.get("totalCalories") or 0)
        bucket["duration"] += float(payload.get("duration") or payload.get("totalDuration") or 0)
    if not daily:
        return {"value": None, "unit": "步", "date": None,
                "provenance": "local_derived", "source_metric": "steps_detail"}
    date = max(daily)
    value = daily[date]
    return {
        "value": int(value["steps"]),
        "unit": "步",
        "date": date,
        "distance_km": round(value["distance"] / 1000, 2),
        "calories_kcal": round(value["calories"] / 1000, 1),
        "active_minutes": round(value["duration"] / 60_000, 1),
        "provenance": "local_derived",
        "source_metric": "steps_detail",
        "method": "同一自然日步数明细求和",
    }


def _timestamp_date(value) -> str | None:
    try:
        timestamp = int(value)
        if timestamp < 100_000_000_000:
            timestamp *= 1000
        return dt.datetime.fromtimestamp(timestamp / 1000, BEIJING).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _latest_sleep(rows: list[dict]) -> dict:
    state_names = {1: "awake", 2: "deep", 3: "rem", 4: "light", 5: "nap"}
    daily: dict[str, dict[str, float]] = defaultdict(
        lambda: {"deep": 0, "rem": 0, "light": 0, "awake": 0, "nap": 0}
    )
    for row in rows:
        if row["metric"] != "sleep_status":
            continue
        payload = row["payload"]
        state = state_names.get(payload.get("sleepState"))
        if state is None:
            continue
        start = payload.get("startTimestamp")
        end = payload.get("endTimestamp")
        try:
            minutes = max(0, (int(end) - int(start)) / 60_000)
        except (TypeError, ValueError):
            continue
        date = _timestamp_date(end) or row["date"]
        if date:
            daily[date][state] += minutes
    if not daily:
        return {"value_minutes": None, "unit": "分钟", "date": None,
                "provenance": "local_derived", "source_metric": "sleep_status"}
    date = max(daily)
    stages = {key: round(value) for key, value in daily[date].items()}
    total = stages["deep"] + stages["rem"] + stages["light"]
    return {
        "value_minutes": total,
        "unit": "分钟",
        "date": date,
        "stages_minutes": stages,
        "provenance": "local_derived",
        "source_metric": "sleep_status",
        "method": "睡眠阶段时段去重后按深睡、REM、浅睡汇总；清醒和小睡单列",
    }


def _latest_point_summary(rows: list[dict], metric: str, value_key: str,
                          label: str, unit: str) -> dict:
    daily: dict[str, list[float]] = defaultdict(list)
    derived = False
    for row in rows:
        if row["metric"] != metric or not row["date"]:
            continue
        value = row["payload"].get(value_key)
        if isinstance(value, (int, float)):
            daily[row["date"]].append(float(value))
            derived = derived or bool(row["payload"].get("source"))
    if not daily:
        return {"label": label, "average": None, "unit": unit, "date": None,
                "provenance": "local_derived", "source_metric": metric}
    date = max(daily)
    values = daily[date]
    return {
        "label": label,
        "average": round(mean(values), 1),
        "median": round(median(values), 1),
        "minimum": round(min(values), 1),
        "maximum": round(max(values), 1),
        "samples": len(values),
        "unit": unit,
        "date": date,
        "provenance": "local_derived",
        "source_metric": metric,
        "derived_source": "physical_mental_detail" if derived else None,
        "method": "同一自然日原始/明细读数的均值、中位数、最小值和最大值",
    }


def _latest_sleep_index(rows: list[dict]) -> dict:
    candidates = [row for row in rows if row["metric"] == "sleep_index" and row["date"]]
    if not candidates:
        return {"date": None, "provenance": "vendor_summary",
                "source_metric": "sleep_index"}
    row = max(candidates, key=lambda item: item["date"])
    payload = row["payload"]
    return {
        "date": row["date"],
        "recovery_rate": payload.get("sleepRecoveryRate"),
        "average_spo2": payload.get("avgSleepSpo2"),
        "basal_heart_rate": payload.get("basalSleepHeartRate"),
        "heart_rate_warning": payload.get("hasHeartRateWarning"),
        "provenance": "vendor_summary",
        "source_metric": "sleep_index",
        "method": "OPPO 云端返回的睡眠指数/汇总字段，算法未在本项目中复现",
    }


def _data_quality(rows: list[dict]) -> dict:
    metrics: dict[str, dict] = {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["metric"]].append(row)
    today = dt.datetime.now(BEIJING).date()
    for metric, metric_rows in sorted(grouped.items()):
        dates = sorted({row["date"] for row in metric_rows if row["date"]})
        latest = dates[-1] if dates else None
        age = (today - dt.date.fromisoformat(latest)).days if latest else None
        metrics[metric] = {
            "rows": len(metric_rows),
            "days_with_data": len(dates),
            "first_date": dates[0] if dates else None,
            "last_date": latest,
            "latest_age_days": age,
            "status": "fresh" if age is not None and age <= 1 else "stale" if age is not None else "unknown",
        }
    return {
        "metrics": metrics,
        "metrics_with_data": len(metrics),
        "total_rows": len(rows),
        "last_fetched_at": max((row["fetched_at"] for row in rows), default=None),
        "notice": "空白日期可能表示设备未上传、云端无记录或未同步，不能直接解释为健康事件不存在。",
    }


def _trends(rows: list[dict], limit: int = 30) -> dict:
    specs = {
        "heart_rate": ("heart_rate_detail", "heartRateValue", "mean"),
        "blood_oxygen": ("blood_oxygen_detail", "bloodOxygenSaturationValue", "mean"),
        "hrv": ("hrv", "heartRateVarValue", "median"),
        "stress": ("stress_detail", "stressValue", "mean"),
        "steps": ("steps_detail", "steps", "sum"),
    }
    output: dict[str, list[dict]] = {}
    for name, (metric, key, operation) in specs.items():
        daily: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            if row["metric"] != metric or not row["date"]:
                continue
            value = row["payload"].get(key)
            if isinstance(value, (int, float)):
                daily[row["date"]].append(float(value))
        points = []
        for date in sorted(daily)[-limit:]:
            values = daily[date]
            value = sum(values) if operation == "sum" else median(values) if operation == "median" else mean(values)
            points.append({"date": date, "value": round(value, 1)})
        output[name] = points

    sleep_daily: dict[str, float] = defaultdict(float)
    for row in rows:
        if row["metric"] != "sleep_status":
            continue
        payload = row["payload"]
        if payload.get("sleepState") not in (2, 3, 4):
            continue
        try:
            minutes = max(0, (int(payload["endTimestamp"]) - int(payload["startTimestamp"])) / 60_000)
        except (KeyError, TypeError, ValueError):
            continue
        date = _timestamp_date(payload.get("endTimestamp")) or row["date"]
        if date:
            sleep_daily[date] += minutes
    output["sleep"] = [{"date": date, "value": round(sleep_daily[date], 1)}
                       for date in sorted(sleep_daily)[-limit:]]
    return output


def build_summary(db_path: str | Path) -> dict:
    rows = _load_rows(db_path)
    return {
        "generated_at": dt.datetime.now(BEIJING).isoformat(timespec="seconds"),
        "overview": {
            "steps": _latest_steps(rows),
            "sleep": _latest_sleep(rows),
            "sleep_index": _latest_sleep_index(rows),
            "heart_rate": _latest_point_summary(rows, "heart_rate_detail", "heartRateValue",
                                                  "心率", "bpm"),
            "blood_oxygen": _latest_point_summary(rows, "blood_oxygen_detail",
                                                    "bloodOxygenSaturationValue", "血氧", "%"),
            "hrv": _latest_point_summary(rows, "hrv", "heartRateVarValue", "HRV", "ms"),
            "stress": _latest_point_summary(rows, "stress_detail", "stressValue", "压力", "分"),
        },
        "data_quality": _data_quality(rows),
        "trends": _trends(rows),
        "provenance": {
            "raw": "OPPO 云端返回的原始/明细记录",
            "vendor_summary": "OPPO 云端或设备算法给出的汇总/指数；本服务不声称知道其完整算法",
            "local_derived": "本服务基于缓存明细做的透明求和、时长或描述性统计",
        },
        "disclaimer": "仅用于个人数据查看与趋势整理，不提供诊断、治疗建议或紧急告警。",
    }
