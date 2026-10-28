import json
import cloud_mcp as c

metrics = [
    ("sleep_status", 120),
    ("sleep_index", 120),
    ("heart_rate_detail", 30),
    ("heart_rate_stat", 180),
    ("blood_oxygen_detail", 30),
    ("blood_oxygen_stat", 180),
    ("physical_mental_detail", 180),
    ("hrv", 180),
    ("stress_detail", 180),
]
for metric, days in metrics:
    result = c.cloud_sync(metric, days)
    safe = {k: v for k, v in result.items() if k not in {"raw_sample", "data"}}
    print(json.dumps(safe, ensure_ascii=False), flush=True)
print(json.dumps(c.cloud_status(), ensure_ascii=False))
