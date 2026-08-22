# edgepy

A single-file Python runner - [`edge_pyodide.py`](edge_pyodide.py) - that executes
scripts, modules, and an interactive REPL inside Microsoft Edge using Pyodide (CPython
compiled to WebAssembly), driven over the Chrome DevTools Protocol. Nothing is installed:
no pip, no venv, no admin rights, and once a vendor folder exists it runs fully offline.

It exists for locked-down Windows machines where `python.exe` is allowed but package
installation is not, and for agents that need a throwaway Python sandbox with a real
exit code: stdout/stderr stream back byte-exact as the script writes them, `sys.exit()`
and tracebacks behave like CPython's, local folders are mounted into the sandbox so
sibling imports work, and `--json` wraps a run in one machine-readable envelope. Every
tool failure is a structured `{"error": ...}` on stderr with a `class` to branch on.

The script is standard library only (Python >= 3.10, Edge >= 137) - copy
`edge_pyodide.py` and a vendor folder anywhere and it runs. The folder holds the tests
and examples.

## Quickstart

On a machine with internet, build the vendor folder once (`full` is a 334 MiB download
that extracts to ~375 MiB; `core` is 6.4 MiB but cannot load numpy/pandas):

```powershell
cd edge-pyodide
py -3 edge_pyodide.py doctor                                   # Edge, policies, ports, vendor folder
py -3 edge_pyodide.py fetch --flavor full --pkg tabulate       # online, once: Pyodide 314.0.5 + a PyPI wheel
```

Then run things:

```powershell
py -3 edge_pyodide.py run examples\hello.py -- a b             # file: folder mounted, sys.argv set
py -3 edge_pyodide.py run --pkg numpy examples\numpy_demo.py   # bundled package (full flavor)
py -3 edge_pyodide.py run --pkg tabulate -c "import tabulate; print(tabulate.__version__)"
"Rudy" | py -3 edge_pyodide.py run examples\input_demo.py      # piped stdin feeds input()
py -3 edge_pyodide.py repl                                     # >>> prompt inside the sandbox
```

`pip install -e .` (no runtime dependencies are pulled in) adds the `edgepy` console
script so the same commands read `edgepy run ...`. On an offline machine copy the whole
`vendor\` folder next to the script or point `EDGEPY_VENDOR_DIR` at it.

Boot is 1.7-2.0 s headless; the numpy demo is 2.7 s end to end. Every run launches a
fresh Edge on a throwaway profile and tears it down afterwards.

## Commands

| Command | What it does |
|---|---|
| `run SCRIPT [args...]` | Run a file. Its folder is zipped in memory and mounted at `/mnt/<folder>` (cwd inside the sandbox), `__file__` and `sys.argv` are set, sibling imports work. Put `--` before script options. |
| `run -m MODULE [args...]` | Run a module as `__main__` via `runpy`. Mounts the **current directory** (use `--mount` / `--no-mount` to control what is visible). |
| `run -c CODE [args...]` | Run inline code. Mounts nothing. |
| `run - [args...]` | Run code read from stdin. Mounts nothing; stdin is consumed, so the sandbox gets `--stdin none` and `input()` raises `OSError`. |
| `repl` | Interactive prompt backed by `pyodide.console` (`PyodideConsole` on full, `Console` on core): `...` continuation, expression echo, tracebacks. `exit()` / Ctrl-D end it with the exit code; Ctrl-C during evaluation restarts the sandbox (variables are lost; mounts and `--pkg` loads are replayed). Mounts the cwd. |
| `fetch` | (Online) Download the Pyodide distribution and pure-Python wheels into the vendor folder; writes `edgepy-manifest.json`. Resumes partial downloads, verifies sha256 from the GitHub release digest, skips downloads that are already present with the expected size/sha256. |
| `doctor [--live]` | Preflight JSON: Edge path/version, enterprise policies decoded, run-dir writability, vendor flavor/wheels/manifest, port bind, registry-shadowed MIME types, proxy env, Python. `--live` also boots a headless sandbox and probes JSPI, `input()`, exit codes, and teardown. **Always exits 0** - read `ok` / `failed`. |
| `packages` | List bundled Pyodide packages present on disk and vendored wheels. |
| `clean` | Remove stale run folders under the run root and kill orphaned `msedge.exe` processes that were launched for them. |

Flags on every command: `--verbose` (diagnostics to stderr, including the browser
console and Edge argv), `--vendor-dir`, `--edge-path`.

Session flags on `run` and `repl`:

| Flag | Meaning |
|---|---|
| `--pkg NAME` (repeatable) | Load before running. Bundled packages go through `pyodide.loadPackage`; anything else must be a vendored wheel and goes through micropip against the local index. Bundled packages imported anywhere in the **script's own source** are auto-loaded (`loadPackagesFromImports`); `--pkg` is required for vendored wheels and for imports made inside other modules. |
| `--mount DIR[=NAME]` (repeatable) | Expose a folder at `/mnt/NAME` (default: folder name) and put it on `sys.path`. Pruned of `.git`, `.venv`, `node_modules`, `__pycache__` and similar; unreadable (locked) files are skipped with a `--verbose` note; 64 MB cap on the uncompressed tree (counted before zipping). Two folders cannot share an explicit NAME (exit 2); an implicit basename clash gets a short hash suffix so the script's own folder is always mounted. |
| `--no-mount` | Do not auto-mount the script folder / cwd. |
| `--stdin auto\|prompt\|lines\|none` | How `input()` is fed (see Output contract). Default `auto`. |
| `--show` / `--devtools` | Visible Edge window / visible window with DevTools auto-opened (`window.pyodide` is available in the console). |
| `--window-size W,H` | Edge window size (default `1200,800`). |

`run` only: `--timeout SECONDS` (abort with exit 124; `0` disables, negative is exit 2),
`--json` (one envelope instead of pass-through), `--keep-open` (leave Edge open until
Enter or the window closes).

`fetch` only: `--flavor full|core` (default full), `--pyodide-version`, `--pkg
NAME[SPEC]` (e.g. `pyyaml==6.0.2`), `--requirements FILE` (plain requirements and nested
`-r` includes; other pip directives such as `-e` / `--index-url` are reported under
`unresolved`), `--wheel PATH|URL` (copied in verbatim), `--verify-only` (check the folder
against its manifest), `--force` (re-download and re-extract).

Importable API - every CLI verb is a thin layer over `EdgeRuntime`:

```python
from edge_pyodide import EdgeRuntime

with EdgeRuntime(timeout=30, on_stdout=None, on_stderr=None) as rt:   # None = capture only
    rt.load_packages(["numpy"])
    res = rt.run_code("import numpy; print(numpy.__version__)")
    print(res.exit_code, res.stdout, res.duration_s)
    print(rt.eval("1 + 1"))
```

`run_file(path, argv, mount_parent=True)`, `run_module(name, argv)`, `run_code(code,
argv)`, `eval(expr)`, `mount(dir, name)`, `install(names)`, `repl_push(line)`,
`restart()`. Results are `RunResult(exit_code, stdout, stderr, duration_s, traceback,
truncated, packages_loaded)`.

## Output contract

- **`run` / `repl`** - the script's stdout and stderr pass through **byte-exact and
  live** (every `write()` inside the sandbox becomes one DevTools binding event; a
  64 MB capture cap per stream sets `truncated`). The script's exit code is returned
  verbatim: `sys.exit(3)` -> 3, an uncaught exception -> 1 with a Python-style traceback
  on stderr (shim and `runpy` frames stripped), `sys.exit("msg")` -> 1 with `msg` on
  stderr. In PowerShell read it from `$LASTEXITCODE`.
- **`--json`** - nothing is passed through; one envelope is printed on stdout:
  `{"exit_code", "stdout", "stderr", "truncated", "duration_s", "packages_loaded",
  "pyodide_version", "edge_version"}`. The process exit code is still the script's. On a
  `--timeout` the envelope is still printed (exit_code 124, whatever was captured so far)
  before the error envelope goes to stderr.
- **`fetch` / `doctor` / `packages` / `clean`** print JSON on stdout and exit 0 (`doctor`
  always; the others get the error envelope and exit 1/2 when they fail).
- **stdin** - `auto` picks `prompt` mode: each `input()` becomes Edge's `window.prompt()`
  dialog, answered over CDP with one line read from edgepy's stdin on demand - a terminal
  or a pipe alike, and a script that never calls `input()` never touches stdin (so a parent
  process holding the pipe open cannot hang it). EOF on stdin -> `EOFError` in the sandbox.
  `--stdin lines` pre-reads the whole pipe (UTF-8) and feeds it line by line; `auto` picks
  it only with `--devtools`, because a DevTools frontend can answer dialogs before edgepy
  does. `--stdin none` -> `input()` raises `OSError: [Errno 29] I/O error` (Pyodide's EIO,
  **not** `EOFError`). `run -` consumes stdin for the code, so the sandbox gets `none`.
  Prompts without a trailing newline are visible; piped input is not echoed.
- **`--timeout`** - `Runtime.terminateExecution` stops the code and the run ends with
  exit 124 (`--timeout 3` on `while True: pass` returns in about 5.7 s total, no
  `msedge.exe` left behind). The interpreter is unusable after termination, so there is
  no "continue after timeout".
- **What works in the sandbox** - `asyncio.run()`, `await`, and `time.sleep()` (JSPI
  stack switching, Edge >= 137). What does not exist: threads, `subprocess`, sockets; the
  sandbox has no network, so `requests` / `urllib` calls fail by design.
- **Failure** -> `{"error": {"class", "http_status", "message", "hint"}}` on stderr.
  Because a script's own stderr is never rewritten, **stderr starting with `{"error"`
  always means edgepy itself failed**. Exit `2` = bad input, fix the call
  (`ValueError`: classes `validation`, `mount_too_large`). Exit `1` = environment or
  remote failure, fix config or retry (`RuntimeError`: classes `config`,
  `edge_not_found`, `edge_policy`, `edge_launch`, `cdp`, `pyodide_boot`, `sandbox`,
  `vendor_missing`, `vendor_mismatch`, `package_missing`, `fetch`). Exit `124` = class
  `timeout`. Exit `130` = Ctrl-C. Branch on the exit code for who-fixes-it, on `class`
  for what happened.

## Configuration

Real environment variables only, read lazily at the point of use. There is deliberately
no `.env` loading: the tool must run from a bare `python.exe` with nothing installed.

| Variable | Meaning |
|---|---|
| `EDGEPY_VENDOR_DIR` | Folder holding `pyodide/` and `wheels/`. Resolution: `--vendor-dir` > `EDGEPY_VENDOR_DIR` > `<script dir>\vendor` > `%LOCALAPPDATA%\edgepy\vendor`. |
| `EDGEPY_EDGE_PATH` | Explicit `msedge.exe`. Resolution: `--edge-path` > `EDGEPY_EDGE_PATH` > PATH (`msedge` / `microsoft-edge`) > Program Files / LocalAppData (Stable, Beta, Dev, Canary) > `App Paths` registry key. |
| `EDGEPY_RUN_DIR` | Root for per-run profiles and logs (default `%LOCALAPPDATA%\edgepy\run`). Keep it short: Chromium falls back to the default profile past `MAX_PATH`, so edgepy refuses profile paths over 180 chars (`Run directory path is too long for Chromium`, class `edge_launch`). |
| `EDGEPY_TIMEOUT_SECONDS` | Default `--timeout` for `run` (no default otherwise; the REPL never times out). |
| `EDGEPY_SHOW` | `true` = visible Edge window by default. |
| `EDGEPY_PYODIDE_VERSION` | Pyodide release `fetch` downloads (default `314.0.5`). |
| `EDGEPY_CA_BUNDLE` | PEM bundle for `fetch` behind TLS inspection. |
| `HTTPS_PROXY` | Honored by `fetch` only. Loopback traffic never goes through a proxy. |

### Vendor folder and flavors

`vendor\pyodide\` is the unpacked release tarball (flat), `vendor\wheels\` holds
pure-Python wheels, `edgepy-manifest.json` records what `fetch` resolved (wheel
sha256s, `requires`, `unresolved` with reasons, `integrity`). The folder is gitignored.

| Flavor | Download | On disk | Contents |
|---|---|---|---|
| `core` | `pyodide-core-314.0.5.tar.bz2`, 6.4 MiB | 13 files + a micropip wheel from PyPI | Stdlib only. `fetch` adds micropip so `--pkg` wheels still install; its PyPI hash differs from the lockfile, so the manifest records `"integrity": false` and integrity checks are disabled for it. **Cannot load numpy/pandas** - the lockfile lists them but the wheels are absent. |
| `full` | `pyodide-314.0.5.tar.bz2`, 334 MiB | ~375 MiB, 420 files | 356 bundled packages including numpy 2.4.6, pandas 3.0.2, micropip 0.11.1, PyYAML. |

Package resolution in `fetch` is a deliberate PEP 508/440 **subset** with no
`packaging` dependency: PyPI JSON API, newest non-prerelease version satisfying the
specifier, pure `none-any` wheels only (`py3`, `py314` or `cp314` tag), `Requires-Python`
respected, dependency markers evaluated for the **sandbox** (`sys_platform ==
"emscripten"`, `platform_machine == "wasm32"`, `python_version == "3.14"`), `extra ==`
dependencies pulled in only when that extra was requested (`--pkg name[extra]`),
parenthesised or otherwise unparsed markers recorded under `unresolved` with a reason.
A compiled-only package (`pyyaml` on core) is reported as "no pure-Python wheel".
`--wheel PATH|URL` adds a wheel verbatim, which is the escape hatch for anything the
resolver declines.

pip can produce the same wheels if you prefer its resolver:

```powershell
pip download --only-binary=:all: --platform any --python-version 3.14 --no-deps <pkg> -d vendor\wheels
```

If pip hits the private Azure Artifacts index, prefix with `$env:PIP_INDEX_URL =
"https://pypi.org/simple"; $env:PIP_EXTRA_INDEX_URL = ""`. Wheels dropped into
`vendor\wheels\` are served without a manifest entry; `fetch --wheel PATH` registers
the hash as well.

### What runs where (security posture)

- The local `http.server` binds `127.0.0.1` on an ephemeral port; every URL carries a
  per-run random token; responses are `Cache-Control: no-store`.
- Edge runs on a fresh profile under `%LOCALAPPDATA%\edgepy\run\r<8hex>\profile` with
  `--remote-debugging-port=0` (Edge >= 136 refuses remote debugging on the default
  profile), `--no-proxy-server --disable-extensions --disable-sync
  --disable-background-networking`, and `--headless` unless `--show`. Its log is
  `edge.log` in the `r<8hex>` run folder next to the profile. The whole run folder is
  deleted on exit.
- The DevTools websocket handshake sends no `Origin` header, so no
  `--remote-allow-origins` is needed; Edge keeps rejecting browser-originated (Origin-
  bearing) websocket connections, so web pages cannot attach to the debugging port.
- A Windows Job Object kills the Edge tree if edgepy dies; `Browser.close` then
  `taskkill /T` cover normal teardown. `edgepy clean` handles anything left.
- The sandbox has no network and no filesystem beyond what was mounted (a copy, not a
  link: writes inside `/mnt/...` never reach the host).

## Validate (offline - no Edge needed)

```powershell
cd edge-pyodide
python -m pytest                                  # offline; websocket, Edge process, HTTP and registry seams are faked
py -3 edge_pyodide.py packages                    # reads the vendor folder only
py -3 edge_pyodide.py fetch --verify-only         # checks wheels and required files against the manifest
py -3 edge_pyodide.py doctor                      # finds Edge and reads policies but does not launch it
```

pytest 9 is installed globally on this machine; a venv is optional
(`py -3 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".[dev]"`).

## Live smoke test

Each step confirms one thing the offline suite cannot (numbers are from Edge 151 on
Windows 11 with the full flavor):

1. `edgepy doctor --live` - `live_boot` (about 2 s, Edge and Pyodide versions),
   `live_jspi` true, `live_stdin_prompt` round-trips `input()` through `window.prompt()`,
   `live_exit_code` 7, `live_teardown` no `msedge.exe` left.
2. `edgepy run examples\hello.py -- a b` - mount, `__file__` under `/mnt/examples`, `sys.argv`.
3. `edgepy run examples\sibling_import.py` - sibling import from the mounted folder.
4. `cd examples; edgepy run -m mylib.cli -- --name Rudy` - module run with the cwd mounted.
5. `edgepy run --pkg numpy examples\numpy_demo.py` - bundled package load, about 2.7 s end to end.
6. `"Rudy" | edgepy run examples\input_demo.py` - the pipe feeds the first `input()`; the second hits EOF.
7. `edgepy run examples\input_demo.py` from a terminal - the same `prompt()` dialog bridge, interactive.
8. `edgepy run -c "import sys; sys.exit(3)"; $LASTEXITCODE` - 3.
9. `edgepy run --timeout 3 -c "while True: pass"; $LASTEXITCODE` - 124 in about 5.7 s, `timeout` envelope on stderr.
10. `edgepy run --json -c "print('hi')"` - envelope keys.
11. `edgepy repl` - `1+1`, a multi-line `def`, `raise ValueError("x")`, `exit(5)`; `$LASTEXITCODE` is 5.
12. `edgepy clean` - `removed` and `killed_pids` are empty after clean runs.

## How it's built

One file, layered bottom-up; every I/O edge is a module-level seam the tests replace
(`_ws_connect`, `_launch_process`, `_open_url`, `_sleep`, `_kill_tree`,
`_attach_job_object`, `_registry_value`, `_runtime_factory`):

| Section | Role |
|---|---|
| Constants | Pyodide version and URLs, required dist files, timeouts, mount prune list and caps, pinned Content-Type table, Edge search paths and policy keys, error classes, exit codes. |
| Config helpers | `_tag()` rides `error_class` / `http_status` / `hint` on builtin exceptions (no custom classes); lazy env reads for vendor dir, run root, default timeout. |
| Vendor folder | Wheel filename parsing, `load_vendor()` reading `pyodide-lock.json` into `VendorInfo` (flavor = how many bundled wheels exist on disk), `split_packages()` deciding loadPackage vs micropip. |
| WebSocket client | RFC 6455 client over a plain socket: masking, fragmentation, ping/pong, close codes; no `Origin`, no extensions, loopback only. |
| CDP session | Single-threaded send / pump / dispatch: commands wait for their reply while events (`Runtime.bindingCalled`, dialogs, detach) are dispatched; deadlines raise `TimeoutError`. |
| Edge process | `find_edge()`, version from the install folder, policy decoding from `HKLM`/`HKCU`, argv, per-run profile dir, `DevToolsActivePort` wait with log tail on failure, Job Object, `Browser.close` then `taskkill` teardown. |
| Local HTTP server | `http.server` on `127.0.0.1:0` under a token prefix: in-memory routes (runner page, PEP 503 index with `#sha256=` fragments, mount zips) plus flat directories (`pyodide/`, `wheels/`) with the pinned MIME table; basename-only paths. |
| Runner page and boot shim | The HTML page calls `loadPyodide`, installs byte-exact stdout/stderr writers that emit base64 over the binding, configures stdin mode, and loads `_BOOT_PY` - plain Python running inside Pyodide that dispatches `info` / `load` / `install` / `mount` / `run` / `eval` / `repl_push` and formats user tracebacks like CPython. |
| EdgeRuntime | The importable API and context manager: starts server + Edge + CDP, navigates, waits for `ready`, decodes stream events incrementally, answers `prompt()` dialogs, applies deadlines, poisons itself after a termination, and exposes `run_file` / `run_module` / `run_code` / `eval` / `mount` / `install` / `repl_push` / `restart`. |
| fetch | Release asset size/digest from the GitHub API, resumable sha256-verified downloads, streaming tarball extraction, the PEP 508/440 subset resolver against PyPI JSON, micropip vendoring for core, manifest writing, `verify_vendor()`. |
| doctor / packages / clean | Preflight checks as `{id, status, detail, hint}` rows (plus the `--live` probe), vendor listing, stale run-dir removal with orphaned `msedge.exe` kill via CIM. |
| CLI | argparse subcommands, stdin-mode selection, mounts and `--pkg` wiring, the REPL loop, the `--json` envelope, error envelopes and exit-code mapping in `main()`. |

Error convention (repo-wide): **`ValueError` means bad input** - fix the call (exit 2);
**`RuntimeError` means environment or remote failure** - fix config or retry (exit 1).
Messages say what to do next and carry the Edge log tail or a sandbox traceback in
`hint` when one exists.

Tests (`tests/`, `python -m pytest`): offline, mirroring the layers - websocket framing,
CDP dispatch and deadlines, Edge launch and policy decoding against a fake process that
writes `DevToolsActivePort`, the local server and index pages, the boot shim exec'd
under local CPython with a stub `pyodide_js`, the resolver and marker evaluation with
canned PyPI JSON, and CLI exit codes and envelopes via `main([...])`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Microsoft Edge was not found.` (class `edge_not_found`) | Not in PATH, Program Files, LocalAppData, or `App Paths`. Set `EDGEPY_EDGE_PATH` or pass `--edge-path` to `msedge.exe`. |
| `Edge exited with code 0 before DevTools came up.` (class `edge_launch`, or `edge_policy` when a blocking policy is found) | Edge handed the launch to an already-running instance instead of starting on the throwaway profile: typically a `UserDataDir` policy overriding `--user-data-dir` (your normal Edge window receives the URL) or `HeadlessModeEnabled=0`. edgepy relaunches once on a fresh profile before giving up. The hint carries the `edge.log` tail; run `edgepy doctor`. |
| `doctor` shows `policy_RemoteDebuggingAllowed` / `policy_DeveloperToolsAvailability` / `policy_HeadlessModeEnabled` as `fail` (class `edge_policy`) | Enterprise policy blocks remote debugging, DevTools attach (`DeveloperToolsAvailability=2` opens the port but hides page targets), or headless mode (try `--show`). These are registry policies under `SOFTWARE\Policies\Microsoft\Edge` (`HKLM`, then `HKCU`); only IT can lift them. |
| `Edge did not write DevToolsActivePort within 30s.` (class `edge_launch`, or `edge_policy` when a blocking policy is found) | A `UserDataDir` policy relocates the profile, so the port file lands elsewhere (`doctor` decodes it), or `RemoteDebuggingAllowed` / `HeadlessModeEnabled` block it (try `--show`). A run dir that is too long or unwritable fails earlier with `Run directory path is too long` / `is not writable` - set `EDGEPY_RUN_DIR` to a short writable path such as `C:\edgepy-run`. Check `edge.log`. |
| `Pyodide did not become ready within 120s.` / `wasm instantiation failed` (class `pyodide_boot`) | First boot can be slow while antivirus scans the 9.6 MB `pyodide.asm.wasm`; retry or exclude the vendor folder. `wasm instantiation failed` means the wasm was served with the wrong Content-Type or is corrupt: the server pins `application/wasm` itself, so re-run `edgepy fetch --force` and `edgepy doctor` (the `mimetypes` check lists registry-shadowed types; on this machine `.zip` maps to `application/x-zip-compressed`). |
| `Package 'numpy' is part of Pyodide but its wheel is not in ...` (class `package_missing`) | Core flavor: the lockfile knows the package but the wheel is absent. `edgepy fetch --flavor full`. |
| `Package 'x' is neither bundled with Pyodide nor present in ...\wheels` | Not vendored. On an online machine `edgepy fetch --pkg x`, then copy `vendor\wheels` and the manifest over. |
| `fetch` reports `"unresolved": [{"reason": "no pure-Python wheel ..."}]` | The package only ships compiled wheels (e.g. `pyyaml` on core). Use the full flavor if Pyodide bundles it, or `--wheel` with a `py3-none-any` build; `unsupported marker` means a parenthesised or otherwise unparsed dependency marker (unknown variable or operator) - add that dependency by hand with `--wheel`. |
| `edgepy: warning - vendor path is long (N chars)` or odd boot failures after copying the vendor folder | Deep paths break `MAX_PATH`-sensitive loaders. Keep the vendor folder short (`C:\edgepy\vendor`) and set `EDGEPY_VENDOR_DIR`. |
| `OSError: [Errno 29] I/O error` from `input()` | `--stdin none` (or stdin was consumed by `run -`). Pyodide raises EIO, not `EOFError`. Pipe input in or drop the flag. |
| `msedge.exe` processes left behind; `doctor --live` warns `live_teardown` | edgepy was killed hard (no Job Object, e.g. already inside another job). Run `edgepy clean`; it kills processes whose command line names a stale run dir. |
| Runs hang or fail only on the corporate network; `proxy_env` lists `HTTPS_PROXY` | Loopback already bypasses proxies (`--no-proxy-server`, no proxy handler for `127.0.0.1`). If a PAC or TLS-inspecting proxy still interferes it affects `fetch` only: set `HTTPS_PROXY` and `EDGEPY_CA_BUNDLE`. |
| A run fails once right after Edge updated itself | The updater swaps the versioned binary folder next to `msedge.exe` while edgepy is launching (Edge exits 0 immediately). edgepy already relaunches once on a fresh profile; if that also fails, retry and `edgepy doctor` shows the new `edge_version`. |
| `Mount ... exceeds 64 MB` (class `mount_too_large`, exit 2) | `run -m` and `repl` mount the cwd; running them from a folder that contains `vendor\` (~375 MiB plus the downloaded archives) trips the cap. `cd` into the project folder, or use `--no-mount` with an explicit `--mount DIR`. |
| `Edge closed the DevTools connection` / `DevTools detached from the page` with `--show` | The window was closed mid-run. Keep it open until the run ends, or use `--keep-open` to inspect afterwards. |
| `pip download` fails with a 401/403 from `pkgs.dev.azure.com` | The global pip config points at a private index. Prefix with `$env:PIP_INDEX_URL = "https://pypi.org/simple"; $env:PIP_EXTRA_INDEX_URL = ""`. |

## Follow-ups

- Real Ctrl-C: run Pyodide in a Web Worker with a `SharedArrayBuffer` interrupt buffer
  so a running loop can be interrupted without losing interpreter state (today
  `Runtime.terminateExecution` ends the sandbox; the REPL restarts it).
- Export files out of the sandbox: a `--out DIR` that copies a path from the Pyodide
  filesystem back to the host after the run (mounts are one-way copies today).
- Persistent profile cache: reuse a warmed profile / HTTP cache across runs to shave the
  boot time, while keeping the per-run token and throwaway-by-default posture.
- REPL tab completion and history via `pyodide.console` completions.
- wasm32 wheel resolution in `fetch`: accept `pyemscripten_*_wasm32` wheels from PyPI
  that match the vendored Pyodide ABI, not only `py3-none-any`.
