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
source is itself a secret.
"""

from __future__ import annotations

import os
import re
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
SECRET_FILE = re.compile(
    r"(?i)(?:^|[\\/])(?:\.env(?:\.(?!example$|sample$|template$|dist$)[\w.-]+)?"
    r"|[^\\/]+\.(?:pem|key|pfx|p12)|id_(?:rsa|dsa|ecdsa|ed25519)|\.git-credentials|[._]netrc"
    r"|credentials\.json|\.npmrc|msal_token_cache[^\\/]*|accesstokens\.json)$"
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
UNPARSED_DESTRUCTIVE = re.compile(
    r"\b(?:rm|rmdir|rd|del|erase|remove-item|ri|move-item|mv)\b"
    r"|\bgit\b.*\b(?:reset|clean|checkout|restore|branch|push|update-ref)\b"
    r"|\baz\b.*\b(?:delete|purge)\b",
    re.IGNORECASE | re.DOTALL,
)


# --- context ------------------------------------------------------------------

@dataclass
class Context:
    """Session facts, computed lazily: most commands never need git at all."""

    session_cwd: Path
    variables: dict[str, str] = field(default_factory=dict)  # literal values seen so far, lower-cased names
    tainted: set[str] = field(default_factory=set)  # variables holding a secret, lower-cased names
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

    def expand(self, text: str) -> str:
        """Substitute variables whose literal value this command set earlier."""
        def value(match: re.Match) -> str:
            name = (match.group(1) or match.group(2)).lower()
            return self.variables.get(name, match.group(0))
        return VARIABLE_REF.sub(value, text)


def resolve_path(raw: str, cwd: Path | None) -> Path | None:
    """Absolute path for a delete or move target, or None when it cannot be known here."""
    text = raw.strip()
    if not text:
        return None
    home, temp = str(Path.home()), tempfile.gettempdir()
    lowered = text.lower()
    for prefix, value in (
        ("$env:userprofile", home), ("$env:home", home), ("${home}", home), ("$home", home),
        ("%userprofile%", home), ("$env:temp", temp), ("$env:tmp", temp), ("%temp%", temp), ("%tmp%", temp),
    ):
        if lowered.startswith(prefix):
            text = value + text[len(prefix):]
            break
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
        if target in PROTECTED_BRANCHES:
            return "deny", "Direct pushes to protected branches are blocked. Use a task branch and PR policy path."
    if not refspecs:
        # A bare push sends the current branch to its upstream: check both names.
        if current_branch(repo) in PROTECTED_BRANCHES or upstream_branch(repo) in PROTECTED_BRANCHES:
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
        return ("delete" if program in PS_REMOVE else "move"), targets, recursive
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
    if kind != "delete":
        return None
    unresolved = [t for t, p in zip(targets, resolved) if p is None]
    if unresolved:
        return "ask", (
            f"`{program}` deletes an unresolved target ({', '.join(unresolved[:3])}). The guard cannot "
            "see what this removes until the shell expands it. Confirm the expanded path list first."
        )
    in_repo = [t for t, p in zip(targets, resolved) if not is_scratch(p, ctx)]
    if len(in_repo) >= BULK_DELETE_THRESHOLD:
        shown = ", ".join(in_repo[:6]) + (f", +{len(in_repo) - 6} more" if len(in_repo) > 6 else "")
        return "ask", f"`{program}` removes {len(in_repo)} paths in one command ({shown}). Confirm the full list first."
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
    """Whether the statement's output carries a secret (so a variable it fills is tainted)."""
    program, args = statement.program, statement.argv[1:]
    if program in VALUE_PRINTERS | FILE_PRINTERS | {"base64"} or not program or len(statement.argv) == 1:
        if secret_names(statement.argv, ctx, statement.dialect):
            return True
    if program in FILE_PRINTERS and any(is_secret_file(a) for a in args):
        return True
    if program == "printenv" and any(SECRET_NAME.search(a) for a in args):
        return True
    return cli_secret_reason(statement) is not None or bool(ENV_VARIABLE_CALL.search(" ".join(statement.argv)) and secret_names(statement.argv, ctx, statement.dialect))


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
    if not reaches_transcript(statement):
        return None
    program, argv = statement.program, statement.argv
    args = shell_parse.without_redirections(argv[1:])
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


def prod_approval(statement: shell_parse.Statement) -> str | None:
    text = " ".join(statement.argv).lower()
    if PROD_DEPLOY_APPROVAL.search(text) and AZURE_PIPELINE_APPROVAL.search(text):
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

def next_cwd(statement: shell_parse.Statement, cwd: Path | None, ctx: Context) -> Path | None:
    if "-" in statement.argv[1:]:
        return None  # `cd -` returns to a directory this command never named
    targets = [a for a in statement.argv[1:] if not a.startswith("-")]
    if not targets:
        return Path.home() if statement.dialect == "bash" else cwd
    return resolve_path(ctx.expand(targets[0]), cwd)


def assess(command: str, tool: str, ctx: Context) -> tuple[str, str]:
    dialect = "powershell" if tool == "PowerShell" else "bash"
    parsed = shell_parse.parse(command, dialect)
    asks: list[str] = []
    notes: list[str] = []
    cwd: Path | None = ctx.session_cwd
    for statement in parsed.statements:
        for name, value in statement.literals.items():
            expanded = ctx.expand(value)
            ctx.variables[name.lower()] = expanded
            if secret_names([value], ctx):
                ctx.tainted.add(name.lower())
        if not statement.argv:
            continue
        if statement.assigns:
            for name in statement.assigns:
                ctx.variables.pop(name.lower(), None)  # command output: value unknown here
                if reads_secret(statement, ctx):
                    ctx.tainted.add(name.lower())
        program = statement.program
        if program in CD_PROGRAMS:
            cwd = next_cwd(statement, cwd, ctx)
            continue
        if program == "git":
            sub, args, directory = git_invocation(statement.argv)
            repo = resolve_path(ctx.expand(directory), cwd) if directory else cwd
            if repo is None and needs_repo_state(sub, args):
                asks.append("This git command depends on which repository it runs in, and that is unknown after the preceding cd. Confirm the branch it touches.")
                continue
            verdict = check_git(args, sub, repo or ctx.session_cwd)
            if verdict and verdict[0] == "deny":
                return verdict
            if verdict:
                asks.append(verdict[1])
        for check in (secret_print, lambda s, _c: prod_approval(s)):
            reason = check(statement, ctx)
            if reason:
                return "deny", reason
        verdict = check_files(statement, cwd, ctx)
        if verdict and verdict[0] == "deny":
            return verdict
        if verdict:
            asks.append(verdict[1])
        reason = azure_write(statement)
        if reason:
            asks.append(reason)
        notes.extend(finish_notes(statement, ctx))
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
    decision, reason = assess(command, str(payload.get("tool_name") or "Bash"), ctx)
    builder = {"deny": deny_pre_tool, "ask": ask_pre_tool, "allow": allow_pre_tool}[decision]
    return emit_json(builder(reason))


if __name__ == "__main__":
    raise SystemExit(main())
