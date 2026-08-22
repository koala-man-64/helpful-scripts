"""Module entry point: `edgepy run -m mylib.cli -- --name Rudy`."""
import argparse

from mylib import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mylib")
    parser.add_argument("--name", default="world")
    args = parser.parse_args(argv)
    print(f"mylib {__version__} says hello, {args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
