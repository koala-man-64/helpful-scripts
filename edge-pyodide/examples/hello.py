"""Smallest possible edgepy check: prints where it runs and echoes its arguments."""
import platform
import sys

print(f"hello from Python {platform.python_version()} on {sys.platform}")
print("argv:", sys.argv[1:])
print("__file__:", __file__)
