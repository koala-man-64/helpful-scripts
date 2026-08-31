"""Scrub employer/corporate metadata from .docx files, stdlib only.

Word documents that have ever lived on a corporate machine tend to accumulate
metadata you do not want to send elsewhere: information-protection sensitivity
labels (MSIP), classification tags (Titus and friends), a stale document title,
an attached-template path that exposes a local Windows username, custom
SharePoint properties, and hyperlinks silently rewritten through an email
security gateway (urldefense / safelinks). Most of it survives "Save As" and is
visible to anyone who inspects the file - including a recipient's DLP tooling.

This tool reports all of that, and (unless --dry-run) removes what can be
removed safely at the package level:

  * docProps/custom.xml           - custom properties (MSIP_Label_*, *Classification,
                                    TitusGUID, SharePoint template residue, ...)
  * docMetadata/LabelInfo.xml     - sensitivity-label part, when present
  * cp:lastPrinted                - stale print timestamp in core.xml
  * w:attachedTemplate            - settings.xml reference + rel whose target is a
                                    local path like C:/Users/<name>/AppData/...
  * app.xml <Template>            - reset to Normal.dotm
  * dc:title / authors            - set with --title / --title-from-name / --author

Rewritten hyperlinks (urldefense.com, safelinks.protection.outlook.com) are
REPORTED but not auto-fixed - the tool cannot know the intended clean URL.
Fix those in Word, or pre-process the source document.

Usage:
    python scrub_docx_metadata.py FILE_OR_DIR [more files...] [options]

Options:
    --dry-run           report only, change nothing
    --title TEXT        set dc:title on every processed file
    --title-from-name   set dc:title to each file's name (without extension)
    --author NAME       set dc:creator and cp:lastModifiedBy

Files are modified in place; keep a backup. Exit code 1 if any file still has
findings after processing (or in --dry-run when findings exist).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from pathlib import Path

DROP_PARTS = ("docProps/custom.xml", "docMetadata/LabelInfo.xml")
SUSPICIOUS_PROP_PATTERNS = ("MSIP_Label", "Classification", "TitusGUID")
LINK_REWRITER_HOSTS = ("urldefense.com", "safelinks.protection.outlook.com")


def collect(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            out.extend(sorted(path.glob("*.docx")))
        elif path.suffix.lower() == ".docx":
            out.append(path)
        else:
            print(f"skipping non-docx argument: {p}", file=sys.stderr)
    return out


def inspect(data: dict[str, bytes]) -> list[str]:
    findings = []
    for part in DROP_PARTS:
        if part in data:
            findings.append(f"contains {part}")
    if "docProps/custom.xml" in data:
        custom = data["docProps/custom.xml"].decode("utf-8", "replace")
        names = re.findall(r'name="([^"]+)"', custom)
        hits = [n for n in names if any(pat.lower() in n.lower() for pat in SUSPICIOUS_PROP_PATTERNS)]
        if hits:
            findings.append(f"classification/sensitivity properties: {', '.join(sorted(set(hits))[:6])}")
        elif names:
            findings.append(f"{len(names)} custom properties")
    core = data.get("docProps/core.xml", b"").decode("utf-8", "replace")
    if "<cp:lastPrinted>" in core:
        findings.append("stale lastPrinted timestamp")
    m = re.search(r"<dc:title>([^<]+)</dc:title>", core)
    if m and m.group(1).strip():
        findings.append(f"document title: {m.group(1).strip()!r}")
    for rels_name in [n for n in data if n.endswith(".rels")]:
        rels = data[rels_name].decode("utf-8", "replace")
        for host in LINK_REWRITER_HOSTS:
            for target in re.findall(rf'Target="([^"]*{re.escape(host)}[^"]*)"', rels):
                findings.append(f"gateway-rewritten hyperlink ({host}) in {rels_name}: {target[:80]}...")
        for target in re.findall(r'Target="(file:///[^"]*Users[^"]*)"', rels):
            findings.append(f"local-path template reference in {rels_name}: {target}")
    app = data.get("docProps/app.xml", b"").decode("utf-8", "replace")
    m = re.search(r"<Template>([^<]+)</Template>", app)
    if m and m.group(1) not in ("Normal.dotm", "Normal"):
        findings.append(f"template name: {m.group(1)!r}")
    m = re.search(r"<Company>([^<]+)</Company>", app)
    if m and m.group(1).strip():
        findings.append(f"company: {m.group(1).strip()!r}")
    return findings


def scrub(data: dict[str, bytes], title: str | None, author: str | None) -> list[str]:
    actions = []
    for part in DROP_PARTS:
        if part in data:
            del data[part]
            actions.append(f"removed {part}")
    ct = data["[Content_Types].xml"].decode("utf-8")
    for part in DROP_PARTS:
        ct, n = re.subn(rf'<Override PartName="/{re.escape(part)}"[^/]*/>', "", ct)
        if n:
            actions.append(f"removed content-type override for {part}")
    data["[Content_Types].xml"] = ct.encode("utf-8")
    rels = data["_rels/.rels"].decode("utf-8")
    for part in DROP_PARTS:
        rels, n = re.subn(rf'<Relationship [^>]*Target="{re.escape(part)}"[^>]*/>', "", rels)
        if n:
            actions.append(f"removed package rel for {part}")
    data["_rels/.rels"] = rels.encode("utf-8")

    core = data["docProps/core.xml"].decode("utf-8")
    core, n = re.subn(r"<cp:lastPrinted>.*?</cp:lastPrinted>", "", core, flags=re.S)
    if n:
        actions.append("removed lastPrinted")
    if title is not None:
        core, n = re.subn(r"(<dc:title>).*?(</dc:title>)", rf"\g<1>{title}\g<2>", core, flags=re.S)
        if n == 0:
            core = re.sub(r"(<cp:coreProperties[^>]*>)", rf"\g<1><dc:title>{title}</dc:title>", core)
        actions.append(f"set title to {title!r}")
    if author is not None:
        core = re.sub(r"(<dc:creator>).*?(</dc:creator>)", rf"\g<1>{author}\g<2>", core, flags=re.S)
        core = re.sub(r"(<cp:lastModifiedBy>).*?(</cp:lastModifiedBy>)", rf"\g<1>{author}\g<2>", core, flags=re.S)
        actions.append(f"set creator/lastModifiedBy to {author!r}")
    data["docProps/core.xml"] = core.encode("utf-8")

    if "docProps/app.xml" in data:
        app = data["docProps/app.xml"].decode("utf-8")
        app, n = re.subn(r"<Template>(?!Normal)[^<]*</Template>", "<Template>Normal.dotm</Template>", app)
        if n:
            actions.append("reset template name to Normal.dotm")
        data["docProps/app.xml"] = app.encode("utf-8")

    if "word/settings.xml" in data:
        st = data["word/settings.xml"].decode("utf-8")
        st, n = re.subn(r"<w:attachedTemplate[^/]*/>", "", st)
        if n:
            actions.append("removed attachedTemplate from settings.xml")
        data["word/settings.xml"] = st.encode("utf-8")
    if "word/_rels/settings.xml.rels" in data:
        srels = data["word/_rels/settings.xml.rels"].decode("utf-8")
        srels, n = re.subn(r"<Relationship [^>]*attachedTemplate[^>]*/>", "", srels)
        if n:
            actions.append("removed attachedTemplate rel")
        data["word/_rels/settings.xml.rels"] = srels.encode("utf-8")
    return actions


def process(path: Path, args: argparse.Namespace) -> bool:
    """Returns True when the file is clean after processing."""
    with zipfile.ZipFile(path) as zin:
        names = zin.namelist()
        data = {n: zin.read(n) for n in names}
        infos = {n: zin.getinfo(n) for n in names}

    print(f"== {path.name}")
    findings = inspect(data)
    for f in findings:
        print(f"   found: {f}")
    if not findings:
        print("   clean")
    if args.dry_run:
        # an existing document title is informational, not a defect
        return not [f for f in findings if not f.startswith("document title:")]

    title = path.stem if args.title_from_name else args.title
    actions = scrub(data, title, args.author)
    for a in actions:
        print(f"   {a}")
    tmp = path.with_suffix(".docx.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in names:
            if name in data:
                zout.writestr(infos[name], data[name])
    shutil.move(tmp, path)

    with zipfile.ZipFile(path) as z:
        remaining = inspect({n: z.read(n) for n in z.namelist()})
    # a set title is expected after scrubbing; report everything else
    remaining = [f for f in remaining if not f.startswith("document title:")]
    for f in remaining:
        print(f"   STILL PRESENT (manual fix needed): {f}")
    return not remaining


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help=".docx files and/or directories of them")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--title")
    ap.add_argument("--title-from-name", action="store_true")
    ap.add_argument("--author")
    args = ap.parse_args()
    files = collect(args.paths)
    if not files:
        print("no .docx files found", file=sys.stderr)
        return 2
    all_clean = all([process(f, args) for f in files])
    return 0 if all_clean else 1


if __name__ == "__main__":
    sys.exit(main())
