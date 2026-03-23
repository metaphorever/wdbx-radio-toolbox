from pathlib import Path
import yaml

_config: dict | None = None

def load_config(path: str = "config.yaml") -> dict:
    global _config
    if _config is None:
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
        with open(config_path) as f:
            _config = yaml.safe_load(f)
    return _config

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
