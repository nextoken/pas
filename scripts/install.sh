#!/bin/bash

# PAS (Process Automation Setups) Automated Installer
# Clones PAS to ~/.pas-toolkit and runs setup

set -e

PAS_DIR="$HOME/.pas-toolkit"
REPO_URL="https://github.com/nextoken/pas.git"
HOMEBREW_INSTALL_URL="https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
# Per-user Homebrew prefix (no sudo); official installer respects HOMEBREW_PREFIX
USER_HOMEBREW_PREFIX="${HOME}/.local"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Starting PAS installation...${NC}"

# 0. Optional: Auto-install Homebrew if missing (requires Git; official method)
if ! command -v brew >/dev/null 2>&1; then
    if command -v git >/dev/null 2>&1; then
        echo -e "Homebrew not found. Installing via official method..."
        # Default: per-user (no sudo). Option: system-wide for users who know they want it.
        HOMEBREW_SYSTEM=""
        if [ -t 0 ]; then
            echo -e "  [U]ser only (default, no sudo) — install to ${USER_HOMEBREW_PREFIX}"
            echo -e "  [s]ystem-wide — install to /opt/homebrew or /usr/local (may prompt for password)"
            read -r -p "Install for this user or system? [U/s] " choice
            case "${choice}" in
                s|S|system) HOMEBREW_SYSTEM=1 ;;
                *) HOMEBREW_SYSTEM="" ;;
            esac
        fi
        if [ -z "$HOMEBREW_SYSTEM" ]; then
            echo -e "Installing Homebrew for this user to ${USER_HOMEBREW_PREFIX}..."
            HOMEBREW_PREFIX="$USER_HOMEBREW_PREFIX" NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL "$HOMEBREW_INSTALL_URL")"
            eval "$("$USER_HOMEBREW_PREFIX/bin/brew" shellenv)"
        else
            echo -e "Installing Homebrew system-wide..."
            NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL "$HOMEBREW_INSTALL_URL")"
            if [ -x /opt/homebrew/bin/brew ]; then
                eval "$(/opt/homebrew/bin/brew shellenv)"
            elif [ -x /usr/local/bin/brew ]; then
                eval "$(/usr/local/bin/brew shellenv)"
            fi
        fi
        echo -e "${GREEN}Homebrew installed.${NC}"
    fi
fi

# 1. Dependency Check
echo -e "Checking dependencies..."
if ! command -v git >/dev/null 2>&1; then
    echo -e "${RED}Error: git is not installed. Install Git first (e.g. macOS: xcode-select --install).${NC}"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo -e "${RED}Error: python3 is not installed.${NC}"
    exit 1
fi

# Check for pip or python3 -m pip
if ! command -v pip >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then
    echo -e "${RED}Error: pip is not installed. Please install python3-pip.${NC}"
    exit 1
fi
echo -e "${GREEN}Dependencies OK.${NC}"

# 2. Cloning/Updating
if [ -d "$PAS_DIR" ]; then
    echo -e "Updating PAS in $PAS_DIR..."
    cd "$PAS_DIR"
    git pull
else
    echo -e "Cloning PAS to $PAS_DIR..."
    git clone "$REPO_URL" "$PAS_DIR"
    cd "$PAS_DIR"
fi

# 3. Setup Execution
echo -e "Running setup..."
if command -v make >/dev/null 2>&1; then
    make setup
else
    echo -e "make not found, running setup scripts directly..."
    # Fallback manual setup
    ln -sfn "$PAS_DIR/bin" "$HOME/bin_pas"
    chmod +x scripts/refresh-bin
    chmod +x scripts/setup-path
    ./scripts/refresh-bin
    ./scripts/setup-path
fi

echo -e "\n${GREEN}PAS installation/update complete!${NC}"
echo -e "If you didn't choose to refresh your shell during setup, please restart your terminal or run:"
echo -e "source ~/.zshrc (or your appropriate shell config)"

