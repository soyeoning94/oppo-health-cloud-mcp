#!/usr/bin/env python3
"""Generate auth.json for the OPPO Health cloud MCP from a rooted Android phone.

The phone must be reachable over adb and `su` must work (`adb shell su -c id` -> uid=0).
Two sources are combined:

* ``open_sdk_AC_ACCOUNT_AUTH_FILE_NAME.properties`` -> ``token`` + ``token_auth_id``
* ``com.sensorsdata.analytics.android.sdk.SensorsDataAPI.xml`` -> ``ssoid``

Both the legacy "one key per line" properties format and the single-line
Java-escaped JSON used by OPPO Health 6.7.17 are supported.

Examples::

    python gen_auth.py --serial 192.168.1.23:41234
    python gen_auth.py --auth-properties-file auth.properties --sensors-file sensors.xml
    python gen_auth.py --serial 192.168.1.23:41234 --verify
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import subprocess
import sys

PKG = "com.heytap.health"
AUTH_FILE_NAME = "open_sdk_AC_ACCOUNT_AUTH_FILE_NAME.properties"
SENSORS_PREFS = "shared_prefs/com.sensorsdata.analytics.android.sdk.SensorsDataAPI.xml"
CANDIDATE_DIRS = [
    f"/data/data/{PKG}/files",
    f"/data/data/{PKG}/shared_prefs",
    f"/data/user/0/{PKG}/files",
    f"/data/user/0/{PKG}/shared_prefs",
]


def parse_auth_properties(text: str) -> dict:
    """Extract token/deviceId from either properties layout."""
    if not text or not text.strip():
        raise ValueError("empty auth properties")

    normalized = text.replace("\\:", ":").replace('\\"', '"')
    start, end = normalized.find("{"), normalized.rfind("}")
    if start != -1 and end > start:
        payload = json.loads(normalized[start:end + 1])
        token = payload.get("accessToken")
        device = payload.get("deviceId")
        expires = payload.get("accessTokenExp")
    else:
        fields = {}
        for line in normalized.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
        token = fields.get("accessToken")
        device = fields.get("deviceId")
        expires = fields.get("accessTokenExp")

    if isinstance(device, str) and device[:1] in '{"':
        try:
            decoded = json.loads(device)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, str):
            device = decoded
        elif isinstance(decoded, dict):
            device = decoded.get("deviceId") or decoded.get("id") or device

    if not token or not device:
        raise ValueError("auth properties missing accessToken or deviceId")

    result = {"token": str(token).strip(), "token_auth_id": str(device).strip()}
    if expires is not None:
        try:
            result["access_token_exp"] = int(expires)
        except (TypeError, ValueError):
            pass
    return result


def parse_ssoid(xml_text: str) -> str:
    """Read ssoid from the Sensors Analytics super_properties blob."""
    match = re.search(r'<string name="super_properties">(.*?)</string>', xml_text or "", re.S)
    if not match:
        raise ValueError("super_properties not found in sensors prefs")
    raw = html.unescape(match.group(1)).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"super_properties is not valid JSON: {exc}") from exc
    ssoid = data.get("ssoid") or data.get("virtual_ssoid") or data.get("userTagId")
    if not ssoid:
        raise ValueError("ssoid not present in super_properties")
    return str(ssoid).strip()


def build_auth_payload(token: str, token_auth_id: str, ssoid: str) -> dict:
    return {"token": token, "token_auth_id": token_auth_id, "ssoid": ssoid}


def adb_run(args: list[str], serial: str | None, binary: str, timeout: int = 120) -> str:
    command = [binary] + (["-s", serial] if serial else []) + args
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SystemExit(f"adb not found: {binary}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise SystemExit(f"adb {' '.join(args)} failed: {detail}")
    return proc.stdout


def read_root_file(path: str, serial: str | None, binary: str) -> str:
    quoted = path.replace("'", "'\\''")
    return adb_run(["shell", f"su -c 'cat {quoted}'"], serial, binary)


def find_device_file(name: str, serial: str | None, binary: str) -> str:
    script = (
        "for d in " + " ".join(CANDIDATE_DIRS) + "; do "
        f'[ -f "$d/{name}" ] && echo "$d/{name}"; done'
    )
    output = adb_run(["shell", f"su -c '{script}'"], serial, binary)
    candidates = [line.strip() for line in output.splitlines() if line.strip()]
    if not candidates:
        raise SystemExit(f"{name} not found on device; is the app installed and su working?")
    return candidates[0]


def describe(payload: dict) -> str:
    """Human summary that never prints the token itself."""
    lines = [
        f"token: {len(payload['token'])} chars",
        f"token_auth_id: {len(payload['token_auth_id'])} chars",
        f"ssoid: {payload['ssoid']}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", help="adb serial, e.g. 192.168.1.23:41234")
    parser.add_argument("--adb", default=os.environ.get("ADB", "adb"), help="path to adb binary")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.json"))
    parser.add_argument("--auth-properties-file", help="parse a local copy instead of reading the phone")
    parser.add_argument("--sensors-file", help="local copy of the SensorsData prefs XML")
    parser.add_argument("--verify", action="store_true", help="call cloud_status() after writing auth.json")
    args = parser.parse_args(argv)

    if args.auth_properties_file:
        with open(args.auth_properties_file, encoding="utf-8") as handle:
            properties_text = handle.read()
    else:
        auth_path = find_device_file(AUTH_FILE_NAME, args.serial, args.adb)
        properties_text = read_root_file(auth_path, args.serial, args.adb)

    if args.sensors_file:
        with open(args.sensors_file, encoding="utf-8") as handle:
            sensors_text = handle.read()
    else:
        sensors_path = find_device_file(SENSORS_PREFS, args.serial, args.adb)
        sensors_text = read_root_file(sensors_path, args.serial, args.adb)

    parsed = parse_auth_properties(properties_text)
    ssoid = parse_ssoid(sensors_text)
    payload = build_auth_payload(parsed["token"], parsed["token_auth_id"], ssoid)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.chmod(args.out, 0o600)

    print(f"wrote {args.out}")
    print(describe(payload))
    expires = parsed.get("access_token_exp")
    if expires:
        when = datetime.datetime.fromtimestamp(expires / 1000).astimezone()
        print(f"accessTokenExp: {when.isoformat()}")

    if args.verify:
        os.environ.setdefault("OPPO_HEALTH_AUTH_FILE", os.path.abspath(args.out))
        import cloud_mcp

        status = cloud_mcp.cloud_status()
        print(json.dumps({"auth_ready": status.get("auth_ready")}, ensure_ascii=False))
        if not status.get("auth_ready"):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
