"""Same-origin requests using the browser's cookie jar. Secrets stay in this process."""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any
from . import common, policy

SAFE_HEADERS = frozenset({"accept", "content-type", "if-match", "if-none-match", "range"})


def body_headers(content_type: str | None = None) -> dict[str, str]:
    """Headers for a request with a file body. CLI --json-file and MCP browser_commit both send exactly these."""
    if content_type is not None and (not content_type.strip() or any(ord(c) < 32 or ord(c) == 127 for c in content_type)):
        raise common._tag(ValueError("content_type must be a non-empty single-line header value."), common.ERR_VALIDATION)
    return {"Accept": "application/json", "Content-Type": content_type or "application/json"}


def public_headers(headers: dict[str, str]) -> dict[str, str]:
    blocked = ("cookie", "authorization", "token", "session", "g_ck", "location", "refresh", "authentication", "authenticate")
    return {str(k): str(v) for k, v in headers.items() if not any(s in k.lower() for s in blocked)}


def origin(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise common._tag(ValueError("API URL must have a credential-free HTTP(S) origin."), common.ERR_GUARDED)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise common._tag(ValueError("Invalid API origin port."), common.ERR_VALIDATION) from exc
    return parts.scheme, parts.hostname.lower(), port


class ApiMixin:
    def h_api(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        page = self.target_page(args)
        raw = str(args.get("url") or "")
        if not raw or raw.startswith("//"):
            raise common._tag(ValueError("API needs a same-origin path or URL."), common.ERR_USAGE)
        url = urllib.parse.urljoin(page.url, raw)
        if origin(url) != origin(page.url):
            raise common._tag(ValueError("API requests must stay on the current tab's exact origin."), common.ERR_GUARDED)
        common._check_url(url, self.paths, self.config)
        method = str(args.get("method", "GET")).upper()
        if method not in {"GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"}:
            raise common._tag(ValueError("Unsupported API method."), common.ERR_USAGE)
        policy.authorize(self.config, "api", {**args, "method": method})
        headers = args.get("headers") or {"Accept": "application/json"}
        if not isinstance(headers, dict) or any(not isinstance(k, str) or k.lower() not in SAFE_HEADERS or not isinstance(v, str) or "\n" in v or "\r" in v for k, v in headers.items()):
            raise common._tag(ValueError("API headers may only contain Accept, Content-Type, conditional headers and Range."), common.ERR_GUARDED)
        headers = dict(headers)
        if args.get("servicenow"):
            session_key = None
            for frame in page.frames:
                try:
                    if origin(frame.url or page.url) != origin(page.url): continue
                    value = frame.evaluate("() => typeof window.g_ck === 'string' ? window.g_ck : null")
                except Exception:
                    continue
                if isinstance(value, str) and value and len(value) < 4096:
                    session_key = value
                    break
            if session_key is None:
                raise common._tag(RuntimeError("The signed-in ServiceNow page has no usable session token. Request a human sign-in handoff; browser authentication is not qualified for this page."), "unsupported_browser_auth")
            headers["X-UserToken"] = session_key
        maximum = args.get("max_response_bytes", 8 * 1024 * 1024)
        if type(maximum) is not int or not 1 <= maximum <= 100 * 1024 * 1024:
            raise common._tag(ValueError("Response limit must be between 1 byte and 100 MiB."), common.ERR_VALIDATION)
        body = None
        if args.get("body_file"):
            body_path = self._api_path(str(args["body_file"]), args)
            if body_path.stat().st_size > 100 * 1024 * 1024:
                raise common._tag(ValueError("API request body exceeds 100 MiB."), common.ERR_VALIDATION)
            body = body_path.read_bytes()
        if method in {"GET", "HEAD"} and body is not None:
            raise common._tag(ValueError("GET and HEAD requests cannot carry a body."), common.ERR_USAGE)
        self.action_attempted = method not in {"GET", "HEAD"}
        response = None
        try:
            response = self.ctx.request.fetch(url, method=method, headers=headers, data=body,
                                              timeout=max(timeout, .5) * 1000, max_redirects=0, max_retries=0)
            status, response_headers = response.status, public_headers(response.headers)
            if status >= 400:
                self._observe("http_errors", page)
            data = response.body()
            if len(data) > maximum:
                raise common._tag(RuntimeError("API response exceeded the requested byte limit."), "response_too_large")
            self.response_cursor += 1
            self.response_events.append((self.response_cursor, page, method, url, status))
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            # Playwright error strings can contain request headers. Never propagate those strings.
            self._observe("failed_requests", page)
            raise common._tag(RuntimeError("Browser API transport failed; inspect the receipt before repeating a write."), "unavailable") from None
        finally:
            if response is not None: response.dispose()
        result = {"status": status, "headers": response_headers, "url": common.redact_url(url), "effect": "request_completed"}
        if method not in {"GET", "HEAD"} and not 200 <= status < 300:
            result["write_outcome"] = "unknown"
        if status in {301, 302, 303, 307, 308, 401, 403}:
            handoff = self.journal.handoff(self.session_id, "Browser API authentication requires human verification", {
                "signed_in": origin(page.url)[1], "require_navigation": True, "navigation": self.nav_counts.get(page.main_frame, 0)})
            result.update(needs_human=True, handoff_id=handoff)
            data = b"Authentication requires human verification."
        if args.get("save_response"):
            self.journal.require_space(len(data))
            self.paths.outputs.mkdir(parents=True, exist_ok=True)
            target = self.paths.outputs / (args["_command_id"] + ".body")
            target.write_bytes(data)
            result["body_file"] = str(target)
        else:
            try: result["body"] = json.loads(data)
            except (ValueError, UnicodeError): result["body"] = data.decode("utf-8", errors="replace")
        result["untrusted"] = ["body", "url", "headers"]
        return result

    def _api_path(self, candidate: str, args: dict[str, Any], must_exist: bool = True) -> Path:
        # File paths are checked against the caller's working directory, never the daemon's own cwd (the profile folder).
        cwd = None
        if args.get("_cwd"):
            cwd = Path(str(args["_cwd"]))
            if not cwd.is_absolute(): raise common._tag(ValueError("Caller cwd must be absolute."), common.ERR_VALIDATION)
            if candidate.strip():
                path = Path(candidate.strip()).expanduser()
                if not path.is_absolute():
                    path = cwd / path
                    if not path.is_absolute():
                        raise common._tag(ValueError("File path cannot be resolved against the caller cwd."), common.ERR_VALIDATION)
                    candidate = str(path)
        return common._check_local_path(candidate, self.paths, self.config, must_exist, base=cwd)
