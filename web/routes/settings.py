"""
System settings — NAS paths and key config values editable from the UI.
Saves to config.local.yaml so config.yaml (the template) stays clean.
"""
import logging
from pathlib import Path

import jinja2
import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from archive_manager.nas import nas_is_writable
from shared.config import _PROJECT_ROOT, get

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings")

_templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_templates_dir),
    autoescape=jinja2.select_autoescape(),
    auto_reload=True,
    cache_size=0,
))

LOCAL_CONFIG_PATH = _PROJECT_ROOT / "config.local.yaml"

# Fields exposed in the UI: (dot-key, label, hint)
EDITABLE_FIELDS = [
    ("nas.mount_point",          "NAS Mount Point",          "e.g. /mnt/wdbx-share"),
    ("nas.archive_path",         "NAS Archive Path",         "e.g. /mnt/wdbx-share/Shows/AutoArchive"),
    ("nas.overnight_output_path","NAS Overnight Output Path","e.g. /mnt/wdbx-share/overnight-programming"),
    ("local_staging.path",       "Local Staging Path",       "Fallback when NAS is unreachable"),
    ("database.path",            "Database Path",            "e.g. /home/wdbx/wdbx-toolbox/wdbx.db"),
    ("logging.file",             "Log File Path",            "e.g. /home/wdbx/wdbx-toolbox/logs/wdbx.log"),
    ("library.detection_ref_path", "Detection Reference Path", "NAS folder of historical underwriting MP3s"),
]


def _load_local_config() -> dict:
    if LOCAL_CONFIG_PATH.exists():
        with open(LOCAL_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_local_config(data: dict) -> None:
    LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_CONFIG_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def _set_nested(d: dict, dot_key: str, value: str) -> None:
    parts = dot_key.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


@router.get("/", response_class=HTMLResponse)
def settings_page(request: Request):
    nas_ok = nas_is_writable()
    current = {key: get(key, "") for key, _, _ in EDITABLE_FIELDS}
    local_exists = LOCAL_CONFIG_PATH.exists()

    return templates.TemplateResponse(request, "settings.html", {
        "fields": EDITABLE_FIELDS,
        "current": current,
        "nas_ok": nas_ok,
        "local_exists": local_exists,
        "local_config_path": str(LOCAL_CONFIG_PATH),
        "filename_template": get("archive.filename_template", "{date} [{show}] - WDBX"),
    })


@router.post("/save")
async def save_settings(request: Request):
    form = await request.form()
    local_cfg = _load_local_config()

    for dot_key, _, _ in EDITABLE_FIELDS:
        value = (form.get(dot_key) or "").strip()
        if value:
            _set_nested(local_cfg, dot_key, value)

    template_value = (form.get("archive.filename_template") or "").strip()
    if template_value:
        _set_nested(local_cfg, "archive.filename_template", template_value)

    _save_local_config(local_cfg)

    # Reset the config cache so new values take effect immediately
    import shared.config as cfg_module
    cfg_module._config = None

    logger.info("Settings saved to %s", LOCAL_CONFIG_PATH)
    return RedirectResponse("/settings", status_code=303)
