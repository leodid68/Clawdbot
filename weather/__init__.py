"""Simmer Weather Trading Bot — trades Polymarket weather markets using NOAA forecasts."""

# Backward-compatible module-level exports for Simmer SDK integration
from .config import Config

def load_config(schema=None, skill_file=None, config_filename="config.json"):
    """Legacy wrapper — loads Config from the package directory."""
    from pathlib import Path
    config_dir = str(Path(__file__).parent)
    return Config.load(config_dir)

def get_config_path(skill_file=None, config_filename="config.json"):
    """Legacy wrapper — returns path to config.json."""
    from pathlib import Path
    return Path(__file__).parent / config_filename

def update_config(updates, skill_file=None, config_filename="config.json"):
    """Legacy wrapper — updates and saves config."""
    from pathlib import Path
    config_dir = str(Path(__file__).parent)
    cfg = Config.load(config_dir)
    cfg.update(updates)
    cfg.save(config_dir)
    return {f.name: getattr(cfg, f.name) for f in __import__("dataclasses").fields(cfg)}

# Optional: Trade Journal integration
try:
    from tradejournal import log_trade  # noqa: F401
    JOURNAL_AVAILABLE = True
except ImportError:
    try:
        from skills.tradejournal import log_trade  # noqa: F401
        JOURNAL_AVAILABLE = True
    except ImportError:
        JOURNAL_AVAILABLE = False
        def log_trade(*args, **kwargs):
            """No-op stub when tradejournal is not installed."""
            pass
