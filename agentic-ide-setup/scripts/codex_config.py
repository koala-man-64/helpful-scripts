"""Export the portable Codex allowlist using Python 3.11's TOML parser."""

import argparse
import json
from pathlib import Path
import tomllib


def export(source: Path, destination: Path, defaults: dict) -> None:
    config = tomllib.loads(source.read_text(encoding="utf-8-sig"))
    # Defaults are reviewed bundle policy, not the current session's preferences.
    portable = dict(defaults)
    for key in ("personality", "tool_output_token_limit"):
        if key in config:
            portable[key] = config[key]
    lines = [f"{key} = {json.dumps(value)}" for key, value in portable.items()]
    features = config.get("features", {})
    lines.append("\n[features]")
    for key in ("multi_agent", "memories"):
        if key in features:
            lines.append(f"{key} = {json.dumps(features[key])}")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "config.template.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Inventory only: plugin installation and authentication stay with the host.
    plugins = sorted(config.get("plugins", {}))
    (destination / "plugins.txt").write_text("".join(f"{p}\n" for p in plugins), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    export(args.source, args.destination, manifest["codexDefaults"])
