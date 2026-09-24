"""PreToolUse guard for Bash and PowerShell: the shell permission authority.

The command is parsed into the statements it runs (shell_parse), and every rule
reads a statement's program and arguments, never quoted data or heredoc bodies.

- Tier 1 denies, whoever asks: discarding work, force pushes, pushes to a
  protected branch, recursive deletes or moves outside the repository,
  printing secrets into the transcript, approving production gates.
- Tier 2 asks: force-deleting an unmerged branch, deletes whose targets are
  unresolved or many, Azure resource writes outside boards/repos/pipelines/
  devops, `checkout <rev> -- <paths>`, and destructive-looking commands that
  could not be parsed.
- Tier 3 allows everything else, with finish-workflow notes where relevant. So
  settings.json permissions.allow is not consulted for shell tools, while
  permissions.deny/ask still apply as a backstop.

Precedence is deny, then ask, then allow: an allowed statement never lifts an
ask raised by another statement in the same command. Literal assignments are
followed (`SP=...; rm -rf "$SP/x"`), and a variable assigned from a secret
source is itself a secret. A child process's assignments stay in the child,
and a value is used only when nothing else in the command may change it
(untrusted_names); an unknown value leaves its target unresolved, which asks.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import shell_parse
from hook_utils import (
    allow_pre_tool,
    ask_pre_tool,
    azure_devops_agent_authority_lines,
    deny_pre_tool,
    emit_json,
    extract_command,
    pull_request_title_guidance,
    read_hook_input,
    run_git,
)

PROTECTED_BRANCHES = frozenset({"main", "master", "trunk", "develop", "staging", "production"})
# Scratch locations besides the OS temp directory; C:\tmp is a working directory in these sessions.
EXTRA_SCRATCH_ROOTS = ("C:/tmp",)
BULK_DELETE_THRESHOLD = 3
BROAD_PATHSPECS = frozenset({".", "./", ":/", ":(top)", "*", ":/*"})

CD_PROGRAMS = frozenset({"cd", "pushd", "chdir", "set-location", "sl", "push-location"})
PS_REMOVE = frozenset({"remove-item", "ri", "rm", "del", "erase", "rd", "rmdir"})
PS_MOVE = frozenset({"move-item", "mi", "mv", "move"})
PS_VALUE_PARAMS = frozenset({"-filter", "-include", "-exclude", "-credential", "-stream"})
PS_PATH_PARAMS = frozenset({"-path", "-literalpath", "-lp", "-pspath"})
FILE_PRINTERS = frozenset({"cat", "head", "tail", "less", "more", "type", "bat", "get-content", "gc", "select-string", "sls"})
VALUE_PRINTERS = frozenset({"echo", "printf", "print", "write-output", "write", "write-host"})
ENV_LISTERS = frozenset({"get-childitem", "gci", "dir", "ls", "get-item", "gi"})
VARIABLE_DUMPERS = frozenset({"declare", "typeset", "export", "readonly", "local"})
# Pipeline stages that pass their input on to the transcript rather than consuming it.
PASS_THROUGH = frozenset({
    "cat", "tee", "grep", "egrep", "fgrep", "rg", "findstr", "select-string", "sls", "sed", "awk",
    "cut", "sort", "uniq", "head", "tail", "less", "more", "tr", "jq", "base64", "xxd", "od",
    "select-object", "select", "format-table", "ft", "format-list", "fl", "format-wide", "fw",
    "out-string", "out-host", "out-default", "write-output", "write-host", "echo", "printf", "column",
    "nl", "fold", "rev", "strings", "where-object", "where", "?", "foreach-object", "foreach", "%",
    "sort-object", "tee-object", "group-object", "convertto-json", "convertfrom-json", "convertto-csv",
    "convertfrom-csv",
})
GREP_PROGRAMS = frozenset({"grep", "egrep", "fgrep", "rg"})
GREP_SUMMARY_LONG = frozenset({"--count", "--quiet", "--silent", "--files-with-matches", "--files-without-match"})
SELECT_VALUE_OPTIONS = frozenset({"-first", "-last", "-skip", "-skiplast", "-index"})
# A sed pattern that strips the value from every NAME=value line: nothing, or any
# name, before `=.*`. `s/(TOKEN|KEY)=.*//` masks only some names, so it is not one.
# Like `cut -d= -f1` and `awk -F=`, it cannot mask the continuation lines of a
# multi-line value; `compgen -e` lists names only.
SED_MASKS_EVERY_VALUE = re.compile(r"\^?(?:\\\(\[\^=\]\*\\\)|\(\[\^=\]\*\)|\[\^=\]\*)?=\.[*+]\$?")

SECRET_NAME = re.compile(
    r"(?:^|_)(?:PAT|TOKENS?|SECRETS?|PASSWORD|PASSWD|CREDENTIALS?|DSN|APIKEY|SAS)(?:_|$)"
    r"|(?:API|PRIVATE|ACCESS|ACCOUNT|CLIENT)_?(?:KEY|SECRET)|CONNECTION_?STRING",
    re.IGNORECASE,
)
VARIABLE_REF = re.compile(r"\$\{?(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}?|%([A-Za-z_][A-Za-z0-9_]*)%", re.IGNORECASE)
ENV_VARIABLE_CALL = re.compile(r"getenvironmentvariable\(\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
LENGTH_READ = re.compile(r"\.(?:length|count)\b", re.IGNORECASE)
# Home and temp spelled as variables in bash, PowerShell or cmd, ending at a path separator.
KNOWN_ROOT_VARIABLE = re.compile(
    r"^(?:\$\{?(home|tmpdir|temp|tmp|env:userprofile|env:home|env:temp|env:tmp)\}?|%(userprofile|temp|tmp)%)(?=[\\/]|$)",
    re.IGNORECASE,
)
SECRET_FILE = re.compile(
    r"(?i)(?:^|[\\/])(?:\.env(?:rc|[-\w]*)(?:\.(?!example$|sample$|template$|dist$)[\w.-]+)?"
    r"|[^\\/]+\.(?:pem|key|pfx|p12)|id_(?:rsa|dsa|ecdsa|ed25519)|\.git-credentials|[._]netrc"
    r"|credentials\.json|\.npmrc|\.pypirc|msal_token_cache[^\\/]*|accesstokens\.json|credentials\.tfrc\.json"
    r"|\.kube[\\/]config|\.docker[\\/]config\.json|gh[\\/]hosts\.yml)$"
)
# A data file named for a secret (dsn_relay.txt, access-token.json, secrets.yaml,
# x.tok) holds one; source and docs named for one (token_audit.py) do not, and
# neither do LLM token accounting files (token_usage.json).
SECRET_NAMED_FILE = re.compile(
    r"(?i)(?:^|[-_.])(?:pat|tokens?(?![-_](?:usage|counts?|audit|budget|report|stats|trim|limits?|metrics|burn))"
    r"|secrets?|passwords?|passwd|credentials?|creds|dsn|sas"
    r"|api[-_]?keys?|conn(?:ection)?[-_]?str(?:ing)?s?)(?:[-_.\d]|$)"
)
# CLI calls that print a credential unless a --query names only other fields.
SECRET_FIELD = re.compile(
    r"(?i)value|password|passwd|connection_?string|key(?!_?(?:name|id|vault))|token(?!_?type)|secret(?!_?name)|credential|sas"
)
AZ_SECRET_COMMANDS = (
    ("account", "get-access-token"),
    ("keyvault", "secret", "show"),
    ("containerapp", "secret", "show"),
    ("storage", "account", "keys", "list"),
    ("storage", "account", "show-connection-string"),
    ("storage", "account", "generate-sas"),
    ("storage", "container", "generate-sas"),
    ("storage", "blob", "generate-sas"),
    ("cosmosdb", "keys", "list"),
    ("redis", "list-keys"),
    ("acr", "credential", "show"),
    ("ad", "sp", "credential", "reset"),
    ("ad", "app", "credential", "reset"),
    ("ad", "sp", "create-for-rbac"),
    ("webapp", "deployment", "list-publishing-credentials"),
    ("webapp", "deployment", "list-publishing-profiles"),
    ("functionapp", "keys", "list"),
    ("functionapp", "function", "keys", "list"),
    ("eventhubs", "namespace", "authorization-rule", "keys", "list"),
    ("servicebus", "namespace", "authorization-rule", "keys", "list"),
    ("search", "admin-key", "show"),
    ("search", "query-key", "list"),
    ("cognitiveservices", "account", "keys", "list"),
    ("staticwebapp", "secrets", "list"),
    ("staticwebapp", "appsettings", "list"),
    ("webapp", "config", "appsettings", "list"),
    ("webapp", "config", "connection-string", "list"),
    ("functionapp", "config", "appsettings", "list"),
    ("batch", "account", "keys", "list"),
    ("signalr", "key", "list"),
)
OTHER_SECRET_COMMANDS = (
    ("gh", "auth", "token"),
    ("gcloud", "auth", "print-access-token"),
    ("gcloud", "auth", "print-identity-token"),
    ("gcloud", "auth", "application-default", "print-access-token"),
    ("aws", "ecr", "get-login-password"),
    ("aws", "sts", "get-session-token"),
    ("aws", "secretsmanager", "get-secret-value"),
    ("gcloud", "secrets", "versions", "access"),
    ("op", "inject"),
    ("npm", "token", "create"),
    ("vault", "kv", "get"),
    ("vault", "read"),
    ("op", "read"),
    ("doppler", "secrets"),
    ("heroku", "config"),
)
NOT_SECRET_SUFFIXES = frozenset({
    "py", "ps1", "psm1", "psd1", "sh", "bash", "zsh", "bat", "cmd", "md", "rst", "adoc",
    "ts", "tsx", "js", "jsx", "mjs", "cjs", "cs", "csproj", "sln", "go", "rs", "java", "kt", "rb", "php",
    "c", "h", "cpp", "hpp", "swift", "scala", "sql", "html", "css", "scss", "vue", "svelte", "ipynb",
    "example", "sample", "template", "dist",
})

PROD_DEPLOY_APPROVAL = re.compile(r"\b(?:prod|production|deploy-prod|prod-[a-z0-9-]*)\b")
AZURE_PIPELINE_APPROVAL = re.compile(
    r"\baz\s+pipelines\b.*\bapprov|\baz\s+devops\s+invoke\b.*\bapprov"
    r"|\b(?:curl|invoke-restmethod|irm|iwr|invoke-webrequest)\b.*\bapprove-check\b"
)
AZURE_ACTIONS = frozenset({"delete", "purge", "create", "update", "scale", "start", "stop", "restart"})
# Command groups the user has pre-authorized for agents; the ask tier must not undo that.
AZURE_EXEMPT_GROUPS = frozenset({"boards", "repos", "pipelines", "devops"})
# Argument words that make a statement worth confirming when its program is unknown.
DESTRUCTIVE_WORDS = frozenset({
    "push", "reset", "clean", "checkout", "restore", "switch", "branch", "stash", "worktree", "update-ref",
    "rm", "rmdir", "remove-item", "del", "delete", "purge", "-rf", "-fr", "-r", "--force", "-f", "--hard",
    "--mirror", "-recurse",
})
UNPARSED_DESTRUCTIVE = re.compile(
    r"\b(?:rm|rmdir|rd|del|erase|remove-item|ri|move-item|mv)\b"
    r"|\bgit\b.*\b(?:reset|clean|checkout|restore|branch|push|update-ref)\b"
    r"|\baz\b.*\b(?:delete|purge)\b",
    re.IGNORECASE | re.DOTALL,
)


# --- variables ----------------------------------------------------------------

# `$global:x` and the like name the plain variable within a command's own scope.
PS_SCOPE_PREFIX = re.compile(r"^(?:global|script|local|private|using|variable):", re.IGNORECASE)
BASH_EXPANSION = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
PS_EXPANSION = re.compile(r"\$(?:\{((?:[A-Za-z]+:)?[A-Za-z_][A-Za-z0-9_]*)\}|((?:[A-Za-z]+:)?[A-Za-z_][A-Za-z0-9_]*))")
# Ways to set a variable by a name the guard never sees: no value the command sets can be trusted.
OPAQUE_TEXT = re.compile(r"PSVariable|ExecutionContext|\]::Create\b|\.InvokeScript\b|-NoNewScope\b|\bvariable:", re.IGNORECASE)
# Bash builtins that set a variable named by an argument, and the PowerShell cmdlets that do.
BASH_NAME_WRITERS = frozenset({
    "read", "mapfile", "readarray", "getopts", "unset", "wait", "let", "declare", "typeset", "local", "export", "readonly",
})
PS_VARIABLE_CMDLETS = frozenset({
    "set-variable", "sv", "new-variable", "nv", "clear-variable", "clv", "remove-variable", "rv", "get-variable", "gv",
})
# git configuration given through the environment, which the guard's own git calls cannot see.
GIT_ENV_CONFIG = re.compile(r"\bGIT_CONFIG_(?:PARAMETERS|COUNT|KEY_\d+|VALUE_\d+|GLOBAL|SYSTEM)\b|--config-env")


def variable_key(name: str, dialect: str) -> str:
    """Bash names are case-sensitive; PowerShell and cmd names are not."""
    return name if dialect == "bash" else PS_SCOPE_PREFIX.sub("", name).lower()


def in_block(scope: tuple[tuple[int, str], ...]) -> bool:
    return any(kind == shell_parse.BLOCK for _, kind in scope)


def untrusted_names(command: str, statements: list[shell_parse.Statement]) -> set[str]:
    """Variables this command may set in ways the guard does not follow, so their values stay unknown.

    A value is trusted only when every mention of the name is an assignment the
    guard applies or a plain `$NAME` read. Any other mention -- `read NAME`,
    `printf -v NAME`, `for NAME in`, `unset NAME`, `NAME+=`, `declare -n
    R=NAME`, `Set-Variable NAME`, `-OutVariable NAME`, or an assignment in a
    function body or in a string another program runs -- may change it.
    """
    sites: dict[str, int] = {}
    folded: set[str] = set()
    for statement in statements:
        if not statement.runs_bodies:
            for body in statement.bodies:
                # Input no program runs as code cannot set a variable; only `read NAME` (outside it) can.
                command = command.replace(body, " ", 1)
        if in_block(statement.scope):
            continue  # never applied, so not a site: the mention makes the name untrusted
        for name in statement.literals:
            key = variable_key(name, statement.dialect)
            sites[key] = sites.get(key, 0) + 1
            if statement.dialect != "bash":
                folded.add(key)
    if OPAQUE_TEXT.search(command):
        return set(sites)
    untrusted = set()
    for key, count in sites.items():
        powershell = key in folded
        flags = re.IGNORECASE if powershell else 0
        name = re.escape(key.split(":")[-1])
        prefix = "env:" if key.startswith("env:") else "(?:(?:global|script|local|private|using|variable):)?"
        after = r"(?![\w:])" if powershell else r"(?!\w)"  # `$x:y` is another PowerShell variable
        # `$NAME` forms that write: `$x = `, `$x++`, `$a, $b = `, `foreach ($x in`, `[ref]$x`, `[string]$x = `.
        writes = sum(
            1
            for m in re.finditer(rf"\$\{{?{prefix}{name}\}}?{after}", command, flags)
            if command[max(0, m.start() - 1):m.start()] == "]"
            or re.match(r"\s*[-+*/%]?=(?!=)|\+\+|--(?![\w-])|\s*,|\s+in\b", command[m.end():])
        )
        if powershell:
            # A bare PowerShell name only reaches a variable through a -*Variable parameter
            # (any abbreviation of one); cmdlets like Set-Variable make every value unknown.
            writes += len(re.findall(rf"-[A-Za-z]+(?::|\s+)['\"]?\+?{name}(?![\w\\/:]|\.\w)", command, flags))
        else:
            # `read NAME`, `for NAME in`, `unset NAME`, `NAME+=`, `R=NAME`, `{NAME}>`, and `printf -vNAME`;
            # not `$NAME`, a path segment, a drive letter, a file name or an option letter.
            writes += len(re.findall(rf"(?<![\w$\\/.:-])(?<!\$\{{){name}(?![\w\\/]|:[\\/]|\.\w)", command))
            writes += len(re.findall(rf"(?<![\w-])-[A-Za-z]*[apv]{name}(?!\w)", command))
        if writes != count:
            untrusted.add(key)
    return untrusted


def changes_any_variable(statement: shell_parse.Statement) -> bool:
    """Whether this statement may set variables by names the guard cannot read."""
    program = statement.program
    args = shell_parse.without_redirections(statement.argv[1:])
    if statement.dialect == "bash":
        if program in {"source", "."}:
            return True
        if program == "eval":
            return any("$" in a for a in args)
        if program == "printf":
            names = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-v"]
            names += [a[2:] for a in args if a.startswith("-v") and len(a) > 2]
            return any("$" in n for n in names)
        if program in {"declare", "typeset", "local"} and "n" in short_flags(args):
            return True  # a nameref writes through to whatever it names
        return program in BASH_NAME_WRITERS and any("$" in a for a in args)
    if statement.dialect == "powershell":
        # Dot-sourcing runs a script in this scope. (What a script file does is outside the
        # guard either way; it could delete as easily as reassign.)
        return (
            program in PS_VARIABLE_CMDLETS
            or statement.dot_sourced
            or (program in {"invoke-expression", "iex"} and any("$" in a for a in args))
        )
    return False


# --- context ------------------------------------------------------------------

@dataclass
class Context:
    """Session facts, computed lazily: most commands never need git at all."""

    session_cwd: Path
    variables: dict[str, str] = field(default_factory=dict)  # literal values in this shell, by variable_key
    tainted: set[str] = field(default_factory=set)  # variables holding a secret, lower-cased names
    delete_targets: list[str] = field(default_factory=list)  # non-scratch targets across the whole command
    inline_aliases: dict[str, str] = field(default_factory=dict)  # `git -c alias.x=...` in effect, lower-cased names
    untrusted: set[str] = field(default_factory=set)  # variable_keys the command may change unseen: never resolved
    literal_values: list[str] = field(default_factory=list)  # every literal value the command assigns
    env_config: bool = False  # the command gives git configuration through its environment
    offline: bool = False  # judging carried text as if it ran: no git calls, no finish notes, no further scans
    scanned: set[str] = field(default_factory=set)  # carried text already judged
    scope: tuple[tuple[int, str], ...] = ()  # scope of the statement being judged
    dialect: str = "bash"  # dialect of the statement being judged
    _tables: dict[tuple[tuple[int, str], ...], dict[str, str]] = field(default_factory=dict)  # values in child scopes
    _root: Path | None = None
    _status: list[str] | None = None

    @property
    def root(self) -> Path:
        if self._root is None:
            code, output = run_git(["rev-parse", "--show-toplevel"], self.session_cwd)
            self._root = Path(output) if code == 0 and output else self.session_cwd
        return self._root

    def status_lines(self) -> list[str]:
        if self._status is None:
            code, output = run_git(["status", "--short", "--branch"], self.root)
            self._status = output.splitlines() if code == 0 else []
        return self._status

    def scratch_roots(self) -> list[Path]:
        roots = [tempfile.gettempdir(), os.environ.get("TEMP"), os.environ.get("TMP"), *EXTRA_SCRATCH_ROOTS]
        return [Path(root) for root in roots if root]

    def table(self, scope: tuple[tuple[int, str], ...] | None = None) -> dict[str, str]:
        """Values visible in a scope. A child process starts with a copy of its parent's and keeps its own changes."""
        scope = self.scope if scope is None else scope
        if not scope:
            return self.variables
        if scope not in self._tables:
            self._tables[scope] = dict(self.table(scope[:-1]))
        return self._tables[scope]

    def assign(self, name: str, value: str) -> None:
        # A block may run later, repeatedly or never, so its assignment is not applied;
        # untrusted_names has already made such a name unknown.
        if not in_block(self.scope):
            self.table()[variable_key(name, self.dialect)] = value

    def forget(self, name: str) -> None:
        """The variable now holds a value the guard does not know, here and in every enclosing scope."""
        key = variable_key(name, self.dialect)
        for depth in range(len(self.scope), -1, -1):
            self.table(self.scope[:depth]).pop(key, None)

    def forget_all(self) -> None:
        """No value is known any more in this shell. A child process cannot reach its parent's."""
        for depth in range(len(self.scope), -1, -1):
            self.table(self.scope[:depth]).clear()
            if depth and self.scope[depth - 1][1] == shell_parse.CHILD:
                break

    def expand(self, text: str) -> str:
        """Substitute variables whose literal value this command set earlier. cmd's %NAME% reads the environment."""
        if self.dialect == "cmd":
            return text
        table = self.table()

        def value(match: re.Match) -> str:
            key = variable_key(match.group(1) or match.group(2), self.dialect)
            return match.group(0) if key in self.untrusted else table.get(key, match.group(0))

        return (BASH_EXPANSION if self.dialect == "bash" else PS_EXPANSION).sub(value, text)


def resolve_path(raw: str, cwd: Path | None) -> Path | None:
    """Absolute path for a delete or move target, or None when it cannot be known here."""
    text = raw.strip()
    if not text:
        return None
    home, temp = str(Path.home()), tempfile.gettempdir()
    known = KNOWN_ROOT_VARIABLE.match(text)
    if known:
        name = next(group for group in known.groups() if group).lower()
        text = (home if name in {"home", "userprofile", "env:home", "env:userprofile"} else temp) + text[known.end():]
    if text == "~" or text.startswith(("~/", "~\\")):
        text = home + text[1:]
    drive = re.match(r"^/([a-zA-Z])(?=/|$)", text)
    if drive:
        text = f"{drive.group(1).upper()}:/{text[drive.end():].lstrip('/')}"
    elif text == "/tmp" or text.startswith("/tmp/"):
        text = temp + text[4:]
    if re.search(r"[$%{]", text):
        return None
    parts = re.split(r"[\\/]", text)
    for index, part in enumerate(parts):
        if any(ch in part for ch in "*?["):
            # A glob can only reach below its fixed prefix.
            text = "/".join(parts[:index]) or "."
            break
    path = Path(text)
    if not path.is_absolute():
        if cwd is None or path.drive:
            return None
        path = cwd / path
    try:
        return path.resolve(strict=False)
    except (OSError, ValueError):
        return None


def _under(path: Path, bases: list[Path]) -> bool:
    for base in bases:
        try:
            resolved = base.resolve(strict=False)
        except (OSError, ValueError):
            continue
        if path == resolved or resolved in path.parents:
            return True
    return False


def is_inside(path: Path, ctx: Context) -> bool:
    return _under(path, [ctx.root, *ctx.scratch_roots()])


def is_scratch(path: Path, ctx: Context) -> bool:
    """Inside a scratch root and not inside the repository: the repository wins when it lives in temp."""
    return _under(path, ctx.scratch_roots()) and not _under(path, [ctx.root])


# --- git ----------------------------------------------------------------------

GIT_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"})
PUSH_OPTIONS_WITH_VALUE = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})


def git_invocation(argv: list[str]) -> tuple[str, list[str], str | None]:
    """(subcommand, its arguments without redirections, the -C directory if any)."""
    argv = [argv[0], *shell_parse.without_redirections(argv[1:])]
    index, directory = 1, None
    while index < len(argv):
        arg = argv[index]
        if arg in GIT_OPTIONS_WITH_VALUE:
            if arg == "-C" and index + 1 < len(argv):
                directory = argv[index + 1]
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return arg, argv[index + 1:], directory
    return "", [], directory


# git ignores an alias that shadows a built-in, so only other names can be aliases.
KNOWN_GIT_COMMANDS = frozenset({
    "add", "am", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file", "check-ignore",
    "checkout", "cherry", "cherry-pick", "clean", "clone", "commit", "config", "count-objects", "credential",
    "describe", "diff", "diff-files", "diff-index", "diff-tree", "difftool", "fetch", "for-each-ref",
    "format-patch", "fsck", "gc", "grep", "hash-object", "help", "init", "log", "ls-files", "ls-remote",
    "ls-tree", "maintenance", "merge", "merge-base", "mergetool", "mv", "name-rev", "notes", "prune", "pull",
    "push", "range-diff", "rebase", "reflog", "remote", "repack", "replace", "rerere", "reset", "restore",
    "rev-list", "rev-parse", "revert", "rm", "shortlog", "show", "show-branch", "show-ref", "sparse-checkout",
    "stash", "status", "submodule", "switch", "symbolic-ref", "tag", "update-index", "update-ref", "var",
    "verify-commit", "version", "whatchanged", "worktree", "lfs",
})
MAX_ALIAS_DEPTH = 8  # alias cycles stop here; a real chain is never this deep


def inline_aliases(argv: list[str]) -> dict[str, str]:
    """Aliases set on the command line: `git -c alias.x='!...' x`."""
    found = {}
    for index, arg in enumerate(argv[:-1]):
        if arg == "-c":
            key, sep, value = argv[index + 1].partition("=")
            if sep and key.lower().startswith("alias."):
                found[key[len("alias."):].lower()] = value
    return found


CONFIG_VALUE_OPTIONS = frozenset({"-f", "--file", "--blob", "--type", "--default", "--comment", "--value", "--url"})


def alias_definition(sub: str, args: list[str]) -> tuple[str, str] | None:
    """(name, value) when this `git config` call sets an alias: `git config [set] alias.x value`."""
    if sub != "config":
        return None
    positional = operands(args, CONFIG_VALUE_OPTIONS)
    if positional[:1] == ["set"]:
        positional = positional[1:]
    if len(positional) >= 2 and positional[0].lower().startswith("alias."):
        return positional[0][len("alias."):], positional[1]
    return None


def alias_command(value: str, args: list[str]) -> str:
    """The command line an alias runs: `!cmd` is shell code, anything else is a git subcommand."""
    rest = " ".join(shlex.quote(a) for a in args)
    body = value[1:] if value.startswith("!") else f"git {value}"
    return f"{body} {rest}".strip()


def short_flags(args: list[str]) -> set[str]:
    letters: set[str] = set()
    for arg in args:
        if arg == "--":
            break
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 1:
            letters.update(arg[1:])
    return letters


def operands(args: list[str], skip_values_of: frozenset[str] = frozenset()) -> list[str]:
    """Non-option arguments; everything after `--` counts."""
    found, index = [], 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            found.extend(args[index + 1:])
            break
        if arg in skip_values_of:
            index += 2
            continue
        if not arg.startswith("-"):
            found.append(arg)
        index += 1
    return found


def current_branch(repo: Path) -> str:
    code, output = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    return output if code == 0 else ""


def upstream_branch(repo: Path) -> str:
    code, output = run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], repo)
    return output.split("/", 1)[1] if code == 0 and "/" in output else ""


def default_base(repo: Path) -> str:
    code, output = run_git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], repo)
    if code == 0 and output:
        return output
    for candidate in ("origin/main", "origin/master"):
        if run_git(["rev-parse", "--verify", "--quiet", candidate], repo)[0] == 0:
            return candidate
    return ""


def branch_exists(repo: Path, branch: str) -> bool:
    """A branch that does not exist cannot lose work; git will just report the error."""
    return run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo)[0] == 0


def squash_merged(repo: Path, branch: str) -> bool:
    """True when every file the branch changed has the same content on the base branch.

    Squash merges leave the branch unmerged in git's eyes, so `branch -d` never
    works for a merged task branch. Identical content for everything the branch
    touched is the evidence that its work landed.
    """
    base = default_base(repo)
    if not base:
        return False
    code, fork = run_git(["merge-base", base, branch], repo)
    if code != 0 or not fork:
        return False
    code, names = run_git(["diff", "--name-only", fork, branch], repo)
    if code != 0:
        return False
    files = [name for name in names.splitlines() if name]
    if not files:
        return True
    return run_git(["diff", "--quiet", branch, base, "--", *files], repo)[0] == 0


def worktree_holds_work(target: str, repo: Path) -> bool:
    """Whether a forced worktree removal would lose anything: tracked changes or untracked files.

    Ignored files (build output, caches) are not work. A path that is not a
    directory holds nothing; one git cannot read is treated as holding work.
    """
    path = resolve_path(target, repo)
    if path is None:
        return True
    if not path.is_dir():
        return False
    code, output = run_git(["status", "--porcelain"], path)
    return code != 0 or bool(output.strip())


def needs_repo_state(sub: str, args: list[str]) -> bool:
    """Checks that must read the repository: a bare push, or force-deleting a branch."""
    letters = short_flags(args)
    if sub == "push":
        return len(operands(args, PUSH_OPTIONS_WITH_VALUE)) < 2
    return sub == "branch" and ("D" in letters or ("d" in letters and "f" in letters) or "--delete" in args)


def check_git(args: list[str], sub: str, repo: Path) -> tuple[str, str] | None:
    letters = short_flags(args)
    if sub == "reset" and "--hard" in args:
        return "deny", "git reset --hard is blocked. Preserve the current worktree and inspect diffs instead."
    if sub == "clean":
        force = "f" in letters or "--force" in args
        dry = "n" in letters or "--dry-run" in args
        if force and "d" in letters and not dry:
            return "deny", "git clean with force/delete flags is blocked. Review untracked files and remove only explicit task-owned paths."
    if sub in {"checkout", "switch"} and "--" not in args and (
        "f" in letters or "--force" in args or "--discard-changes" in args
    ):
        return "deny", f"git {sub} --force/--discard-changes is blocked because it discards uncommitted changes. Commit or stash them first."
    if sub == "worktree" and operands(args)[:1] == ["remove"] and ("f" in letters or "--force" in args):
        holding = [t for t in operands(args)[1:] if worktree_holds_work(t, repo)]
        if holding:
            return "ask", (
                f"git worktree remove --force would delete uncommitted or untracked work in {holding[0]}. "
                "Confirm nothing there is needed."
            )
    if sub == "stash" and operands(args)[:1] in (["drop"], ["clear"]):
        return "ask", f"git stash {operands(args)[0]} permanently discards stashed work. Confirm the stash entries are no longer needed."
    if sub == "checkout":
        if any(a in args for a in ("--theirs", "--ours", "-p", "--patch", "-m", "--merge")):
            return None
        if "--" in args:
            separator = args.index("--")
            revisions = [a for a in args[:separator] if not a.startswith("-")]
            paths = args[separator + 1:]
            if paths:
                if revisions and not any(p in BROAD_PATHSPECS for p in paths):
                    return "ask", "git checkout <rev> -- <paths> overwrites working-tree files. Confirm the paths hold nothing to keep."
                return "deny", "git checkout -- <paths> is blocked because it discards work. Inspect the diff and request explicit approval before reverting."
        if operands(args) == ["."]:
            return "deny", "git checkout . is blocked because it discards all working-tree changes."
    if sub == "restore":
        staged = "--staged" in args or "S" in letters
        worktree = "--worktree" in args or "W" in letters
        if staged and not worktree:
            return None
        if any(p in BROAD_PATHSPECS for p in operands(args, frozenset({"-s", "--source"}))):
            return "deny", "git restore of the whole tree is blocked because it discards broad working-tree changes. Restore explicit task-owned paths."
    if sub == "branch":
        force_delete = "D" in letters or (("d" in letters or "--delete" in args) and ("f" in letters or "--force" in args))
        if force_delete and "r" not in letters and "--remotes" not in args:
            unmerged = [b for b in operands(args) if branch_exists(repo, b) and not squash_merged(repo, b)]
            if unmerged:
                # Unmerged work may be deleted only with explicit human approval.
                return "ask", (
                    f"git branch -D would delete unmerged work on {', '.join(unmerged)}: its changes are not on the "
                    "base branch. Approve only if that work is truly abandoned."
                )
    if sub == "update-ref" and ("-d" in args or "--delete" in args):
        refs = [a[len("refs/heads/"):] for a in operands(args) if a.startswith("refs/heads/")]
        unmerged = [r for r in refs if branch_exists(repo, r) and not squash_merged(repo, r)]
        if unmerged:
            return "ask", f"Deleting {', '.join(unmerged)} with update-ref would delete unmerged work. Approve only if it is abandoned."
    if sub == "push":
        return check_push(args, letters, repo)
    return None


def check_push(args: list[str], letters: set[str], repo: Path) -> tuple[str, str] | None:
    if "--mirror" in args:
        return "deny", "git push --mirror is blocked: it overwrites and deletes every remote ref, protected branches included."
    if "f" in letters or "--force" in args:
        return "deny", "git push --force is blocked. Use --force-with-lease on your own task branch."
    refspecs = operands(args, PUSH_OPTIONS_WITH_VALUE)[1:]
    if any(r.startswith("+") for r in refspecs):
        return "deny", "A +refspec force push is blocked. Use --force-with-lease on your own task branch."
    if "--all" in args or "--branches" in args:
        return "deny", "git push --all is blocked: it pushes every local branch, protected branches included."
    for refspec in refspecs:
        target = refspec.split(":", 1)[1] if ":" in refspec else refspec
        target = target.removeprefix("refs/heads/")
        if target in {"HEAD", "@"}:
            target = current_branch(repo)
        # Case-folded: on Windows and macOS remotes, MAIN and main can be the same ref.
        if target.lower() in PROTECTED_BRANCHES:
            return "deny", "Direct pushes to protected branches are blocked. Use a task branch and PR policy path."
    if not refspecs:
        # A bare push sends the current branch to its upstream: check both names.
        if current_branch(repo).lower() in PROTECTED_BRANCHES or upstream_branch(repo).lower() in PROTECTED_BRANCHES:
            return "deny", "This push would update a protected branch (current branch or its upstream). Push a task branch instead."
    return None


# --- files --------------------------------------------------------------------

def delete_and_move_targets(statement: shell_parse.Statement) -> tuple[str, list[str], bool]:
    """(kind, targets, recursive) where kind is "delete", "move" or ""."""
    program, args = statement.program, shell_parse.without_redirections(statement.argv[1:])
    dialect = statement.dialect
    if dialect == "cmd":
        lowered = [a.lower() for a in args]
        if program in {"rd", "rmdir", "del", "erase"}:
            return "delete", [a for a in args if not a.startswith("/")], "/s" in lowered
        if program == "move":
            return "move", [a for a in args if not a.startswith("/")], False
        return "", [], False
    if dialect == "powershell" and (program in PS_REMOVE or program in PS_MOVE):
        lowered = [a.lower() for a in args]
        if any(a.startswith("-whatif") for a in lowered):
            return "", [], False
        recursive = any(a.startswith("-r") and "recurse".startswith(a[1:].split(":")[0]) for a in lowered)
        if program == "rm":
            # Unix habit through the alias: `rm -rf` binds nothing, but treat the intent as recursive.
            recursive = recursive or any(re.fullmatch(r"-[a-z]*r[a-z]*", a) for a in lowered)
        targets, index = [], 0
        while index < len(args):
            arg, low = args[index], lowered[index]
            name = low.split(":", 1)[0]
            if name in PS_PATH_PARAMS:
                if ":" in low:
                    targets.append(arg.split(":", 1)[1])
                elif index + 1 < len(args):
                    targets.append(args[index + 1])
                index += 1 if ":" in low else 2
                continue
            if name in PS_VALUE_PARAMS or name == "-destination":
                if name == "-destination" and index + 1 < len(args):
                    targets.append(args[index + 1])
                index += 2
                continue
            if not arg.startswith("-"):
                targets.append(arg)
            index += 1
        # `-Path a,b` is an array: each element is its own target.
        targets = [part for target in targets for part in target.split(",") if part]
        return ("delete" if program in PS_REMOVE else "move"), targets, recursive
    if program == "find" and "-delete" in args:
        # find deletes below its starting points (the arguments before the first expression).
        roots = []
        for arg in args:
            if arg.startswith(("-", "(", "!", ")")):
                break
            roots.append(arg)
        return "delete", roots or ["."], True
    if program == "rm":
        letters = short_flags(args)
        return "delete", operands(args), bool(letters & {"r", "R"}) or "--recursive" in args
    if program == "mv":
        return "move", operands(args), False
    if program == "git":
        sub, sub_args, _ = git_invocation(statement.argv)
        if sub == "rm":
            return "delete", operands(sub_args), False
    return "", [], False


def check_files(statement: shell_parse.Statement, cwd: Path | None, ctx: Context) -> tuple[str, str] | None:
    kind, targets, recursive = delete_and_move_targets(statement)
    if not kind:
        return None
    program = statement.program
    resolved = [resolve_path(ctx.expand(t), cwd) for t in targets]
    outside = [t for t, p in zip(targets, resolved) if p is not None and not is_inside(p, ctx)]
    if outside and (recursive or kind == "move"):
        return "deny", (
            f"Recursive delete or move outside the repository root is blocked ({outside[0]}). "
            "Restrict filesystem changes to explicit task-owned paths inside the workspace."
        )
    try:
        root = ctx.root.resolve(strict=False)
    except (OSError, ValueError):
        root = None
    whole_repo = [
        t for t, p in zip(targets, resolved)
        if root is not None and p is not None and (p == root or p in root.parents or _under(p, [root / ".git"]))
    ]
    if whole_repo and (recursive or kind == "move"):
        return "deny", (
            f"This would remove or move the repository itself ({whole_repo[0]}): its root, a parent of it, or .git. "
            "Delete the specific task-owned paths instead."
        )
    unresolved = [t for t, p in zip(targets, resolved) if p is None]
    if unresolved:
        verb = "deletes" if kind == "delete" else "moves to or from"
        return "ask", (
            f"`{program}` {verb} an unresolved target ({', '.join(unresolved[:3])}). The guard cannot "
            "see which path this is until the shell expands it. Confirm the expanded path list first."
        )
    if kind == "delete":
        # Counted across the whole command, so `rm a; rm b; rm c` is the same bulk delete as `rm a b c`.
        ctx.delete_targets.extend(t for t, p in zip(targets, resolved) if not is_scratch(p, ctx))
    return None


# --- secrets ------------------------------------------------------------------

def secret_names(args: list[str], ctx: Context, dialect: str = "bash") -> list[str]:
    """Variables in these arguments that hold a secret, by name or by taint."""
    found = []
    for arg in args:
        for match in VARIABLE_REF.finditer(arg):
            name = match.group(1) or match.group(2)
            if dialect == "powershell" and LENGTH_READ.match(arg, match.end()):
                continue  # $p.Length says how long the secret is, not what it is
            if SECRET_NAME.search(name) or name.lower() in ctx.tainted:
                found.append(name)
        for match in ENV_VARIABLE_CALL.finditer(arg):
            if SECRET_NAME.search(match.group(1)):
                found.append(match.group(1))
    return found


def is_secret_file(arg: str) -> bool:
    """A credential file by name (.env, keys, token caches) or a data file named for a secret."""
    if SECRET_FILE.search(arg):
        return True
    name = re.split(r"[\\/]", arg.rstrip("\\/"))[-1]
    if name == arg and "." not in name:
        return False  # a bare word is a search pattern or option value more often than a file
    stem, *suffixes = name.split(".")
    if suffixes and suffixes[-1].lower() in {"tok", "token"}:
        return True
    if any(suffix.lower() in NOT_SECRET_SUFFIXES for suffix in suffixes):
        return False
    return bool(SECRET_NAMED_FILE.search(stem))


def reads_secret(statement: shell_parse.Statement, ctx: Context) -> bool:
    """Whether the statement's output carries a secret (so a variable it fills is tainted).

    Anything that would be a secret print if it reached the transcript is a source,
    plus encodings of a secret (base64) and GetEnvironmentVariable of a secret name.
    """
    program = statement.program
    if program in VALUE_PRINTERS | FILE_PRINTERS | {"base64"} or not program or len(statement.argv) == 1:
        if secret_names(statement.argv, ctx, statement.dialect):
            return True
    if ENV_VARIABLE_CALL.search(" ".join(statement.argv)) and secret_names(statement.argv, ctx, statement.dialect):
        return True
    return secret_output(statement, ctx) is not None


def _option(args: list[str], *names: str) -> str:
    return next((args[i + 1] for i, a in enumerate(args[:-1]) if a.lower() in names), "")


def _command_words(args: list[str]) -> tuple[str, ...]:
    words = []
    for arg in args:
        if arg.startswith("-"):
            break
        words.append(arg.lower())
    return tuple(words)


def cli_secret_reason(statement: shell_parse.Statement) -> str | None:
    """A CLI call whose output is a credential: a token, key, password or connection string."""
    program, args = statement.program, statement.argv[1:]
    words = _command_words(args)
    flags = {a.lower() for a in args if a.startswith("-")}
    advice = "Capture it into a variable, or --query only non-secret fields."
    if program == "az":
        query, output = _option(args, "--query"), _option(args, "-o", "--output").lower()
        if output == "none" or (query and not SECRET_FIELD.search(query)):
            return None
        if any(words[: len(cmd)] == cmd for cmd in AZ_SECRET_COMMANDS):
            return f"`az {' '.join(words)}` prints a credential. {advice}"
        if words[:3] == ("containerapp", "secret", "list") and "--show-values" in flags:
            return f"`az containerapp secret list --show-values` prints secret values. {advice}"
        if words[:2] == ("acr", "login") and "--expose-token" in flags:
            return f"`az acr login --expose-token` prints a registry token. {advice}"
        return None
    if any(program == cmd[0] and words[: len(cmd) - 1] == cmd[1:] for cmd in OTHER_SECRET_COMMANDS):
        return f"`{program} {' '.join(words)}` prints a credential. Capture it into a variable instead of printing it."
    if program == "aws" and words[:2] == ("configure", "get") and len(words) > 2 and SECRET_FIELD.search(words[2]):
        return f"`aws configure get {words[2]}` prints a credential."
    if program == "aws" and words[:1] == ("ssm",) and words[1:2] and words[1].startswith("get-parameter") and "--with-decryption" in flags:
        return "`aws ssm get-parameter --with-decryption` prints a decrypted secret."
    if program == "op" and words[:2] == ("item", "get") and flags & {"--fields", "--reveal", "--otp"}:
        return "`op item get --fields/--reveal` prints a stored secret."
    if program == "gh" and words[:2] == ("auth", "status") and flags & {"-t", "--show-token"}:
        return "`gh auth status --show-token` prints the GitHub token."
    if program == "git" and "credential" in args and "fill" in args:
        return "`git credential fill` prints a stored password."
    if program == "kubectl":
        output = (_option(args, "-o", "--output") or next((a.split("=", 1)[1] for a in args if a.startswith(("-o=", "--output="))), "")).lower()
        if words[:2] in {("get", "secret"), ("get", "secrets")} and output.startswith(("yaml", "json", "jsonpath", "go-template")):
            return "`kubectl get secret -o yaml|json` prints secret data. Read only the keys you need, without values."
        if words[:2] == ("config", "view") and "--raw" in flags:
            return "`kubectl config view --raw` prints cluster credentials."
    return None


def _select_properties(args: list[str]) -> list[str]:
    properties, skip_value = [], False
    for arg in args:
        if skip_value:
            skip_value = False
        elif arg.lower() in SELECT_VALUE_OPTIONS:
            skip_value = True
        elif not arg.startswith("-"):
            properties.extend(p for p in arg.split(",") if p)
    return properties


def stage_passes_values(argv: tuple[str, ...]) -> bool:
    """Whether a later pipeline stage hands the values it receives on to the transcript.

    A stage that consumes its input (wc, docker login --password-stdin), counts or
    tests it (grep -c/-q/-l), keeps only names (cut -d= -f1, sed 's/=.*//',
    awk -F= '{print $1}', Select-Object Name), or writes to a file stops them.
    """
    if not argv:
        return True
    program, args = shell_parse.program_name(argv[0]), list(argv[1:])
    if program not in PASS_THROUGH or shell_parse.redirects_stdout(args):
        return False
    joined = " ".join(args)
    if program in GREP_PROGRAMS:
        short = [a for a in args if a.startswith("-") and not a.startswith("--")]
        if any(re.fullmatch(r"-[A-Za-z]*[cqlL][A-Za-z]*", a) for a in short) or {a.lower() for a in args} & GREP_SUMMARY_LONG:
            return False
    if program in {"select-string", "sls"} and "-quiet" in {a.lower() for a in args}:
        return False
    if program == "sed":
        for arg in args:
            if len(arg) > 2 and arg[0] == "s" and not arg[1].isalnum():
                if SED_MASKS_EVERY_VALUE.fullmatch(arg[2:].split(arg[1])[0]):
                    return False
    if program == "cut" and re.search(r"-d\s*=", joined) and re.search(r"-f\s*1(?![\d,-])", joined):
        return False
    if program == "awk" and re.search(r"-F\s*=", joined) and any(re.fullmatch(r"\{\s*print\s+\$1\s*;?\s*\}", a.strip()) for a in args):
        return False
    if program in {"select-object", "select", "foreach-object", "foreach", "%"}:
        properties = _select_properties(args)
        if properties and "(block)" not in properties and not any(SECRET_FIELD.search(p) for p in properties):
            return False
    return True


def reaches_transcript(statement: shell_parse.Statement) -> bool:
    """Output that is captured, tested, redirected to a file, or stopped by a later stage is not printed."""
    if statement.captured or statement.stdout_redirected:
        return False
    return all(stage_passes_values(stage) for stage in statement.downstream)


def secret_print(statement: shell_parse.Statement, ctx: Context) -> str | None:
    return secret_output(statement, ctx) if reaches_transcript(statement) else None


def secret_output(statement: shell_parse.Statement, ctx: Context) -> str | None:
    """Why this statement's output carries a secret, wherever that output then goes."""
    program, argv = statement.program, statement.argv
    args = shell_parse.without_redirections(argv[1:])
    if statement.heredoc_refs and program in VALUE_PRINTERS | FILE_PRINTERS | PASS_THROUGH:
        # `cat <<EOF` ... `Bearer $tok` ... `EOF`: the body is expanded, then printed.
        names = [n for n in statement.heredoc_refs if SECRET_NAME.search(n) or n.lower() in ctx.tainted]
        if names:
            return f"The heredoc fed to `{program}` expands ${names[0]}, which prints the secret. Pass it to its consumer without printing it."
    if program in VALUE_PRINTERS | FILE_PRINTERS:
        names = secret_names(args, ctx, statement.dialect)
        if names:
            return f"Printing ${names[0]} would put a secret in the transcript. Use it without echoing it."
    if program in FILE_PRINTERS and any(is_secret_file(a) for a in args):
        return "Printing a credential file (.env, keys, token caches, secret-named data files) is blocked. Read only the non-secret fields you need."
    if statement.dialect != "powershell" and program in {"printenv", "env", "set", "export"} and not args:
        return (
            f"`{program}` with no arguments prints every environment variable, secrets included. "
            "List names only (`compgen -e | grep X`), or count matches (`env | grep -c X`)."
        )
    if program == "printenv" and any(SECRET_NAME.search(a) for a in args):
        return "printenv of a secret variable is blocked."
    if statement.dialect != "powershell" and program in VARIABLE_DUMPERS and "-p" in args:
        names = [a for a in args if not a.startswith("-")]
        if not names or any(SECRET_NAME.search(n) or n.lower() in ctx.tainted for n in names):
            return f"`{program} -p` prints variable values, secrets included. Use the value without printing it."
    if program in {"get-variable", "gv"}:
        lowered = [a.lower() for a in args]
        names = [args[i + 1] for i, a in enumerate(lowered[:-1]) if a in {"-name", "-n"}] + [
            a for i, a in enumerate(args) if not a.startswith("-") and (i == 0 or lowered[i - 1] not in {"-name", "-n", "-scope"})
        ]
        names = [n.lstrip("$") for name in names for n in name.split(",") if n]
        if any(SECRET_NAME.search(n) or n.lower() in ctx.tainted for n in names) or (not names and ctx.tainted):
            return "Get-Variable prints variable values, secrets included. Use the value without printing it."
    if program in ENV_LISTERS and args:
        target = args[0].lower().rstrip("\\/*")
        if target == "env:" or (target.startswith("env:") and SECRET_NAME.search(target[4:])):
            return (
                "Listing environment variables prints secrets. List names only "
                "(`Get-ChildItem env: | Select-Object -ExpandProperty Name`), or read the specific non-secret variable."
            )
    if statement.dialect == "powershell" and len(argv) == 1 and secret_names(argv, ctx, statement.dialect):
        return f"Evaluating {argv[0]} on its own prints the secret."
    return cli_secret_reason(statement)


# --- Azure and approvals --------------------------------------------------------

def azure_write(statement: shell_parse.Statement) -> str | None:
    if statement.program != "az":
        return None
    path = []
    for arg in statement.argv[1:]:
        if arg.startswith("-"):
            break
        path.append(arg.lower())
    action = next((word for word in path if word in AZURE_ACTIONS), None)
    if not action or not path or path[0] in AZURE_EXEMPT_GROUPS:
        return None
    group = " ".join(path[: path.index(action)])
    return f"`az {group} {action}` mutates a live Azure resource. Confirm subscription, resource group, and blast radius first."


# Writes to pipeline check configurations: the approvals and gates that protect environments.
CHECK_CONFIGURATION = re.compile(r"\bpipelineschecks\b|_apis/pipelines/checks/configurations", re.IGNORECASE)
WRITE_METHODS = frozenset({"patch", "put", "post", "delete"})
# az devops invoke --http-method, and PowerShell's -Method (any abbreviation, `:` or space).
NAMED_METHOD = re.compile(r"(?:--http-method|-me\w*)[:=\s]+['\"]?(\w+)", re.IGNORECASE)
PS_BODY = re.compile(r"(?:^|\s)-(?:bo|inf|fo)\w*", re.IGNORECASE)
# curl short options that take a value; the value may be glued (`-XPATCH`) or the next argument.
CURL_VALUE_OPTIONS = frozenset("AbcCdDeEFHKmoPQrtTuUwxXyYz")


def curl_request(args: list[str]) -> tuple[str | None, bool]:
    """(explicit method, whether a body is sent) for curl's arguments, combined short options included."""
    method, body, get, index = None, False, False, 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("--"):
            name, sep, value = arg.partition("=")
            if name == "--request":
                method = value if sep else (args[index + 1] if index + 1 < len(args) else "")
                index += 1 if sep else 2
                continue
            body = body or name.startswith(("--data", "--form", "--json", "--upload-file"))
            get = get or name == "--get"
        elif arg.startswith("-") and len(arg) > 1:
            for position, letter in enumerate(arg[1:], start=1):
                get = get or letter == "G"
                body = body or letter in "dFT"
                if letter in CURL_VALUE_OPTIONS:
                    glued = arg[position + 1:]
                    value = glued or (args[index + 1] if index + 1 < len(args) else "")
                    if letter == "X":
                        method = value
                    index += 0 if glued else 1  # the value was the next argument
                    break
        index += 1
    # -G turns the body into a query string, so the request stays a GET.
    return method, body and not get


def writes_to_url(statement: shell_parse.Statement, text: str) -> bool:
    """Whether a request writes: an explicit write method, or a body that implies one."""
    program = statement.program
    if program == "curl":
        method, body = curl_request(statement.argv[1:])
        return (method or ("post" if body else "get")).strip("'\"").lower() in WRITE_METHODS
    named = NAMED_METHOD.search(text)
    method = named.group(1).lower() if named else None
    if program in {"invoke-restmethod", "irm", "invoke-webrequest", "iwr"} and method is None:
        # A -Body, -InFile or -Form with no -Method sends a POST.
        return bool(PS_BODY.search(text))
    return method in WRITE_METHODS


def gate_change(statement: shell_parse.Statement, ctx: Context) -> str | None:
    """Changing a check configuration can weaken an approval gate, which is the user's to change."""
    text = " ".join(ctx.expand(a) for a in statement.argv)
    if CHECK_CONFIGURATION.search(text) and writes_to_url(statement, text):
        return (
            "This changes a pipeline check configuration: an approval gate on an environment. "
            "Protected gates are user-owned; confirm this change was asked for."
        )
    return None


def prod_approval(statement: shell_parse.Statement, ctx: Context) -> str | None:
    text = " ".join(ctx.expand(a) for a in statement.argv).lower()
    if not AZURE_PIPELINE_APPROVAL.search(text):
        return None
    # A variable the guard will not resolve must not hide `prod`: any value the command assigns counts.
    hidden = re.search(r"[$%]", text) and PROD_DEPLOY_APPROVAL.search(" ".join(ctx.literal_values).lower())
    if PROD_DEPLOY_APPROVAL.search(text) or hidden:
        return (
            "Production deployment approvals are user-owned. Do not approve production "
            "pipeline, environment, or check gates; record the approval as the remaining blocker."
        )
    return None


FINISH_PATTERNS = (
    re.compile(r"^git\b.*\b(?:commit|push)\b"),
    re.compile(r"^az repos pr (?:create|set-vote)\b"),
    re.compile(r"^az repos pr update\b.*--(?:auto-complete|status completed|delete-source-branch|squash|transition-work-items)"),
    re.compile(r"^gh pr create\b"),
)


def finish_notes(statement: shell_parse.Statement, ctx: Context) -> list[str]:
    text = " ".join(statement.argv).lower()
    if not any(p.search(text) for p in FINISH_PATTERNS):
        return []
    notes = ["Finish-workflow command allowed under the standing finish approval after safety checks."]
    guidance = pull_request_title_guidance(text)
    if guidance:
        notes.append(guidance)
    lines = ctx.status_lines()
    pending = [line for line in lines if not line.startswith("##")]
    if pending:
        notes.append(f"Working tree is dirty ({len(pending)} pending path(s)); confirm task-owned scope before committing or pushing.")
    if any(line.startswith("##") and "[gone]" in line for line in lines):
        notes.append("Current branch upstream is gone; confirm branch ownership before pushing.")
    if text.startswith("az repos pr") and any(m in text for m in ("--auto-complete", "--status completed", "set-vote", "--vote")):
        notes.append("Azure DevOps authority reminder:")
        notes.extend(azure_devops_agent_authority_lines())
        notes.append("Proceed only when branch policies, required checks, and review rules allow it.")
    return notes


# --- assessment ---------------------------------------------------------------

SCAN_LIMIT = 24  # pieces of carried text judged per command
SCAN_MAX_CHARS = 20000
# Programs whose arguments and input are data, or code in a language other than the shell's.
# Their text is never judged as a shell command ("data is never a command").
DATA_PROGRAMS = frozenset({
    "echo", "printf", "print", "write-output", "write", "write-host", "write-error", "write-warning",
    "write-verbose", "write-information", "out-file", "out-string", "out-host", "set-content", "sc",
    "add-content", "ac", "new-item", "ni", "tee", "tee-object", "cat", "type", "get-content", "gc", "more",
    "less", "head", "tail", "grep", "egrep", "fgrep", "rg", "findstr", "select-string", "sls",
    "sed", "awk", "jq", "yq", "perl", "python", "python3", "py", "node", "deno", "bun", "ruby", "php",
    "dotnet", "java", "sqlite3", "psql",
    "git", "gh", "az", "curl", "wget", "invoke-restmethod", "irm", "invoke-webrequest", "iwr",
    "npm", "pnpm", "yarn", "npx", "pip", "uv", "pytest",
    # bash words whose arguments are values: loop lists, tests, prompts, declarations
    "for", "select", "test", "[", "[[", "read", "declare", "typeset", "local", "export", "readonly",
})
# A PowerShell hashtable entry, `CommandLine='...'`: the value is what a method would run.
HASHTABLE_KEY = re.compile(r"^\s*[A-Za-z_][\w.]*\s*=\s*(?!=)")
# Built-in PowerShell cmdlets that take data, by exact name. A Verb-Noun shape alone proves
# nothing: any function or script can be named that way. Cmdlets that run code (Invoke-Command,
# Start-Job, New-ScheduledTaskAction, Add-Type, New-Object) are deliberately absent, and so are
# Get-WmiObject and Get-CimInstance, whose objects have methods that start processes.
PS_DATA_CMDLETS = frozenset({
    "select-object", "where-object", "sort-object", "group-object", "measure-object", "compare-object",
    "format-table", "format-list", "format-wide", "out-null", "clear-content", "copy-item", "test-path",
    "join-path", "split-path", "resolve-path", "convert-path", "get-childitem", "get-item", "get-itemproperty",
    "set-itemproperty", "get-date", "get-process", "get-service",
    "get-authenticodesignature", "get-filehash", "get-command", "get-help", "get-member", "get-location",
    "convertto-json", "convertfrom-json", "convertto-csv", "convertfrom-csv", "import-csv", "export-csv",
    "read-host", "start-sleep", "get-acl", "test-connection", "get-random", "get-unique", "select-xml",
    "write-debug", "get-variable",
})
# Programs whose code the parser reads itself, and programs judged by their path arguments.
KNOWN_PROGRAMS = DATA_PROGRAMS | PS_DATA_CMDLETS | CD_PROGRAMS | PS_REMOVE | PS_MOVE | frozenset({
    "bash", "sh", "zsh", "dash", "ksh", "cmd", "powershell", "pwsh", "eval", "trap", "alias",
    "invoke-expression", "iex", "find", "wsl", "start-process", "saps", "start", "cp", "copy",
})
# Commands worth finding inside an unknown program's arguments (`docker exec box rm -rf /x`).
TAIL_COMMANDS = frozenset({
    "rm", "rmdir", "del", "erase", "rd", "remove-item", "ri", "mv", "move", "move-item", "mi", "git",
    "bash", "sh", "zsh", "dash", "ksh", "cmd", "powershell", "pwsh", "eval", "find", "xargs",
    "invoke-expression", "iex",
})


def unknown_program(statement: shell_parse.Statement) -> bool:
    """A program the guard neither parses nor knows to take data: it may run text it is given."""
    return statement.program not in KNOWN_PROGRAMS


def carried_text(statement: shell_parse.Statement, ctx: Context) -> list[str]:
    """Text an unknown program is given that it might run: multi-word arguments, a command
    within its arguments (`ssh host rm -rf /x`), and input it does not run itself."""
    argv = statement.argv
    if statement.method_arguments:
        # A method may run an argument as a command line (Win32_Process.Create, WScript.Shell.Run),
        # given as a literal or through a variable.
        values = (HASHTABLE_KEY.sub("", ctx.expand(a)) for a in argv)
        return [value for value in values if re.search(r"\s", value.strip())]
    if statement.dialect == "powershell" and len(argv) == 1 and (argv[0] == "@here@" or re.search(r"\s", argv[0])):
        # A PowerShell string or here-string on its own is a value. It is code where it is handed
        # to something that runs it ([scriptblock]::Create), but not where a data cmdlet takes it
        # or a variable stores it (`$body = @'...'@`, `@{ A = ('Basic ' + $t) }`).
        if statement.captured:
            return []
        consumer = shell_parse.program_name(statement.downstream[0][0]) if statement.downstream and statement.downstream[0] else ""
        if consumer in DATA_PROGRAMS | PS_DATA_CMDLETS:
            return []
        value = [HASHTABLE_KEY.sub("", argv[0])] if argv[0] != "@here@" else []  # @{CommandLine='...'}
        return value + ([] if statement.runs_bodies else list(statement.bodies))
    if statement.program == "git":
        # git takes data, but a -c value is a command under any key that runs one. The parser
        # judges the keys it knows in full; the values of all other keys get this check.
        settings = [b.partition("=") for a, b in zip(argv[1:], argv[2:]) if a == "-c"]
        return [
            value
            for key, sep, value in settings
            if sep and re.search(r"\s", value.strip())
            and shell_parse.git_config_code(key.lower(), value) is None
        ]
    if not unknown_program(statement):
        return []
    texts = []
    for index, arg in enumerate(argv[1:], start=1):
        value = ctx.expand(arg)
        option = re.match(r"^--?[\w-]+=", value)
        if option:
            value = value[option.end():]  # --exec=CMD
        if re.search(r"\s", value.strip()):
            texts.append(value)
        if shell_parse.program_name(arg) in TAIL_COMMANDS:
            quote = shlex.quote if statement.dialect == "bash" else (lambda a: "'" + a.replace("'", "''") + "'")
            texts.append(" ".join([argv[index], *(quote(a) for a in argv[index + 1:])]))
    if not statement.runs_bodies:
        texts.extend(statement.bodies)
    return texts


def hidden_command(statement: shell_parse.Statement, ctx: Context, depth: int) -> str | None:
    """Text that would be blocked if a program ran it as a command.

    The parser reads the code known runners are given (`sh -c`, `eval`,
    `bash <<EOF`, git's `-c core.pager=`...). Any other program may run text
    too (`watch`, `ssh`, a script generator), so text that would be denied as
    a command is confirmed with a human rather than trusted as data.
    """
    for text in carried_text(statement, ctx):
        if len(text) > SCAN_MAX_CHARS or text in ctx.scanned or len(ctx.scanned) >= SCAN_LIMIT:
            continue
        ctx.scanned.add(text)
        inner = Context(session_cwd=ctx.session_cwd, tainted=ctx.tainted, offline=True, _root=ctx._root)
        try:
            decision, reason = assess(text, "PowerShell" if statement.dialect == "powershell" else "Bash", inner, depth + 1)
        except Exception:  # noqa: BLE001 - text that does not parse as code is not a finding
            continue
        if decision == "deny":
            snippet = " ".join(text.split())[:80]
            return (
                f"`{statement.program}` is given text that would be blocked if it ran as a command "
                f"(`{snippet}`): {reason} Confirm it is only data."
            )
    return None


def next_cwd(statement: shell_parse.Statement, cwd: Path | None, ctx: Context) -> Path | None:
    if "-" in statement.argv[1:]:
        return None  # `cd -` returns to a directory this command never named
    targets = [a for a in statement.argv[1:] if not a.startswith("-")]
    if not targets:
        return Path.home() if statement.dialect == "bash" else cwd
    return resolve_path(ctx.expand(targets[0]), cwd)


def assess(command: str, tool: str, ctx: Context, depth: int = 0) -> tuple[str, str]:
    dialect = "powershell" if tool == "PowerShell" else "bash"
    parsed = shell_parse.parse(command, dialect)
    ctx.untrusted.update(untrusted_names(command, parsed.statements))
    ctx.literal_values.extend(v for s in parsed.statements for v in s.literals.values())
    ctx.env_config = ctx.env_config or bool(GIT_ENV_CONFIG.search(command))
    asks: list[str] = []
    notes: list[str] = []
    # Carried text has no working directory of its own: relative targets stay unresolved.
    cwd: Path | None = None if ctx.offline else ctx.session_cwd
    for statement in parsed.statements:
        ctx.scope, ctx.dialect = statement.scope, statement.dialect
        for name, value in statement.literals.items():
            ctx.assign(name, ctx.expand(value))
            if secret_names([value], ctx):
                ctx.tainted.add(name.lower())
        if not statement.argv:
            continue
        if changes_any_variable(statement):
            ctx.forget_all()
        if not ctx.offline:
            reason = hidden_command(statement, ctx, depth)
            if reason:
                asks.append(reason)
        if re.search(r"[$%]", statement.argv[0]) and not (statement.dialect == "powershell" and len(statement.argv) == 1):
            # A program named by a variable (`G=git; $G push -f`, `& $g ...`) is judged by its value.
            # A lone PowerShell `$p` is an expression that prints, not a program; secret_print judges it.
            expanded = ctx.expand(statement.argv[0])
            if re.search(r"[$%]", expanded):
                names = secret_names(statement.argv, ctx, statement.dialect)
                if statement.dialect == "powershell" and reaches_transcript(statement) and names:
                    # `$x -join ','`, `$__sub1.Value`: a PowerShell expression prints its value.
                    return "deny", f"This PowerShell expression uses ${names[0]} and prints its value. Use it without printing it."
                if names and reaches_transcript(statement):
                    # `$TOOL "$tok"`: the unknown program may be a printer, so a human confirms it.
                    asks.append(f"`{statement.argv[0]}` runs a program the guard cannot resolve with ${names[0]} as an argument; it may print it. Confirm what it runs.")
                if DESTRUCTIVE_WORDS & {a.lower() for a in statement.argv[1:]}:
                    asks.append(f"`{statement.argv[0]}` names the program through a variable the guard cannot resolve, and its arguments look destructive. Confirm what it runs.")
                ctx.forget_all()  # an unknown program may be `source`, `eval` or `read`
                continue
            statement.argv[:1] = shell_parse.split_words(expanded, statement.dialect)
        if statement.assigns:
            for name in statement.assigns:
                ctx.forget(name)  # command output: value unknown here
                if reads_secret(statement, ctx):
                    ctx.tainted.add(name.lower())
        program = statement.program
        if program in CD_PROGRAMS:
            cwd = next_cwd(statement, cwd, ctx)
            continue
        if program == "git":
            sub, args, directory = git_invocation(statement.argv)
            repo = resolve_path(ctx.expand(directory), cwd) if directory else cwd
            alias = None
            # `-c alias.x=...` holds for this git process and the ones it starts, never for later commands:
            # `-c alias.a=b -c alias.b='!...' a` needs alias.b when `git b` is judged one level down.
            effective = {**ctx.inline_aliases, **inline_aliases(statement.argv)}
            definition = alias_definition(sub, args)
            if definition:
                alias = (f"defines the git alias `{definition[0]}`", alias_command(definition[1], []))
            elif sub and sub not in KNOWN_GIT_COMMANDS:
                value = effective.get(sub.lower())
                if value is None and not ctx.offline:
                    code, output = run_git(["config", "--get", f"alias.{sub}"], repo or ctx.session_cwd)
                    value = output if code == 0 and output else None
                if value is not None:
                    alias = (f"runs the git alias `{sub}`", alias_command(value, args))
            if ctx.env_config or any(a.startswith("--config-env") for a in statement.argv):
                asks.append(
                    "This git command takes configuration from the environment (GIT_CONFIG_*, --config-env), "
                    "which can make git run commands the guard cannot see. Confirm what it runs."
                )
            if alias:
                if depth >= MAX_ALIAS_DEPTH:
                    asks.append("This git alias expands through several other aliases. Confirm what it runs.")
                else:
                    # A shell alias runs in a child process: it reads these values, and nothing it assigns
                    # comes back. It shares the secret taint and the bulk-delete count.
                    inner = Context(
                        session_cwd=repo or ctx.session_cwd,
                        variables=dict(ctx.table()),
                        tainted=ctx.tainted,
                        delete_targets=ctx.delete_targets,
                        inline_aliases=effective,
                        untrusted=ctx.untrusted,
                        literal_values=ctx.literal_values,
                        env_config=ctx.env_config,
                        offline=ctx.offline,
                        scanned=ctx.scanned,
                    )
                    decision, reason = assess(alias[1], "Bash", inner, depth + 1)
                    if decision == "deny":
                        return "deny", f"This command {alias[0]}, which runs `{alias[1]}`: {reason}"
                    if decision == "ask":
                        asks.append(f"This command {alias[0]}, which runs `{alias[1]}`: {reason}")
            if repo is None and needs_repo_state(sub, args):
                asks.append("This git command depends on which repository it runs in, and that is unknown after the preceding cd. Confirm the branch it touches.")
                continue
            verdict = check_git(args, sub, repo or ctx.session_cwd)
            if verdict and verdict[0] == "deny":
                return verdict
            if verdict:
                asks.append(verdict[1])
        for check in (secret_print, prod_approval):
            reason = check(statement, ctx)
            if reason:
                return "deny", reason
        verdict = check_files(statement, cwd, ctx)
        if verdict and verdict[0] == "deny":
            return verdict
        if verdict:
            asks.append(verdict[1])
        for check in (azure_write, lambda s: gate_change(s, ctx)):
            reason = check(statement)
            if reason:
                asks.append(reason)
        if not ctx.offline:
            notes.extend(finish_notes(statement, ctx))
    if len(ctx.delete_targets) >= BULK_DELETE_THRESHOLD:
        shown = ", ".join(ctx.delete_targets[:6]) + (f", +{len(ctx.delete_targets) - 6} more" if len(ctx.delete_targets) > 6 else "")
        asks.append(f"This command removes {len(ctx.delete_targets)} paths ({shown}). Confirm the full list first.")
    if not parsed.complete and UNPARSED_DESTRUCTIVE.search(command):
        asks.append("This command could not be parsed reliably (unbalanced quotes or substitution) and looks destructive. Confirm what it runs.")
    if asks:
        return "ask", asks[0]
    if notes:
        return "allow", " ".join(dict.fromkeys(notes))
    return "allow", "Shell safety guard passed: no blocked pattern, bulk or unresolved delete, or ungoverned Azure resource write."


def main() -> int:
    payload = read_hook_input()
    command = extract_command(payload)
    if not command.strip():
        return 0
    ctx = Context(session_cwd=Path(str(payload.get("cwd") or os.getcwd())))
    try:
        decision, reason = assess(command, str(payload.get("tool_name") or "Bash"), ctx)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: a crashed hook fails open, this fails safe
        decision, reason = "ask", (
            f"The shell guard could not assess this command ({type(exc).__name__}: {exc}). Confirm what it runs."
        )
    builder = {"deny": deny_pre_tool, "ask": ask_pre_tool, "allow": allow_pre_tool}[decision]
    return emit_json(builder(reason))


if __name__ == "__main__":
    raise SystemExit(main())
