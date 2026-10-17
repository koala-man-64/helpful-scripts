"""Agent-facing contracts and recovery behavior."""
import argparse
import json

import pytest

import agent_browser as ab


def test_full_encoded_reply_is_bounded_and_recoverable():
    original = {"value": "🌍\\\"" * 14000}
    encoded = ab._json_line(original)
    assert len((encoded + "\n").encode("utf-8")) <= ab.STDOUT_BUDGET
    assert "\n" not in encoded
    receipt = json.loads(encoded)
    assert receipt["contract_version"] == 1 and receipt["truncated"]
    # A later output must not invalidate this paging ID.
    ab._json_line({"value": "other" * 10000})
    offset, chunks = 0, []
    while offset is not None:
        page = ab._cmd_output(argparse.Namespace(profile=None, id=receipt["output_id"], offset=offset))
        assert len(ab._json_line(page).encode("utf-8")) < ab.STDOUT_BUDGET
        chunks.append(page["chunk"])
        offset = page["next_offset"]
    assert json.loads("".join(chunks))["value"] == original["value"]


def test_oversized_error_stays_json():
    encoded = ab._json_line({"error": {"class": "validation", "message": "x" * 50000}})
    assert json.loads(encoded)["error"]["class"] == "validation"
    assert len(encoded.encode()) < ab.STDOUT_BUDGET


def test_file_argument_conflict_is_rejected_before_browser():
    args = argparse.Namespace(text="inline", text_file="file.txt", profile=None)
    with pytest.raises(ValueError, match="exactly one"):
        ab._input_text(args, "text")


def test_telemetry_distinguishes_network_and_http_failures(make_daemon):
    daemon, context, page = make_daemon()
    response = type("Response", (), {"status": 503})()
    page.emit("response", response)
    page.emit("requestfailed", object())
    assert daemon.telemetry["http_errors"] == 1
    assert daemon.telemetry["failed_requests"] == 1
    result = daemon.dispatch("ping", {}, 1)
    assert result["observed"]["http_errors"] == 0
