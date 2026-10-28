import json

import pytest

from gen_auth import parse_auth_properties, parse_ssoid


def test_parse_auth_properties_handles_single_line_escaped_json():
    raw = (
        '1000000001={"accessToken"\\:\\"ACCESS_abc123\\",'
        '\\"deviceId\\":\\"device-xyz\\",\\"accessTokenExp\\":1795716835220}'
    )

    parsed = parse_auth_properties(raw)

    assert parsed["token"] == "ACCESS_abc123"
    assert parsed["token_auth_id"] == "device-xyz"
    assert parsed["access_token_exp"] == 1795716835220


def test_parse_auth_properties_handles_line_per_key_format():
    raw = "\n".join([
        "accessToken=ACCESS_legacy",
        "deviceId=device-legacy",
        "accessTokenExp=1795716835220",
    ])

    parsed = parse_auth_properties(raw)

    assert parsed["token"] == "ACCESS_legacy"
    assert parsed["token_auth_id"] == "device-legacy"


def test_parse_auth_properties_rejects_missing_fields():
    with pytest.raises(ValueError):
        parse_auth_properties("accessToken=only-token")


def test_parse_ssoid_reads_sensors_super_properties():
    xml = (
        '<map>\n'
        '  <string name="super_properties">'
        '{&quot;ssoid&quot;:&quot;1000000001&quot;,&quot;app_id&quot;:&quot;x&quot;}'
        '</string>\n'
        '</map>'
    )

    assert parse_ssoid(xml) == "1000000001"


def test_parse_ssoid_rejects_missing_value():
    with pytest.raises(ValueError):
        parse_ssoid("<map></map>")


def test_build_auth_payload_shape():
    from gen_auth import build_auth_payload

    payload = build_auth_payload("ACCESS_x", "device-y", "1000000001")

    assert payload == {
        "token": "ACCESS_x",
        "token_auth_id": "device-y",
        "ssoid": "1000000001",
    }
    assert json.dumps(payload)  # serialisable
