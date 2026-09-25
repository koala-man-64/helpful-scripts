"""Check repository-owned Codex files and preview/disable duplicate skill discovery.

The host config is deliberately not compared with config.template.toml: it contains
machine-specific state. Only skills present in both discovery roots are considered.
"""

import argparse
import copy
import difflib
import os
from pathlib import Path
import re
import stat
import sys
import tomllib
import uuid


def normalized(text: str, home: Path) -> str:
    # Match ProfileTools.ConvertFrom-PortableText for owned Markdown/scripts.
    for marker, value in {
        "__USERPROFILE__": home,
        "__APPDATA__": home / "AppData" / "Roaming",
        "__LOCALAPPDATA__": home / "AppData" / "Local",
    }.items():
        text = text.replace(marker, str(value))
    return text.replace("\r\n", "\n").replace("\r", "\n")


def owned_file_drift(profile: Path, codex: Path, home: Path) -> list[str]:
    findings = []
    if not (profile / "AGENTS.md").is_file() or not (profile / "skills").is_dir():
        return [f"canonical profile is incomplete: {profile}"]
    sources = [profile / "AGENTS.md", *(profile / "skills").rglob("*")]
    for source in sources:
        if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".pyc":
            continue
        relative = source.relative_to(profile)
        target = codex / relative
        if not target.is_file():
            findings.append(f"missing owned Codex file: {target}")
        elif normalized(source.read_text(encoding="utf-8-sig"), home) != target.read_text(encoding="utf-8-sig"):
            findings.append(f"owned Codex file differs: {target}")
    return findings


def duplicate_review(profile: Path, codex: Path, duplicates: dict[str, Path], home: Path, *, report: bool = True) -> list[str]:
    """Explain ownership and differences; refuse ambiguous, non-owned content."""
    blocked = []
    for other in duplicates.values():
        kept = codex / "skills" / other.parent.name
        relative_files = {
            p.relative_to(root) for root in (kept, other.parent) for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        }
        differences = []
        for relative in sorted(relative_files):
            left, right = kept / relative, other.parent / relative
            if not left.is_file() or not right.is_file():
                differences.append(str(relative))
                continue
            try:
                equal = normalized(left.read_text(encoding="utf-8-sig"), home) == normalized(right.read_text(encoding="utf-8-sig"), home)
            except UnicodeError:
                equal = left.read_bytes() == right.read_bytes()
            if not equal:
                differences.append(str(relative))
        owned = (profile / "skills" / other.parent.name / "SKILL.md").is_file()
        if report:
            print(f"Keep Codex discovery setting: {kept / 'SKILL.md'}; disable Codex discovery of: {other}")
            print(f"  source-owned: {owned}; differing files: {', '.join(differences) or 'none (normalized identical)'}")
        if differences and not owned:
            blocked.append(f"non-owned duplicate differs; explicit resolution required: {other.parent.name}")
    return blocked


def path_identity(path: Path) -> tuple:
    """Reject links/junctions at every ancestor and capture target/parent identity."""
    identities = []
    for current in (path, *path.parents):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(f"reparse/link path is not allowed: {current}")
        if current == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise ValueError(f"config must be a regular file with one link: {path}")
        identities.append((info.st_dev, info.st_ino))
    return tuple(identities)


def file_permissions(path: Path) -> bytes | int:
    if os.name != "nt":
        return stat.S_IMODE(path.stat().st_mode)
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = api.GetFileSecurityW
    get_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get_security.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    get_security(str(path), 4, None, 0, ctypes.byref(needed))  # DACL_SECURITY_INFORMATION
    if not needed.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(needed.value)
    if not get_security(str(path), 4, buffer, len(buffer), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.raw[:needed.value]


def create_private_file(path: Path, permissions: bytes | int):
    """Create empty files with the source DACL before writing any config bytes."""
    if os.name != "nt":
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(fd, permissions)
        return os.fdopen(fd, "wb")
    import ctypes
    from ctypes import wintypes
    import msvcrt
    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wintypes.BOOL)]
    descriptor = ctypes.create_string_buffer(permissions)
    attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), ctypes.addressof(descriptor), False)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(SecurityAttributes), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x40000000, 0, ctypes.byref(attributes), 1, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise
    output = os.fdopen(fd, "wb")
    try:
        if file_permissions(path) != permissions:
            api = ctypes.WinDLL("advapi32", use_last_error=True)
            dacl = ctypes.c_void_p()
            present, defaulted = wintypes.BOOL(), wintypes.BOOL()
            get_dacl = api.GetSecurityDescriptorDacl
            get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
            get_dacl.restype = wintypes.BOOL
            if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
                raise ctypes.WinError(ctypes.get_last_error())
            # CreateFile can adjust inheritance flags. Explicitly preserve the DACL
            # and its protection state while the new file is still empty.
            protected = int.from_bytes(permissions[2:4], "little") & 0x1000
            set_security = api.SetNamedSecurityInfoW
            set_security.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            set_security.restype = wintypes.DWORD
            error = set_security(str(path), 1, 4 | (0x80000000 if protected else 0x20000000), None, None, dacl, None)
            if error:
                raise ctypes.WinError(error)
        if file_permissions(path) != permissions:
            raise ValueError("created file permissions differ from original")
    except BaseException:
        output.close()
        path.unlink()
        raise
    return output


def replace_config(path: Path, original: bytes, candidate: bytes, identity: tuple, validate) -> Path:
    """Back up exact bytes and atomically replace after fresh drift/CAS checks.

    Callers must serialize other config writers during apply; the final comparison
    detects observed edits but is not an OS-level compare-and-swap transaction.
    """
    tomllib.loads(candidate.decode("utf-8-sig"))
    permissions = file_permissions(path)
    def check():
        validate()
        if path_identity(path) != identity or path.read_bytes() != original or path_identity(path) != identity or file_permissions(path) != permissions:
            raise ValueError("config changed since preview; rerun with a fresh preview")
    check()
    backup = path.with_name(f"{path.name}.agentic-ide-setup-backup-{uuid.uuid4().hex}")
    temporary = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with create_private_file(temporary, permissions) as output:
            output.write(candidate)
            output.flush()
            os.fsync(output.fileno())
        check()
        if file_permissions(temporary) != permissions:
            raise ValueError("replacement permissions differ from original")
        with create_private_file(backup, permissions) as output:
            output.write(original)
            output.flush()
            os.fsync(output.fileno())
        check()
        if file_permissions(backup) != permissions:
            raise ValueError("backup permissions differ from original")
        os.replace(temporary, path)
        path_identity(path)
        if file_permissions(path) != permissions or path.read_bytes() != candidate:
            raise ValueError(f"post-replacement verification failed; preserve changes and review backup: {backup}")
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def skill_names(root: Path) -> set[str]:
    return {p.parent.name for p in root.glob("*/SKILL.md") if p.is_file()}


def skill_path(path: str) -> str:
    return path.replace("\\", "/").casefold().rstrip("/")


def duplicate_paths(codex: Path, agents: Path) -> dict[str, Path]:
    def names(root):
        result = {}
        for name in skill_names(root):
            key = name.casefold() if os.name == "nt" else name
            if key in result:
                raise ValueError(f"ambiguous skill names: {root}")
            result[key] = name
        return result
    left, right = names(codex / "skills"), names(agents / "skills")
    return {skill_path(str(agents / "skills" / right[key] / "SKILL.md")): agents / "skills" / right[key] / "SKILL.md"
            for key in sorted(left.keys() & right.keys())}


def config_entries(config: str) -> list[dict]:
    parsed = tomllib.loads(config)
    entries = parsed.get("skills", {}).get("config", [])
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("skills.config must be an array of tables")
    return entries


def enabled_duplicates(config: str, duplicates: dict[str, Path]) -> list[Path]:
    entries = config_entries(config)
    by_path: dict[str, list[dict]] = {}
    for entry in entries:
        if isinstance(entry.get("path"), str):
            by_path.setdefault(skill_path(entry["path"]), []).append(entry)
    return [path for key, path in duplicates.items()
            if not by_path.get(key) or any(entry.get("enabled", True) is not False for entry in by_path[key])]


TABLE = re.compile(r"(?m)^\s*\[\[skills\.config\]\]\s*(?:#.*)?$")
ANY_TABLE = re.compile(r"(?m)^\s*\[{1,2}[^\]\r\n]+\]{1,2}\s*(?:#.*)?$")
PATH = re.compile(r'(?m)^\s*path\s*=\s*["\']([^"\']+)["\']\s*(?:#.*)?$')
ENABLED = re.compile(r"(?m)^(\s*enabled\s*=\s*)(?:true|false)(\s*(?:#.*)?)$")


def disabled_config(config: str, duplicates: dict[str, Path]) -> str:
    """Change only matching skills.config blocks, retaining all unrelated bytes."""
    config_entries(config)
    expected = copy.deepcopy(tomllib.loads(config))
    expected_entries = expected.setdefault("skills", {}).setdefault("config", [])
    for key, path in duplicates.items():
        matching = [entry for entry in expected_entries if isinstance(entry.get("path"), str) and skill_path(entry["path"]) == key]
        if not matching:
            expected_entries.append({"path": path.as_posix(), "enabled": False})
        for entry in matching:
            entry["enabled"] = False
    if not duplicates:
        return config
    headers = list(TABLE.finditer(config))
    all_headers = list(ANY_TABLE.finditer(config))
    sections = []
    seen = set()
    for index, header in enumerate(headers):
        end = next((match.start() for match in all_headers if match.start() > header.start()), len(config))
        section = config[header.start():end]
        path_match = PATH.search(section)
        if not path_match or skill_path(path_match.group(1)) not in duplicates:
            continue
        key = skill_path(path_match.group(1))
        seen.add(key)
        if ENABLED.search(section):
            replacement = ENABLED.sub(r"\g<1>false\g<2>", section, count=1)
        else:
            replacement = section[:path_match.end()] + "\nenabled = false" + section[path_match.end():]
        sections.append((header.start(), end, replacement))
    for start, end, replacement in reversed(sections):
        config = config[:start] + replacement + config[end:]
    for key, path in duplicates.items():
        if key not in seen:
            config = config.rstrip("\r\n") + f'\n\n[[skills.config]]\npath = "{path.as_posix()}"\nenabled = false\n'
    config_entries(config)
    if enabled_duplicates(config, duplicates):
        raise ValueError("duplicate skill entries remain enabled")
    if tomllib.loads(config) != expected:
        raise ValueError("candidate changes unrelated config; unsupported TOML layout")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path(__file__).resolve().parents[1] / "profile" / "codex")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--apply-dedup", action="store_true", help="Back up and update only duplicate skills.config entries")
    args = parser.parse_args(argv)
    args.home = args.home.absolute()
    codex, agents = args.home / ".codex", args.home / ".agents"
    config_path = codex / "config.toml"
    try:
        findings = owned_file_drift(args.profile, codex, args.home)
        identity = path_identity(config_path)
        original = config_path.read_bytes()
        config = original.decode("utf-8-sig")
        duplicates = duplicate_paths(codex, agents)
        blockers = findings + duplicate_review(args.profile, codex, duplicates, args.home)
        def validate():
            current = owned_file_drift(args.profile, codex, args.home)
            if current or duplicate_paths(codex, agents) != duplicates:
                raise ValueError("canonical files or duplicate discovery changed; rerun preview")
            if duplicate_review(args.profile, codex, duplicates, args.home, report=False):
                raise ValueError("non-owned differing duplicates require resolution")
        for path in enabled_duplicates(config, duplicates):
            findings.append(f"duplicate skill enabled for Codex: {path}")
        updated = disabled_config(config, duplicates)
        if args.apply_dedup and blockers:
            raise ValueError("apply refused: " + "; ".join(blockers))
        findings.extend(blocker for blocker in blockers if blocker not in findings)
        if updated != config:
            diff = difflib.unified_diff(config.splitlines(keepends=True), updated.splitlines(keepends=True),
                                        fromfile=str(config_path), tofile=f"{config_path} (proposed)")
            print("".join(diff), end="")
            if args.apply_dedup:
                candidate = (b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + updated.encode("utf-8")
                backup = replace_config(config_path, original, candidate, identity, validate)
                print(f"Updated {config_path}; backup: {backup}")
                findings = [finding for finding in findings if not finding.startswith("duplicate skill enabled")]
        for finding in findings:
            print(finding)
        print(f"{len(findings)} Codex drift finding(s)")
        return int(bool(findings))
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"Codex drift check failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
