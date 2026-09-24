"""Regression tests for the shell guard and its parser.

Cases come from the 2026-09-23 audit (the 48 in the remediation plan's
Appendix A), the independent Codex review of the Codex guard port, and the new
rules. Every guard case goes through assess() against a real temporary git
repository, so rules that read repository state (bare push, squash-merged
branch deletion) see real refs. "Outside" is a path under the home directory
that is neither the repository nor a scratch root; nothing is ever deleted.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS))

import pre_tool_use_bash_guard as guard  # noqa: E402
import shell_parse  # noqa: E402

OUT = Path.home() / "guard-test-outside"
WINDOWS = os.name == "nt"


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def make_repo(path: Path) -> Path:
    origin = path.parent / f"{path.name}-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True)
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("start\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    git(path, "remote", "add", "origin", str(origin))
    git(path, "push", "-q", "-u", "origin", "main")
    git(path, "remote", "set-head", "origin", "main")
    return path


def git_bash(path: Path) -> str:
    """C:\\x\\y as Git Bash writes it: /c/x/y."""
    text = str(path).replace("\\", "/")
    return f"/{text[0].lower()}{text[2:]}"


class GuardTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="guard-tests-")
        cls.base = Path(cls._tmp.name).resolve()
        cls.repo = make_repo(cls.base / "repo")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def decide(self, command: str, tool: str = "Bash", repo: Path | None = None) -> str:
        return guard.assess(command, tool, guard.Context(session_cwd=repo or self.repo))[0]

    def assertDecisions(self, cases: list[tuple[str, str, str]]) -> None:
        for tool, command, expected in cases:
            with self.subTest(tool=tool, command=command):
                self.assertEqual(self.decide(command, tool), expected)


class AuditCaseTests(GuardTestCase):
    """Appendix A of the remediation plan: every case the audit probed, plus the new ones."""

    def test_recursive_delete_outside_the_repository(self) -> None:
        cases = [
            ("Bash", f"rm -rf {OUT}", "deny"),
            ("Bash", "rm -rf ~/guard-test-outside", "deny"),
            ("Bash", f"rm -r -f {OUT}", "deny"),
            ("PowerShell", f"Remove-Item -Recurse -Force {OUT}", "deny"),
            ("PowerShell", f"Remove-Item {OUT} -Recurse -Force", "deny"),
            ("PowerShell", f"Remove-Item -LiteralPath {OUT} -Recurse", "deny"),
            ("Bash", f"cmd /c rmdir /s /q {OUT}", "deny"),
            ("Bash", "rm -rf ./build", "allow"),
            ("Bash", "rm -rf C:/tmp/aac-cli", "allow"),
        ]
        if WINDOWS:
            cases += [
                ("Bash", f"rm -rf {git_bash(OUT)}", "deny"),
                ("Bash", f"cd {git_bash(OUT)} && rm -rf build", "deny"),
            ]
        self.assertDecisions(cases)

    def test_protected_branch_and_force_pushes(self) -> None:
        self.assertDecisions([
            ("Bash", "git push", "deny"),  # the class repository sits on main
            ("Bash", "git push -u origin main", "deny"),
            ("Bash", "git push origin main", "deny"),
            ("Bash", "git push origin feature:main", "deny"),
            ("Bash", "git push origin +main", "deny"),
            ("Bash", "git push origin +feature", "deny"),
            ("Bash", "git push origin HEAD:refs/heads/main", "deny"),
            ("Bash", f"git -C {self.repo} push origin main", "deny"),
            ("Bash", "git push --all origin", "deny"),
            ("Bash", "git push --mirror origin", "deny"),
            ("Bash", "git push -uf origin my-branch", "deny"),
            ("Bash", "git push --force-with-lease origin my-branch", "allow"),
            ("Bash", "git push --force-if-includes --force-with-lease origin my-branch", "allow"),
            ("Bash", "git push -u origin claude/topic", "allow"),
            ("Bash", "git push origin --delete claude/topic", "allow"),
        ])

    def test_discarding_work(self) -> None:
        self.assertDecisions([
            ("Bash", "git checkout -- src/app.py", "deny"),
            ("Bash", "git checkout -- .", "deny"),
            ("Bash", "git checkout .", "deny"),
            ("Bash", "git checkout --theirs src/app.py", "allow"),
            ("Bash", "git checkout --detach origin/main", "allow"),
            ("Bash", "git checkout origin/main -- src/app.py", "ask"),
            ("Bash", "git restore .", "deny"),
            ("Bash", "git restore --worktree .", "deny"),
            ("Bash", "git restore .gitignore", "allow"),
            ("Bash", "git restore --staged .", "allow"),
            ("Bash", "git reset HEAD~1 --hard", "deny"),
            ("Bash", "git reset --soft HEAD~1", "allow"),
            ("Bash", "git clean -f -d", "deny"),
            ("Bash", "git clean --force -d", "deny"),
            ("Bash", "git clean -n -d", "allow"),
            ("Bash", "git clean -f untracked.txt", "allow"),
        ])

    def test_secret_printing(self) -> None:
        self.assertDecisions([
            ("Bash", "cat src/auth/token_parser.py", "allow"),
            ("Bash", "set -euo pipefail; az account get-access-token --query expiresOn -o tsv", "allow"),
            ("Bash", "cat .env", "deny"),
            ("Bash", "cat .env.example", "allow"),
            ("PowerShell", "Get-Content $HOME\\.azure\\msal_token_cache.json", "deny"),
            ("Bash", "echo $AZURE_DEVOPS_EXT_PAT", "deny"),
            ("Bash", 'echo "Deploying with $GITHUB_TOKEN"', "deny"),
            ("Bash", "printenv", "deny"),
            ("Bash", "az account get-access-token", "deny"),
            ("Bash", "DSN=$(az keyvault secret show --vault-name kv --name X --query value -o tsv)", "allow"),
            ("Bash", "az keyvault secret show --vault-name kv --name X --query value -o tsv", "deny"),
            ("Bash", "auth=$(printf ':%s' \"$tok\" | base64 -w0)", "allow"),
            ("PowerShell", "$env:POSTGRES_DSN = az keyvault secret show --vault-name kv --name X --query value -o tsv", "allow"),
            ("PowerShell", "$env:GITHUB_TOKEN", "deny"),
            ("PowerShell", "Get-ChildItem env:", "deny"),
            ("PowerShell", "Get-ChildItem env:PATH", "allow"),
        ])

    def test_data_is_never_a_command(self) -> None:
        self.assertDecisions([
            ("Bash", "cat >> notes.md <<'EOF'\nRotate the PAT next week\nEOF", "allow"),
            ("Bash", "git commit -q -F - <<'EOF'\nfix(auth): refresh token on 401\nEOF", "allow"),
            ("Bash", "grep -rn 'git reset --hard' docs/", "allow"),
            ("Bash", f"git commit -m \"$(cat <<'EOF'\nfix\n\nrm -rf {OUT}\nEOF\n)\"", "allow"),
            ("PowerShell", f"@'\nRemove-Item -Recurse {OUT}\n'@ | Set-Content x.txt", "allow"),
        ])

    def test_azure_and_known_limits(self) -> None:
        self.assertDecisions([
            ("Bash", "az containerapp delete -n foo --resource-group AssetAllocationRG --yes", "ask"),
            ("Bash", "az group delete -n AssetAllocationRG --yes", "ask"),
            ("Bash", "az boards work-item update --id 1 --state Closed", "allow"),
            # A known limit of pattern rules: interpreter code is opaque here, and
            # auto mode's classifier still reviews it.
            ("Bash", f"python -c \"import shutil; shutil.rmtree(r'{OUT}')\"", "allow"),
        ])


class AdversarialTests(GuardTestCase):
    """Bypasses the independent Codex review found in the sibling Codex guard."""

    def test_quoted_heredoc_marker_does_not_hide_a_statement(self) -> None:
        self.assertEqual(self.decide(f"echo 'text\n<<EOF'\nrm -rf {OUT}\nEOF"), "deny")

    def test_arithmetic_shift_is_not_a_heredoc(self) -> None:
        self.assertEqual(self.decide(f"x=$(( 1 << 2 ))\nrm -rf {OUT}"), "deny")
        self.assertEqual(self.decide("echo $(( 1 << 2 ))"), "allow")

    def test_cmd_single_quotes_are_not_quotes(self) -> None:
        self.assertEqual(self.decide(f"cmd /c \"echo 'a & rmdir /s /q {OUT}'\""), "deny")

    def test_malformed_input_asks_only_when_destructive(self) -> None:
        self.assertEqual(self.decide("echo 'unfinished"), "allow")
        self.assertEqual(self.decide(f"echo 'x\nrm -rf {OUT}"), "ask")

    def test_bare_push_from_branch_tracking_a_protected_upstream(self) -> None:
        repo = make_repo(self.base / "tracking")
        git(repo, "switch", "-q", "-c", "feature")
        git(repo, "branch", "-q", "--set-upstream-to", "origin/main")
        self.assertEqual(self.decide("git push", repo=repo), "deny")
        git(repo, "branch", "-q", "--unset-upstream")
        self.assertEqual(self.decide("git push", repo=repo), "allow")

    def test_nested_shells_are_parsed(self) -> None:
        encoded = base64.b64encode(f"Remove-Item -Recurse {OUT}".encode("utf-16-le")).decode()
        self.assertDecisions([
            ("Bash", f"bash -c \"rm -rf {OUT}\"", "deny"),
            ("Bash", f"powershell -NoProfile -EncodedCommand {encoded}", "deny"),
            ("PowerShell", f"(Remove-Item {OUT} -Recurse)", "deny"),
            ("Bash", f"echo \"$(rm -rf {OUT})\"", "deny"),
        ])


class RuleTests(GuardTestCase):
    def test_deny_beats_ask_and_ask_beats_allow(self) -> None:
        self.assertEqual(self.decide("git push origin claude/topic && az group delete -n x --yes"), "ask")
        self.assertEqual(self.decide(f"az group delete -n x --yes; rm -rf {OUT}"), "deny")

    def test_unresolved_and_bulk_deletes_ask(self) -> None:
        self.assertDecisions([
            ("Bash", 'for d in a b; do rm -rf "$d"; done', "ask"),
            ("Bash", "rm a.txt b.txt c.txt", "ask"),
            ("Bash", "cd - && rm -rf build", "ask"),
            ("PowerShell", f"Get-ChildItem {OUT} | ForEach-Object {{ Remove-Item $_.FullName -Recurse }}", "ask"),
        ])

    def test_secret_named_data_files_are_secrets(self) -> None:
        self.assertDecisions([
            ("Bash", "head -c 30 /c/tmp/dsn_relay3202.txt", "deny"),
            ("Bash", "cat access-token.json", "deny"),
            ("Bash", "cat ~/.config/app/refresh_token", "deny"),
            ("Bash", "cat session.tok", "deny"),
            ("PowerShell", "Get-Content C:\\tmp\\secrets.yaml", "deny"),
            ("PowerShell", "$v = Get-Content C:\\tmp\\db_password.txt; Write-Output $v", "deny"),
            ("Bash", "wc -c < session.tok", "allow"),
            ("Bash", "cat token_audit.py", "allow"),
            ("Bash", "head -40 claude_code_token_audit.py", "allow"),
            ("Bash", "cat token_usage.json", "allow"),
            ("Bash", "cat secrets.example.json", "allow"),
            ("Bash", "cat patch.txt path.txt", "allow"),
            ("PowerShell", "Select-String -Pattern token -Path build.log", "allow"),
        ])

    def test_credential_printing_clis_are_secret_prints(self) -> None:
        show = "az containerapp secret show -n app -g rg --secret-name consumer-key"
        self.assertDecisions([
            ("Bash", f"{show} --query value -o tsv 2>&1 | sed 's/^/v=/'", "deny"),
            ("PowerShell", f"{show} --query value -o tsv", "deny"),
            ("Bash", f"v=$({show} --query value -o tsv); curl -s -H \"X-Key: $v\" https://example.invalid", "allow"),
            ("Bash", f"{show} --query name -o tsv", "allow"),
            ("Bash", "az storage account keys list -n acct", "deny"),
            ("Bash", "az storage account keys list -n acct --query [].keyName -o tsv", "allow"),
            ("Bash", "az storage account show-connection-string -n acct -o tsv", "deny"),
            ("Bash", "az ad sp create-for-rbac -n deployer", "deny"),
            ("Bash", "az ad app credential list --id x --query [].endDateTime -o tsv", "allow"),
            ("Bash", "az account get-access-token --query accessToken -o tsv", "deny"),
            ("Bash", "az account get-access-token --query expiresOn -o tsv", "allow"),
            ("Bash", "az keyvault secret list --vault-name kv --query [].name -o tsv", "allow"),
            ("Bash", "az acr login -n reg --expose-token", "deny"),
            ("Bash", "gh auth token", "deny"),
            ("Bash", "gh auth status --show-token", "deny"),
            ("Bash", "gh auth status", "allow"),
            ("Bash", "gh auth token | docker login ghcr.io -u me --password-stdin", "allow"),
            ("Bash", "kubectl get secret db -o jsonpath='{.data.password}'", "deny"),
            ("Bash", "kubectl get secret db -o yaml", "deny"),
            ("Bash", "kubectl get secrets", "allow"),
            ("Bash", "printf 'protocol=https\\nhost=github.com\\n' | git credential fill", "deny"),
            ("Bash", "aws configure get aws_secret_access_key", "deny"),
            ("Bash", "aws configure get region", "allow"),
        ])

    def test_later_stages_that_count_or_mask_stop_the_values(self) -> None:
        self.assertDecisions([
            ("Bash", "env | grep -i 'AZURE_DEVOPS\\|ADO_' | sed 's/=.*/=<set>/'", "allow"),
            ("Bash", "env | grep -c PR_BODY", "allow"),
            ("Bash", "env | grep -q AZURE_DEVOPS_EXT_PAT && echo set", "allow"),
            ("Bash", "env | cut -d= -f1 | sort", "allow"),
            ("Bash", "env | awk -F= '{print $1}'", "allow"),
            ("Bash", "echo \"$GITHUB_TOKEN\" | grep -c . | tee n.txt", "allow"),
            ("Bash", "env | grep -i azure", "deny"),
            ("Bash", "env | cut -d= -f2", "deny"),
            ("Bash", "env | grep PAT | sed 's/x/y/'", "deny"),
            ("Bash", "env | grep -i ^AGENTCOORD | sed 's/\\(TOKEN\\|SECRET\\|KEY\\)=.*/\\1=<redacted>/'", "deny"),
            ("Bash", "env | grep -iE 'CLAUDE|CODEX' | sed -E 's/(TOKEN|KEY|SECRET)=.*/\\1=<redacted>/'", "deny"),
            ("Bash", "env | sed 's/^\\([^=]*\\)=.*/\\1/'", "allow"),
            ("Bash", "env | sed -E 's/^([^=]*)=.*$/\\1/'", "allow"),
            ("Bash", "compgen -e | grep AZURE", "allow"),
            ("Bash", "env | grep PAT > vars.txt", "allow"),
            ("PowerShell", "Get-ChildItem env: | Select-Object -ExpandProperty Name", "allow"),
            ("PowerShell", "Get-ChildItem env: | Where-Object Name -like '*PAT*' | Select-Object Name", "allow"),
            ("PowerShell", "Get-ChildItem env: | Where-Object Name -like '*PAT*'", "deny"),
            ("PowerShell", "Get-ChildItem env: | Select-Object Name, Value", "deny"),
            ("PowerShell", "Get-ChildItem env: | ForEach-Object { \"$($_.Name)=$($_.Value)\" }", "deny"),
            ("PowerShell", "Get-Content .env | Select-Object -First 3", "deny"),
        ])

    def test_conditions_and_lengths_do_not_print_secrets(self) -> None:
        fetch = "$p = [Environment]::GetEnvironmentVariable('AZURE_DEVOPS_EXT_PAT','Machine')"
        self.assertDecisions([
            ("PowerShell", f"{fetch}; if ($p) {{ Write-Host \"found PAT (length $($p.Length))\" }} else {{ Write-Host 'none' }}", "allow"),
            ("PowerShell", f"{fetch}; if (-not $p) {{ exit 1 }}; while ($p -eq '') {{ break }}", "allow"),
            ("PowerShell", f"{fetch}; Write-Host \"PAT: $p\"", "deny"),
            ("PowerShell", f"{fetch}; $p", "deny"),
            ("PowerShell", f"{fetch}; Write-Host ($p)", "deny"),
            ("Bash", "echo \"length ${#GITHUB_TOKEN}\"", "allow"),
            ("Bash", "echo \"$GITHUB_TOKEN.count\"", "deny"),
        ])

    def test_moves_outside_are_denied(self) -> None:
        self.assertDecisions([
            ("Bash", f"mv {OUT}/a.txt ./a.txt", "deny"),
            ("Bash", f"mv ./a.txt {OUT}/a.txt", "deny"),
            ("Bash", "mv a.txt b.txt", "allow"),
        ])

    def test_branch_force_delete_allows_only_squash_merged_branches(self) -> None:
        repo = make_repo(self.base / "squash")
        git(repo, "switch", "-q", "-c", "topic")
        (repo / "feature.txt").write_text("done\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "topic work")
        git(repo, "switch", "-q", "-c", "wip", "main")
        (repo / "wip.txt").write_text("unfinished\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "wip")
        git(repo, "switch", "-q", "main")
        # Land topic's content on main as one new commit: a squash merge.
        git(repo, "checkout", "topic", "--", "feature.txt")
        git(repo, "commit", "-q", "-m", "squash topic")
        git(repo, "push", "-q", "origin", "main")
        self.assertEqual(self.decide("git branch -D topic", repo=repo), "allow")
        self.assertEqual(self.decide("git branch -D wip", repo=repo), "ask")
        self.assertEqual(self.decide("git branch --delete --force wip", repo=repo), "ask")
        self.assertEqual(self.decide("git branch -d wip", repo=repo), "allow")
        self.assertEqual(self.decide("git branch -D no-such-branch 2>&1", repo=repo), "allow")

    def test_finish_commands_carry_notes(self) -> None:
        decision, reason = guard.assess("gh pr create --title x", "Bash", guard.Context(session_cwd=self.repo))
        self.assertEqual(decision, "allow")
        self.assertIn("Pull request title rule", reason)


class ReplayRegressionTests(GuardTestCase):
    """Decisions the historical replay of 2,649 commands showed the first rewrite got wrong."""

    def test_a_variable_filled_from_a_secret_is_a_secret(self) -> None:
        fetch = "tok=$(powershell.exe -NoProfile -Command \"[Environment]::GetEnvironmentVariable('AZURE_DEVOPS_EXT_PAT','Machine')\" | tr -d '\\r')"
        self.assertDecisions([
            ("Bash", f"{fetch}; echo $tok", "deny"),
            ("Bash", f"{fetch}; auth=$(printf ':%s' \"$tok\" | base64 -w0); echo \"$auth\"", "deny"),
            ("Bash", f"{fetch}; auth=$(printf ':%s' \"$tok\" | base64 -w0); curl -s -H \"Authorization: Basic $auth\" https://example.invalid", "allow"),
            ("PowerShell", "$pat = [Environment]::GetEnvironmentVariable('AZURE_DEVOPS_EXT_PAT','Machine'); Write-Output $pat", "deny"),
        ])

    def test_piping_or_redirecting_a_secret_is_not_printing_it(self) -> None:
        self.assertDecisions([
            ("Bash", 'echo "$GITHUB_TOKEN" | docker login ghcr.io -u me --password-stdin', "allow"),
            ("Bash", 'echo "$GITHUB_TOKEN" | tee token.txt', "deny"),
            ("Bash", "DSN=$(az keyvault secret show --vault-name kv --name X --query value -o tsv); printf '%s' \"$DSN\" > .pgdsn", "allow"),
            ("Bash", "DSN=$(az keyvault secret show --vault-name kv --name X --query value -o tsv) python migrate.py", "allow"),
        ])

    def test_literal_assignments_are_followed(self) -> None:
        self.assertDecisions([
            ("Bash", 'S="C:/tmp/stage"; rm -rf "$S/build"', "allow"),
            ("Bash", f'T="{OUT.as_posix()}"; rm -rf "$T"', "deny"),
            ("PowerShell", f'$c = "{OUT}"; Remove-Item $c -Recurse -Force', "deny"),
            ("PowerShell", '$sp = "C:\\tmp\\scratch"; Remove-Item "$sp\\canary" -Recurse', "allow"),
        ])

    def test_scratch_only_bulk_deletes_do_not_ask(self) -> None:
        self.assertEqual(self.decide("rm C:/tmp/a.json C:/tmp/b.json C:/tmp/c.sh"), "allow")

    def test_broad_checkout_from_a_revision_is_denied(self) -> None:
        self.assertEqual(self.decide("git checkout HEAD -- ."), "deny")

    def test_redirections_are_not_git_arguments(self) -> None:
        repo = make_repo(self.base / "redirect")
        git(repo, "switch", "-q", "-c", "claude/topic")
        self.assertEqual(self.decide("git push --force-with-lease 2>&1", repo=repo), "allow")


class AdversarialReviewTests(GuardTestCase):
    """Bypasses and false positives from the independent adversarial review of this rewrite."""

    def test_a_program_named_by_a_variable_is_judged_by_its_value(self) -> None:
        self.assertDecisions([
            ("Bash", "G=git; $G push --force origin main", "deny"),
            ("Bash", 'CMD="git push"; $CMD --force origin topic', "deny"),
            ("PowerShell", "$g = 'git'; & $g push --force origin main", "deny"),
            ("Bash", f'R=rm; $R -rf "{OUT.as_posix()}"', "deny"),
            ("Bash", "$UNKNOWN push --force", "ask"),
            ("Bash", "$PYTHON -m pytest -q", "allow"),
        ])

    def test_env_does_not_hide_the_command(self) -> None:
        self.assertDecisions([
            ("Bash", "env printenv", "deny"),
            ("Bash", "env git push --force origin main", "deny"),
            ("Bash", "env -i PATH=/usr/bin git reset --hard", "deny"),
            ("Bash", f"env rm -rf {OUT.as_posix()}", "deny"),
            ("Bash", "env PYTHONPATH=. py -m pytest", "allow"),
        ])

    def test_git_aliases_are_judged_by_what_they_run(self) -> None:
        repo = make_repo(self.base / "aliases")
        git(repo, "config", "alias.hardreset", "!git reset --hard")
        git(repo, "config", "alias.fp", "push --force")
        git(repo, "config", "alias.st", "status --short")
        self.assertEqual(self.decide("git hardreset", repo=repo), "deny")
        self.assertEqual(self.decide("git fp origin claude/topic", repo=repo), "deny")
        self.assertEqual(self.decide("git st", repo=repo), "allow")
        self.assertEqual(self.decide("git config alias.nuke '!git clean -fdx'", repo=repo), "deny")
        self.assertEqual(self.decide("git -c alias.x='!git reset --hard' x", repo=repo), "deny")
        self.assertEqual(self.decide("git config alias.lg 'log --oneline --graph'", repo=repo), "allow")

    def test_forced_checkout_switch_worktree_and_stash_discards(self) -> None:
        self.assertDecisions([
            ("Bash", "git checkout -f", "deny"),
            ("Bash", "git checkout -f main", "deny"),
            ("Bash", "git switch -f main", "deny"),
            ("Bash", "git switch --discard-changes main", "deny"),
            ("Bash", "git switch -c claude/new-topic", "allow"),
            ("Bash", "git worktree remove ../scratch-wt", "allow"),
            ("Bash", "git stash clear", "ask"),
            ("Bash", "git stash drop", "ask"),
            ("Bash", "git stash pop", "allow"),
        ])

    def test_forced_worktree_removal_asks_only_when_work_would_be_lost(self) -> None:
        repo = make_repo(self.base / "wt-main")
        clean = self.base / "wt-clean"
        dirty = self.base / "wt-dirty"
        git(repo, "worktree", "add", "-q", "-b", "clean-topic", str(clean))
        git(repo, "worktree", "add", "-q", "-b", "dirty-topic", str(dirty))
        (clean / ".gitignore").write_text("build/\n", encoding="utf-8")
        git(clean, "add", ".gitignore")
        git(clean, "commit", "-q", "-m", "ignore build")
        (clean / "build").mkdir()
        (clean / "build" / "out.bin").write_text("x", encoding="utf-8")
        (dirty / "notes.txt").write_text("unsaved\n", encoding="utf-8")
        self.assertEqual(self.decide(f"git worktree remove --force {clean.as_posix()}", repo=repo), "allow")
        self.assertEqual(self.decide(f"git worktree remove --force {dirty.as_posix()}", repo=repo), "ask")
        self.assertEqual(self.decide(f"git worktree remove -f {(self.base / 'gone').as_posix()}", repo=repo), "allow")
        self.assertEqual(self.decide('git worktree remove --force "$WT"', repo=repo), "ask")

    def test_comma_joined_powershell_paths_are_each_a_target(self) -> None:
        self.assertDecisions([
            ("PowerShell", f"Remove-Item -Path ./bin,{OUT.as_posix()} -Recurse -Force", "deny"),
            ("PowerShell", "Remove-Item -Path a.txt,b.txt,c.txt", "ask"),
        ])

    def test_launchers_and_find_do_not_hide_their_commands(self) -> None:
        self.assertDecisions([
            ("PowerShell", f"Start-Process powershell -ArgumentList '-Command','Remove-Item {OUT} -Recurse -Force' -Wait", "deny"),
            ("PowerShell", "Start-Process git -ArgumentList 'push','--force' -NoNewWindow", "deny"),
            ("Bash", "find . -delete", "deny"),
            ("Bash", f"find {OUT.as_posix()} -name '*.log' -delete", "deny"),
            ("Bash", "find build -name '*.pyc' -delete", "allow"),
            ("Bash", "find /tmp/x -exec rm -rf {} \\;", "ask"),
            ("Bash", "find . -name '*.py' -exec grep -l TODO {} +", "allow"),
            ("Bash", "find . -type f | xargs -I {} rm -rf {}", "ask"),
            ("Bash", "timeout -s KILL 10 git push --force", "deny"),
        ])

    def test_unresolved_move_destinations_ask(self) -> None:
        self.assertEqual(self.decide('mv notes.txt "$UNKNOWN_DEST/notes.txt"'), "ask")

    def test_more_credential_readers(self) -> None:
        self.assertDecisions([
            ("Bash", "gcloud secrets versions access latest --secret=my-secret", "deny"),
            ("Bash", "aws secretsmanager get-secret-value --secret-id foo", "deny"),
            ("Bash", "aws ssm get-parameter --name /x/y --with-decryption", "deny"),
            ("Bash", "aws ssm get-parameter --name /x/y", "allow"),
            ("Bash", "op item get MyLogin --fields password", "deny"),
            ("Bash", "az functionapp config appsettings list -n app -g rg", "deny"),
            ("Bash", "az webapp config appsettings list -n app -g rg --query [].name -o tsv", "allow"),
            ("Bash", "tok=$(az keyvault secret show --vault-name kv --name X --query value -o tsv); declare -p tok", "deny"),
            ("Bash", "declare -p", "deny"),
            ("Bash", "declare -p BUILD_NUMBER", "allow"),
            ("PowerShell", "$p = [Environment]::GetEnvironmentVariable('AZURE_DEVOPS_EXT_PAT'); Get-Variable -Name p -ValueOnly", "deny"),
            ("PowerShell", "$p = [Environment]::GetEnvironmentVariable('AZURE_DEVOPS_EXT_PAT'); (Get-Variable p).Value", "deny"),
            ("Bash", "cat ~/.kube/config", "deny"),
            ("PowerShell", "Get-Content ~/.docker/config.json", "deny"),
            ("Bash", "cat ~/.config/gh/hosts.yml", "deny"),
            ("Bash", "cat .env2", "deny"),
            ("Bash", "cat .envrc", "deny"),
            ("Bash", "cat .env.example", "allow"),
        ])

    def test_production_approval_through_a_variable_is_denied(self) -> None:
        self.assertEqual(self.decide('ENV=prod-east; az pipelines release approve --environment "$ENV" --id 123'), "deny")

    def test_protected_branch_names_are_case_folded(self) -> None:
        self.assertEqual(self.decide("git push origin feature:MAIN"), "deny")

    def test_bulk_deletes_count_across_statements(self) -> None:
        self.assertDecisions([
            ("Bash", "rm a.txt; rm b.txt; rm c.txt", "ask"),
            ("Bash", "rm a.txt && rm b.txt", "allow"),
        ])

    def test_deleting_the_repository_itself_is_denied(self) -> None:
        self.assertDecisions([
            ("Bash", "rm -rf .", "deny"),
            ("Bash", "rm -rf .git", "deny"),
            ("PowerShell", "Remove-Item -Recurse -Force .git", "deny"),
            ("Bash", "rm -rf build", "allow"),
            ("Bash", "rm .git/index.lock", "allow"),
        ])

    def test_bash_temp_variables_are_scratch(self) -> None:
        self.assertDecisions([
            ("Bash", 'rm -rf "$TEMP/mytempscratch"', "allow"),
            ("Bash", 'rm -rf "$TMPDIR/x" "$TMP/y" "${TEMP}/z"', "allow"),
            ("Bash", 'rm -rf "$TEMPLATE_DIR/x"', "ask"),
        ])


class SubstitutionTests(GuardTestCase):
    """A command substitution's output goes where the outer command sends it."""

    def test_substitutions_passed_to_a_consumer_are_not_prints(self) -> None:
        token = "$(az account get-access-token --query accessToken -o tsv)"
        self.assertDecisions([
            ("Bash", 'psql "$(cat "$SECRET_FILE")" -c "select 1"', "allow"),
            ("Bash", f'curl -s -H "Authorization: Bearer {token}" https://example.invalid', "allow"),
            ("PowerShell", f'Invoke-RestMethod -Uri https://example.invalid -Headers @{{Authorization = "Bearer {token}"}}', "allow"),
            ("PowerShell", f'$h = "Bearer {token}"; Invoke-RestMethod -Headers @{{Authorization = $h}} -Uri https://example.invalid', "allow"),
        ])

    def test_substitutions_passed_to_a_printer_are_prints(self) -> None:
        token = "$(az account get-access-token --query accessToken -o tsv)"
        self.assertDecisions([
            ("Bash", f'echo "{token}"', "deny"),
            ("Bash", "echo $(cat .env)", "deny"),
            ("Bash", "echo `cat .env`", "deny"),
            ("Bash", "diff <(az keyvault secret show --vault-name kv -n x --query value -o tsv) expected.txt", "deny"),
            ("PowerShell", f"Write-Output ({token[2:-1]})", "deny"),
            ("PowerShell", f'Write-Host "Token: {token}"', "deny"),
            ("PowerShell", "(Get-Content .env) -join ','", "deny"),
            ("PowerShell", f'$h = "Bearer {token}"; Write-Output $h', "deny"),
        ])

    def test_unquoted_heredoc_bodies_run_their_substitutions(self) -> None:
        self.assertDecisions([
            ("Bash", f"cat <<EOF\n$(rm -rf {OUT.as_posix()})\nEOF", "deny"),
            ("Bash", f"cat <<'EOF'\n$(rm -rf {OUT.as_posix()})\nEOF", "allow"),
            ("Bash", f"cat <<\\EOF\n$(rm -rf {OUT.as_posix()})\nEOF", "allow"),
            ("Bash", "cat <<EOF\ntoken: $(az account get-access-token --query accessToken -o tsv)\nEOF", "deny"),
            ("Bash", "cat > notes.txt <<EOF\nbuilt on $(date)\nEOF", "allow"),
        ])


class ReviewRoundTwoTests(GuardTestCase):
    """Bypasses from the second round of the adversarial review."""

    def test_inline_alias_chains_keep_their_definitions(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            ("Bash", f"git -c alias.a=b -c alias.b='!rm -rf {target}' a", "deny"),
            ("Bash", f"git -c alias.a=b -c alias.b=c -c alias.c='!rm -rf {target}' a", "deny"),
            ("Bash", "git -c alias.a=b -c alias.b='status --short' a", "allow"),
        ])

    def test_an_unresolved_program_with_a_secret_argument_asks(self) -> None:
        fetch = "tok=$(az keyvault secret show --vault-name kv --name X --query value -o tsv)"
        self.assertDecisions([
            ("Bash", f'{fetch}; $UNKNOWN "$tok"', "ask"),
            ("Bash", f'{fetch}; $UNKNOWN "$tok" > out.txt', "allow"),
            ("Bash", "$UNKNOWN --version", "allow"),
        ])

    def test_heredoc_bodies_expand_plain_variables(self) -> None:
        fetch = "tok=$(az keyvault secret show --vault-name kv --name X --query value -o tsv)"
        self.assertDecisions([
            ("Bash", f"{fetch}; cat <<EOF\nBearer $tok\nEOF", "deny"),
            ("Bash", "cat <<EOF\nuser: $GITHUB_TOKEN\nEOF", "deny"),
            ("Bash", f"{fetch}; cat <<'EOF'\nBearer $tok\nEOF", "allow"),
            ("Bash", f"{fetch}; cat > .req <<EOF\nBearer $tok\nEOF", "allow"),
            ("Bash", f"{fetch}; curl -s -d @- https://example.invalid <<EOF\n{{\"t\": \"$tok\"}}\nEOF", "allow"),
            ("Bash", "cat <<EOF\nbuilt $BUILD_ID on $HOSTNAME\nEOF", "allow"),
        ])

    def test_heredoc_references_belong_to_the_statement_that_opened_it(self) -> None:
        """Review round 3: a trailer after <<EOF on the same line must not take the body's references."""
        fetch = "tok=$(az keyvault secret show --vault-name kv --name X --query value -o tsv)"
        self.assertDecisions([
            ("Bash", f"{fetch}; cat <<EOF; true\nBearer $tok\nEOF", "deny"),
            ("Bash", f"{fetch}; cat <<EOF | tee copy.txt\nBearer $tok\nEOF", "deny"),
            ("Bash", f"{fetch}; cat <<EOF && echo done\nBearer $tok\nEOF", "deny"),
            ("Bash", f"{fetch}; curl -s -d @- https://example.invalid <<EOF; echo sent\n{{\"t\": \"$tok\"}}\nEOF", "allow"),
        ])
        statements = shell_parse.parse("cat <<EOF; true\nBearer $tok\nEOF", "bash").statements
        self.assertEqual({s.program: s.heredoc_refs for s in statements}, {"cat": ("tok",), "true": ()})

    def test_aliases_see_the_outer_commands_taint(self) -> None:
        fetch = "tok=$(az keyvault secret show --vault-name kv --name X --query value -o tsv)"
        self.assertDecisions([
            ("Bash", f"{fetch}; git -c alias.leak='!echo $tok' leak", "deny"),
            ("Bash", f"{fetch}; git -c alias.use='!curl -s -H \"Bearer $tok\" https://example.invalid' use", "allow"),
        ])

    def test_bulk_deletes_count_through_aliases(self) -> None:
        self.assertEqual(self.decide("rm c.txt; git -c alias.rm2='!rm a.txt b.txt' rm2"), "ask")

    def test_glued_env_split_string_is_unwrapped(self) -> None:
        self.assertDecisions([
            ("Bash", "env -S'printenv'", "deny"),
            ("Bash", "env --split-string='git push --force origin main'", "deny"),
        ])

    def test_an_internal_error_asks_instead_of_failing_open(self) -> None:
        saved = guard.assess
        guard.assess = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            payload = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": str(self.repo)}
            out = io.StringIO()
            with mock.patch.object(guard, "read_hook_input", return_value=payload), contextlib.redirect_stdout(out):
                guard.main()
        finally:
            guard.assess = saved
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "ask")
        self.assertIn("could not assess", decision["permissionDecisionReason"])


class ReviewRoundFourTests(GuardTestCase):
    """Round 4: values the command may change unseen, and code the parser did not read."""

    def test_assignments_in_a_child_process_stay_there(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            # The review's case. The alias body runs in a child shell, so $S is still the outside path;
            # its `S=build` is a mention the guard cannot place, so it asks.
            ("Bash", f'S="{target}"; git -c alias.wash="!S=build; true" wash; rm -rf "$S/x"', "ask"),
            ("Bash", f"S=\"{target}\"; sh -c 'S=build'; rm -rf \"$S/x\"", "deny"),
            ("Bash", f"S=\"{target}\"; bash -c 'S=build; true'; rm -rf \"$S/x\"", "deny"),
            ("Bash", f'S="{target}"; ( S=build ); rm -rf "$S/x"', "deny"),
            ("Bash", f'S="{target}"; echo $(S=build); rm -rf "$S/x"', "deny"),
            ("Bash", f"S=\"{target}\"; find . -maxdepth 0 -exec sh -c 'S=build' \\; ; rm -rf \"$S/x\"", "deny"),
            ("PowerShell", f"$S='{OUT}'; pwsh -Command '$S=\"build\"'; Remove-Item -Recurse -Force \"$S\\x\"", "deny"),
            # Inside the child its own assignments hold.
            ("Bash", f"sh -c 'S={target}; rm -rf \"$S/x\"'", "deny"),
            ("Bash", "sh -c 'S=build; rm -rf \"$S/x\"'", "allow"),
            # A group runs in this shell, so its assignment holds here.
            ("Bash", f'S="{target}"; {{ S=build; }}; rm -rf "$S/x"', "allow"),
        ])

    def test_a_configured_alias_cannot_change_the_callers_values(self) -> None:
        repo = make_repo(self.base / "wash")
        git(repo, "config", "alias.wash", "!S=build; true")
        target = OUT.as_posix()
        self.assertEqual(self.decide(f'S="{target}"; git wash; rm -rf "$S/x"', repo=repo), "deny")

    def test_values_the_command_may_change_unseen_are_not_used(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            ("Bash", f'S=build; printf -v S %s "{target}"; rm -rf "$S/x"', "ask"),
            ("Bash", f'S=build; printf -vS %s "{target}"; rm -rf "$S/x"', "ask"),
            ("Bash", f'S=build; read S <<< "{target}"; rm -rf "$S/x"', "ask"),
            ("Bash", f'S=build; for S in "{target}"; do :; done; rm -rf "$S/x"', "ask"),
            ("Bash", 'S=build; unset S; rm -rf "$S/x"', "ask"),
            ("Bash", f'S=build; declare -n R=S; R="{target}"; rm -rf "$S/x"', "ask"),
            ("Bash", f'S=build; mapfile -t S <<< "{target}"; rm -rf "$S/x"', "ask"),
            ("Bash", 'S=build; S+=/../../..; rm -rf "$S/x"', "ask"),
            ("Bash", f'S=build; true | S="{target}"; rm -rf "$S/x"', "ask"),
            ("Bash", f'S="{target}"; f() {{ S=build; }}; rm -rf "$S/x"', "ask"),
            ("Bash", f"S=build; trap 'S={target}' DEBUG; rm -rf \"$S/x\"", "ask"),
            ("Bash", f'S=build; V=S; eval "$V={target}"; rm -rf "$S/x"', "ask"),
            ("Bash", 'S=build; source ./setup.sh; rm -rf "$S/x"', "ask"),
            ("PowerShell", f"$S='build'; Set-Variable -Name S -Value '{OUT}'; Remove-Item -Recurse -Force \"$S\\x\"", "ask"),
            ("PowerShell", f"$S='build'; Write-Output '{OUT}' -OutVariable S; Remove-Item -Recurse -Force \"$S\\x\"", "ask"),
            ("PowerShell", f"$S='build'; Write-Output '{OUT}' -OutV S; Remove-Item -Recurse -Force \"$S\\x\"", "ask"),
            ("PowerShell", f"$S='build'; foreach ($S in @('{OUT}')) {{ }}; Remove-Item -Recurse -Force \"$S\\x\"", "ask"),
            ("PowerShell", f"$S='build'; & {{ $S='{OUT}' }}; Remove-Item -Recurse -Force \"$S\\x\"", "ask"),
            ("PowerShell", f"$S='build'; [string]$S = '{OUT}'; Remove-Item -Recurse -Force \"$S\\x\"", "ask"),
            ("PowerShell", "$S='build'; . .\\setup.ps1; Remove-Item -Recurse -Force \"$S\\x\"", "ask"),
            # Seen and applied: literal `eval` code and `$global:` run in this scope.
            ("Bash", f"S=build; eval 'S={target}'; rm -rf \"$S/x\"", "deny"),
            ("PowerShell", f"$S='build'; $global:S='{OUT}'; Remove-Item -Recurse -Force \"$S\\x\"", "deny"),
        ])

    def test_bash_names_are_case_sensitive(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            ("Bash", f'OUT="{target}"; out=build; rm -rf "$OUT/x"', "deny"),
            ("Bash", f'out="{target}"; OUT=build; rm -rf "$OUT/x"', "allow"),
        ])

    def test_short_powershell_names_are_not_confused_with_paths(self) -> None:
        self.assertDecisions([
            ("PowerShell", '$p = "C:\\tmp\\p"; Remove-Item "$p\\x" -Recurse', "allow"),
            ("PowerShell", '$d = "C:\\tmp\\d.d"; Remove-Item $d -Recurse', "allow"),
        ])

    def test_function_bodies_eval_and_trap_are_read(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            ("Bash", f"f() {{ rm -rf {target}/x; }}; f", "deny"),
            ("Bash", f"function f {{ rm -rf {target}/x; }}; f", "deny"),
            ("Bash", f"f()\n{{\n  rm -rf {target}/x\n}}\nf", "deny"),
            ("Bash", f"eval 'rm -rf {target}/x'", "deny"),
            ("Bash", f"trap 'rm -rf {target}/x' EXIT", "deny"),
            ("Bash", "f() { rm -rf build; }; f", "allow"),
            ("Bash", "trap 'rm -f .lock' EXIT", "allow"),
        ])

    def test_git_runs_code_from_its_options_and_environment(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            ("Bash", f"git -c core.fsmonitor='rm -rf {target}/x' status", "deny"),
            ("Bash", f"git -c core.pager='rm -rf {target}/x' log", "deny"),
            ("Bash", f"git -c pager.log='rm -rf {target}/x' log", "deny"),
            ("Bash", f"git rebase -x 'rm -rf {target}/x' HEAD~1", "deny"),
            ("Bash", f"git rebase --exec='rm -rf {target}/x' HEAD~1", "deny"),
            ("Bash", f"git bisect run rm -rf {target}/x", "deny"),
            ("Bash", f"git submodule foreach 'rm -rf {target}/x'", "deny"),
            ("Bash", f"git filter-branch --tree-filter 'rm -rf {target}/x' HEAD", "deny"),
            ("Bash", f"git difftool -x 'rm -rf {target}/x' HEAD~1", "deny"),
            ("Bash", f"GIT_SEQUENCE_EDITOR='rm -rf {target}/x' git rebase -i HEAD~2", "deny"),
            ("Bash", f"export GIT_SSH_COMMAND='rm -rf {target}/x'; git fetch", "deny"),
            # A pager's output reaches the transcript; a credential helper's goes back to git.
            ("Bash", "git -c core.pager='echo $GITHUB_TOKEN' log", "deny"),
            ("Bash", "git -c credential.helper='!f() { echo \"password=$GITHUB_TOKEN\"; }; f' fetch", "allow"),
            ("Bash", "git -c core.pager=cat log -1", "allow"),
            ("Bash", "GIT_EDITOR=true git rebase --continue", "allow"),
        ])

    def test_alias_definitions_and_inline_alias_scope(self) -> None:
        target = OUT.as_posix()
        repo = make_repo(self.base / "alias-scope")
        git(repo, "config", "alias.y", f"!rm -rf {target}/x")
        cases = [
            (f"git config set alias.z '!rm -rf {target}/x'; git z", "deny"),
            (f"git config --comment note alias.z '!rm -rf {target}/x'", "deny"),
            # `-c alias.y=status` holds for that one git process; the next `git y` runs the configured alias.
            ("git -c alias.y=status status; git y", "deny"),
            ("git config set alias.lg 'log --oneline'", "allow"),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(self.decide(command, repo=repo), expected)

    def test_git_configuration_from_the_environment_asks(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            ("Bash", f"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.y GIT_CONFIG_VALUE_0='!rm -rf {target}/x' git y", "ask"),
            ("Bash", f"V='!rm -rf {target}/x' git --config-env=alias.y=V y", "ask"),
        ])

    def test_an_untrusted_variable_cannot_hide_a_production_approval(self) -> None:
        self.assertDecisions([
            ("Bash", "E=prod; az pipelines approve --id 1 --environment $E", "deny"),
            ("Bash", "E=prod; sh -c 'E=dev'; az pipelines approve --id 1 --environment $E", "deny"),
            ("Bash", "E=prod; read E; az pipelines approve --id 1 --environment $E", "deny"),
            ("Bash", "az pipelines approve --id 1 --environment staging", "allow"),
        ])

    def test_changing_a_pipeline_check_asks(self) -> None:
        """From the replay: a PATCH to an environment's Approval check changes a protected gate."""
        self.assertDecisions([
            ("PowerShell", "az devops invoke --org $o --area PipelinesChecks --resource configurations "
                           "--route-parameters project=$p id=216 --http-method PATCH --in-file C:\\tmp\\a.json", "ask"),
            ("Bash", "curl -s -X DELETE -u :$PAT https://dev.azure.com/o/p/_apis/pipelines/checks/configurations/216?api-version=7.1", "ask"),
            ("PowerShell", "Invoke-RestMethod -Method Patch -Uri \"$o/$p/_apis/pipelines/checks/configurations/216\" -Body $b", "ask"),
            # Reading check configurations changes nothing.
            ("Bash", "az devops invoke --area PipelinesChecks --resource configurations --route-parameters project=p --http-method GET", "allow"),
        ])

    def test_unknown_programs_asking_to_run_blocked_text(self) -> None:
        """The backstop: text an unrecognized program is given, that would be denied as a command, asks."""
        target = OUT.as_posix()
        self.assertDecisions([
            ("Bash", f"watch -n1 'rm -rf {target}/x'", "ask"),
            ("Bash", "ssh host 'git push --force origin main'", "ask"),
            ("Bash", f"docker exec box rm -rf {target}/x", "ask"),
            ("Bash", f'schtasks /create /tn x /tr "cmd /c rd /s /q {OUT}\\x"', "ask"),
            # Data stays data: known data programs, and harmless text.
            ("Bash", "grep -rn 'git push --force' docs/", "allow"),
            ("Bash", "echo 'git reset --hard'", "allow"),
            ("Bash", "python -c \"print('rm -rf /')\"", "allow"),
            ("Bash", "mytool --message 'chore: update docs'", "allow"),
            ("PowerShell", f"'Remove-Item -Recurse {OUT}' | Set-Content notes.txt", "allow"),
            # From the replay: loop lists and stored values are data.
            ("Bash", 'for s in "git reset --hard is blocked" "other"; do grep -c "$s" log.txt; done', "allow"),
            ("PowerShell", "$body = @'\nApprove the prod deployment with az pipelines approve\n'@; $body.Length", "allow"),
            ("PowerShell", "$hdr = @{ Authorization = ('Basic ' + 'dXNlcjpwdw==') }; $hdr.Count", "allow"),
            # Positional parameters can carry a command: `set -- rm ...; "$@"`.
            ("Bash", f'set -- rm -rf {target}/x; "$@"', "ask"),
        ])

    def test_code_fed_to_a_shell_is_read(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            ("Bash", f"bash <<'EOF'\nrm -rf {target}/x\nEOF", "deny"),
            ("Bash", f"sh -s <<EOF\nrm -rf {target}/x\nEOF", "deny"),
            ("Bash", f"echo 'rm -rf {target}/x' | sh", "deny"),
            ("Bash", f"printf '%s\\n' 'rm -rf {target}/x' | bash -s", "deny"),
            ("Bash", f"bash <<< 'rm -rf {target}/x'", "deny"),
            ("PowerShell", f"iex @'\nRemove-Item -Recurse -Force '{OUT}\\x'\n'@", "deny"),
            ("PowerShell", f"'Remove-Item -Recurse -Force {OUT}\\x' | pwsh -Command -", "deny"),
            ("PowerShell", f"& ([scriptblock]::Create('Remove-Item -Recurse -Force {OUT}\\x'))", "deny"),
            ("PowerShell", f"$b = [scriptblock]::Create('Remove-Item -Recurse -Force {OUT}\\x'); & $b", "deny"),
            ("Bash", f"case x in x) rm -rf {target}/x;; esac", "deny"),
            ("Bash", f"case x in y) true;; x) rm -rf {target}/x;; esac", "deny"),
            ("Bash", f"coproc N {{ rm -rf {target}/x; }}", "deny"),
            ("Bash", f"alias ll='rm -rf {target}/x'", "deny"),
            # A script file's input is data, and so is a file written from a heredoc.
            ("Bash", "bash build.sh <<'EOF'\nrm -rf build\nEOF", "allow"),
            ("Bash", f"cat > notes.md <<'EOF'\nrm -rf {target}/x\nEOF", "allow"),
        ])

    def test_a_here_string_never_opens_a_heredoc(self) -> None:
        """`<<<` once read as a heredoc opener, which hid the lines after it as heredoc data."""
        target = OUT.as_posix()
        self.assertEqual(self.decide(f"cat <<< 'x'\nrm -rf {target}/x\nx"), "deny")

    def test_powershell_encoded_command_alias_is_decoded(self) -> None:
        encoded = base64.b64encode(f"Remove-Item -Recurse -Force {OUT}\\x".encode("utf-16-le")).decode()
        self.assertEqual(self.decide(f"powershell -ec {encoded}", "PowerShell"), "deny")


class ReviewRoundFiveTests(GuardTestCase):
    """Round 5: git config keys that run commands, cmdlet-shaped names, implicit request methods."""

    def test_every_git_config_command_is_judged(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            ("Bash", f"git -c interactive.diffFilter='!rm -rf {target}' add -p", "deny"),
            ("Bash", f"git -c interactive.diffFilter='rm -rf {target}' add -p", "deny"),
            ("Bash", f"git -c gc.recentObjectsHook='rm -rf {target}' gc", "deny"),
            ("Bash", f"git -c some.key='!rm -rf {target}' status", "deny"),
            # A key the parser does not know still has its value checked, by the backstop.
            ("Bash", f"git -c some.futureCommand='rm -rf {target}' status", "ask"),
            # url.<ext::command>.insteadOf makes git run the base as a command.
            ("Bash", f"git -c protocol.ext.allow=always -c 'url.ext::sh -c rm% -rf% {target}.insteadOf=https://example.invalid/r' fetch https://example.invalid/r", "deny"),
            ("Bash", f"git clone 'ext::sh -c rm% -rf% {target}' copy", "deny"),
            ("Bash", "git -c user.name='Rudy Prokes' log -1", "allow"),
        ])

    def test_cmdlet_shaped_names_are_not_trusted_as_data(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            ("PowerShell", f'evil-cmd "rm -rf {target}"', "ask"),
            ("PowerShell", f'do-thing "rm -rf {target}"', "ask"),
            ("PowerShell", f"'rm -rf {target}' | evil-cmd", "ask"),
            ("PowerShell", 'Write-Output "git reset --hard"', "allow"),
            ("PowerShell", 'Select-String -Pattern "git push --force" -Path notes.md', "allow"),
            ("PowerShell", f"'rm -rf {target}' | Out-File notes.txt", "allow"),
        ])

    def test_implicit_and_glued_write_methods_to_a_check_configuration_ask(self) -> None:
        url = "https://dev.azure.com/o/p/_apis/pipelines/checks/configurations/5?api-version=7.1"
        self.assertDecisions([
            ("Bash", f'curl -XPATCH "{url}" -d "{{}}"', "ask"),
            ("Bash", f'curl -sXPATCH "{url}"', "ask"),
            ("Bash", f'curl -s -d "{{}}" "{url}"', "ask"),
            ("Bash", f'curl -sd "{{}}" "{url}"', "ask"),
            ("Bash", f'curl --json "{{}}" "{url}"', "ask"),
            ("Bash", f'curl --request=DELETE "{url}"', "ask"),
            ("Bash", f'curl -s "{url}"', "allow"),
            ("Bash", f'curl -G -d "top=5" "{url}"', "allow"),
            ("PowerShell", f'Invoke-RestMethod -Uri "{url}" -Body $json -ContentType "application/json"', "ask"),
            ("PowerShell", f'Invoke-RestMethod -Uri "{url}" -Meth Put -Body $json', "ask"),
            ("PowerShell", f'Invoke-RestMethod -Uri "{url}" -Method:Patch', "ask"),
            ("PowerShell", f'Invoke-RestMethod -Uri "{url}" -Method Get', "allow"),
        ])

    def test_an_opaque_statement_in_a_subshell_leaves_the_parent_values_alone(self) -> None:
        target = OUT.as_posix()
        self.assertDecisions([
            ("Bash", f'S="{target}"; (eval "$X"); rm -rf "$S/y"', "deny"),
            # In the shell itself, it does make every value unknown.
            ("Bash", f'S="{target}"; eval "$X"; rm -rf "$S/y"', "ask"),
        ])


class ScopeParserTests(unittest.TestCase):
    def test_statements_record_the_processes_and_blocks_they_run_in(self) -> None:
        parsed = shell_parse.parse("S=1; eval 'S=5'; sh -c 'S=2'; ( S=3 ); f() { S=4; }")
        kinds = {s.literals["S"]: tuple(kind for _, kind in s.scope) for s in parsed.statements if "S" in s.literals}
        self.assertEqual(kinds, {"1": (), "5": (), "2": ("child",), "3": ("child",), "4": ("block",)})

    def test_only_plain_assignments_are_literals(self) -> None:
        bash = [s.literals for s in shell_parse.parse("A=1; B+=2; C[0]=3; export D=4; E=$(pwd)").statements if s.literals]
        self.assertEqual(bash, [{"A": "1"}, {"D": "4"}])
        ps = [s.literals for s in shell_parse.parse("$a = 'x'; $b += 'y'; $c[0] = 'z'; $d.e = 'w'", "powershell").statements if s.literals]
        self.assertEqual(ps, [{"a": "x"}])

    def test_code_git_runs_from_its_options_is_parsed(self) -> None:
        argvs = [s.argv for s in shell_parse.parse("git -c core.pager='less -R' -c user.name=x rebase -x 'make test' main").statements]
        self.assertIn(["less", "-R"], argvs)
        self.assertIn(["make", "test"], argvs)
        self.assertNotIn(["x"], argvs)


class ParserTests(unittest.TestCase):
    def argvs(self, command: str, dialect: str = "bash") -> list[list[str]]:
        return [s.argv for s in shell_parse.parse(command, dialect).statements]

    def test_heredoc_bodies_are_dropped(self) -> None:
        argvs = self.argvs("cat <<'EOF' > f\nrm -rf /\nEOF\necho done")
        self.assertNotIn(["rm", "-rf", "/"], argvs)
        self.assertIn(["echo", "done"], argvs)

    def test_substitution_in_assignment_is_captured(self) -> None:
        parsed = shell_parse.parse("X=$(az account show)")
        self.assertEqual([(s.argv[:3], s.assigns) for s in parsed.statements], [(["az", "account", "show"], ("X",))])

    def test_literal_assignments_and_pipes_are_reported(self) -> None:
        statements = shell_parse.parse('S="C:/tmp/x"; echo hi | docker login > out.txt').statements
        self.assertEqual(statements[0].literals, {"S": "C:/tmp/x"})
        self.assertEqual((statements[1].program, statements[1].pipe_to), ("echo", "docker"))
        self.assertTrue(statements[2].stdout_redirected)

    def test_powershell_script_blocks_and_herestrings(self) -> None:
        argvs = self.argvs("ls | ForEach-Object { Remove-Item $_ }\n@'\nrm x\n'@", "powershell")
        self.assertIn(["Remove-Item", "$_"], argvs)
        self.assertNotIn(["rm", "x"], argvs)

    def test_unclosed_quote_is_incomplete(self) -> None:
        self.assertFalse(shell_parse.parse("echo 'x").complete)
        self.assertTrue(shell_parse.parse("echo 'x'").complete)

    def test_redirections_are_not_arguments(self) -> None:
        self.assertEqual(shell_parse.without_redirections(["build", "2>&1", ">", "log.txt", "x"]), ["build", "x"])


if __name__ == "__main__":
    unittest.main()
