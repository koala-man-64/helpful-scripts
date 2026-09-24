"""Split a shell command into the statements it runs, for the shell guard.

Guard rules must match what a command runs, never the data it carries. So
heredoc and here-string bodies are dropped, quoted text stays inside its token,
and the code inside command substitutions, subshells, script blocks, `bash -c`,
`cmd /c`, `powershell -Command` (including -EncodedCommand) and
`Invoke-Expression` is parsed as statements of its own.

Each statement also says where its output goes (assigned to a variable, tested
as a condition, piped through later stages, or redirected to a file) and which literal assignments the
command makes, so rules can follow `SP=...; rm -rf "$SP/x"` and tell printing a
secret from passing it on. Input that cannot be parsed is reported as
incomplete rather than guessed at, so the caller can fail safe.
"""

from __future__ import annotations

import base64
import binascii
import itertools
import re
import shlex
from dataclasses import dataclass, field
from pathlib import PureWindowsPath

MAX_DEPTH = 6
_SUBSTITUTIONS = itertools.count(1)

_HEREDOC = re.compile(
    r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([A-Za-z0-9_.-]+))"
)
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?\+?=")
_PS_ASSIGNMENT = re.compile(r"^\s*\$([\w:.]+)(?:\[[^\]]*\])?\s*[-+*/]?=(?!=)\s*")
_STDOUT_REDIRECT = re.compile(r"^(?:1?>>?(?!&)|&>>?)")
_BASH_PREFIXES = {"!", "{", "(", "do", "then", "else", "elif", "if", "while", "until",
                  "time", "exec", "command", "builtin", "nohup", "sudo"}
_DECLARATIONS = {"export", "local", "declare", "readonly", "typeset"}
_PS_CONDITION_KEYWORDS = {"if", "elseif", "while", "until", "switch", "foreach", "for"}
_ENV_VALUE_OPTIONS = {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}
# Wrappers that run the command after their options, and which options take a value.
_WRAPPER_VALUE_OPTIONS = {
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-L", "-n", "-P", "-s", "-E", "-d", "-a"},
}
_FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
# Start-Process parameters: tokens that end its -ArgumentList.
_START_PROCESS_PARAMS = {
    "-wait", "-nonewwindow", "-passthru", "-workingdirectory", "-windowstyle", "-verb", "-filepath",
    "-redirectstandardoutput", "-redirectstandarderror", "-redirectstandardinput", "-credential",
    "-loaduserprofile", "-usenewenvironment", "-environment",
}
# Assignment target recorded for code in a PowerShell condition: its value is tested, not printed.
CONDITION = "(condition)"

Nested = tuple[str, str, "str | None"]  # (code, dialect, variable its output is assigned to)


@dataclass
class Statement:
    """One command as it will run: program and arguments, quotes removed."""

    argv: list[str]
    dialect: str  # "bash", "powershell" or "cmd"
    captured: bool = False  # output assigned to a variable, not printed
    assigns: tuple[str, ...] = ()  # variables that receive the output
    literals: dict[str, str] = field(default_factory=dict)  # NAME=value assignments with no command
    downstream: tuple[tuple[str, ...], ...] = ()  # argv of each later stage of the same pipeline
    stdout_redirected: bool = False  # stdout goes to a file (`> f`, `>> f`, `&> f`)

    @property
    def program(self) -> str:
        return program_name(self.argv[0]) if self.argv else ""

    @property
    def pipe_to(self) -> str:
        """Program of the next pipeline stage, "" when not piped."""
        return program_name(self.downstream[0][0]) if self.downstream and self.downstream[0] else ""


@dataclass
class Parsed:
    statements: list[Statement] = field(default_factory=list)
    complete: bool = True


def split_words(text: str, dialect: str = "bash") -> list[str]:
    """A command word split the way the shell would split an expanded value."""
    words, _ = _tokenize(text, dialect)
    return words or [text]


def redirects_stdout(args: "list[str] | tuple[str, ...]") -> bool:
    return any(_STDOUT_REDIRECT.match(a) for a in args)


def program_name(token: str) -> str:
    name = PureWindowsPath(token.replace("/", "\\")).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def parse(command: str, dialect: str = "bash") -> Parsed:
    result = Parsed()
    _parse_into(result, command, dialect, assign_to=None, depth=0)
    return result


def _parse_into(result: Parsed, text: str, dialect: str, assign_to: str | None, depth: int) -> None:
    if depth > MAX_DEPTH:
        result.complete = False
        return
    if dialect == "powershell":
        chunks, complete = _split_powershell(text)
    elif dialect == "cmd":
        chunks, complete = _split_cmd(text)
    else:
        chunks, complete = _split_bash(text)
    result.complete &= complete

    prepared = []
    for chunk_text, nested, piped in chunks:
        chunk_assign = assign_to
        literal_ps: dict[str, str] = {}
        if dialect == "powershell":
            match = _PS_ASSIGNMENT.match(chunk_text)
            if match:
                chunk_text = chunk_text[match.end():]
                if chunk_text.strip()[:1] in {"'", '"'} and len(_tokenize(chunk_text, dialect)[0]) == 1:
                    literal_ps[match.group(1)] = _tokenize(chunk_text, dialect)[0][0]
                    chunk_text = ""
                else:
                    chunk_assign = match.group(1)
        raw, ok = _tokenize(chunk_text, dialect)
        result.complete &= ok
        argv, literals = _strip_prefixes(raw, dialect)
        literals.update(literal_ps)
        prepared.append((argv, literals, nested, piped, chunk_assign))

    for index, (argv, literals, nested, piped, chunk_assign) in enumerate(prepared):
        for code, code_dialect, nested_assign in nested:
            _parse_into(result, code, code_dialect, nested_assign or chunk_assign, depth + 1)
        if literals and not argv:
            result.statements.append(Statement([], dialect, literals=literals))
        if not argv:
            continue
        downstream = []
        stage = index
        while prepared[stage][3] and stage + 1 < len(prepared):
            downstream.append(tuple(prepared[stage + 1][0]))
            stage += 1
        statement = Statement(
            argv,
            dialect,
            captured=chunk_assign is not None,
            assigns=(chunk_assign,) if chunk_assign else (),
            downstream=tuple(downstream),
            stdout_redirected=redirects_stdout(argv[1:]),
        )
        result.statements.append(statement)
        payload = _shell_payload(statement)
        if payload is not None:
            code, code_dialect = payload
            if code is None:
                result.complete = False
            else:
                _parse_into(result, code, code_dialect, chunk_assign, depth + 1)


# --- bash ---------------------------------------------------------------------

def _bash_separator(text: str, i: int) -> str | None:
    for sep in ("&&", "||", "|&", ";;", ";", "|", "&"):
        if text.startswith(sep, i):
            if sep == "&" and ((i > 0 and text[i - 1] in "<>") or text.startswith("&>", i)):
                return None
            if sep == "|" and i > 0 and text[i - 1] == ">":
                return None
            return sep
    return None


def _skip_heredoc_bodies(text: str, i: int, pending: list[tuple[str, bool, bool]]) -> tuple[int, list[Nested]]:
    """Skip the bodies of pending heredocs; return the code an unquoted body runs.

    A quoted delimiter (<<'EOF', <<"EOF", <<\\EOF) makes the body pure data. An
    unquoted one still runs $(...) and backticks, and the body usually feeds a
    printer (cat), so that code is returned uncaptured.
    """
    nested: list[Nested] = []
    for delimiter, strip_tabs, expands in pending:
        body_start = i
        while i < len(text):
            end = text.find("\n", i)
            line = text[i:] if end < 0 else text[i:end]
            line_start = i
            i = len(text) if end < 0 else end + 1
            if (line.lstrip("\t") if strip_tabs else line).rstrip("\r") == delimiter:
                if expands:
                    nested.extend(_body_substitutions(text[body_start:line_start]))
                break
        else:
            if expands:
                nested.extend(_body_substitutions(text[body_start:]))
    return i, nested


def _body_substitutions(body: str) -> list[Nested]:
    found: list[Nested] = []
    k = 0
    while k < len(body):
        if body.startswith("\\", k):
            k += 2
            continue
        if body.startswith("$((", k):
            end = body.find("))", k + 3)
            k = len(body) if end < 0 else end + 2
            continue
        if body.startswith("$(", k):
            _, end, _ok = _split_bash(body, start=k + 2, until_paren=True)
            found.append((body[k + 2:end], "bash", None))
            k = end + 1
            continue
        if body[k] == "`":
            end = body.find("`", k + 1)
            if end < 0:
                break
            found.append((body[k + 1:end], "bash", None))
            k = end + 1
            continue
        k += 1
    return found


def _assignment_target(buf: list[str]) -> str | None:
    """Variable name when the text so far ends inside an assignment word (`NAME=`...)."""
    text = "".join(buf)
    if not text or text[-1].isspace():
        return None
    word = re.split(r"[\s;&|(]", text)[-1]
    match = _ASSIGNMENT.match(word)
    return match.group(1) if match else None


def _substitution(assign: str | None) -> tuple[str | None, str]:
    """(where the nested output goes, placeholder for the outer command's text).

    In an assignment the output fills that variable. Anywhere else it becomes
    part of the outer command's arguments, so it is captured into a fresh
    placeholder variable: the outer command then decides whether it is printed
    (`echo "$__sub1"`) or only used (`curl -H "Bearer $__sub1"`).
    """
    if assign is not None:
        return assign, "$(sub)"
    name = f"__sub{next(_SUBSTITUTIONS)}"
    return name, f"${name}"


def _scan_bash_double(text: str, i: int, assign: str | None) -> tuple[int, list[Nested], bool, str]:
    """From the opening quote at i: index of the closing quote, nested code, ok, and the
    quoted text with each substitution replaced by its placeholder."""
    nested: list[Nested] = []
    out: list[str] = ['"']
    j = i + 1
    while j < len(text):
        c = text[j]
        if c == "\\":
            out.append(text[j:j + 2])
            j += 2
            continue
        if c == '"':
            out.append('"')
            return j, nested, True, "".join(out)
        if text.startswith("$((", j):
            end = text.find("))", j + 3)
            if end < 0:
                return len(text), nested, False, "".join(out)
            out.append("0")
            j = end + 2
            continue
        if text.startswith("$(", j):
            _, end, ok = _split_bash(text, start=j + 2, until_paren=True)
            target, placeholder = _substitution(assign)
            nested.append((text[j + 2:end], "bash", target))
            out.append(placeholder)
            if not ok:
                return len(text), nested, False, "".join(out)
            j = end + 1
            continue
        if c == "`":
            end = text.find("`", j + 1)
            if end < 0:
                return len(text), nested, False, "".join(out)
            target, placeholder = _substitution(assign)
            nested.append((text[j + 1:end], "bash", target))
            out.append(placeholder)
            j = end + 1
            continue
        out.append(c)
        j += 1
    return len(text), nested, False, "".join(out)


def _split_bash(text: str, start: int = 0, until_paren: bool = False):
    """Split bash text into (statement text, nested code, piped) chunks.

    With until_paren, stop at the unquoted `)` that closes a `$(` or `(` and
    return (chunks, index of that paren, ok) instead of (chunks, ok).
    """
    chunks: list[tuple[str, list[Nested], bool]] = []
    buf: list[str] = []
    nested: list[Nested] = []
    pending: list[tuple[str, bool, bool]] = []
    ok = True
    i, n = start, len(text)

    def flush(piped: bool = False) -> None:
        chunks.append(("".join(buf), list(nested), piped))
        buf.clear()
        nested.clear()

    def at_word_start() -> bool:
        tail = "".join(buf[-1:])[-1:]
        return not tail or tail.isspace() or tail in ";&|("

    while i < n:
        c = text[i]
        if c == "'":
            end = text.find("'", i + 1)
            if end < 0:
                buf.append(text[i:])
                ok = False
                break
            buf.append(text[i:end + 1])
            i = end + 1
            continue
        if c == '"':
            end, inner, fine, quoted = _scan_bash_double(text, i, _assignment_target(buf))
            nested.extend(inner)
            buf.append(quoted)
            if not fine:
                ok = False
                break
            i = end + 1
            continue
        if c == "\\":
            if text.startswith("\\\n", i):
                i += 2
                continue
            buf.append(text[i:i + 2])
            i += 2
            continue
        if c == "#" and at_word_start():
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if text.startswith("$((", i) or (text.startswith("((", i) and at_word_start()):
            # Arithmetic: `<<` in here is a shift, never a heredoc.
            end = text.find("))", i + 2)
            if end < 0:
                buf.append(text[i:])
                ok = False
                break
            buf.append("0" if text.startswith("$((", i) else " (arith) ")
            i = end + 2
            continue
        # Substitution placeholders take no surrounding spaces, so `X=$(cmd)`
        # stays one assignment word and its command is known to be captured.
        if text.startswith("$(", i):
            _, end, fine = _split_bash(text, start=i + 2, until_paren=True)
            target, placeholder = _substitution(_assignment_target(buf))
            nested.append((text[i + 2:end], "bash", target))
            buf.append(placeholder)
            if not fine:
                ok = False
                break
            i = end + 1
            continue
        if text.startswith("<(", i) or text.startswith(">(", i):
            # Process substitution: the outer command reads or feeds it through a
            # file, and may print it (`cat <(...)`), so it is not treated as captured.
            _, end, fine = _split_bash(text, start=i + 2, until_paren=True)
            nested.append((text[i + 2:end], "bash", _assignment_target(buf)))
            buf.append("$(sub)")
            if not fine:
                ok = False
                break
            i = end + 1
            continue
        if c == "`":
            end = text.find("`", i + 1)
            if end < 0:
                buf.append(text[i:])
                ok = False
                break
            target, placeholder = _substitution(_assignment_target(buf))
            nested.append((text[i + 1:end], "bash", target))
            buf.append(placeholder)
            i = end + 1
            continue
        if c == "(":
            _, end, fine = _split_bash(text, start=i + 1, until_paren=True)
            nested.append((text[i + 1:end], "bash", None))
            buf.append(" (group) ")
            if not fine:
                ok = False
                break
            i = end + 1
            continue
        if c == ")" and until_paren:
            flush()
            return chunks, i, ok
        if text.startswith("<<", i) and not text.startswith("<<<", i):
            match = _HEREDOC.match(text, i)
            if match:
                delimiter = next(g for g in match.groups()[1:] if g is not None)
                expands = match.group(4) is not None and text[match.start(4) - 1] != "\\"
                pending.append((delimiter, match.group(1) == "-", expands))
                buf.append(" <<heredoc ")
                i = match.end()
                continue
        if c == "\n":
            flush()
            i, body_code = _skip_heredoc_bodies(text, i + 1, pending)
            chunks[-1][1].extend(body_code)
            pending = []
            continue
        sep = _bash_separator(text, i)
        if sep:
            flush(piped=sep in {"|", "|&"})
            i += len(sep)
            continue
        buf.append(c)
        i += 1
    flush()
    if until_paren:
        return chunks, n, False
    return chunks, ok


# --- PowerShell ------------------------------------------------------------------

def _line_ends_after(text: str, i: int) -> bool:
    stripped = text[i:].lstrip(" \t")
    return not stripped or stripped[0] in "\r\n"


def _herestring_end(text: str, i: int, closer: str) -> int:
    """Index just after the closer of a here-string whose opener ends at i, or -1."""
    pos = text.find("\n", i)
    while pos >= 0:
        if text.startswith(closer, pos + 1):
            return pos + 1 + len(closer)
        pos = text.find("\n", pos + 1)
    return -1


def _ps_subexpressions(body: str) -> list[Nested]:
    found: list[Nested] = []
    i = body.find("$(")
    while i >= 0:
        _, end, _ok = _split_powershell(body, start=i + 2, closer=")")
        found.append((body[i + 2:end], "powershell", None))
        i = body.find("$(", end + 1)
    return found


def _split_powershell(text: str, start: int = 0, closer: str | None = None):
    chunks: list[tuple[str, list[Nested], bool]] = []
    buf: list[str] = []
    nested: list[Nested] = []
    ok = True
    i, n = start, len(text)

    def flush(piped: bool = False) -> None:
        chunks.append(("".join(buf), list(nested), piped))
        buf.clear()
        nested.clear()

    def at_word_start() -> bool:
        tail = "".join(buf[-1:])[-1:]
        return not tail or tail.isspace() or tail in ";|({"

    while i < n:
        c = text[i]
        if (text.startswith("@'", i) or text.startswith('@"', i)) and _line_ends_after(text, i + 2):
            quote = text[i + 1]
            end = _herestring_end(text, i + 2, quote + "@")
            if end < 0:
                ok = False
                break
            if quote == '"':
                nested.extend(_ps_subexpressions(text[i + 2:end - 2]))
            buf.append(" @here@ ")
            i = end
            continue
        if c == "'":
            j = i + 1
            while True:
                k = text.find("'", j)
                if k < 0:
                    j = -1
                    break
                if text.startswith("''", k):
                    j = k + 2
                    continue
                j = k + 1
                break
            if j < 0:
                buf.append(text[i:])
                ok = False
                break
            buf.append(text[i:j])
            i = j
            continue
        if c == '"':
            j = i + 1
            quoted = ['"']
            while j < n and text[j] != '"':
                if text[j] == "`":
                    quoted.append(text[j:j + 2])
                    j += 2
                    continue
                if text.startswith("$(", j):
                    _, end, fine = _split_powershell(text, start=j + 2, closer=")")
                    # Part of the string's value: captured into a placeholder the outer command carries.
                    target, placeholder = _substitution(None)
                    nested.append((text[j + 2:end], "powershell", target))
                    quoted.append(placeholder)
                    if not fine:
                        j = n
                        break
                    j = end + 1
                    continue
                quoted.append(text[j])
                j += 1
            if j >= n:
                buf.append(text[i:])
                ok = False
                break
            buf.append("".join(quoted) + '"')
            i = j + 1
            continue
        if c == "`":
            if i + 1 < n and text[i + 1] in "\r\n":
                i += 2
                continue
            buf.append(text[i:i + 2])
            i += 2
            continue
        if text.startswith("<#", i):
            end = text.find("#>", i + 2)
            if end < 0:
                ok = False
                break
            i = end + 2
            continue
        if c == "#" and at_word_start():
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if c in "({":
            inner_closer = ")" if c == "(" else "}"
            _, end, fine = _split_powershell(text, start=i + 1, closer=inner_closer)
            inner = text[i + 1:end]
            last_word = re.split(r"[\s;|({}]", "".join(buf).rstrip())[-1].lower()
            condition = c == "(" and last_word in _PS_CONDITION_KEYWORDS
            method_call = c == "(" and not condition and not at_word_start() and "".join(buf)[-1:] not in {"$", "@"}
            if c == "(" and not condition and not method_call:
                # `(expr)`, `$(expr)`, `@(expr)`: a value the outer statement uses or prints,
                # so it is captured into a placeholder that the outer statement carries.
                if "".join(buf)[-1:] in {"$", "@"}:
                    buf[-1] = buf[-1][:-1]  # the $ or @ operator becomes part of the placeholder
                target, placeholder = _substitution(None)
                nested.append((inner, "powershell", target))
                buf.append(placeholder)
            else:
                # A condition's value is tested, not printed; a script block's output is its own.
                nested.append((inner, "powershell", CONDITION if condition else None))
                # A method call keeps its arguments visible in the token, so a rule can
                # read GetEnvironmentVariable('NAME'); the arguments are parsed as code too.
                buf.append(f"({inner})" if method_call else " (block) ")
            if not fine:
                ok = False
                break
            i = end + 1
            continue
        if closer and c == closer:
            flush()
            return chunks, i, ok
        if c in "\n;":
            flush()
            i += 1
            continue
        if text.startswith("&&", i) or text.startswith("||", i):
            flush()
            i += 2
            continue
        if c == "|":
            flush(piped=True)
            i += 1
            continue
        buf.append(c)
        i += 1
    flush()
    if closer:
        return chunks, n, False
    return chunks, ok


# --- cmd.exe ---------------------------------------------------------------------

def _split_cmd(text: str):
    """cmd.exe quotes only with double quotes; single quotes are ordinary text."""
    chunks: list[tuple[str, list[Nested], bool]] = []
    buf: list[str] = []
    quoted = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            quoted = not quoted
            buf.append(c)
            i += 1
            continue
        if quoted:
            buf.append(c)
            i += 1
            continue
        if c == "^" and i + 1 < n:
            buf.append(text[i + 1])
            i += 2
            continue
        if text.startswith("&&", i) or text.startswith("||", i):
            chunks.append(("".join(buf), [], False))
            buf.clear()
            i += 2
            continue
        if c == "&" and i > 0 and text[i - 1] == ">":
            buf.append(c)
            i += 1
            continue
        if c in "&|\n":
            chunks.append(("".join(buf), [], c == "|"))
            buf.clear()
            i += 1
            continue
        buf.append(" " if c in "()" else c)
        i += 1
    chunks.append(("".join(buf), [], False))
    return chunks, not quoted


# --- tokens ----------------------------------------------------------------------

def _keep_path_backslashes(text: str) -> str:
    """Double a backslash that precedes an ordinary path character.

    Bash would drop it (`C:\\Users` becomes `C:Users`), but agents on Windows write
    Windows paths that way and mean the Windows path, so the guard reads it as
    one. Real escapes (`\\"`, `\\$`, `\\ `) and single-quoted text are untouched.
    """
    out: list[str] = []
    quote = None
    i = 0
    while i < len(text):
        c = text[i]
        if quote == "'":
            quote = None if c == "'" else quote
        elif c == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt.isalnum() or nxt in "_.-~":
                out.append("\\\\")
                i += 1
                continue
            out.append(c + nxt)
            i += 2
            continue
        elif c == '"':
            quote = None if quote == '"' else '"'
        elif c == "'" and quote is None:
            quote = "'"
        out.append(c)
        i += 1
    return "".join(out)


def _tokenize(text: str, dialect: str) -> tuple[list[str], bool]:
    if dialect == "bash":
        try:
            return shlex.split(_keep_path_backslashes(text), posix=True), True
        except ValueError:
            return text.split(), False
    tokens: list[str] = []
    buf: list[str] = []
    started = False
    quote = None
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                if dialect == "powershell" and quote == "'" and text.startswith("''", i):
                    buf.append("'")
                    i += 2
                    continue
                quote = None
            elif dialect == "powershell" and quote == '"' and c == "`" and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 1
            else:
                buf.append(c)
            i += 1
            continue
        if c == '"' or (c == "'" and dialect == "powershell"):
            quote, started = c, True
        elif c.isspace():
            if started:
                tokens.append("".join(buf))
                buf, started = [], False
        elif c == "`" and dialect == "powershell" and i + 1 < len(text):
            buf.append(text[i + 1])
            started = True
            i += 1
        else:
            buf.append(c)
            started = True
        i += 1
    if started:
        tokens.append("".join(buf))
    return tokens, quote is None


def _strip_prefixes(argv: list[str], dialect: str) -> tuple[list[str], dict[str, str]]:
    """Drop leading assignments, keywords and wrappers; return the literal assignments.

    Assignment prefixes on a command (`X=1 cmd`) only set that command's
    environment, so they are not returned as persistent literals.
    """
    literals: dict[str, str] = {}
    prefix_assignments: dict[str, str] = {}
    changed = True
    while argv and changed:
        changed = False
        head = argv[0]
        match = _ASSIGNMENT.match(head) if dialect == "bash" else None
        if match:
            value = head[match.end():]
            if "$(sub)" not in value:
                prefix_assignments[match.group(1)] = value
            argv, changed = argv[1:], True
        elif dialect == "bash" and head in _BASH_PREFIXES:
            argv, changed = argv[1:], True
        elif dialect == "powershell" and head in {"&", "."}:
            argv, changed = argv[1:], True
        elif dialect == "bash" and program_name(head) == "env":
            # `env [-i] [-u NAME] [NAME=value]... command`: the command is what runs.
            # A bare `env` (or only options) prints the environment, so it stays.
            rest = argv[1:]
            while rest and (rest[0].startswith("-") or _ASSIGNMENT.match(rest[0])):
                option, rest = rest[0], rest[1:]
                if option in _ENV_VALUE_OPTIONS and rest:
                    if option in {"-S", "--split-string"}:
                        rest = _tokenize(rest[0], "bash")[0] + rest[1:]
                        break
                    rest = rest[1:]
            if rest:
                argv, changed = rest, True
        elif program_name(head) in _WRAPPER_VALUE_OPTIONS:
            # timeout/nice/stdbuf/xargs run the command after their own options.
            value_options = _WRAPPER_VALUE_OPTIONS[program_name(head)]
            rest = argv[1:]
            while rest and (rest[0].startswith("-") or (program_name(head) == "timeout" and rest[0].replace(".", "").rstrip("smhd").isdigit())):
                option, rest = rest[0], rest[1:]
                if option in value_options and rest:
                    rest = rest[1:]
            argv, changed = rest, True
        elif dialect == "cmd" and head.lower() == "for" and "do" in (a.lower() for a in argv):
            lowered = [a.lower() for a in argv]
            argv, changed = argv[lowered.index("do") + 1:], True
    if not argv:
        literals.update(prefix_assignments)
    elif argv[0].lower() in _DECLARATIONS and all(_ASSIGNMENT.match(a) for a in argv[1:]):
        for arg in argv[1:]:
            match = _ASSIGNMENT.match(arg)
            value = arg[match.end():]
            if "$(sub)" not in value:
                literals[match.group(1)] = value
        argv = []
    return argv, literals


_REDIRECT = re.compile(r"^(?:\d*|&)(?:>>?|<<?|>&|<&|>\|)")


def without_redirections(args: list[str]) -> list[str]:
    """Arguments minus redirections: `2>&1`, `>out.txt`, and `> out.txt` with its target."""
    kept: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        match = _REDIRECT.match(arg)
        if match:
            skip_next = match.end() == len(arg) and not arg.endswith(("&", "|"))
            continue
        kept.append(arg)
    return kept


def _shell_payload(statement: Statement) -> tuple[str | None, str] | None:
    """Code a statement hands to another shell, as (code, dialect); code None if unreadable."""
    program, args = statement.program, statement.argv[1:]
    lowered = [a.lower() for a in args]
    if program in {"bash", "sh", "zsh", "dash", "ksh"}:
        for index, arg in enumerate(args):
            if re.fullmatch(r"-[a-z]*c[a-z]*", arg) and index + 1 < len(args):
                return args[index + 1], "bash"
        return None
    if program == "cmd":
        for index, arg in enumerate(lowered):
            if arg in {"/c", "/k", "/r"}:
                return " ".join(args[index + 1:]), "cmd"
        return None
    if program in {"powershell", "pwsh"}:
        for index, arg in enumerate(lowered):
            name = arg.lstrip("-/")
            if arg.startswith(("-", "/")) and name and "encodedcommand".startswith(name) and name.startswith("e"):
                if index + 1 >= len(args):
                    return None, "powershell"
                try:
                    return base64.b64decode(args[index + 1]).decode("utf-16-le"), "powershell"
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    return None, "powershell"
            if arg.startswith(("-", "/")) and name and "command".startswith(name) and name.startswith("c"):
                return " ".join(args[index + 1:]), "powershell"
        return None
    if program in {"invoke-expression", "iex"}:
        code = [a for a in args if a.lower() not in {"-command", "-c"}]
        return " ".join(code), "powershell"
    if program == "wsl":
        code = args[1:] if lowered[:1] in (["-e"], ["--exec"]) else args
        return " ".join(code), "bash"
    if program == "find":
        # `-exec CMD {} ;` runs CMD once per match; `{}` becomes an unresolved target.
        commands, index = [], 0
        while index < len(args):
            if args[index] in _FIND_EXEC:
                end = index + 1
                while end < len(args) and args[end] not in {";", "\\;", "+"}:
                    end += 1
                commands.append(" ".join("$FIND_MATCH" if a == "{}" else shlex.quote(a) for a in args[index + 1:end]))
                index = end
            index += 1
        return (" ; ".join(commands), "bash") if commands else None
    if statement.dialect == "powershell" and program in {"start-process", "saps", "start"}:
        file_path, arguments, index = "", [], 0
        while index < len(args):
            low = lowered[index]
            if low in {"-filepath", "-file", "-fi"} and index + 1 < len(args):
                file_path, index = args[index + 1], index + 2
                continue
            if low in {"-argumentlist", "-args", "-arguments"}:
                index += 1
                while index < len(args) and lowered[index] not in _START_PROCESS_PARAMS:
                    arguments.extend(part for part in args[index].split(",") if part)
                    index += 1
                continue
            if not args[index].startswith("-") and not file_path:
                file_path = args[index]
            index += 1
        return (" ".join([file_path, *arguments]), "powershell") if file_path else None
    return None
