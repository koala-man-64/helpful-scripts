"""Needs the full Pyodide distribution: `edgepy run --pkg numpy numpy_demo.py`."""
import numpy as np

a = np.arange(12, dtype=float).reshape(3, 4)
print("matrix:\n", a)
print("row means:", a.mean(axis=1))
print("numpy", np.__version__)
