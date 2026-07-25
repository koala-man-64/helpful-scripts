import json
import sys


SUBSTANTIVE_MARKERS = (
    "added ",
    "updated ",
    "changed ",
    "implemented ",
    "created ",
    "removed ",
    "patched ",
    "refactored ",
    "configured ",
    "fixed ",
    "installed ",
    "edited ",
    "propagated ",
    "deployed ",
    "committed ",
    "pushed ",
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        return 0

    if not isinstance(payload, dict) or payload.get("stop_hook_active"):
        return 0

    message = payload.get("last_assistant_message")
    if not isinstance(message, str):
        return 0

    normalized = f" {message.lower()} "
    if not any(marker in normalized for marker in SUBSTANTIVE_MARKERS):
        return 0
    if "runtime-ownership-enforcer" in normalized:
        return 0

    json.dump(
        {
            "decision": "block",
            "reason": (
                "Before finishing, complete the team closeout summary: "
                "runtime-ownership-enforcer completed, not applicable, or blocker."
            ),
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
