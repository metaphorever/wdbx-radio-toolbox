from pathlib import Path
import yaml

_config: dict | None = None

# Project root is two levels up from this file (shared/config.py → project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

def load_config(path: str = "config.yaml") -> dict:
    global _config
    if _config is None:
        config_path = Path(path)
        if not config_path.is_absolute():
            config_path = _PROJECT_ROOT / config_path
        # Also check for a local override
        local = config_path.with_name("config.local.yaml")
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        with open(config_path) as f:
            _config = yaml.safe_load(f)
        # Merge local overrides if present (local values win)
        if local.exists():
            with open(local) as f:
                local_cfg = yaml.safe_load(f) or {}
            _deep_merge(_config, local_cfg)
    return _config

def _deep_merge(base: dict, override: dict) -> None:
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val

def get(key: str, default=None):
    """Dot-notation key access, e.g. get('pacifica.station_prefix')"""
    cfg = load_config()
    parts = key.split(".")
    val = cfg
    for part in parts:
        if not isinstance(val, dict):
            return default
        val = val.get(part)
        if val is None:
            return default
    return val
