"""
Unit tests for NAS path validation in the settings UI.

Regression coverage for the 2026-06-12 incident where a copy-pasted
nas.archive_path with a doubled segment (/mnt/wdbx-share/wdbx-share/...)
was saved unchecked and mkdir(parents=True) silently built the wrong
tree on the NAS.
"""
from web.routes.settings import _validate_nas_paths


def _values(mount, archive=None, overnight=None):
    return {
        "nas.mount_point": mount,
        "nas.archive_path": archive or "",
        "nas.overnight_output_path": overnight or "",
    }


def test_valid_existing_paths_pass(tmp_path):
    archive = tmp_path / "Shows" / "AutoArchive"
    archive.mkdir(parents=True)
    errors, warnings, notes = _validate_nas_paths(
        _values(str(tmp_path), archive=str(archive)), nas_ok=True)
    assert errors == []
    assert warnings == []
    assert notes == []


def test_path_outside_mount_point_is_error():
    errors, warnings, _ = _validate_nas_paths(
        _values("/mnt/wdbx-share", archive="/mnt/other-share/Shows"), nas_ok=True)
    assert len(errors) == 1
    assert "not under the NAS mount point" in errors[0]
    assert warnings == []


def test_sibling_prefix_does_not_count_as_under_mount():
    # /mnt/wdbx-share2 starts with the string "/mnt/wdbx-share" but is a sibling
    errors, _, _ = _validate_nas_paths(
        _values("/mnt/wdbx-share", archive="/mnt/wdbx-share2/Shows"), nas_ok=True)
    assert len(errors) == 1


def test_dotdot_escape_is_error():
    errors, _, _ = _validate_nas_paths(
        _values("/mnt/wdbx-share", archive="/mnt/wdbx-share/../etc"), nas_ok=True)
    assert len(errors) == 1


def test_relative_path_is_error():
    errors, _, _ = _validate_nas_paths(
        _values("/mnt/wdbx-share", archive="Shows/AutoArchive"), nas_ok=True)
    assert len(errors) == 1
    assert "absolute path" in errors[0]


def test_missing_dir_on_mounted_nas_is_warning(tmp_path):
    # The doubled-segment typo: under the mount point, but the dir doesn't exist
    doubled = tmp_path / tmp_path.name / "Shows" / "AutoArchive"
    errors, warnings, notes = _validate_nas_paths(
        _values(str(tmp_path), archive=str(doubled)), nas_ok=True)
    assert errors == []
    assert len(warnings) == 1
    assert "does not exist" in warnings[0]
    assert notes == []


def test_missing_dir_when_nas_offline_is_note_only(tmp_path):
    missing = tmp_path / "Shows" / "AutoArchive"
    errors, warnings, notes = _validate_nas_paths(
        _values(str(tmp_path), archive=str(missing)), nas_ok=False)
    assert errors == []
    assert warnings == []
    assert len(notes) == 1
    assert "offline" in notes[0]


def test_both_nas_paths_are_validated(tmp_path):
    errors, warnings, _ = _validate_nas_paths(
        _values(str(tmp_path),
                archive="/elsewhere/Shows",
                overnight=str(tmp_path / "nonexistent")),
        nas_ok=True)
    assert len(errors) == 1      # archive outside mount
    assert len(warnings) == 1    # overnight missing on mounted NAS


def test_empty_values_are_skipped():
    errors, warnings, notes = _validate_nas_paths(
        _values("/mnt/wdbx-share"), nas_ok=True)
    assert (errors, warnings, notes) == ([], [], [])
