import argparse
import sys

from hook_utils import format_pull_request_title


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prefix a pull request title with the current Codex conversation title."
    )
    parser.add_argument("title", nargs="*", help="Existing pull request title.")
    parser.add_argument(
        "--conversation-title",
        help="Explicit conversation title. Defaults to local Codex thread metadata.",
    )
    args = parser.parse_args()

    existing_title = " ".join(args.title).strip()
    if not existing_title and not sys.stdin.isatty():
        existing_title = sys.stdin.read().strip()

    print(
        format_pull_request_title(
            existing_title,
            conversation_title=args.conversation_title,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
