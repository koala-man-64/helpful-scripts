"""Attachment handling: inline text files and encode images for vision input.

Two API shapes are produced because the part formats differ:
  - "responses" (Responses API and Foundry agents): input_text / input_image,
    where image_url is a plain data-URL string.
  - "chat" (Chat Completions): text / image_url, where image_url is a nested
    object ``{"url": <data-url>}``.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def classify(path_str: str) -> tuple[str, Path]:
    """Return ("image" | "text", path) for an attachment, validating existence and size.

    The returned path is resolved to absolute so it stays valid in stored
    transcripts even if the server is later relaunched from a different cwd.
    """
    path = Path(path_str)
    if not path.is_file():
        raise ValueError(f"Attachment not found: {path_str}")
    if path.stat().st_size > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"Attachment too large (over {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB): {path_str}"
        )
    path = path.resolve()
    kind = "image" if path.suffix.lower() in _IMAGE_MIME else "text"
    return kind, path


def image_data_url(path: Path) -> str:
    mime = _IMAGE_MIME[path.suffix.lower()]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        # Hit on replay when a stored attachment was deleted or moved since it
        # was attached; without this the conversation dies with a raw traceback.
        raise ValueError(
            f"Attachment {path} could not be read ({exc}). It may have been moved or "
            "deleted since it was attached; restore the file or delete the conversation."
        ) from exc
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def inline_text(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Attachment {path} is not UTF-8 text or a supported image "
            f"({', '.join(sorted(_IMAGE_MIME))}); PDFs are not supported."
        ) from exc
    return f"--- file: {path.name} ---\n{content}\n--- end file ---"


def assemble_content(text: str, image_paths: list[str], shape: str) -> str | list[dict[str, Any]]:
    """Build message content for the given API shape; plain string when no images."""
    if not image_paths:
        return text
    urls = [image_data_url(Path(p)) for p in image_paths]
    if shape == "responses":
        parts: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        parts.extend({"type": "input_image", "image_url": url} for url in urls)
    else:
        parts = [{"type": "text", "text": text}]
        parts.extend({"type": "image_url", "image_url": {"url": url}} for url in urls)
    return parts


def build_user_content(
    prompt: str, attachment_paths: list[str] | None, shape: str
) -> tuple[str | list[dict[str, Any]], str, list[dict[str, str]]]:
    """Turn a prompt plus attachment paths into API content.

    Returns (content, transcript_text, attachment_meta): ``content`` is what the
    API receives; ``transcript_text`` is the prompt with text files inlined (what
    the conversation store keeps - never base64); ``attachment_meta`` records
    ``{"path", "kind"}`` per attachment so images can be re-encoded on replay.
    """
    text_parts = [prompt]
    image_paths: list[str] = []
    meta: list[dict[str, str]] = []
    for raw in attachment_paths or []:
        kind, path = classify(raw)
        meta.append({"path": str(path), "kind": kind})
        if kind == "text":
            text_parts.append(inline_text(path))
        else:
            image_paths.append(str(path))
    text = "\n\n".join(text_parts)
    return assemble_content(text, image_paths, shape), text, meta
