#!/usr/bin/env python3
"""Offline specification consistency checks; never imports or runs project clients."""
import json
from pathlib import Path
import re
import sys

def main():
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
    if len(blocks) != 1:
        print("FAIL: expected exactly one machine-readable protocol contract")
        return 1
    try:
        c = json.loads(blocks[0])
        assert c["schema_version"] == 1
        assert c["mode"] == "offline_observation_only"
        assert c["network_allowed"] is False
        assert c["automatic_submission_retry"] is False
        assert c["runtime_enforced"] is False
        assert c["sensitive_values_allowed"] is False
        states = set(c["states"])
        assert len(states) == len(c["states"])
        assert states == {"NEW", "OBSERVED", "PENDING_CONFIRM", "CONFIRMED", "CHECKPOINT", "RATE_LIMITED", "REJECTED", "UNKNOWN", "STOPPED"}
        edges = c["transitions"]
        assert set(edges) == states
        assert all(len(v) == len(set(v)) and set(v) <= states for v in edges.values())
        assert edges["STOPPED"] == []
        assert all(edges[s] == ["STOPPED"] for s in ("CHECKPOINT", "RATE_LIMITED", "REJECTED", "UNKNOWN", "CONFIRMED"))
        assert c["classification_priority"] == ["CHECKPOINT", "RATE_LIMITED", "REJECTED", "CONFIRMED", "PENDING_CONFIRM", "UNKNOWN"]
        assert c["confirmed_requires"] == ["explicit_confirmation_evidence", "authenticated_session_evidence"]
        assert c["success_insufficient"] == ["http_200", "http_302", "cookie_present", "account_id_present", "error_disappeared"]
        assert {"evidence_id", "source_ref", "source_sha256", "captured_at", "observed_at", "transport_status", "application_error", "parse_status", "state", "confidence", "redacted"} <= set(c["evidence_required"])
        assert c["confidence_values"] == ["recorded", "inferred", "unknown"]
        assert c["captured_at_unknown_value"] is None
        assert c["claim_status"] == {"legacy": "historical_record_only", "graphql_submit": "unresolved", "android_submit": "static_analysis_only", "bloks_confirmation": "unconfirmed"}
        names = set()
        for case in c["transition_cases"]:
            assert set(case) == {"name", "from", "to", "allowed"}
            assert case["name"] not in names
            names.add(case["name"])
            assert case["from"] in states and case["to"] in states
            assert type(case["allowed"]) is bool
            actual = case["to"] in edges[case["from"]]
            assert actual == case["allowed"], case["name"]
        assert len(names) >= 14
        for heading in ("证据分级", "结果判定", "会话与字段生命周期", "失败与停止", "敏感信息", "验收与未完成项"):
            assert heading in text, heading
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        print("FAIL: invalid protocol contract: " + str(exc))
        return 1
    print("PASS: protocol contract schema and safety invariants")
    print(f"PASS: {len(names)} transition cases; {len(states)} states")
    print("SCOPE: offline document checks only; runtime and live protocol not tested")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
