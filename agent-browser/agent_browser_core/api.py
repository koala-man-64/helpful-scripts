"""Same-origin requests using the browser's cookie jar. Secrets stay in this process."""
from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any, Iterable
from . import common, policy

SAFE_HEADERS = frozenset({"accept", "content-type", "if-match", "if-none-match", "range"})


def body_headers(content_type: str | None = None) -> dict[str, str]:
    """Headers for a request with a file body. CLI --json-file and MCP browser_commit both send exactly these."""
    if content_type is not None and (not content_type.strip() or any(ord(c) < 32 or ord(c) == 127 for c in content_type)):
        raise common._tag(ValueError("content_type must be a non-empty single-line header value."), common.ERR_VALIDATION)
    return {"Accept": "application/json", "Content-Type": content_type or "application/json"}


# Response headers never returned: a name holding any of these words or ending in "key" (x-api-key,
# ocp-apim-subscription-key), and the redirect pair (Location is read separately, only to classify a redirect).
CREDENTIAL_HEADER_WORDS = ("cookie", "auth", "token", "session", "g_ck", "g-ck", "secret", "csrf", "xsrf", "password", "credential")
REDIRECT_HEADERS = frozenset({"location", "refresh"})
LINK_TARGET_RE = re.compile(r"<([^>]*)>")


def public_headers(headers: dict[str, str]) -> dict[str, str]:
    """The response headers a caller sees: credential-named and redirect headers dropped, the rest single-line and
    without invisible characters, with their URLs redacted (Content-Location and Link targets, relative ones too)."""
    out = {}
    for key, value in headers.items():
        lower = common.strip_invisible(key).lower()
        if lower in REDIRECT_HEADERS or lower.endswith("key") or any(word in lower for word in CREDENTIAL_HEADER_WORDS):
            continue
        text = str(value)
        if lower == "content-location":
            text = common.redact_url(text)
        elif lower == "link":
            text = LINK_TARGET_RE.sub(lambda m: "<" + common.redact_url(m.group(1)) + ">", text)
        else:
            text = common.redact_text(text)
        # Link lists every pagination target, so it gets a larger cap than other values.
        out[common.sanitize_text(key, 100, single_line=True)] = common.sanitize_text(text, 4000 if lower == "link" else 1000, single_line=True)
    return out


def origin(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise common._tag(ValueError("API URL must have a credential-free HTTP(S) origin."), common.ERR_GUARDED)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise common._tag(ValueError("Invalid API origin port."), common.ERR_VALIDATION) from exc
    return parts.scheme, parts.hostname.lower(), port


def is_redirect(status: int) -> bool:
    # 304 Not Modified answers a conditional request; it carries no body and points nowhere.
    return 300 <= status < 400 and status != 304


def location_header(headers: dict[str, str]) -> str:
    """The raw Location value whatever its case: Playwright lowercases header names, other transports may not."""
    return next((str(v) for k, v in headers.items() if str(k).lower() == "location"), "")


def needs_sign_in(status: int, target: str, request_url: str) -> bool:
    """401, or a redirect that leaves the request's origin or heads for a sign-in page or identity provider."""
    if status == 401:
        return True
    if not is_redirect(status) or not target.strip():
        return False
    # Browsers read a backslash as a slash in http(s) URLs, so //host and \\host are the same off-origin target.
    resolved = urllib.parse.urljoin(request_url, target.strip().replace("\\", "/"))
    try:
        if origin(resolved) != origin(request_url):
            return True
    except ValueError:
        return True  # not an HTTP(S) origin, so not this one
    parts = urllib.parse.urlsplit(resolved)
    return bool(common.SIGNIN_PATH_RE.search(parts.path)) or common._is_idp_host(parts.hostname or "")


# An inline body (every CLI and MCP reply) never returns this browser session's tokens; a saved body file
# (save_response, the Python bridge) stays byte-exact. After strip_invisible, in this order:
# 1. Known values: the page's g_ck (same-origin frames, read like --servicenow) and the cookie values for the request
#    and page URLs before and after the request, each at least KNOWN_VALUE_MIN characters with 8 distinct ones,
#    become *** wherever they occur.
# 2. Backstop for tokens the daemon cannot know: a token-shaped value (TOKEN_VALUE) after a credential-named key
#    (common._sensitive_key: g_ck, sysparm_ck, X-UserToken, JSESSIONID, glide_session_store, ...) in script, JSON, a
#    query or a cookie line, or in an <input>/<meta> named like one; Bearer and Basic credentials; JWTs.
# 3. Check: the result is decoded (HTML entities, JS/JSON escapes, percent-encoding, twice) and read with everything
#    but letters and digits removed. A known value still found there (encoded, split or case-changed), or a backstop
#    match that only the decoding shows, withholds the whole body.
# Header values and JSON numbers (as digits) go through the same scrub. A Range request is refused inline: a slice
# can end inside a token, and no known value matches half of one.
SCRUB_BODY_CAP = 8 * 1024 * 1024  # a larger inline body is withheld; 8 MiB takes 1-2 s to scrub, a hostile one up to ~4.5 s
KNOWN_VALUE_MIN = 16
KNOWN_LETTERS_MIN = 12  # a known value's letters-and-digits form shorter than this is not looked for in step 3
G_CK_JS = "() => typeof window.g_ck === 'string' ? window.g_ck : null"
TOKEN_VALUE = r"(?=[A-Za-z0-9_.~%+/=-]*[0-9A-Z])[A-Za-z0-9_.~%+/=-]{16,}"  # 16+ token characters with a digit or a capital
TOKEN_VALUE_RE = re.compile(TOKEN_VALUE)
QUOTE = r"(?:[\"']|\\[\"']|&quot;|&#3[49];|&#[xX]2[27];)"  # a quote: raw, backslash-escaped or an HTML entity
SEPARATOR = r"(?:(?:" + QUOTE + r"|\]){0,2}\s*[:=]|" + QUOTE + r"\s*,)"  # key: v, key = v, or 'key', 'v' (an argument list)
# Only the key is matched, so a key that is not credential-named never consumes a pair inside its value
# (Cookie: glide_session_store=...); the value is read from where the key ends.
KEY_RE = re.compile(r"(?<![\w$-])[A-Za-z_$][\w$-]*(?=" + SEPARATOR + ")")
VALUE_AFTER_KEY_RE = re.compile(SEPARATOR + r"\s*" + QUOTE + r"?(?P<value>" + TOKEN_VALUE + ")")
AUTH_SCHEME_RE = re.compile(r"(?<![\w-])(?i:bearer|basic)\s+" + QUOTE + r"?(?P<value>" + TOKEN_VALUE + ")")
# A tag ends at its first >, even inside a quoted attribute: a quote-aware pattern backtracks for tens of seconds on
# 8 MiB of unclosed <input ", so such a tag is left to the known values and step 3.
TAG_RE = re.compile(r"<(?:input|meta)\b[^>]{0,2000}>", re.I)
MARKUP_RE = re.compile(r"<[^<>]*>")
LONE_SURROGATE_RE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")  # json.loads keeps an unpaired \ud800
ATTR_RE = re.compile(r"""(?P<name>[\w:-]+)\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s"'>]+))""")
JS_ESCAPE_RE = re.compile(r"\\(?:u\{([0-9A-Fa-f]{1,6})\}|u([0-9A-Fa-f]{4})|x([0-9A-Fa-f]{2}))")
NOT_LETTER_RE = re.compile(r"[^0-9A-Za-z]+")
WITHHELD = {
    "unverified": "Body withheld: this session's cookies could not be read, so its tokens could not be masked.",
    "too_large": "Body withheld: an inline body over 8 MiB is not scrubbed of session tokens.",
    "too_deep": "Body withheld: its JSON is nested too deeply to scrub.",
    "session_token": "Body withheld: it holds a session token in a form that cannot be masked in place (encoded, split or case-changed).",
}


def _known_value(value: Any) -> bool:
    return isinstance(value, str) and KNOWN_VALUE_MIN <= len(value) <= 4096 and len(set(value)) >= 8


def _attr(match: re.Match[str]) -> str:
    return next(g for g in ("dq", "sq", "bare") if match.group(g) is not None)


def mask_token_carriers(text: str) -> tuple[str, int]:
    """text with step 2's token values masked, and how many were."""
    count = 0

    def key_values(text: str) -> str:
        nonlocal count
        out, last = [], 0
        for key in KEY_RE.finditer(text):
            value = VALUE_AFTER_KEY_RE.match(text, key.end()) if common._sensitive_key(key.group(0)) else None
            if value and value.start("value") >= last:
                count += 1
                out += [text[last:value.start("value")], common.MASK]
                last = value.end("value")
        return "".join(out) + text[last:]

    def scheme(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return m.group(0)[:m.start("value") - m.start()] + common.MASK

    def tag(m: re.Match[str]) -> str:
        nonlocal count
        markup = m.group(0)
        attrs = list(ATTR_RE.finditer(markup))
        if not any(a.group("name").lower() in {"name", "id", "property"} and common._sensitive_key(a.group(_attr(a))) for a in attrs):
            return markup
        out, last = [], 0
        for a in attrs:
            group = _attr(a)
            run = TOKEN_VALUE_RE.match(a.group(group))
            if a.group("name").lower() in {"value", "content"} and run:
                count += 1
                out += [markup[last:a.start(group)], common.MASK]
                last = a.start(group) + run.end()
        return "".join(out) + markup[last:]

    if len(text) < 13:  # the shortest JWT_RE match; every other carrier is longer
        return text, 0
    text = key_values(text)
    text = AUTH_SCHEME_RE.sub(scheme, text)
    text = TAG_RE.sub(tag, text)
    text, jwts = common.JWT_RE.subn(common.MASK, text)
    return text, count + jwts


def _unescape_js(m: re.Match[str]) -> str:
    code = int(next(g for g in m.groups() if g), 16)
    return chr(code) if code < 0x110000 else m.group(0)


def _decoded(text: str) -> str:
    for _ in range(2):  # twice, for a value encoded twice (%2561)
        decoded = urllib.parse.unquote(JS_ESCAPE_RE.sub(_unescape_js, html.unescape(text)))
        if decoded == text:
            break
        text = decoded
    return text


def _letters(text: str) -> str:
    """Letters and digits only, lowercased. A non-ASCII character whose NFKC form holds exactly one ASCII letter or
    digit (fullwidth, mathematical, circled, superscript) is read as it; every other one is dropped, ligatures and
    multi-letter symbols included. Each distinct character is folded once and the text translated: NFKC over a whole
    string is quadratic in characters that expand (U+FDFA becomes 18), and translate is slow when one becomes several."""
    fold = {}
    for c in set(text):
        if ord(c) > 127:
            ascii_form = NOT_LETTER_RE.sub("", unicodedata.normalize("NFKC", c))
            fold[ord(c)] = ascii_form if len(ascii_form) == 1 else ""
    return NOT_LETTER_RE.sub("", text.translate(fold)).lower()


class BodyScrub:
    """One inline body's scrub (see SCRUB_BODY_CAP): text() for every string and header value, number() for every
    JSON number, item() for every JSON object member."""

    def __init__(self, known: Iterable[str]) -> None:
        self.known = sorted({v for v in known if _known_value(v)}, key=len, reverse=True)  # a longer value may hold a shorter
        self.masked = 0
        self.strings: list[str] = []  # every scrubbed string in document order, JSON keys included

    def text(self, value: str) -> str:
        # An unpaired surrogate cannot be encoded as UTF-8, so the reply carrying it would never be sent.
        value = common.strip_invisible(LONE_SURROGATE_RE.sub(chr(0xFFFD), value))
        for known in self.known:
            if known in value:
                self.masked += value.count(known)
                value = value.replace(known, common.MASK)
        value, count = mask_token_carriers(value)
        self.masked += count
        self.strings.append(value)
        return value

    def number(self, value: int | float) -> Any:
        """A JSON number read as its digits: one that holds a known value comes back as a string with *** in it."""
        digits = str(value)
        scrubbed = self.text(digits)
        return value if scrubbed == digits else scrubbed

    def item(self, key: str, value: Any) -> Any:
        """A credential-named member's token-shaped string or number is masked, as step 2 masks key: value in text."""
        if isinstance(value, bool) or not isinstance(value, (str, int, float)) or not common._sensitive_key(key):
            return value
        text = str(value)
        run = TOKEN_VALUE_RE.match(text)
        if not run:
            return value
        self.masked += 1
        return common.MASK + text[run.end():]

    def leftover(self, shown: str) -> bool:
        """Step 3: does the scrubbed body, decoded and without separators, still show a token? Known values are looked
        for in the scrubbed strings themselves (JSON serialisation would turn a newline into the letter n); the backstop
        reads `shown`, the body as returned (serialised JSON keeps each key next to its value)."""
        strings = " ".join(self.strings)
        decoded = _decoded(strings)
        # Also without markup, so pieces split by <b>, <wbr> or a comment meet again.
        views = {_letters(strings), _letters(decoded), _letters(MARKUP_RE.sub("", decoded))}
        for known in self.known:
            for form in {_letters(known), _letters(_decoded(known))}:
                if len(form) >= KNOWN_LETTERS_MIN and any(form in view for view in views):
                    return True
        decoded = _decoded(shown)
        return decoded != shown and mask_token_carriers(decoded)[1] > 0


def scrub_body(data: bytes, known: Iterable[str] | None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """The reply fields for an inline body and its (public) headers: `body` (JSON, else UTF-8 text with undecodable
    bytes replaced) and `headers` after the scrub, with `body_masked` (how many values became ***). When the body is
    withheld: a placeholder `body`, `body_withheld` (a WITHHELD key) and no headers. known=None means the session's
    values could not be read."""
    reason = "unverified" if known is None else "too_large" if len(data) > SCRUB_BODY_CAP else None
    if reason is None:
        scrub = BodyScrub(known or ())
        scrubbed_headers = {scrub.text(name): scrub.text(value) for name, value in (headers or {}).items()}
        try:
            try:
                body = json.loads(data)
            except (ValueError, UnicodeError):
                body = scrub.text(data.decode("utf-8", errors="replace"))
                shown = body
            else:
                body = common.scrub_json(body, scrub.text, scrub.item, scrub.number)
                shown = json.dumps(body, ensure_ascii=False)
        except RecursionError:
            reason = "too_deep"
        else:
            if not scrub.leftover(shown + " " + json.dumps(scrubbed_headers, ensure_ascii=False)):
                out = {"body": body, "headers": scrubbed_headers}
                return {**out, "body_masked": scrub.masked} if scrub.masked else out
            reason = "session_token"
    return {"body": WITHHELD[reason], "body_withheld": reason, "headers": {}}


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
        inline = not args.get("save_response")
        if inline and any(k.lower() == "range" for k in headers):
            raise common._tag(ValueError("Range needs a saved response (the Python bridge): an inline body is scrubbed whole, and a slice could end inside a token."),
                              common.ERR_GUARDED)
        session_keys = self._session_keys(page) if inline or args.get("servicenow") else []
        if args.get("servicenow"):
            if not session_keys:
                raise common._tag(RuntimeError("The ServiceNow page has no usable session token (not signed in, or an unsupported page). Browser authentication is not qualified for this page."),
                                  "unsupported_browser_auth", hint=common._hint("api_auth_unavailable", self.profile))
            headers["X-UserToken"] = session_keys[0]
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
        known_before = self._known_values(session_keys, [url, page.url]) if inline else None
        self.action_attempted = method not in {"GET", "HEAD"}
        response = None
        try:
            response = self.ctx.request.fetch(url, method=method, headers=headers, data=body,
                                              timeout=max(timeout, .5) * 1000, max_redirects=0, max_retries=0)
            # Location is read before public_headers drops it; it is used only to classify, never returned.
            status, location, response_headers = response.status, location_header(response.headers), public_headers(response.headers)
            if status >= 400:
                self._observe("http_errors", page)
            data = response.body()
            if len(data) > maximum:
                raise common._tag(RuntimeError("API response exceeded the requested byte limit."), "response_too_large")
            self._record_response(page, method, url, status)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            # Playwright error strings can contain request headers. Never propagate those strings.
            self._observe("failed_requests", page)
            raise common._tag(RuntimeError("Browser API transport failed; inspect the receipt before repeating a write."), "unavailable") from None
        finally:
            if response is not None: response.dispose()
        result = {"status": status, "headers": response_headers, "url": common.redact_url(url), "effect": "request_completed"}
        notes = []
        if method not in {"GET", "HEAD"} and not 200 <= status < 300:
            result["write_outcome"] = "unknown"
        # Redirect bodies are never returned: they echo the target, which can carry OAuth codes and state.
        if is_redirect(status):
            result["redirect"] = True
            data = b"Redirect body withheld; redirects are not followed."
        if needs_sign_in(status, location, url):
            handoff = self.open_handoff("Browser API authentication requires human verification", {
                "signed_in": origin(page.url)[1], "require_navigation": True, "navigation": self.nav_counts.get(page.main_frame, 0)})
            result.update(needs_human=True, handoff_id=handoff)
            data = b"Authentication requires human verification."
        elif is_redirect(status) or status == 403:
            # A 403 is usually an access decision a sign-in cannot change, so its body (untrusted, like any body) is returned.
            notes.append(common._hint("api_not_auth", self.profile))
        if not inline:
            self.journal.require_space(len(data), self.paths.workspace)
            self.paths.outputs.mkdir(parents=True, exist_ok=True)
            target = self.paths.outputs / (args["_command_id"] + ".body")
            target.write_bytes(data)  # byte-exact: a saved body is not scrubbed
            result["body_file"] = str(target)
        else:
            # A response may replace or delete an outgoing cookie. Keep both snapshots, and withhold if either failed.
            known_after = self._known_values(session_keys, [url, page.url])
            known = None if known_before is None or known_after is None else [*known_before, *known_after]
            result.update(scrub_body(data, known, response_headers))
            if "body_withheld" in result:
                notes.append(common._hint("api_body_withheld", self.profile))
        if notes:
            result["note"] = " ".join(notes)
        result["untrusted"] = ["body", "url", "headers"]
        return result

    def _session_keys(self, page: Any) -> list[str]:
        """The page's usable g_ck values, from its same-origin frames in order; a frame that cannot be read is skipped."""
        keys = []
        for frame in page.frames:
            try:
                if origin(frame.url or page.url) != origin(page.url): continue
                value = frame.evaluate(G_CK_JS)
            except Exception:
                continue
            if isinstance(value, str) and value and len(value) < 4096 and value not in keys:
                keys.append(value)
        return keys

    def _known_values(self, session_keys: list[str], urls: list[str]) -> list[str] | None:
        """What an inline body must not echo: the page's g_ck values and the cookie values for these URLs. None when
        the cookies cannot be read, so the body is withheld."""
        try:
            values = [str(c.get("value") or "") for c in self.ctx.cookies(urls)]
        except Exception:  # noqa: BLE001 - a closed or crashed context; nothing is known, so nothing is returned
            return None
        return [*session_keys, *values]

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
