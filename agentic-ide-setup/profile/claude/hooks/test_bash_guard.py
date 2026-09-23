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
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
