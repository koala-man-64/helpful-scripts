from pathlib import Path


def log_bytes():
    failure = " ERROR BUILD417 src/route.py:1 readiness route missing 本手 火候 "
    text = "x" * 76000 + failure
    return (text + "y" * (153000 - len(text))).encode("utf-8")


if __name__ == "__main__":
    path = Path("logs/build.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(log_bytes())
