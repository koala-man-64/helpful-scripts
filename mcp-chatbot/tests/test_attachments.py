from __future__ import annotations

import base64
from pathlib import Path

import pytest

from mcp_chatbot import attachments

PNG_BYTES = b"\x89PNG\r\n\x1a\nfakepngdata"


def test_text_file_inlined_with_filename_header(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("hello world", encoding="utf-8")
    content, text, meta = attachments.build_user_content("Q", [str(f)], "responses")
    assert content == text
    assert text.startswith("Q\n\n--- file: notes.txt ---\n")
    assert "hello world" in text
    assert text.endswith("--- end file ---")
    assert meta == [{"path": str(f.resolve()), "kind": "text"}]


def test_multiple_text_files_all_inlined(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    a.write_text("print('a')", encoding="utf-8")
    b = tmp_path / "b.md"
    b.write_text("# b", encoding="utf-8")
    _, text, meta = attachments.build_user_content("Q", [str(a), str(b)], "chat")
    assert "--- file: a.py ---" in text
    assert "--- file: b.md ---" in text
    assert [m["kind"] for m in meta] == ["text", "text"]


def test_images_become_data_urls_with_correct_mime(tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(PNG_BYTES)
    jpg = tmp_path / "b.JPG"
    jpg.write_bytes(b"\xff\xd8\xff-jpeg")
    content, text, meta = attachments.build_user_content("look", [str(png), str(jpg)], "responses")
    assert text == "look"
    assert isinstance(content, list)
    assert content[0] == {"type": "input_text", "text": "look"}
    assert content[1]["type"] == "input_image"
    url = content[1]["image_url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG_BYTES
    assert content[2]["image_url"].startswith("data:image/jpeg;base64,")
    assert [m["kind"] for m in meta] == ["image", "image"]


def test_chat_shape_uses_nested_image_url(tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(PNG_BYTES)
    content, _, _ = attachments.build_user_content("look", [str(png)], "chat")
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "look"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_no_attachments_returns_plain_prompt() -> None:
    content, text, meta = attachments.build_user_content("just text", None, "responses")
    assert content == "just text"
    assert text == "just text"
    assert meta == []


def test_missing_file_raises() -> None:
    with pytest.raises(ValueError, match="Attachment not found"):
        attachments.build_user_content("Q", ["does/not/exist.txt"], "chat")


def test_binary_non_image_raises(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\xff\xfe\x00binary")
    with pytest.raises(ValueError, match="PDFs are not supported"):
        attachments.build_user_content("Q", [str(pdf)], "chat")


def test_oversized_attachment_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_BYTES", 4)
    f = tmp_path / "big.txt"
    f.write_text("12345", encoding="utf-8")
    with pytest.raises(ValueError, match="too large"):
        attachments.classify(str(f))


def test_relative_paths_stored_as_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A relative path valid at attach time must not break replay after the
    # server is relaunched from a different cwd.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel.png").write_bytes(PNG_BYTES)
    _, _, meta = attachments.build_user_content("q", ["rel.png"], "responses")
    assert meta == [{"path": str((tmp_path / "rel.png").resolve()), "kind": "image"}]


def test_missing_image_on_reencode_gives_clear_error(tmp_path: Path) -> None:
    # Hit when replaying a transcript whose image was deleted since attach time.
    with pytest.raises(ValueError, match="could not be read"):
        attachments.image_data_url(tmp_path / "gone.png")
