"""Small shared helpers used across data_eng modules."""

import numpy as np


def safe_float(val, decimals: int = 2) -> float | None:
    """Convert a value to a rounded float, returning None for None/NaN/inf.

    Used when writing metric values to DuckDB / dicts so that non-finite
    numbers become explicit None instead of leaking NaN/inf downstream.
    """
    if val is None:
        return None
    f = float(val)
    if np.isnan(f) or np.isinf(f):
        return None
    return round(f, decimals)
