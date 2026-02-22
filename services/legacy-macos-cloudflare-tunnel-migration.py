#!/usr/bin/env python3
"""Configure Cloudflared to survive macOS reboots using an existing tunnel.

TODO: This script should auto-scan existing personal setup and system setup before proceeding.

Features:
- Scans ~/.cloudflared for credential JSON files, prompts for selection when multiple exist.
- Reuses the existing ~/.cloudflared/config.yml for the selected tunnel (no edits to ingress).
- Detects cloudflared binary path (/opt/homebrew/bin, /usr/local/bin, or PATH).
- Prompts before sudo elevation and any destructive action; supports overwrite, backup, or skip.
- Configures system changes (/etc/cloudflared, /Library/LaunchDaemons).
- Sets permissions/ownership, creates log file, and loads launchd service.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


PLIST_PATH = Path("/Library/LaunchDaemons/com.cloudflare.cloudflared.plist")
SYSTEM_CONFIG_DIR = Path("/etc/cloudflared")
LOG_PATH = Path("/var/log/cloudflared.log")
HOME_CLOUDFLARED = Path("~").expanduser() / ".cloudflared"


@dataclass
class SelectedTunnel:
    """Represents a chosen tunnel credential and config."""

    credential_file: Path
    tunnel_id: str
    config_file: Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.replace("@pas-executable", "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "PREREQUISITE: A personal Cloudflared tunnel must already be set up and working on this machine (check ~/.cloudflared).\n\n"
            "NOTE: The script is intended to auto-scan existing personal and system setups before proceeding."
        )
    )
    parser.parse_args()

    ensure_root_or_reexec()
    original_user = resolve_original_user()
    user_home = Path(f"~{original_user}").expanduser()
    cloudflared_dir = user_home / ".cloudflared"

    if not cloudflared_dir.exists():
        sys.exit(f"Missing {cloudflared_dir}; aborting.")

    credential = choose_tunnel_credential(cloudflared_dir)
    config_file = cloudflared_dir / "config.yml"
    if not config_file.exists():
        sys.exit(f"Missing config file at {config_file}")

    selected = SelectedTunnel(
        credential_file=credential,
        tunnel_id=credential.stem,
        config_file=config_file,
    )

    cloudflared_bin = detect_cloudflared_binary()
    print(f"Using cloudflared binary: {cloudflared_bin}")

    if not confirm_config_contains_tunnel(config_file, selected.tunnel_id):
        print(
            "Warning: selected tunnel id not found in config.yml. "
            "You may need to update your config to match. Continuing anyway."
        )
        if not prompt_yes_no("Continue without config.yml containing the tunnel id?"):
            sys.exit("Aborted by user.")

    # Summarize planned actions
    actions_to_perform = [
        f"Create system directory: {SYSTEM_CONFIG_DIR}",
        f"Copy config: {selected.config_file} -> {SYSTEM_CONFIG_DIR / 'config.yml'}",
        f"Copy credential: {selected.credential_file} -> {SYSTEM_CONFIG_DIR / selected.credential_file.name}",
        f"Create log file: {LOG_PATH}",
        f"Write launchd plist: {PLIST_PATH}",
        "Reload launchd service",
    ]

    print("\nPlan of actions:")
    for idx, action in enumerate(actions_to_perform, 1):
        print(f"  {idx}. {action}")

    if not prompt_yes_no("\nProceed with these actions?"):
        sys.exit("Aborted by user.")

    completed_actions: list[str] = []

    ensure_directories()
    completed_actions.append(f"Created/verified directory: {SYSTEM_CONFIG_DIR}")

    copy_config_with_confirmation(selected.config_file)
    completed_actions.append(f"Copied config to {SYSTEM_CONFIG_DIR / 'config.yml'}")

    copy_credential_with_confirmation(selected.credential_file)
    completed_actions.append(f"Copied credential to {SYSTEM_CONFIG_DIR / selected.credential_file.name}")

    ensure_log_file()
    completed_actions.append(f"Created/verified log file: {LOG_PATH}")

    write_plist_with_confirmation(cloudflared_bin)
    completed_actions.append(f"Wrote launchd plist: {PLIST_PATH}")

    reload_launchd()
    completed_actions.append("Reloaded launchd service")

    print("\n" + "=" * 40)
    print("Summary of actions completed:")
    for action in completed_actions:
        print(f"  [✓] {action}")
    print("=" * 40)

    print("\nSetup complete.")
    print("Verify status with:")
    print("  sudo launchctl list | grep cloudflared")
    print("  sudo tail -n 50 /var/log/cloudflared.log")


def ensure_root_or_reexec() -> None:
    """Ensure the script runs with root privileges; re-exec with sudo if needed."""
    if os.geteuid() == 0:
        return

    print("This script needs root privileges to configure system-level Cloudflared settings.")
    if not prompt_yes_no("Proceed with sudo elevation?"):
        sys.exit("Aborted: root privileges are required for system configuration.")

    original_user = os.environ.get("USER", "")
    os.environ.setdefault("ORIGINAL_USER", original_user)
    print("Elevating privileges with sudo...")
    os.execvp("sudo", ["sudo", "-E", sys.executable, *sys.argv])


def resolve_original_user() -> str:
    """Return the non-root user that invoked the script."""
    for var in ("ORIGINAL_USER", "SUDO_USER", "USER"):
        user = os.environ.get(var)
        if user:
            return user
    return os.environ.get("LOGNAME", "root")


def ensure_directories() -> None:
    """Create required system directories with correct permissions."""
    SYSTEM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SYSTEM_CONFIG_DIR, 0o755)
    try:
        shutil.chown(SYSTEM_CONFIG_DIR, user="root", group="wheel")
    except PermissionError:
        print(f"Warning: unable to chown {SYSTEM_CONFIG_DIR}; continue if intentional.")


def choose_tunnel_credential(cloudflared_dir: Path) -> Path:
    """Pick a credential JSON file from ~/.cloudflared, prompting when multiple exist."""
    candidates = sorted(
        p for p in cloudflared_dir.glob("*.json") if p.is_file()
    )
    if not candidates:
        sys.exit(f"No credential JSON files found in {cloudflared_dir}")
    if len(candidates) == 1:
        print(f"Found credential: {candidates[0].name}")
        return candidates[0]
    print("Multiple credentials found. Select one:")
    for idx, path in enumerate(candidates, start=1):
        print(f"  {idx}) {path.name}")
    while True:
        choice = input(f"Enter choice [1-{len(candidates)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]
        print("Invalid selection, try again.")


def confirm_config_contains_tunnel(config_file: Path, tunnel_id: str) -> bool:
    """Check whether config.yml references the selected tunnel id."""
    try:
        content = config_file.read_text()
    except OSError as exc:
        sys.exit(f"Failed to read {config_file}: {exc}")
    return tunnel_id in content


def copy_config_with_confirmation(config_file: Path) -> None:
    """Copy ~/.cloudflared/config.yml into /etc/cloudflared with confirmation."""
    dest = SYSTEM_CONFIG_DIR / "config.yml"
    handle_existing(dest)
    shutil.copy2(config_file, dest)
    os.chmod(dest, 0o644)
    try:
        shutil.chown(dest, user="root", group="wheel")
    except PermissionError:
        print(f"Warning: unable to chown {dest}; continue if intentional.")
    print(f"Wrote config to {dest}")


def copy_credential_with_confirmation(credential_file: Path) -> None:
    """Copy the selected tunnel credential into /etc/cloudflared."""
    dest = SYSTEM_CONFIG_DIR / credential_file.name
    handle_existing(dest)
    shutil.copy2(credential_file, dest)
    os.chmod(dest, 0o600)
    try:
        shutil.chown(dest, user="root", group="wheel")
    except PermissionError:
        print(f"Warning: unable to chown {dest}; continue if intentional.")
    print(f"Wrote credential to {dest}")


def ensure_log_file() -> None:
    """Create log file with correct permissions."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.touch(exist_ok=True)
    os.chmod(LOG_PATH, 0o644)
    try:
        shutil.chown(LOG_PATH, user="root", group="wheel")
    except PermissionError:
        print(f"Warning: unable to chown {LOG_PATH}; continue if intentional.")


def detect_cloudflared_binary() -> Path:
    """Find the cloudflared binary."""
    candidates = [
        Path("/opt/homebrew/bin/cloudflared"),
        Path("/usr/local/bin/cloudflared"),
    ]
    for path in candidates:
        if path.exists():
            return path
    which = shutil.which("cloudflared")
    if which:
        return Path(which)
    sys.exit("cloudflared binary not found. Install cloudflared first.")


def write_plist_with_confirmation(cloudflared_bin: Path) -> None:
    """Write the launchd plist and set permissions."""
    plist_content = generate_plist(cloudflared_bin)
    handle_existing(PLIST_PATH)
    PLIST_PATH.write_text(plist_content)
    os.chmod(PLIST_PATH, 0o644)
    try:
        shutil.chown(PLIST_PATH, user="root", group="wheel")
    except PermissionError:
        print(f"Warning: unable to chown {PLIST_PATH}; continue if intentional.")
    print(f"Wrote plist to {PLIST_PATH}")


def generate_plist(cloudflared_bin: Path) -> str:
    """Return launchd plist XML content."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cloudflare.cloudflared</string>
    <key>ProgramArguments</key>
    <array>
        <string>{cloudflared_bin}</string>
        <string>tunnel</string>
        <string>--config</string>
        <string>{SYSTEM_CONFIG_DIR / "config.yml"}</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{LOG_PATH}</string>
    <key>StandardErrorPath</key>
    <string>{LOG_PATH}</string>
    <key>WorkingDirectory</key>
    <string>{SYSTEM_CONFIG_DIR}</string>
</dict>
</plist>
"""


def reload_launchd() -> None:
    """Reload the launchd service."""
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], check=False)
    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode != 0:
        print("Failed to load launchd service:")
        print(result.stderr or result.stdout)
        sys.exit(result.returncode)
    print("launchd service loaded.")
    print("Reminder: verify with `sudo launchctl list | grep cloudflared` and `sudo tail -n 50 /var/log/cloudflared.log`.")


def handle_existing(target: Path) -> None:
    """Prompt user before overwriting an existing path."""
    if not target.exists():
        return
    print(f"{target} already exists.")
    action = prompt_overwrite_action()
    if action == "skip":
        print(f"Skipped writing {target}")
        sys.exit("User chose to skip; stopping to avoid partial setup.")
    if action == "backup":
        backup_path = target.with_suffix(target.suffix + f".bak-{int(time.time())}")
        shutil.copy2(target, backup_path)
        print(f"Backed up to {backup_path}")
    if action == "abort":
        sys.exit("Aborted by user.")
    # overwrite proceeds


def prompt_overwrite_action() -> str:
    """Prompt for overwrite action and return choice."""
    options = {
        "o": "overwrite",
        "b": "backup",
        "s": "skip",
        "q": "abort",
    }
    prompt = "[o]verwrite / [b]ackup+overwrite / [s]kip / [q]uit (default: q): "
    while True:
        choice = input(prompt).strip().lower() or "q"
        if choice in options:
            return options[choice]
        print("Invalid choice.")


def prompt_yes_no(message: str) -> bool:
    """Prompt for yes/no, default no."""
    while True:
        choice = input(f"{message} [y/N]: ").strip().lower()
        if choice in {"y", "yes"}:
            return True
        if choice in {"n", "no", ""}:
            return False
        print("Please enter y or n.")


if __name__ == "__main__":
    main()

