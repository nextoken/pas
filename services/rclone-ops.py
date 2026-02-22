#!/usr/bin/env python3
"""
@pas-executable
Guided setup for rclone Google Drive remotes (service-account first) for OpenClaw and other PAS tools.
"""

import json
import os
import shlex
import sys
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (  # type: ignore
    console,
    load_pas_config,
    save_pas_config,
    format_menu_choices,
    prompt_toolkit_menu,
    prompt_yes_no,
    run_command,
    copy_to_clipboard,
    get_pas_config_dir,
)
from rich.panel import Panel
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.completion import PathCompleter


# --- Configuration URLs ---
GOOGLE_CLOUD_CONSOLE_URL = "https://console.cloud.google.com/"
GOOGLE_CLOUD_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
RCLONE_DRIVE_DOCS_URL = "https://rclone.org/drive/"
# --------------------------

# --- Service Configuration ---
RCLONE_SERVICE = "rclone"
DEFAULT_PROFILE_ID = "openclaw"
DEFAULT_REMOTE_NAME = "gdrive"
DEFAULT_TARGET = f"{DEFAULT_REMOTE_NAME}:OpenClaw"
DEFAULT_SCOPE_SERVICE_ACCOUNT = "drive.file"
DEFAULT_SCOPE_OAUTH = "drive.file"
# -----------------------------

# --- Binary Detection ---
RCLONE_BINARY_CANDIDATES = [
    "/opt/homebrew/bin/rclone",
    "/usr/local/bin/rclone",
    "/usr/bin/rclone",
]
BREW_BINARY_CANDIDATES = [
    "/opt/homebrew/bin/brew",
    "/usr/local/bin/brew",
    "/usr/bin/brew",
]
LOCAL_SHELL = "/bin/zsh"
BREW_PACKAGE_RCLONE = "rclone"
# ------------------------


class RcloneOps:
    def __init__(self) -> None:
        self.config: Dict[str, Any] = load_pas_config(RCLONE_SERVICE)
        self.profiles: Dict[str, Dict[str, Any]] = self.config.get("profiles", {})
        self.current_profile_id: Optional[str] = self.config.get("current_profile_id")
        self._path_completer = PathCompleter(expanduser=True)
        # Default place to keep long-lived keys under PAS control.
        self.keys_dir: Path = get_pas_config_dir() / "keys"
        self.keys_dir.mkdir(parents=True, exist_ok=True)

        if not self.profiles:
            # Seed with a default OpenClaw-oriented profile shell (no secrets).
            self._ensure_default_profile()

    # --- Config helpers ---
    def _ensure_default_profile(self) -> None:
        if DEFAULT_PROFILE_ID in self.profiles:
            return
        self.profiles[DEFAULT_PROFILE_ID] = {
            "id": DEFAULT_PROFILE_ID,
            "name": "OpenClaw Drive",
            "remote_name": DEFAULT_REMOTE_NAME,
            "target": DEFAULT_TARGET,
            "auth_type": "service_account",  # or 'oauth'
            "service_account_file": None,
            "client_id": None,
            "client_secret": None,
            "scope": DEFAULT_SCOPE_SERVICE_ACCOUNT,
            "notes": "Default profile for OpenClaw sidecar storage.",
        }
        self.current_profile_id = DEFAULT_PROFILE_ID
        self._save()

    def _save(self) -> None:
        self.config["profiles"] = self.profiles
        if self.current_profile_id:
            self.config["current_profile_id"] = self.current_profile_id
        save_pas_config(RCLONE_SERVICE, self.config)

    def _get_current_profile(self) -> Optional[Dict[str, Any]]:
        if not self.current_profile_id:
            return None
        return self.profiles.get(self.current_profile_id)

    # --- Binary detection / installation ---
    def _resolve_rclone_binary(self) -> Optional[str]:
        # First, trust PATH via rclone directly.
        res = run_command(["rclone", "--version"])
        if res.returncode == 0:
            # We don't get the path here, but PATH works, so just use 'rclone'.
            return "rclone"

        # Fallback to common absolute locations.
        for candidate in RCLONE_BINARY_CANDIDATES:
            if Path(candidate).exists() and os.access(candidate, os.X_OK):
                return candidate

        return None

    def _is_local_admin_user(self) -> bool:
        if sys.platform != "darwin":
            return False
        res = run_command(["id", "-Gn"])
        if res.returncode != 0:
            return False
        groups = (res.stdout or "").strip().split()
        return "admin" in groups

    def _maybe_install_rclone_with_brew(self) -> bool:
        """
        Offer to install rclone via Homebrew for macOS admin users.
        Returns True if an install was attempted (success not guaranteed).
        """
        if sys.platform != "darwin":
            return False
        if not self._is_local_admin_user():
            return False

        brew_bin = None
        for candidate in BREW_BINARY_CANDIDATES:
            if Path(candidate).exists() and os.access(candidate, os.X_OK):
                brew_bin = candidate
                break

        if not brew_bin:
            return False

        if not prompt_yes_no(
            f"'rclone' was not found. Install it via Homebrew now from {brew_bin}?",
            default=True,
        ):
            return False

        console.print("[cyan]Installing rclone via Homebrew...[/cyan]")
        # Use a login shell so brew can pick up PATH and environment.
        run_command(
            [LOCAL_SHELL, "-lc", f"{shlex.quote(brew_bin)} install {BREW_PACKAGE_RCLONE}"],
            capture_output=False,
        )
        return True

    def _ensure_rclone_available(self) -> Optional[str]:
        rclone_bin = self._resolve_rclone_binary()
        if rclone_bin:
            return rclone_bin

        console.print("[bold yellow]rclone is not installed or not on PATH.[/bold yellow]")
        if self._maybe_install_rclone_with_brew():
            rclone_bin = self._resolve_rclone_binary()
            if rclone_bin:
                return rclone_bin

        console.print(
            "[red]rclone could not be found.[/red] "
            "Please install rclone manually (see https://rclone.org/install/) "
            "and then re-run this tool."
        )
        return None

    # --- Profile management ---
    def _choose_profile(self) -> Optional[str]:
        if not self.profiles:
            console.print("[yellow]No rclone profiles found yet.[/yellow]")
            return None

        items: List[Dict[str, Any]] = []
        for pid, profile in self.profiles.items():
            name = profile.get("name") or pid
            target = profile.get("target") or ""
            label = f"{name}  ->  {target}"
            if pid == self.current_profile_id:
                label += "  (ACTIVE)"
            items.append({"title": label, "value": pid})

        items.append({"title": "[Back]", "value": "__back__"})
        items.append({"title": "[Quit]", "value": "__quit__"})

        choice = prompt_toolkit_menu(format_menu_choices(items, "title", "value"))
        if not choice or choice in ("__back__", "__quit__"):
            return None
        return choice

    def manage_profiles_menu(self) -> None:
        while True:
            console.print("\n[bold cyan]Rclone Profiles[/bold cyan]")
            menu_items = [
                {"title": "Select active profile", "value": "select"},
                {"title": "Create new profile (clone from active)", "value": "create"},
                {"title": "Rename profile", "value": "rename"},
                {"title": "Delete profile", "value": "delete"},
                {"title": "[Back]", "value": "back"},
                {"title": "[Quit]", "value": "quit"},
            ]
            choice = prompt_toolkit_menu(format_menu_choices(menu_items))
            if not choice or choice in ("back", "quit"):
                return

            if choice == "select":
                pid = self._choose_profile()
                if pid:
                    self.current_profile_id = pid
                    self._save()
                    console.print(f"[green]Active profile set to[/green] [bold]{pid}[/bold].")
            elif choice == "create":
                base = self._get_current_profile() or self.profiles.get(DEFAULT_PROFILE_ID)
                if not base:
                    base = self.profiles[next(iter(self.profiles))]
                default_name = (base.get("name") or "New Profile") + " Copy"
                name = input(f"Profile name [{default_name}]: ").strip() or default_name
                # Ensure unique id
                pid_base = name.lower().replace(" ", "-")
                pid = pid_base
                idx = 1
                while pid in self.profiles:
                    idx += 1
                    pid = f"{pid_base}-{idx}"
                new_profile = dict(base)
                new_profile["id"] = pid
                new_profile["name"] = name
                self.profiles[pid] = new_profile
                self.current_profile_id = pid
                self._save()
                console.print(f"[green]Created profile[/green] [bold]{name}[/bold].")
            elif choice == "rename":
                pid = self._choose_profile()
                if pid:
                    current_name = self.profiles[pid].get("name") or pid
                    new_name = input(f"New name for '{current_name}' (leave blank to cancel): ").strip()
                    if new_name:
                        self.profiles[pid]["name"] = new_name
                        self._save()
                        console.print("[green]Profile renamed.[/green]")
            elif choice == "delete":
                pid = self._choose_profile()
                if pid and prompt_yes_no(
                    f"Really delete profile '{pid}'? This does NOT touch rclone.conf.", default=False
                ):
                    self.profiles.pop(pid, None)
                    if self.current_profile_id == pid:
                        self.current_profile_id = next(iter(self.profiles), None)
                    self._save()
                    console.print("[green]Profile deleted.[/green]")

    # --- Service-account flow (recommended) ---
    def _guide_service_account_creation(self) -> None:
        """
        Show step-by-step instructions for creating a service account and key file.
        This does not automate clicks, but gives the single most direct route.
        """
        text = (
            "[bold]You don't have a robot account key file [Google service account JSON] yet.[/bold]\n\n"
            "Follow these steps in your browser (you can do this on any machine where you're logged into Google):\n\n"
            "1. Open Google Cloud Console: "
            f"[cyan]{GOOGLE_CLOUD_CONSOLE_URL}[/cyan]\n"
            "2. Create or select a project to use for OpenClaw / PAS.\n"
            "3. Go to [bold]APIs & Services → Library[/bold] and make sure [bold]Google Drive API[/bold] is [bold]Enabled[/bold].\n"
            "4. Open the Credentials page directly:\n"
            f"   [cyan]{GOOGLE_CLOUD_CREDENTIALS_URL}[/cyan]\n"
            "   (This is the Google Cloud Console URL for APIs & Services → Credentials.)\n"
            "5. Click [bold]Create credentials → Service account[/bold].\n"
            "   - Give it a clear name, e.g. 'openclaw-bot'.\n"
            "   - Finish the wizard (roles are usually optional for simple Drive sharing).\n"
            "6. On the new service account details page, go to the [bold]Keys[/bold] tab.\n"
            "   - Click [bold]Add key → Create new key[/bold].\n"
            "   - Choose [bold]JSON[/bold] and click [bold]Create[/bold].\n"
            "   - A JSON file will be downloaded to your machine — this is the robot account key.\n\n"
            "7. In Google Drive (your normal account), create or pick the folder(s) you want OpenClaw to use.\n"
            "   - Right click the folder → [bold]Share[/bold].\n"
            "   - Share it with the service account's email address (shown in the Cloud Console), "
            "giving it [bold]Editor[/bold] access.\n\n"
            "When you're done, come back here and drag the downloaded JSON file into this terminal when prompted.\n"
        )
        console.print(Panel(text, title="How to create a service account + key", border_style="green"))

    def setup_service_account_flow(self) -> None:
        console.print(
            Panel(
                "[bold]Goal:[/bold] Help OpenClaw (and other tools) use a specific Google Drive folder.\n\n"
                "- We recommend a [bold]robot account [Google service account][/bold] that only sees the folders you share with it.\n"
                "- You keep owning the data in your normal Google account.\n"
                "- OpenClaw writes into a dedicated folder like 'OpenClaw' in your Drive.",
                title="Service-account based Drive setup (recommended)",
                border_style="green",
            )
        )

        profile = self._get_current_profile() or self.profiles.get(DEFAULT_PROFILE_ID)
        if not profile:
            self._ensure_default_profile()
            profile = self._get_current_profile()
        assert profile is not None

        # Remote name [nickname used inside rclone]
        default_remote = profile.get("remote_name") or DEFAULT_REMOTE_NAME
        remote_name = input(
            f"Connection nickname inside rclone [remote name, e.g. 'gdrive'] [{default_remote}]: "
        ).strip() or default_remote

        # Target folder [where OpenClaw will store files]
        default_target = profile.get("target") or DEFAULT_TARGET
        target_prompt = (
            "Folder in Drive that OpenClaw will use "
            "[rclone target, e.g. 'gdrive:OpenClaw'] "
            f"[{default_target}]: "
        )
        target = input(target_prompt).strip() or default_target

        # Service account JSON key [robot account key file]
        existing_sa = profile.get("service_account_file") or ""

        sa_path: Optional[Path] = None
        while True:
            sa_prompt = (
                "Path to robot account key file [Google service account JSON].\n"
                "If you don't have this yet, type nothing and press Enter to see step-by-step browser instructions.\n"
                "Drag the file into this terminal or paste the full path.\n"
                f"service_account_file [{existing_sa}]: "
            )
            sa_path_in = pt_prompt(sa_prompt, completer=self._path_completer).strip() or existing_sa

            if not sa_path_in:
                # Guide user through creating the service account + key.
                self._guide_service_account_creation()
                # Loop back to ask again.
                continue

            sa_path = Path(sa_path_in).expanduser()
            if not sa_path.exists():
                console.print(
                    f"[red]Service account JSON file not found at[/red] [bold]{sa_path}[/bold]. "
                    "Please check the path or follow the creation guide."
                )
                # Ask if they want to see the guide, then loop.
                if prompt_yes_no("Show the creation steps again?", default=True):
                    self._guide_service_account_creation()
                continue

            try:
                _ = json.loads(sa_path.read_text())
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Could not parse JSON file:[/red] {e}")
                if not prompt_yes_no("Try selecting a different JSON file?", default=True):
                    return
                existing_sa = ""
                continue

            # If we reach here, we have a valid JSON file.
            break

        # Offer to copy JSON into ~/.pas/keys for long-term, tool-managed storage.
        if not str(sa_path).startswith(str(self.keys_dir)):
            dest_name = sa_path.name
            dest = self.keys_dir / dest_name
            # Avoid clobbering an existing different file.
            idx = 1
            while dest.exists():
                try:
                    if dest.read_bytes() == sa_path.read_bytes():
                        # Same contents, safe to reuse.
                        break
                except Exception:
                    pass
                dest = self.keys_dir / f"{sa_path.stem}-{idx}{sa_path.suffix}"
                idx += 1

            if not dest.exists() or dest.resolve() != sa_path.resolve():
                msg = (
                    f"Copy this key file into [bold]{self.keys_dir}[/bold] for long-term use?\n"
                    "You can delete it from Downloads afterward if you like."
                )
                if prompt_yes_no(msg, default=True):
                    try:
                        shutil.copy2(sa_path, dest)
                        console.print(f"[green]Copied service account key to[/green] [bold]{dest}[/bold].")
                        sa_path = dest
                    except Exception as e:  # noqa: BLE001
                        console.print(f"[red]Failed to copy key file:[/red] {e}")

        # Optional: root folder ID (anchor this connection to one shared folder)
        console.print(
            "\n[bold]Optional but recommended:[/bold]\n"
            "If you shared a specific folder (e.g. 'OpenClaw') with this robot account, "
            "you can paste that folder's URL here so rclone only sees that folder.\n"
            "Example URL: https://drive.google.com/drive/folders/[FOLDER_ID]\n"
        )
        existing_root = profile.get("root_folder_id") or ""
        root_input = input(
            f"Google Drive folder URL or ID for the shared folder [root_folder_id] [{existing_root}]: "
        ).strip() or existing_root

        root_folder_id: Optional[str] = None
        if root_input:
            # Try to extract folder ID from common URL forms; fall back to raw input.
            val = root_input
            if "folders/" in val:
                val = val.split("folders/", 1)[1]
                val = val.split("?", 1)[0]
            if "id=" in val and ("/" not in val):
                # Handle URLs like ...?id=<ID>
                val = val.split("id=", 1)[1]
                val = val.split("&", 1)[0]
            root_folder_id = val.strip().strip("/")

        # Scope selection (what Drive access this connection has)
        console.print(
            "\n[bold]Choose how broad the robot's access should be [Google Drive API scope]:[/bold]\n"
            "1. Only files and folders this robot created or that were explicitly shared with it [drive.file] (recommended).\n"
            "2. All files in the Drive this robot can see [drive].\n"
            "3. Enter a custom scope string.\n"
        )
        default_scope = profile.get("scope") or DEFAULT_SCOPE_SERVICE_ACCOUNT
        scope_choice = input(f"Scope choice [1-3] (default 1 => {default_scope}): ").strip() or "1"
        if scope_choice == "2":
            scope = "drive"
        elif scope_choice == "3":
            scope = input(f"Custom scope string [current: {default_scope}]: ").strip() or default_scope
        else:
            scope = "drive.file"

        # Update profile
        profile["remote_name"] = remote_name
        profile["target"] = target
        profile["auth_type"] = "service_account"
        profile["service_account_file"] = str(sa_path)
        profile["scope"] = scope
        if root_folder_id:
            profile["root_folder_id"] = root_folder_id

        # Ensure profile is stored under a stable id
        pid = profile.get("id") or DEFAULT_PROFILE_ID
        profile["id"] = pid
        self.profiles[pid] = profile
        self.current_profile_id = pid
        self._save()

        # Generate rclone command
        cmd = [
            "rclone",
            "config",
            "create",
            remote_name,
            "drive",
            "service_account_file",
            str(sa_path),
            "scope",
            scope,
        ]
        if root_folder_id:
            cmd.extend(["root_folder_id", root_folder_id])
        cmd_str = " ".join(shlex.quote(p) for p in cmd)

        panel_text = (
            "[bold]Next step:[/bold]\n"
            "We will configure rclone so it knows about this connection.\n\n"
            "You can run the following command yourself, or let this tool run it for you:\n\n"
            f"[cyan]{cmd_str}[/cyan]\n\n"
            "This will create an rclone remote named "
            f"[bold]{remote_name}[/bold] that points to your robot account [service account]."
        )
        console.print(Panel(panel_text, title="rclone config create (service account)", border_style="blue"))

        if prompt_yes_no("Copy this command to your clipboard?", default=True):
            copy_to_clipboard(cmd_str)
            console.print("[green]Command copied to clipboard.[/green]")

        rclone_bin = self._ensure_rclone_available()
        if rclone_bin and prompt_yes_no("Run this rclone config command for you now?", default=True):
            cmd_to_run = [
                rclone_bin,
                "config",
                "create",
                remote_name,
                "drive",
                "service_account_file",
                str(sa_path),
                "scope",
                scope,
            ]
            if root_folder_id:
                cmd_to_run.extend(["root_folder_id", root_folder_id])
            res = run_command(cmd_to_run, capture_output=True)
            if res.returncode == 0:
                console.print("[green]rclone remote created successfully.[/green]")
            else:
                console.print("[red]rclone config create failed.[/red]")
                if res.stderr:
                    console.print(res.stderr.strip())

    # --- OAuth client flow (alternative) ---
    def setup_oauth_flow(self) -> None:
        console.print(
            Panel(
                "[bold]Alternative path:[/bold] Use your own Google login [OAuth client] instead of a robot account.\n\n"
                "- Simpler if you already use your main Google account for everything.\n"
                "- You may share a specific folder with this app if you prefer limited access.\n",
                title="OAuth client based Drive setup (alternative)",
                border_style="yellow",
            )
        )

        profile = self._get_current_profile() or self.profiles.get(DEFAULT_PROFILE_ID)
        if not profile:
            self._ensure_default_profile()
            profile = self._get_current_profile()
        assert profile is not None

        default_remote = profile.get("remote_name") or DEFAULT_REMOTE_NAME
        remote_name = input(f"Connection nickname inside rclone [remote name, e.g. 'gdrive'] [{default_remote}]: ").strip() or default_remote

        default_target = profile.get("target") or DEFAULT_TARGET
        target_prompt = (
            "Folder in Drive that OpenClaw will use "
            "[rclone target, e.g. 'gdrive:OpenClaw'] "
            f"[{default_target}]: "
        )
        target = input(target_prompt).strip() or default_target

        console.print(
            Panel(
                "To get a Google [OAuth client_id/client_secret]:\n"
                "1. Go to the Google Cloud Console project you want to use for OpenClaw/rclone.\n"
                "2. Go to [bold]APIs & Services → Library[/bold] and enable the [bold]Google Drive API[/bold] if it is not already enabled.\n"
                "3. Open the Credentials page directly:\n"
                f"   [cyan]{GOOGLE_CLOUD_CREDENTIALS_URL}[/cyan]\n"
                "   (This is the Google Cloud Console URL for APIs & Services → Credentials.)\n"
                "4. Create an [bold]OAuth client ID[/bold] of type [bold]Desktop app[/bold].\n"
                "5. Copy the [bold]Client ID[/bold] and [bold]Client Secret[/bold] here.\n",
                title="Getting client_id and client_secret",
                border_style="blue",
            )
        )

        existing_client_id = profile.get("client_id") or ""
        client_id = input(f"Google Application Client ID [client_id] [{existing_client_id}]: ").strip() or existing_client_id
        existing_secret = profile.get("client_secret") or ""
        client_secret = input("Google Application Client Secret [client_secret]: ").strip() or existing_secret

        if not client_id or not client_secret:
            console.print("[red]Both client_id and client_secret are required for this flow.[/red]")
            return

        console.print(
            "\n[bold]Choose Drive access level [scope]:[/bold]\n"
            "1. Only files/folders created by, or explicitly shared with, this app [drive.file] (recommended).\n"
            "2. All files in your Drive [drive].\n"
            "3. Enter a custom scope string.\n"
        )
        default_scope = profile.get("scope") or DEFAULT_SCOPE_OAUTH
        scope_choice = input(f"Scope choice [1-3] (default 1 => {default_scope}): ").strip() or "1"
        if scope_choice == "2":
            scope = "drive"
        elif scope_choice == "3":
            scope = input(f"Custom scope string [current: {default_scope}]: ").strip() or default_scope
        else:
            scope = "drive.file"

        # Update profile
        profile["remote_name"] = remote_name
        profile["target"] = target
        profile["auth_type"] = "oauth"
        profile["client_id"] = client_id
        profile["client_secret"] = client_secret
        profile["scope"] = scope

        pid = profile.get("id") or DEFAULT_PROFILE_ID
        profile["id"] = pid
        self.profiles[pid] = profile
        self.current_profile_id = pid
        self._save()

        cmd = [
            "rclone",
            "config",
            "create",
            remote_name,
            "drive",
            "client_id",
            client_id,
            "client_secret",
            client_secret,
            "scope",
            scope,
        ]
        cmd_str = " ".join(shlex.quote(p) for p in cmd)

        panel_text = (
            "[bold]Next step:[/bold]\n"
            "We will configure rclone using your own Google login [OAuth].\n\n"
            "You can run the following command yourself, or let this tool run it for you:\n\n"
            f"[cyan]{cmd_str}[/cyan]\n\n"
            "rclone will open a browser window for you to grant access to this app.\n"
        )
        console.print(Panel(panel_text, title="rclone config create (OAuth client)", border_style="blue"))

        if prompt_yes_no("Copy this command to your clipboard?", default=True):
            copy_to_clipboard(cmd_str)
            console.print("[green]Command copied to clipboard.[/green]")

        rclone_bin = self._ensure_rclone_available()
        if rclone_bin and prompt_yes_no("Run this rclone config command for you now?", default=False):
            cmd_to_run = [
                rclone_bin,
                "config",
                "create",
                remote_name,
                "drive",
                "client_id",
                client_id,
                "client_secret",
                client_secret,
                "scope",
                scope,
            ]
            res = run_command(cmd_to_run, capture_output=True)
            if res.returncode == 0:
                console.print("[green]rclone remote created successfully.[/green]")
            else:
                console.print("[red]rclone config create failed.[/red]")
                if res.stderr:
                    console.print(res.stderr.strip())

    # --- Snippets & validation ---
    def show_snippet_for_current(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            console.print("[yellow]No active profile selected.[/yellow]")
            return

        remote_name = profile.get("remote_name") or DEFAULT_REMOTE_NAME
        target = profile.get("target") or DEFAULT_TARGET
        auth_type = profile.get("auth_type") or "service_account"
        scope = profile.get("scope") or (DEFAULT_SCOPE_SERVICE_ACCOUNT if auth_type == "service_account" else DEFAULT_SCOPE_OAUTH)
        root_folder_id = profile.get("root_folder_id") or None

        if auth_type == "service_account":
            sa_file = profile.get("service_account_file") or "<path-to-service-account.json>"
            cmd = [
                "rclone",
                "config",
                "create",
                remote_name,
                "drive",
                "service_account_file",
                sa_file,
                "scope",
                scope,
            ]
            if root_folder_id:
                cmd.extend(["root_folder_id", root_folder_id])
        else:
            client_id = profile.get("client_id") or "<your-client-id>"
            client_secret = profile.get("client_secret") or "<your-client-secret>"
            cmd = [
                "rclone",
                "config",
                "create",
                remote_name,
                "drive",
                "client_id",
                client_id,
                "client_secret",
                client_secret,
                "scope",
                scope,
            ]
            if root_folder_id:
                cmd.extend(["root_folder_id", root_folder_id])

        cmd_str = " ".join(shlex.quote(p) for p in cmd)
        test_cmd_str = f"rclone lsd {shlex.quote(target)}"

        # Build a descriptive summary plus the exact commands.
        text = (
            f"[bold]Active profile:[/bold] {profile.get('name') or profile.get('id')}\n"
            f"- Remote name [rclone remote]: [cyan]{remote_name}[/cyan]\n"
            f"- Target folder [Drive path, e.g. 'gdrive:OpenClaw']: [cyan]{target}[/cyan]\n"
            f"- Auth type: [cyan]{auth_type}[/cyan]\n"
            f"- Scope [Google Drive API scope]: [cyan]{scope}[/cyan]\n"
        )
        if root_folder_id:
            text += f"- Root folder ID [shared folder anchor]: [cyan]{root_folder_id}[/cyan]\n\n"
        else:
            text += "\n"

        text += (
            "[bold]1. Configure rclone:[/bold]\n"
            f"{cmd_str}\n\n"
            "[bold]2. Test access:[/bold]\n"
            f"{test_cmd_str}\n"
        )
        console.print(Panel(text, title="rclone commands for current profile", border_style="magenta"))

        if prompt_yes_no("Copy both commands to clipboard (stacked with newline)?", default=False):
            copy_to_clipboard(cmd_str + "\n" + test_cmd_str)
            console.print("[green]Commands copied to clipboard.[/green]")

    def validate_current_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            console.print("[yellow]No active profile selected.[/yellow]")
            return

        rclone_bin = self._ensure_rclone_available()
        if not rclone_bin:
            return

        target = profile.get("target") or DEFAULT_TARGET
        console.print(f"[cyan]Running: {rclone_bin} lsd {target}[/cyan]")
        res = run_command([rclone_bin, "lsd", target], capture_output=True)
        if res.returncode == 0:
            console.print("[green]Success! rclone can list the target folder.[/green]")
            if res.stdout:
                console.print(res.stdout)
        else:
            console.print("[red]Validation failed.[/red]")
            if res.stderr:
                console.print(res.stderr.strip())
            console.print(
                "\n[dim]Common issues:[/dim]\n"
                "- The service account or OAuth app does not have access to this folder.\n"
                "- The folder name/path is mistyped.\n"
                "- Google Drive API is not enabled for the project.\n"
            )

    # --- Docs/help ---
    def show_help_panel(self) -> None:
        text = (
            "[bold]Design intent:[/bold]\n"
            "This tool is a guided assistant for connecting PAS/OpenClaw to Google Drive via rclone.\n"
            "You only need to know that you have Google Drive and which folder you want OpenClaw to use.\n\n"
            "[bold]Recommended pattern:[/bold]\n"
            "- Use a robot account [Google service account] created in Google Cloud.\n"
            "- Share one or more folders from your normal Google Drive account to that robot account email.\n"
            "- Point rclone at the robot account key file and the shared folder path (e.g. 'gdrive:OpenClaw') with root_folder_id set to that folder.\n\n"
            "[bold]Other patterns (when they make sense):[/bold]\n"
            "- Dedicated storage owner account: use a separate Google account (e.g. 'openclaw-storage@...') that owns all OpenClaw data, and share folders from it to one or more service accounts.\n"
            "- Multiple environments: use different remotes/profiles (and optionally different projects/accounts) for 'staging' vs 'production' so their data never mix.\n"
            "- Personal simple setup (most users): keep using your primary Google account as the owner, share only an 'OpenClaw' folder with the robot account, and anchor the remote with root_folder_id.\n\n"
            "[bold]Sidecar architecture:[/bold]\n"
            "- rclone runs alongside OpenClaw as a sidecar, mounting the shared folder into a local path.\n"
            "- OpenClaw writes its files into that mount; rclone syncs to Google Drive in the background.\n\n"
            f"For more on rclone + Drive, see: [cyan]{RCLONE_DRIVE_DOCS_URL}[/cyan]\n"
        )
        console.print(Panel(text, title="rclone-ops overview", border_style="cyan"))

    # --- Main menu ---
    def main_menu(self) -> None:
        version_blurb = (
            "[bold cyan]rclone-ops[/bold cyan]\n\n"
            "Guided setup for rclone Google Drive remotes used by PAS tools like OpenClaw.\n\n"
            "[bold]Recommended route:[/bold]\n"
            "- Use a robot account [service account] with only specific folders shared to it.\n"
            "- Let this tool generate and (optionally) run the rclone config commands.\n"
        )
        console.print(Panel(version_blurb, title="rclone-ops", border_style="blue"))

        while True:
            active = self._get_current_profile()
            if active:
                console.print(
                    f"\n[dim]Active profile: [bold]{active.get('name') or active.get('id')}[/bold]  "
                    f"→  [cyan]{active.get('target') or DEFAULT_TARGET}[/cyan][/dim]"
                )

            menu_items = [
                {
                    "title": "Set up Drive access with a robot account [service account] (recommended)",
                    "value": "sa",
                },
                {
                    "title": "Set up Drive access with your own Google login [OAuth client]",
                    "value": "oauth",
                },
                {"title": "Manage profiles (folders & connections)", "value": "profiles"},
                {"title": "Show rclone commands for current profile", "value": "snippet"},
                {"title": "Validate current profile (test rclone access)", "value": "validate"},
                {"title": "Help / What is this doing?", "value": "help"},
                {"title": "[Quit]", "value": "quit"},
            ]

            choice = prompt_toolkit_menu(format_menu_choices(menu_items))
            if not choice or choice == "quit":
                return

            if choice == "sa":
                self.setup_service_account_flow()
            elif choice == "oauth":
                self.setup_oauth_flow()
            elif choice == "profiles":
                self.manage_profiles_menu()
            elif choice == "snippet":
                self.show_snippet_for_current()
            elif choice == "validate":
                self.validate_current_profile()
            elif choice == "help":
                self.show_help_panel()


def main() -> None:
    ops = RcloneOps()
    ops.main_menu()


if __name__ == "__main__":
    main()

