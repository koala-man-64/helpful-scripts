"""Named page addresses shared by the sessions of one browser profile."""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import parse_qsl, unquote, urlsplit

from . import common, policy, session


def validate_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120 or not value.isprintable():
        raise common._tag(ValueError("Use a bookmark name of 1 to 120 printable characters."), common.ERR_VALIDATION)
    return value.strip()


def _private_address(url: str, depth: int = 0) -> bool:
    """Check even the URL parts the display redactor drops, including userinfo and hash routes in redirects."""
    if depth > common.NESTED_URL_DEPTH:
        return True
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    if parts.username is not None or common.MASK in common.redact_path(unquote(parts.path)):
        return True
    fragment = unquote(parts.fragment)
    # Hash routers use path?query, while OAuth callbacks can put key=value pairs directly after '#'.
    for query in (parts.query, fragment):
        for key, value in parse_qsl(query, keep_blank_values=True):
            if common.MASK in common._redact_value(key, value, depth):
                return True
            nested = unquote(value).lstrip()
            if nested.lower().startswith(("/", "http://", "https://")) and _private_address(nested, depth + 1):
                return True
    return bool(fragment) and _private_address(fragment, depth + 1)


def _bookmark_url(url: str, paths: common.ProfilePaths) -> str:
    config = policy.profile_config(common._load_config(paths.root), paths.profile)
    url = common._check_url(url, paths, config)
    if urlsplit(url).scheme not in common.ALLOWED_SCHEMES:
        raise common._tag(ValueError("Bookmarks require an HTTP or HTTPS page."), common.ERR_VALIDATION)
    if _private_address(url):
        raise common._tag(ValueError("This address contains credentials or a masked value. Bookmark a clean page URL."), common.ERR_GUARDED)
    return url


class BookmarkStore:
    def __init__(self, paths: common.ProfilePaths):
        self.paths = paths
        self.file = paths.dir / common.BOOKMARKS_FILE

    @classmethod
    def for_caller(cls, profile: str | None, session_id: str | None) -> BookmarkStore:
        paths = common.ProfilePaths(session._resolve_profile(profile), common._home(), common.session_identity(session_id))
        common._check_profile_path_length(paths)
        return cls(paths)

    def _read(self) -> dict[str, str]:
        try:
            entries = json.loads(self.file.read_text(encoding="utf-8"))
            if not isinstance(entries, dict) or not all(isinstance(url, str) for url in entries.values()):
                raise ValueError("Invalid bookmark entries")
        except FileNotFoundError:
            return {}
        except ValueError as exc:
            raise common._tag(RuntimeError("The profile's bookmarks file is invalid."), common.ERR_IO) from exc
        return entries

    @contextmanager
    def _edit(self) -> Iterator[dict[str, str]]:
        # Serialize the whole read/modify/write with other clients and with profile purge.
        with common._profile_locks(self.paths.admission_lock) as acquired:
            if not acquired:
                raise common._tag(RuntimeError("The profile is busy. Retry the bookmark change."), common.ERR_BUSY)
            entries = self._read()
            yield entries
            session._write_json_atomic(self.file, entries)

    def list(self) -> dict[str, Any]:
        entries = self._read()
        return {"profile": self.paths.profile, "bookmarks": [
            {"name": name, "url": common.redact_url(entries[name])} for name in sorted(entries)
        ], "untrusted": ["bookmarks"]}

    def add(self, name: str, url: str, *, replace: bool = False) -> dict[str, Any]:
        name = validate_name(name)
        if type(replace) is not bool:
            raise common._tag(ValueError("replace must be a boolean."), common.ERR_VALIDATION)
        url = _bookmark_url(url, self.paths)
        with self._edit() as entries:
            if name in entries and entries[name] != url and not replace:
                raise common._tag(ValueError("That bookmark name already points to another URL. Use --replace to update it."), common.ERR_VALIDATION)
            entries[name] = url
        return {"profile": self.paths.profile, "bookmark": {"name": name, "url": common.redact_url(url)}, "untrusted": ["bookmark"]}

    def resolve(self, name: str) -> str:
        name = validate_name(name)
        url = self._read().get(name)
        if url is None:
            raise common._tag(ValueError("Bookmark not found in this profile."), common.ERR_NOT_FOUND)
        return _bookmark_url(url, self.paths)

    def remove(self, name: str) -> dict[str, Any]:
        name = validate_name(name)
        with self._edit() as entries:
            if name not in entries:
                raise common._tag(ValueError("Bookmark not found in this profile."), common.ERR_NOT_FOUND)
            del entries[name]
        return {"profile": self.paths.profile, "removed": name}
