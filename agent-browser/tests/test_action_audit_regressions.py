"""Page ownership, caller paths and action deadlines found by the browser audit."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_browser_core import common
from conftest import FakeElement, FakeFrame, FakeLocator, FakePage


def _form_controls(daemon, ctx, page, monkeypatch, *, close_original=False):
    popup = FakePage(ctx, url="http://x/popup")
    inner = FakeFrame(name="form", url="http://x/form")
    page.frames.append(inner)
    controls = {
        (page.main_frame, "First"): FakeLocator(page, FakeElement("e1", tag="INPUT")),
        (inner, "Second"): FakeLocator(page, FakeElement("e2", tag="INPUT")),
        (popup.main_frame, "Second"): FakeLocator(popup, FakeElement("e3", tag="INPUT")),
    }

    def get_by_label(frame, label, *, exact):
        assert exact
        matches = [controls[(frame, label)]] if (frame, label) in controls else []
        return SimpleNamespace(count=lambda: len(matches), nth=lambda index: matches[index])

    monkeypatch.setattr(FakeFrame, "get_by_label", get_by_label, raising=False)
    original_evaluate, original_fill = FakeLocator.evaluate, FakeLocator.fill

    def evaluate(locator, js, timeout=None):
        if "readOnly:el.readOnly" in js:
            return {"tag": "input", "type": "text"}
        return original_evaluate(locator, js, timeout)

    def fill(locator, text, timeout=None):
        original_fill(locator, text, timeout)
        if locator.element is controls[(page.main_frame, "First")].element:
            ctx.pages.append(popup)
            ctx.emit("page", popup)
            if close_original:
                page.close()

    monkeypatch.setattr(FakeLocator, "evaluate", evaluate)
    monkeypatch.setattr(FakeLocator, "fill", fill)
    return controls, inner, popup


def test_form_keeps_original_page_and_iframe_when_first_field_opens_popup(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    controls, inner, popup = _form_controls(daemon, ctx, page, monkeypatch)
    result = daemon.dispatch("fill-form", {"fields": [
        {"target": {"label": "First"}, "value": "one"},
        {"target": {"label": "Second"}, "value": "two"},
    ]}, 30)
    assert result["completed_fields"] == [0, 1]
    assert controls[(inner, "Second")].element.value == "two"
    assert controls[(popup.main_frame, "Second")].element.value == ""
    assert result["tab_id"] == daemon._tab_id(page)


def test_form_stops_if_original_page_closes_without_filling_popup(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    controls, inner, popup = _form_controls(daemon, ctx, page, monkeypatch, close_original=True)
    with pytest.raises((ValueError, RuntimeError)) as error:
        daemon.dispatch("fill-form", {"fields": [
            {"target": {"label": "First"}, "value": "one"},
            {"target": {"label": "Second"}, "value": "two"},
        ]}, 30)
    assert error.value.details["completed_fields"] == [0]
    assert error.value.details["executed"]
    assert controls[(inner, "Second")].element.value == ""
    assert controls[(popup.main_frame, "Second")].element.value == ""


@pytest.mark.parametrize("operation", ["api", "upload"])
@pytest.mark.parametrize("caller_exists", [True, False])
def test_relative_transfer_paths_use_caller_directory_only(make_daemon, tmp_path, monkeypatch, operation, caller_exists):
    daemon, ctx, page = make_daemon(snapshot_text='- button "Upload" [ref=e2]\n')
    page.elements["e2"].is_file_input = True
    daemon_dir, caller_dir = tmp_path / "daemon", tmp_path / "caller"
    daemon_dir.mkdir()
    caller_dir.mkdir()
    (daemon_dir / "payload.json").write_bytes(b"wrong")
    if caller_exists:
        (caller_dir / "payload.json").write_bytes(b"requested")
    monkeypatch.chdir(daemon_dir)
    calls = []
    response = SimpleNamespace(status=200, headers={}, body=lambda: b"{}", dispose=lambda: None)
    ctx.request = SimpleNamespace(fetch=lambda url, **kwargs: calls.append(kwargs) or response)
    args = {"_cwd": str(caller_dir)}
    if operation == "api":
        args.update(operation="api", url="/items", method="POST", body_file="payload.json")
        command = "commit"
    else:
        args.update(target="e2", files=["payload.json"])
        command = "upload"
    if not caller_exists:
        with pytest.raises(ValueError) as error:
            daemon.dispatch(command, args, 5)
        assert error.value.error_class == common.ERR_NOT_FOUND
        assert not calls and not page.elements["e2"].calls
    else:
        result = daemon.dispatch(command, args, 5)
        if operation == "api":
            assert calls[0]["data"] == b"requested"
        else:
            assert result["files"] == [str(caller_dir / "payload.json").replace("\\", "/")]


def test_api_path_rejects_relative_caller_cwd(make_daemon):
    daemon, _, _ = make_daemon()
    with pytest.raises(ValueError) as error:
        daemon._api_path("payload.json", {"_cwd": "relative"})
    assert error.value.error_class == common.ERR_VALIDATION


@pytest.mark.parametrize("operation", ["upload", "api", "screenshot"])
@pytest.mark.parametrize("name", ["session.json", "launch.json", "user-data/Default/Cookies"])
def test_daemon_cwd_in_profile_dir_does_not_expose_token_files(make_daemon, tmp_path, monkeypatch, operation, name):
    daemon, ctx, page = make_daemon(snapshot_text='- button "Upload" [ref=e2]\n')
    page.elements["e2"].is_file_input = True
    store_file = daemon.paths.dir / name
    store_file.parent.mkdir(parents=True, exist_ok=True)
    store_file.write_text('{"token": "daemon-token"}', encoding="utf-8")
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(daemon.paths.dir)  # the daemon's real cwd
    calls = []
    ctx.request = SimpleNamespace(fetch=lambda url, **kwargs: calls.append(kwargs))
    args = {"_cwd": str(caller)}
    # A screenshot into an existing file already fails ("already exists"), so aim it at a new .png in the store.
    shot = store_file.with_suffix(".png")
    if operation == "upload":
        command = "upload"
        args.update(target="e2", files=[str(store_file)])
    elif operation == "api":
        command = "commit"
        args.update(operation="api", url="/items", method="POST", body_file=str(store_file))
    else:
        command = "screenshot"
        args.update(path=str(shot))
    with pytest.raises(ValueError) as error:
        daemon.dispatch(command, args, 5)
    assert error.value.error_class == common.ERR_GUARDED
    assert "agent-browser state folder" in str(error.value)
    assert not calls and not page.elements["e2"].calls
    assert not shot.exists()


@pytest.mark.parametrize("cwd", ["store", 5])
def test_caller_cwd_inside_the_store_or_not_a_string_is_refused(make_daemon, monkeypatch, cwd):
    daemon, _, page = make_daemon(snapshot_text='- button "Upload" [ref=e2]\n')
    page.elements["e2"].is_file_input = True
    daemon.paths.session.write_text('{"token": "daemon-token"}', encoding="utf-8")
    monkeypatch.chdir(daemon.paths.dir)
    args = {"target": "e2", "files": ["session.json"], "_cwd": str(daemon.paths.dir) if cwd == "store" else cwd}
    with pytest.raises(ValueError) as error:
        daemon.dispatch("upload", args, 5)
    if cwd == "store":
        assert error.value.error_class == common.ERR_GUARDED and "agent-browser state folder" in str(error.value)
    else:
        assert error.value.error_class == common.ERR_VALIDATION
    assert not page.elements["e2"].calls


def test_transfer_without_caller_cwd_never_uses_daemon_cwd(make_daemon, tmp_path, monkeypatch):
    daemon, _, page = make_daemon(snapshot_text='- button "Upload" [ref=e2]\n')
    page.elements["e2"].is_file_input = True
    workdir = tmp_path / "daemon-cwd"
    workdir.mkdir()
    (workdir / "payload.json").write_bytes(b"{}")
    monkeypatch.chdir(workdir)
    with pytest.raises(ValueError) as error:
        daemon.dispatch("upload", {"target": "e2", "files": ["payload.json"]}, 5)
    assert error.value.error_class == common.ERR_VALIDATION
    assert not error.value.details["executed"]
    with pytest.raises(ValueError) as error:
        daemon.dispatch("upload", {"target": "e2", "files": [str(workdir / "payload.json")]}, 5)
    assert error.value.error_class == common.ERR_GUARDED
    assert not page.elements["e2"].calls
    staged = daemon.paths.downloads / "payload.json"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"{}")
    result = daemon.dispatch("upload", {"target": "e2", "files": [str(staged)]}, 5)
    assert result["files"] == [str(staged).replace("\\", "/")]


@pytest.mark.parametrize("cwd", [None, "relative"])
def test_screenshot_relative_path_never_resolves_against_daemon_cwd(make_daemon, tmp_path, monkeypatch, cwd):
    daemon, _, _ = make_daemon()
    (tmp_path / "relative").mkdir()
    monkeypatch.chdir(tmp_path)
    args = {"path": "shot.png", **({"_cwd": cwd} if cwd else {})}
    with pytest.raises(ValueError) as error:
        daemon.dispatch("screenshot", args, 5)
    assert error.value.error_class == common.ERR_VALIDATION
    assert not list(tmp_path.rglob("shot.png"))


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-relative paths")
@pytest.mark.parametrize("operation", ["api", "upload"])
def test_transfer_rejects_other_drive_relative_path_before_file_lookup(make_daemon, monkeypatch, operation):
    daemon, _, _ = make_daemon(snapshot_text='- button "Upload" [ref=e2]\n')

    def unexpected_lookup(*_args, **_kwargs):
        pytest.fail("An incompatible drive-relative path reached file lookup")

    monkeypatch.setattr(common, "_check_local_path", unexpected_lookup)
    args = {"_cwd": "C:/caller"}
    if operation == "api":
        command = "commit"
        args.update(operation="api", url="/items", method="POST", body_file="D:payload.json")
    else:
        command = "upload"
        args.update(target="e2", files=["D:payload.json"])
    with pytest.raises(ValueError) as error:
        daemon.dispatch(command, args, 5)
    assert error.value.error_class == common.ERR_VALIDATION
    assert not error.value.details["executed"]


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-relative paths")
@pytest.mark.parametrize(("candidate", "expected"), [
    ("payload.json", "C:/caller/payload.json"),
    ("C:payload.json", "C:/caller/payload.json"),
    ("D:/payload.json", "D:/payload.json"),
])
def test_api_path_preserves_same_drive_relative_and_absolute_paths(make_daemon, monkeypatch, candidate, expected):
    daemon, _, _ = make_daemon()
    monkeypatch.setattr(common, "_check_local_path", lambda value, *_, **__: Path(value))
    assert daemon._api_path(candidate, {"_cwd": "C:/caller"}) == Path(expected)


def test_expectation_cannot_succeed_when_first_observed_after_deadline(make_daemon, fake_clock, monkeypatch):
    daemon, _, page = make_daemon(snapshot_text='- button "Save" [ref=e2]\n')
    visible = {"saved": False}
    monkeypatch.setattr(daemon, "_page_has_text", lambda *_: visible["saved"])
    result = daemon.dispatch("click", {"target": "e2", "expect": ["text:Saved"]}, 10)
    assert result["pending"]
    fake_clock.value += 20
    visible["saved"] = True
    with pytest.raises(RuntimeError) as error:
        daemon.jobs[result["command_id"]]["checker"]()
    assert error.value.error_class == common.ERR_TIMEOUT
    assert error.value.details["executed"]
    assert daemon.journal.rows("manual", result["command_id"])[0]["status"] == "failed"


def test_expectation_observation_that_crosses_deadline_cannot_succeed(make_daemon, fake_clock, monkeypatch):
    daemon, _, page = make_daemon(snapshot_text='- button "Save" [ref=e2]\n')
    slow = {"enabled": False}

    def has_text(*_):
        if slow["enabled"]:
            fake_clock.value += 20
            return True
        return False

    monkeypatch.setattr(daemon, "_page_has_text", has_text)
    result = daemon.dispatch("click", {"target": "e2", "expect": ["text:Saved"]}, 10)
    assert result["pending"]
    slow["enabled"] = True
    with pytest.raises(RuntimeError) as error:
        daemon.jobs[result["command_id"]]["checker"]()
    assert error.value.error_class == common.ERR_TIMEOUT
    assert error.value.details["executed"]
    assert daemon.journal.rows("manual", result["command_id"])[0]["status"] == "failed"
