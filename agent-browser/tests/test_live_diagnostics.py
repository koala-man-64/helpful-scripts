"""Live diagnostics exercise only a same-origin loopback fixture, never an account or external host."""
from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from agent_browser_core import common
from agent_browser_core import diagnostics
from test_live_expansion import PROFILE, cli, live_home, site, pytestmark


def test_goto_receipt_keeps_redacted_errors_and_sanitized_trace(cli, site):
    page_url = f"{site.url}/audit_diagnostics.html"
    result = cli.ok("goto", page_url, "--trace")
    assert result["diagnostics"]["event_count"] > 0
    assert result["trace_file"] and Path(result["trace_file"]).is_file()

    saved = cli.ok("output", "--id", "r" + result["command_id"])
    receipt = json.loads(saved["chunk"])
    outcome = json.loads(receipt["outcome"])
    events = outcome["diagnostics"]["events"]
    rendered = json.dumps(events)
    for secret in ("hunter2", "123456", "Bearer abc", "short-error", "token=short"):
        assert secret not in rendered
    assert any(event.get("kind") in {"console_error", "page_error"} for event in events)
    assert any(event.get("status") == 404 for event in events)
    assert "audit-diagnostic-request" in rendered
    assert "?token=" not in rendered

    trace_path = Path(result["trace_file"])
    assert trace_path.is_relative_to(common.ProfilePaths(PROFILE, cli.home).outputs)
    with zipfile.ZipFile(trace_path) as archive:
        assert archive.namelist() == ["trace.trace"]
        trace_data = archive.read("trace.trace")
    assert b"dom-secret-value" not in trace_data
    assert b"hunter2" not in trace_data
    assert outcome["trace_file"] == result["trace_file"]


def test_sanitized_trace_loads_into_installed_playwright_trace_model(tmp_path):
    try:
        import playwright
    except ImportError:
        pytest.skip("Playwright package is unavailable")
    driver = Path(playwright.__file__).parent / "driver"
    node = driver / ("node.exe" if os.name == "nt" else "node")
    bundle = driver / "package" / "lib" / "coreBundle.js"
    if not node.is_file() or not bundle.is_file():
        pytest.skip("Installed Playwright trace model is unavailable")

    raw_path = tmp_path / "raw.zip"
    safe_path = tmp_path / "safe.zip"
    unpacked = tmp_path / "unpacked"
    events = [
        {"version": 8, "type": "context-options", "origin": "library", "options": {}, "contextId": ""},
        {"type": "before", "callId": "call@1", "startTime": 20.0, "class": "Locator", "method": "fill", "pageId": "page@1"},
        {"type": "after", "callId": "call@1", "endTime": 28.0},
    ]
    with zipfile.ZipFile(raw_path, "w") as archive:
        archive.writestr("trace.trace", "".join(json.dumps(event) + "\n" for event in events))
    safe_path.write_bytes(diagnostics.sanitize_trace_archive(raw_path))
    script = r"""
const core = require(process.argv[1]);
(async () => {
  await core.tools.extractTrace(process.argv[2], process.argv[3]);
  const loader = new core.iso.TraceLoader();
  await loader.load(new core.tools.DirTraceLoaderBackend(process.argv[3]));
  const model = new core.iso.TraceModel(process.argv[2], loader.contextEntries);
  if (model.actions.length !== 1 || model.actions[0].method !== 'fill' || model.actions[0].endTime !== 28)
    throw new Error('Trace Viewer model did not recover the action timing');
})().catch(error => { console.error(error.stack || error); process.exit(1); });
"""
    checked = subprocess.run([str(node), "-e", script, str(bundle), str(safe_path), str(unpacked)],
                             capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    assert checked.returncode == 0, checked.stderr or checked.stdout
