"""Install a Chrome unpacked extension as real files, retaining the previous copy.

Chrome's declarative content-script loader rejects symlinks outside the extension
root, even when the popup and programmatic script injection work with those links.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def install_extension(source: Path, target: Path) -> Path | None:
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("manifest_version") != 3 or "scripts" in manifest.get("background", {}):
        raise ValueError("Use the packaged Chrome Manifest V3 extension")
    for name in ("manifest.json", "background.js", "content.js", "popup.html", "popup.js"):
        if not (source / name).is_file():
            raise ValueError(f"Missing extension file: {name}")
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError("The destination must be a regular directory")
    if source.resolve() == target.resolve():
        raise ValueError("Source and destination must be different directories")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".extension-stage-", dir=target.parent))
    backup = None
    try:
        shutil.copytree(source, stage, symlinks=False, dirs_exist_ok=True)
        # Nix store files and directories are read-only; the user owns this copy.
        for item in [stage, *stage.rglob("*")]:
            if item.is_symlink():
                raise ValueError("The extension must not contain symlinks")
            item.chmod(0o755 if item.is_dir() else 0o644)
        if target.exists():
            backup = Path(tempfile.mkdtemp(prefix="extension-backup-", dir=target.parent)) / "extension"
            target.rename(backup)
        try:
            stage.rename(target)
        except BaseException:
            if backup is not None:
                backup.rename(target)
            raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--register", required=True, type=Path)
    parser.add_argument("--target", type=Path, default=Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "icp/extension")
    parser.add_argument("extension_id")
    args = parser.parse_args()
    if not re.fullmatch("[a-p]{32}", args.extension_id):
        parser.error("Expected the 32-character Chrome extension ID")
    backup = install_extension(args.source, args.target)
    subprocess.run([str(args.register), args.extension_id], check=True)
    print(f"Installed Chrome extension: {args.target}")
    if backup:
        print(f"Previous extension saved at: {backup}")
    print("Reload Apple Passwords at chrome://extensions to activate the update.")


if __name__ == "__main__":
    main()
