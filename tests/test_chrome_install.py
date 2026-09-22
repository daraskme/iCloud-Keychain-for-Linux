import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("chrome_install", Path(__file__).parents[1] / "scripts/install_chrome_extension.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def source_files(tmp_path):
    assets = tmp_path / "nix-store"
    assets.mkdir()
    (assets / "manifest.json").write_text(json.dumps({"manifest_version": 3, "background": {"service_worker": "background.js"}}))
    for name in ("background.js", "content.js", "popup.html", "popup.js"):
        (assets / name).write_text(f"fixture {name}")
    source = tmp_path / "package"
    source.mkdir()
    for path in assets.iterdir():
        (source / path.name).symlink_to(path)
    return source


def test_materializes_nix_symlinks_and_keeps_previous_extension(tmp_path):
    source = source_files(tmp_path)
    target = tmp_path / "user/extension"
    target.mkdir(parents=True)
    (target / "content.js").symlink_to(source / "content.js")
    (target / "old-version.txt").write_text("previous version")
    backup = installer.install_extension(source, target)
    assert (backup / "old-version.txt").read_text() == "previous version"
    assert (backup / "content.js").is_symlink()
    assert not (target / "old-version.txt").exists()
    for path in target.iterdir():
        assert path.is_file() and not path.is_symlink()
        assert path.read_bytes() == (source / path.name).read_bytes()


def test_invalid_package_leaves_existing_extension_untouched(tmp_path):
    source = source_files(tmp_path)
    (source / "manifest.json").write_text('{"manifest_version": 2}')
    target = tmp_path / "extension"
    target.mkdir()
    (target / "content.js").write_text("keep this")
    with pytest.raises(ValueError, match="Manifest V3"):
        installer.install_extension(source, target)
    assert (target / "content.js").read_text() == "keep this"
