# resume-docx-tools

Two small Python tools born from a resume-portfolio remediation, generalized
for reuse. The problem they solve: Microsoft's stock Word resume templates are
actively hostile to applicant-tracking systems (the whole body sits in a layout
table, and the name plus work history are wrapped in content controls that
standard parsers silently drop), and documents that have lived on a corporate
machine carry metadata you do not want in an application PDF's ancestor —
sensitivity labels, classification tags, gateway-rewritten hyperlinks, and
local paths exposing a Windows username.

Demonstrated failure that motivated all this: a plain `python-docx` read of a
stock-template resume returned **no candidate name and an empty work history**.

## scrub_docx_metadata.py — report and strip corporate metadata

Stdlib only, no dependencies. Reports and removes: `docProps/custom.xml`
(MSIP sensitivity labels, `*Classification` tags, TitusGUID, SharePoint
template residue), the `docMetadata/LabelInfo.xml` label part, stale
`lastPrinted` timestamps, `attachedTemplate` references that leak
`C:/Users/<name>/...` paths, and non-default template names. Reports (but does
not auto-fix) hyperlinks rewritten through urldefense / safelinks. Can also
set clean title/author metadata.

```bash
# see what a folder of docx files is carrying, change nothing
python scrub_docx_metadata.py ./applications --dry-run

# scrub in place, set title from each filename and a clean author
python scrub_docx_metadata.py ./applications --title-from-name --author "Jane Doe"
```

Files are modified in place — keep a backup. Exit code 1 means findings
remain (in `--dry-run`: findings exist).

## build_resume_docx.py — Markdown to ATS-safe Word

Requires `python-docx`. Converts a Markdown resume (simple line-based dialect —
see the module docstring) into a clean .docx: plain paragraphs, real
Heading 1/2/3 styles, genuine Word list bullets, working hyperlinks, no
tables, no content controls, clean metadata. Keeping the resume's source of
truth in Markdown and generating the Word file makes the pair impossible to
drift apart.

```bash
python build_resume_docx.py resume.md --check
```

`--check` runs the acceptance test the stock template failed: a plain-text
extraction of the output must return the name and every visible Markdown line
(exact parity, both directions).

## Suggested workflow

1. Keep each resume as Markdown; edit only the Markdown.
2. `build_resume_docx.py resume.md --check` to (re)generate the Word file.
3. `scrub_docx_metadata.py --dry-run` on anything Word touched afterward,
   before sending it anywhere.
