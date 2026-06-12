"""
System settings — NAS paths and key config values editable from the UI.
Saves to config.local.yaml so config.yaml (the template) stays clean.
"""
import logging
import os
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

FIELD_LABELS = {dot_key: label for dot_key, label, _ in EDITABLE_FIELDS}

# NAS paths validated against nas.mount_point before saving
NAS_PATH_KEYS = ("nas.archive_path", "nas.overnight_output_path")


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


def _validate_nas_paths(values: dict, nas_ok: bool) -> tuple[list[str], list[str], list[str]]:
    """Validate the NAS paths in a settings submission before saving.

    Returns (errors, warnings, notes):
      errors   — always block the save (path escapes the mount point)
      warnings — block the save unless the user confirms (directory does not
                 exist on a mounted NAS; a brand-new archive root is almost
                 always a typo, e.g. a doubled path segment)
      notes    — informational only (NAS offline, existence unverifiable)
    """
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    mount = values.get("nas.mount_point") or get("nas.mount_point", "/mnt/wdbx-share")
    mount_norm = os.path.normpath(mount)

    for key in NAS_PATH_KEYS:
        value = values.get(key)
        if not value:
            continue
        label = FIELD_LABELS[key]
        # normpath collapses "a//b", "a/./b" and "a/b/.." so the containment
        # check can't be dodged with relative segments
        norm = os.path.normpath(value)

        if not os.path.isabs(norm):
            errors.append(f"{label}: must be an absolute path (got \"{value}\").")
            continue
        if norm != mount_norm and not norm.startswith(mount_norm.rstrip("/") + "/"):
            errors.append(
                f"{label}: \"{value}\" is not under the NAS mount point ({mount})."
            )
            continue

        if nas_ok:
            if not Path(norm).is_dir():
                warnings.append(
                    f"{label}: \"{value}\" does not exist on the NAS. "
                    "Check for doubled or misspelled path segments — a brand-new "
                    "directory here is almost always a typo."
                )
        else:
            notes.append(f"{label}: NAS is offline, could not verify the directory exists.")

    return errors, warnings, notes


def _settings_context(request: Request, *, nas_ok: bool, current: dict,
                      filename_template: str, errors=(), warnings=(),
                      saved=False, offline_note=False) -> dict:
    return {
        "fields": EDITABLE_FIELDS,
        "current": current,
        "nas_ok": nas_ok,
        "local_exists": LOCAL_CONFIG_PATH.exists(),
        "local_config_path": str(LOCAL_CONFIG_PATH),
        "filename_template": filename_template,
        "errors": list(errors),
        "warnings": list(warnings),
        "saved": saved,
        "offline_note": offline_note,
    }


@router.get("/", response_class=HTMLResponse)
def settings_page(request: Request):
    nas_ok = nas_is_writable()
    current = {key: get(key, "") for key, _, _ in EDITABLE_FIELDS}

    return templates.TemplateResponse(request, "settings.html", _settings_context(
        request,
        nas_ok=nas_ok,
        current=current,
        filename_template=get("archive.filename_template", "{date} [{show}] - WDBX"),
        saved=request.query_params.get("saved") == "1",
        offline_note=request.query_params.get("offline") == "1",
    ))


@router.post("/save")
async def save_settings(request: Request):
    form = await request.form()
    values = {dot_key: (form.get(dot_key) or "").strip() for dot_key, _, _ in EDITABLE_FIELDS}
    template_value = (form.get("archive.filename_template") or "").strip()
    allow_new_dirs = form.get("allow_new_nas_dirs") == "on"

    nas_ok = nas_is_writable()
    errors, warnings, notes = _validate_nas_paths(values, nas_ok)

    if errors or (warnings and not allow_new_dirs):
        logger.warning("Settings rejected: %s", errors + warnings)
        # Re-render with the submitted values so the user can fix them in place
        current = {key: values[key] or get(key, "") for key, _, _ in EDITABLE_FIELDS}
        return templates.TemplateResponse(request, "settings.html", _settings_context(
            request,
            nas_ok=nas_ok,
            current=current,
            filename_template=template_value or get("archive.filename_template", "{date} [{show}] - WDBX"),
            errors=errors,
            warnings=warnings,
        ), status_code=422)

    local_cfg = _load_local_config()
    for dot_key, _, _ in EDITABLE_FIELDS:
        if values[dot_key]:
            _set_nested(local_cfg, dot_key, values[dot_key])

    if template_value:
        _set_nested(local_cfg, "archive.filename_template", template_value)

    _save_local_config(local_cfg)

    # Reset the config cache so new values take effect immediately
    import shared.config as cfg_module
    cfg_module._config = None

    if warnings:
        logger.warning("Settings saved with confirmed warnings: %s", warnings)
    logger.info("Settings saved to %s", LOCAL_CONFIG_PATH)

    url = "/settings?saved=1" + ("&offline=1" if notes else "")
    return RedirectResponse(url, status_code=303)
