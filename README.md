# PAS (Process Automation Setups/Scripts)

> [!WARNING]
> **USE AT YOUR OWN RISK**: This toolkit is provided "as is" without warranty of any kind. The authors are not responsible for any damage, data loss, or system instability caused by using these scripts. Always review scripts before running them, especially those that modify system configurations or manage security credentials.

> [!WARNING]
> **macOS CENTRIC**: This toolkit is primarily designed for and tested on **macOS**. While some scripts may work on Linux or other Unix-like systems, core features like Keychain integration, Screen Sharing (VNC) automation, and certain system configurations are macOS-specific.

> [!WARNING]
> **BREAKING API**: The toolkit evolves quickly as usage patterns change. Commands, flags, and script behavior may change or be removed without notice. Prefer documented usage and avoid relying on undocumented internals in automation.

A collection of automation and semi-automation scripts designed to streamline development and project workflows.

## Overview

This repository contains various scripts and tools used to automate common tasks, from video processing to system configuration. It is built to be a modular toolkit for **developers and power users** with a sufficient technical background.

## The "Living Playbook" Philosophy

The scripts in the `services/` directory are designed to be **living interactive playbooks**. Their primary goal is to significantly reduce the cognitive load of common operations that fall into the "middle ground" of automation.

### AI-First Development & Spec-Driven Usage

Most scripts in this toolkit are **authored by AI** based on high-level specifications. This shifts the focus from manual implementation to **spec-driven automation**:

-   **AI-Authored**: The logic and edge cases are handled by LLMs following the patterns defined in `helpers/core.py` and `dev-guide.md`.
-   **Spec-Driven**: Treat the scripts as executable specifications of a process. Instead of focusing on *how* the code is written, focus on the *intent* and the *outcome* it achieves.
-   **Verified by Human-in-the-Loop**: While AI writes the code, the interactive nature of the scripts ensures that a human operator remains in control, verifying critical steps and providing necessary context.

### Core Principles

1.  **Semi-Automated Operations**: These scripts handle setups and configurations that require a mix of automated API calls and manual user decisions/verifications.
2.  **Guided Discovery**: Unlike fully automated tools (like Ansible or Terraform) that assume you already have all IDs and Tokens ready, PAS helps you find them. Scripts provide direct URLs and interactive hints to help you collect the necessary data from service provider dashboards during execution.
3.  **Tedious but Well-Defined**: They target workflows with multiple steps that are easy to miss or annoying to perform manually (e.g., DNS record management, SSH key deployment, repository initialization).
3.  **Efficiency over AI**: While AI can help, it's overkill to prompt an LLM for every routine operation. These scripts provide a faster, reliable alternative to reading manuals or digging through UI menus.
4.  **Local Data Privacy**: PAS keeps sensitive data (like database passwords and API keys) on your local machine. This prevents accidental leakage to Large Language Models (LLMs) that can occur when pasting logs or configurations into AI prompts for troubleshooting or guidance.
5.  **Composability & Full Automation**: While the primary entry point for many tools is an interactive PUI (Process User Intent) session, the underlying tasks and processes are built as modular, composable elements. This allows the toolkit to transition seamlessly from guided, human-in-the-loop setup to fully automated execution when all intents are pre-defined or passed via CLI arguments.

## What PAS Toolkit Is *Not*

- **Not a general-purpose infra orchestrator (like Ansible/Terraform)**: PAS does not try to be a declarative configuration management system. It focuses on guided, semi-automated workflows and human-in-the-loop setup rather than fully unattended, fleet-wide enforcement of desired state.
- **Not a CI/CD workflow engine (like GitHub Actions)**: There are no runners, jobs, or event triggers here. PAS tools are meant to be invoked locally by an operator in a terminal session (often for “first-mile” setup), not as long-lived pipelines tied to repository events.
- **Not a hosted automation platform (like n8n)**: PAS does not provide a server, visual node editor, or always-on webhook/scheduler model. It assumes a local environment (e.g., macOS), tight integration with local CLIs and Keychain, and rich terminal UX instead of browser-based workflow editing.
- **Not a replacement for general AI agents**: PAS integrates with LLMs (`pas ask`) to help discover tools and explain flows, but the core value is in **opinionated, repeatable scripts** that are faster and more predictable than ad-hoc prompting for routine operations.

## Prerequisites

Before installing PAS, ensure you have the following installed:
- **Git** — On macOS, Git is not included by default; install **Xcode Command Line Tools** if needed (`xcode-select --install`, or run `git` once to get the install prompt).
- **Python 11** (required). Python 12 is not yet supported due to PyTorch dependency compatibility.

The installer can install **Homebrew** for you if it is missing (per-user by default, with an option for system-wide). Homebrew is useful for installing Git (if you prefer it over the Xcode tools) and **pyenv** for Python 11.

We recommend **pyenv** and a **personal, isolated** Python (e.g. `pyenv install 3.11.x` and a dedicated virtualenv or pyenv shell). This keeps PAS and its dependencies separate from the system Python and prepares your environment for **OpenClaw** and other tools that expect a controlled runtime.

## Installation

The easiest way to install PAS is via the automated installer. This will clone the repository to `~/.pas-toolkit` and set up your environment.

PAS uses a **git-based install**: a single-command installer that clones the repository to a dedicated directory (`~/.pas-toolkit`), sets up symlinks, and provides a simple upgrade command (`pas upgrade` or `pas up`) to pull the latest changes.

### Using curl
```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/nextoken/pas/main/scripts/install.sh)"
```

### Using wget
```bash
sh -c "$(wget -qO- https://raw.githubusercontent.com/nextoken/pas/main/scripts/install.sh)"
```

## Usage

The `pas` command is the central entry point for the toolkit.

- **List Tools**: `pas list` - See all available automation tools and their descriptions.
- **AI Help**: `pas ask` - Ask the AI assistant for help or to find the right tool for a task. You will be prompted to set up an LLM API (e.g. API key) when first used.
- **Upgrade**: `pas upgrade` (alias: `pas up`) - Update the toolkit to the latest version and refresh symlinks.
- **View Repo**: `pas repo` - Open the official PAS GitHub repository.

## Updating PAS

Once installed, you can keep PAS up to date by running:
```bash
pas upgrade # or pas up
```
This command will pull the latest changes from the repository and re-run the registration process for all tools. It is a simple, single-command upgrade that keeps your installation current (we use `upgrade` following the Homebrew convention).

## Configuration

PAS uses a dedicated directory for your personal configurations, API keys, and tool-specific profiles:
- **Location**: `~/.pas/`
- **Behavior**: This directory is automatically created the first time any PAS tool needs to save or load settings. Tool-specific profiles (e.g. per-tool configs and state) are kept under `~/.pas/` as well.
- **Schema changes**: When settings or profile schema change, script tools will try best-effort auto migration. Expect breakages occasionally; back up `~/.pas/` if you rely on existing profiles.
- **Security (macOS)**: For users on macOS, sensitive keys (like `token`, `api_key`, `CLOUDFLARE_API_TOKEN`) are automatically moved from plain-text JSON files to the **macOS Keychain**. The JSON files will only store references to these secrets.
- **Portability**: You can easily back up this directory or symlink it to your own personal dotfiles. Note that Keychain secrets stay in your system Keychain.

## Security and Credentials

- **NEVER** commit or include credentials, API keys, or sensitive information in this repository.
- **macOS Keychain Integration**: PAS automatically secures sensitive tokens in the macOS Keychain. This prevents raw tokens from being exposed in plain text files while still allowing scripts to access them seamlessly.
- **Token Rotation**: PAS tracks the age of your secrets. If a token is older than 30 days, you will receive a warning in your terminal to consider rotating it.
- **Note**: Secure Keychain storage is currently only supported on **macOS**. On other systems, secrets are stored as plain text in `~/.pas/`.

## Development Guide

For detailed information on how to create, register, and maintain scripts in this repository, please refer to the [Development Guide](dev-guide.md).

- **`ppui`**: Python Process User Intent - high-level abstract UI system for menus and prompts.

## Project Structure

- `bin/`: Generated symlinks to executable scripts.
- `libs/`: Internal libraries including `ppui` (PUI implementation).
- `media/`: Scripts related to media processing (e.g., `vcut`).
- `services/`: System-level setup and service configuration scripts.
- `Makefile`: Project management commands.

## Troubleshooting

### Manual Setup
If the automated installer fails or you prefer to set up PAS manually:

1.  Clone the repository to `~/.pas-toolkit`:
    ```bash
    git clone git@github.com:nextoken/pas.git ~/.pas-toolkit
    cd ~/.pas-toolkit
    ```
2.  Run the setup command:
    ```bash
    make setup
    ```
3.  If `bin_pas` is not in your `PATH`, add the following line to your shell configuration (e.g., `~/.zshrc` or `~/.bashrc`):
    ```bash
    export PATH="$HOME/bin_pas:$PATH"
    ```

### Reinstallation
If you encounter issues or have an incomplete installation, the recommended fix is to re-run the automated installer:
```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/nextoken/pas/main/scripts/install.sh)"
```
This will pull the latest changes and re-run all setup steps.
