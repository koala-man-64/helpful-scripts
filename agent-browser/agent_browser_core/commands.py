"""CLI bindings for the versioned daemon capabilities."""
from __future__ import annotations

import argparse
import json
from . import common, policy


def commit(args: argparse.Namespace):
    from . import cli
    if not args.arguments or args.arguments[0] in {"commit", "serve", "stop", "start", "clean"}:
        raise common._tag(ValueError("commit needs an action, api, run or journal operation."), common.ERR_USAGE)
    inner = cli._build_parser().parse_args(args.arguments)
    for key in ("profile", "session", "timeout"):
        if getattr(inner, key, None) is None:
            setattr(inner, key, getattr(args, key, None))
    inner.committing = True
    return inner.func(inner)


def extended(args: argparse.Namespace):
    from . import cli, session
    cmd, payload = args.command, {}
    if cmd in {"state", "journal"}:
        payload = {"command_id": args.receipt, "handoff_id": args.handoff, "offset": args.offset, "clear": getattr(args, "clear", False), "resolve": getattr(args, "resolve_handoff", False)}
    elif cmd == "needs-human":
        payload = {"reason": args.reason, "condition": {"signed_in": args.signed_in} if args.signed_in else {}}
    elif cmd == "fill-form":
        paths, config = cli._caller_paths(args)
        path = common._check_local_path(args.file, paths, config)
        if path.stat().st_size > common.REQUEST_CAP // 2:
            raise common._tag(ValueError("Form file exceeds the input budget."), common.ERR_VALIDATION)
        try:
            payload = {"fields": json.loads(path.read_text(encoding="utf-8-sig"))}
        except (ValueError, OSError) as exc:
            raise common._tag(ValueError("Form file must be valid UTF-8 JSON."), common.ERR_VALIDATION) from exc
    elif cmd == "table": payload = {"target": args.target}
    elif cmd == "api":
        payload = {"url": args.url, "method": args.method, "servicenow": args.servicenow}
        if args.json_file:
            paths, config = cli._caller_paths(args)
            payload["body_file"] = str(common._check_local_path(args.json_file, paths, config))
            payload["headers"] = {"Content-Type": "application/json"}
    elif cmd == "run": payload = {"recipe": args.recipe, "number": args.number, "text": cli._input_text(args, "text")}
    return cli._remote(args, cmd, cmd, payload)


def configure(parser, sub, add):
    parser.add_argument("--session", default=None)
    parser.add_argument("--expect", action="append", default=[])
    for sp in sub.choices.values():
        sp.add_argument("--session", default=argparse.SUPPRESS)
        sp.add_argument("--expect", action="append", default=argparse.SUPPRESS)
    p = add("commit", commit, "explicit commit operation under caller approval policy")
    p.add_argument("arguments", nargs=argparse.REMAINDER)
    for name in ("state", "journal"):
        p = add(name, extended, "inspect durable command receipts")
        p.add_argument("--command", dest="receipt")
        p.add_argument("--handoff")
        if name == "state": p.add_argument("--resolve", dest="resolve_handoff", action="store_true")
        p.add_argument("--offset", type=int, default=0)
        if name == "journal": p.add_argument("--clear", action="store_true")
    p = add("needs-human", extended, "focus and flash for a human action")
    p.add_argument("--reason", default="Human action required")
    p.add_argument("--signed-in")
    p = add("fill-form", extended, "preflight and fill fields without submitting")
    p.add_argument("--file", required=True)
    p = add("table", extended, "read one rendered table with row refs")
    p.add_argument("target", nargs="?")
    p = add("api", extended, "same-origin authenticated request")
    p.add_argument("url")
    p.add_argument("--method", default="GET", choices=("GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"))
    p.add_argument("--json-file")
    p.add_argument("--servicenow", action="store_true")
    p = add("run", extended, "preview a built-in recipe; commit run executes it")
    p.add_argument("recipe", choices=("sn/add-work-note",))
    p.add_argument("--number", required=True)
    p.add_argument("--text", default=None)
    p.add_argument("--text-file")
    for name in ("click", "fill", "type"):
        sp = sub.choices[name]
        next(a for a in sp._actions if a.dest == "target").nargs = "?"
        sp.add_argument("--role")
        sp.add_argument("--name")
        sp.add_argument("--label")
    sub.choices["screenshot"].add_argument("--annotate", action="store_true")
    # New verbs receive shared flags as well.
    for name in ("commit", "state", "journal", "needs-human", "fill-form", "table", "api", "run"):
        sp = sub.choices[name]
        sp.add_argument("--session", default=argparse.SUPPRESS)
        sp.add_argument("--expect", action="append", default=argparse.SUPPRESS)
