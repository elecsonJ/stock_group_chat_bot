import os
from pathlib import Path
from typing import Any


def configure_yfinance_cache(yf_module: Any) -> None:
    """Keep yfinance's SQLite timezone cache inside the project workspace."""
    if yf_module is None or not hasattr(yf_module, "set_tz_cache_location"):
        return
    cache_dir = Path(os.getenv("YFINANCE_CACHE_DIR", "data/yfinance_cache"))
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        yf_module.set_tz_cache_location(str(cache_dir.resolve()))
    except Exception:
        return
