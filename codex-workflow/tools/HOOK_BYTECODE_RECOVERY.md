# Verified hook bytecode recovery

`Repair-CodexHookBytecode.ps1` is an explicit incident operation owned by the
central hooks maintainer. It defaults to a read-only preview. The caller must
already have authorization for recovery; the script does not bypass a tool gate
or change source, policy, configuration, or release manifests.

The diagnosed incident was an immutable release containing four unexpected
CPython 3.14 cache files. An optional interoperability test imported installed
modules without disabling bytecode writes, a plausible contributor rather than
exclusive attribution. This helper accepts only `__init__`, `models`,
`subagent_routing`, and `utils` with the exact `.cpython-314.pyc` suffix. It is not
a general cache cleaner.

## Use

From the helpful-scripts checkout, provide the hooks **source** checkout for the
read-only doctor and installation-state discovery:

```powershell
& .\codex-workflow\tools\Repair-CodexHookBytecode.ps1 -SourceRoot C:\src\codex-workflow-hooks
& .\codex-workflow\tools\Repair-CodexHookBytecode.ps1 -SourceRoot C:\src\codex-workflow-hooks -Apply
```

Review the preview before the authorized owner runs `-Apply`. Only one task owns
live recovery. `-InstallStatePath` can supply an explicit absolute `install.json`;
otherwise a source probe obtains the configured path. `-PythonExecutable` selects
the Python executable. Optional `-CodexHome` and `-QuarantineRoot` are explicit
absolute paths. The expected version and manifest digest default to the diagnosed
`3a7c218` release; a different release requires an explicitly reviewed pair of
`-ExpectedVersion` and `-ExpectedManifestDigest` values, with the same exact
four-file diagnosis.

Preflight uses the source doctor's semantic release verifier and requires an
exact installation/version/manifest binding, no tracked-file or invalid-entry
failures, and exactly the cache directory plus four reported files. It rejects
reparse points in the cache and its ancestors, extra files/directories, changed
bytes/state, and quarantine inside the installed release. All Python calls use
`-B` and `PYTHONDONTWRITEBYTECODE=1`.

Apply moves the cache with `Move-Item` into a unique external quarantine,
preserving the original bytes. `recovery.json` records the original hashes and
binding, then verifies destination names/hashes, full doctor health and all seven
owned events. Self-test executes the verified **installed** `hookctl.py` with
`-B`; its executed path, SHA-256, output, and exit code are included. The source
doctor and installed self-test are separate evidence. An already-clean release
performs no move; `-Apply` still runs the installed self-test.

Failure after the move preserves the quarantine and records the failed check.
Do not automatically restore the cache: doing so reintroduces the integrity
failure. A maintainer can inspect the recorded source/destination paths and bytes
for an explicitly authorized reversal. No files are deleted.

## Prevention and checks

Optional installed-module interoperability runs in an isolated `python -B`
subprocess, also sets the bytecode environment flag, and verifies that the full
installed directory inventory and file hashes remain unchanged. It does not leak
installed packages into the test runner's `sys.path` or `sys.modules`.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
py -3 -B -m unittest discover -s codex-workflow/tests -p test_hook_bytecode_recovery.py -v
py -3 -B -m unittest discover -s codex-workflow/tests -p test_candidate_bundle.py -v
```

Recovery tests use temporary fake releases and doctor/self-test fixtures. The
optional real parser test runs only when `CODEX_WORKFLOW_HOOKS_RELEASE` names the
specific verified release; do not run it before prevention is in place.

The quarantine operation is a management-plane recovery action by the central
hooks owner. It is never called by workload execution, benchmark dispatch, normal
hook events, or an automatic retry. Candidate optimization remains disabled and
requires its own evidence and admission gates.
