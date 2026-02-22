# Developer Guide

Welcome to the PAS (Process Automation Setups/Scripts) project. This guide outlines how to contribute new scripts and maintain the project.

## Project Philosophy

PAS is designed to be a "living toolkit." Scripts are added as they are needed and are automatically registered to your system path for easy access.

### Platform Focus: macOS First
PAS is **macOS-centric**. We prioritize deep integration with macOS features (Keychain, `pmset`, Screen Sharing, `launchd`). 
- When writing scripts, assume the primary user is on macOS.
- If a tool performs platform-specific operations, use `sys.platform` to check for `darwin`.
- Provide graceful fallbacks or clear error messages if a tool is run on an unsupported platform.

### Versioning Policy
PAS uses **Calendar Versioning (CalVer)** in the format `YYYY.MM.DD` (with an optional `.N` suffix for multiple releases on the same day).
- The current version is stored in the `VERSION` file at the root.
- Users can check the version using `pas --version`.
- Updates are pulled from the `main` branch, and the version is updated to reflect the date of significant changes or releases.
- Git tags are used to mark versions (e.g., `v2026.01.11`).

The core aim is to create **living interactive playbooks** that reduce the cognitive load of common operations. We prioritize:
- **Semi-automation**: Mixing API calls with human-in-the-loop decisions.
- **Guided Discovery**: Helping users find necessary IDs, tokens, and URLs during the setup process rather than assuming they are already available.
- **Precision**: Automating well-defined but tedious or easy-to-miss steps.
- **Convenience**: Providing immediate terminal utilities that are faster than prompting an AI or navigating complex web menus.

### Cognitive Load Offloading

Tools should assume the user mostly knows their **goal**, not the provider's vocabulary. Whenever possible:
- Design flows so a user can say, for example: _"I have Google Drive and I want OpenClaw to save files there"_ without needing to know about rclone remotes, OAuth scopes, or service accounts.
- Prefer guided, step-by-step wizards over expecting users to piece things together from flags and external docs.
- When you must surface provider terms, lead with plain language, then include the official term in brackets, e.g., `"robot account [Google service account]"`, `"folder OpenClaw will use in your Drive [rclone target gdrive:OpenClaw]"`.

## Core Mechanisms

### Auto-Registration
The project uses a script called `scripts/refresh-bin` to manage access to tools. 

1.  **Scanning**: It scans the repository for files containing the marker `@pas-executable` in the first 5 lines.
2.  **Linking**: For every matching file, it creates a symlink in the `bin/` directory.
3.  **Pathing**: If `~/bin_pas` is in your `PATH` (handled by `make setup`), these tools become available globally.

### Composability
**Prefer composing tools via the CLI (subprocess) rather than in-process Python imports.** This keeps each tool a standalone executable that can be run by users, scripts, or other tools without shared code paths.

- **Do**: Invoke another PAS tool as a subprocess (e.g. resolve the script path and run it with `sys.executable`, or call it by name if `bin/` is on `PATH`). Pass arguments with flags; pass secrets via stdin when possible so they do not appear in process listings.
- **Don’t**: Use `importlib` or `sys.path` hacks to load a sibling script as a module just to call one function.
- **Benefits**: Tools stay decoupled, testable from the shell, and reusable in pipelines or by other tools. Changes to one tool do not require the other to be updated unless the CLI contract changes.
- **Example**: `openclaw-ops` composes `user-ops` for creating an isolated macOS account: it runs `user-ops --list` to check if a user exists (parsing stdout) and `user-ops --create ...` with the password on stdin. See `services/openclaw-ops.py` for the pattern.
- **Opinionated setups**: Some tools (e.g. `openclaw-ops` for OpenClaw) are **strongly opinionated**: they assume specific choices (per-user Homebrew, Python 3.11, default account name, etc.). Document this in the tool’s docstring and UI so users know to use a different workflow if their conventions differ.

## Creating a New Script

To add a new script, follow these steps:

### 1. Choose the Right Directory
- `media/`: For scripts that manipulate video, audio, or images.
- `services/`: For scripts that interact with APIs (Cloudflare, Supabase, etc.) or system configuration.
- `utils/`: For general-purpose internal utilities.

### 2. Standard Header
Every script should start with a shebang, followed by the `@pas-executable` marker and a concise one-line description on the very next line. This ensures `pas list` can display the tool's purpose correctly.

```python
#!/usr/bin/env python3
"""
@pas-executable
A brief one-line description of what this script does.
"""
```

### 3. Capability Summary (Self-Documentation)
To ensure scripts are user-friendly, each tool should display a concise summary upon startup (e.g., using a Rich Panel). This summary should outline:
- What the script is capable of doing.
- Any automated steps it performs.
- What the user is expected to provide or do manually.

This "self-documenting" feature helps users understand the tool's scope immediately without referring back to documentation.

### 4. Argument Parsing
Always provide a `-h/--help` flag. For Python scripts, use `argparse`.

```python
import argparse

def main():
    parser = argparse.ArgumentParser(description="Description for -h output")
    # Add arguments here...
    args = parser.parse_args()
```

### 5. Standardized Menus (MANDATORY)
**All interactive menus MUST use the standardized menu system from `helpers/tui.py` (exposed via `helpers/core.py`).** This ensures consistent UX, proper hotkey support, and accessibility.

**Requirements:**
- **Always** use `format_menu_choices()` to prepare menu items (handles index padding and hotkey formatting)
- **Always** use `prompt_toolkit_menu()` for displaying menus (supports arrow keys and multi-digit hotkeys)
- **Every menu** must include a `[Quit]` option (mapped to `q`) for immediate exit
- **Every sub-menu** (after initial tool startup/auth) must include a `[Back]` option (mapped to `b`)

**Example:**
```python
from helpers.core import format_menu_choices
from helpers.tui import prompt_toolkit_menu

menu_items = [
    {"title": "Option 1", "value": "opt1"},
    {"title": "Option 2", "value": "opt2"},
    {"title": "[Back]", "value": None},
    {"title": "[Quit]", "value": None}
]

formatted_choices = format_menu_choices(menu_items, title_field="title", value_field="value")
selected = prompt_toolkit_menu(formatted_choices)

if not selected or selected == "quit":
    return
```

**DO NOT** use plain `input()` or `print()` for menu selection. The standardized system provides better UX with arrow key navigation, consistent formatting, and proper hotkey handling.

### 6. Shared Description for -h and Panel
**Keep the tool’s identity and descriptions in one place** so the CLI name, panel title, `pas list`, and `-h` stay consistent. User-specific info and argument options stay separate.

- **`TOOL_ID`**: CLI name (what you type to run the tool), e.g. `"user-ops"`, `"openclaw-ops"`. Used by `pas list` as the tool name when present; otherwise the script filename stem is used.
- **`TOOL_TITLE`**: Human-readable title for the panel header, e.g. `"User Management Operations"`, `"OpenClaw environment checker"`. Use in `Panel(title=TOOL_TITLE)`.
- **`TOOL_SHORT_DESC`**: One-line description shown in `pas list`. Use a single, clear sentence.
- **`TOOL_DESCRIPTION`**: Longer description for **`ArgumentParser(description=TOOL_DESCRIPTION)`** and the **main Panel** body (e.g. as the first line or intro).
- Do not duplicate that prose across parser and panel. Reference implementation: `services/openclaw-ops.py`, `services/user-ops.py`, `services/ssh-ops.py`.

### 7. URL Consolidation
To ensure easy maintenance when service providers change their dashboard or documentation structures, **all external URLs and URL templates must be consolidated at the top of the script**. 

**Requirements:**
- Place URLs in a dedicated section after imports.
- Use uppercase constant names (e.g., `CF_DASHBOARD_URL`).
- Use clear templates for dynamic URLs (e.g., `PROVIDER_EDIT_URL_TEMPLATE`).
- Reference these constants throughout the script instead of inlining URL strings or fragments.

**Example:**
```python
# --- Configuration URLs ---
PROVIDER_DASHBOARD_URL = "https://dash.example.com/"
PROVIDER_API_DOCS_URL = "https://docs.example.com/api"
PROVIDER_EDIT_URL_TEMPLATE = f"{PROVIDER_DASHBOARD_URL}project/{{project_id}}/edit"
# --------------------------
```

### 8. Configuration Consolidation
**All hardcoded configuration values must be consolidated at the top of the script** in a dedicated configuration section. This includes ports, protocols, file paths, service names, hostname patterns, system settings, and any other magic numbers or strings that might need to be changed.

**Requirements:**
- Place configuration constants in logical subsections after imports (after URL consolidation section).
- Use uppercase constant names with descriptive prefixes (e.g., `SSH_PORT`, `MACOS_LAUNCHDAEMON_PATH`).
- Group related constants together (e.g., Service Configuration, Hostname Patterns, System Paths).
- Use clear comments to explain the purpose of each constant.
- For complex configurations (like command arguments), create consolidated lists/arrays that can be reused.

**Benefits:**
- **Single source of truth**: Change values once, applies everywhere.
- **Easy maintenance**: No need to search through code for hardcoded values.
- **Documentation**: Constants serve as inline documentation.
- **Consistency**: Ensures same values used throughout script.

**Example:**
```python
# --- Service Configuration ---
SSH_PORT = 22
VNC_PORT = 5900
SSH_SERVICE_PROTOCOL = "ssh://"
VNC_SERVICE_PROTOCOL = "tcp://"
DEFAULT_INGRESS_FALLBACK = "http_status:404"
# --------------------------

# --- Hostname Patterns ---
SSH_HOSTNAME_SUFFIX = "-ssh"
VNC_HOSTNAME_SUFFIX = "-vnc"
# --------------------------

# --- macOS Power Management ---
MACOS_PMSET_SLEEP = 0  # System never sleeps
MACOS_PMSET_DISPLAYSLEEP = 5  # Display sleeps after 5 minutes
MACOS_PMSET_ARGS = [
    "-a",
    "sleep", str(MACOS_PMSET_SLEEP),
    "displaysleep", str(MACOS_PMSET_DISPLAYSLEEP),
]
# --------------------------

# --- Service Paths ---
MACOS_LAUNCHDAEMON_PATH = "/Library/LaunchDaemons/com.cloudflare.cloudflared.plist"
LINUX_SYSTEMD_SERVICE = "cloudflared"
# --------------------------
```

**What to Consolidate:**
- Ports and protocols (e.g., `22`, `5900`, `ssh://`, `tcp://`)
- File and directory paths (e.g., service plist paths, config directories)
- Service names (e.g., `cloudflared`, `systemd` service names)
- Hostname patterns and suffixes (e.g., `-ssh`, `-vnc`)
- System command arguments (e.g., `pmset` settings, `systemctl` commands)
- Default values (e.g., DNS TTL, proxied settings, timeouts)
- Any magic numbers or strings that appear multiple times or might need adjustment

**Reference Implementation:**
See `services/cf-tunnel-ops.py` for a complete example of configuration consolidation.

### 9. Using Helpers
Check `helpers/core.py` and `helpers/cloudflare.py` for shared functionality. **Always** check these first before implementing common logic:
- `load_env_local()` / `save_env_local()`: For managing local secrets in `.env.local`.
- `load_pas_config(service)` / `save_pas_config(service, data)`: For managing persistent service-specific data in `~/.pas/`.
- `prompt_yes_no()`: For interactive confirmations.
- `format_menu_choices(items, ...)`: **Always** use this to prepare items for a menu. It handles index padding and hotkey formatting.
- `prompt_toolkit_menu(choices, hotkeys=...)`: **Always** use this for CLI selection menus. It supports arrow keys and multi-digit hotkeys.
- `copy_to_clipboard(text)`: For system-level clipboard support.
- `run_command(cmd_list)`: For consistent subprocess execution with output capture.
- **Cloudflare (New)**: Use `helpers/cloudflare.py` for all Cloudflare API interactions (Tunnels, DNS, Zero Trust). This module provides robust error handling and idempotent operations (like `update_dns_record`).

## AI Assistant Guidelines

When acting as an AI coding agent for this repository:
1. **Consolidation First**: Before implementing a UI element, config handler, or system utility, check `helpers/core.py`. Do not reinvent common patterns.
2. **Standardized Menus (MANDATORY)**: **ALL** CLI menus MUST use `format_menu_choices` and `prompt_toolkit_menu` from `helpers/core.py`. Never use plain `input()` or `print()` for menu selection. See section 5 above for details.
    - **Universal Hotkeys**: Every menu level must include a `[Quit]` option (mapped to **`q`**) for immediate exit. Every level *after* the initial tool startup/auth must also include a `[Back]` option (mapped to **`b`**) to return to the previous context or exit gracefully.
3. **Security Awareness**: Never store raw tokens or secrets in JSON files. Leverage the existing "secretization" logic in `helpers/core.py`. The toolkit automatically handles Keychain storage on macOS and provides heuristics for URL-based keys (slashes/dots).
4. **Self-Documentation**: Always include a Rich Panel summary at the start of new scripts as per the "Capability Summary" section.
5. **Path Handling**: Use `pathlib.Path` for all filesystem operations. Expand users (`~`) and resolve paths to handle drag-and-drop or quoted input from users.
6. **Robustness**: When using URLs as configuration keys, account for trailing slashes to prevent duplicate entries and secret retrieval failures.
7. **Configuration Consolidation (MANDATORY)**: **ALL** hardcoded values (ports, paths, service names, hostname patterns, system settings) MUST be consolidated as constants at the top of the script. See section 7 above for detailed requirements and examples. Never inline magic numbers or strings that might need to be changed.
8. **Composability**: When a script needs functionality provided by another PAS tool, invoke it via the CLI (subprocess) rather than importing it as a module. See the **Composability** subsection under Core Mechanisms.
9. **Guided Discovery**: When a script requires an ID or token that a user might not have handy (e.g., Cloudflare Account ID), provide a direct URL to the service provider's dashboard where that information can be found. Use `CF_DASHBOARD_URL` or similar constants to make these prompts helpful and actionable.

### 8. Persistent Configuration
For scripts that require persistent storage (e.g., API tokens, bot IDs), use the common configuration directory located at `~/.pas/`.
- **Location**: `~/.pas/`
- **Format**: Service-specific JSON files (e.g., `tg.json`).
- **Access**: Use the helper functions `load_pas_config` and `save_pas_config`.
- **Security (macOS)**: These helpers automatically migrate sensitive keys (defined in `SENSITIVE_KEYS` in `helpers/core.py`) to the macOS Keychain. This is transparent to your script; `load_pas_config` returns the real token values.
- **Rotation**: The system tracks `created_at` timestamps for secrets and warns users if they are older than the rotation period (default: 30 days).

### 9. Security
- **NEVER** commit secrets.
- **Keychain Support**: On macOS, PAS uses the system Keychain for sensitive tokens.
- **Fallback**: On non-macOS systems, secrets are stored in plain text in the `~/.pas/` directory. Developers should be aware of this when designing multi-platform tools.
- **Sensitive Keys**: If you add a new type of token, ensure its key name is added to `SENSITIVE_KEYS` in `helpers/core.py`.

## Common Tasks

### Registering Changes
If you add a new script or rename one, run:
```bash
make setup
```
or just:
```bash
make refresh-bin
```

### Upgrading PAS
To update your local installation of PAS with the latest changes from the repository:
```bash
pas upgrade
```
This will pull the latest code and re-run the setup process.

### GitHub Actions Setup
To configure a repository for automated deployment:
```bash
gh-actions-secrets-vars-setup
```
This tool sets up the necessary GitHub Secrets and Variables for your deployment pipelines and ensures your remote server is ready for deployment.

### Listing Tools
To see all available tools in your PAS installation:
```bash
pas list
```

### Git Key Management
When working on different projects that require different SSH keys, use the `git-use-key` tool provided in this repo:
```bash
git-use-key
```
This sets the `core.sshCommand` for the current repository, allowing you to use a specific key without changing global settings.

## Python Environment
This project typically uses Python 3. Standard libraries are preferred to keep dependencies low. If you must add a dependency, ensure it is common and well-documented, and add it to the `requirements.txt` file at the project root. The `make setup` and `pas upgrade` commands will automatically install these dependencies.

