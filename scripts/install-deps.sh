#!/usr/bin/env bash
# Install Python deps for PAS (pas-core + requirements + ppui).
# Called by Makefile install-deps and by install.sh when make is unavailable.
# PIP_ARGS: if unset, defaults to --break-system-packages (Homebrew / PEP 668).
#          if set to empty (e.g. make install-deps PIP_ARGS=), no extra pip flags.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

declare -a EXTRA=()
if [ -z "${PIP_ARGS+x}" ]; then
	EXTRA=(--break-system-packages)
elif [ -n "${PIP_ARGS}" ]; then
	# shellcheck disable=SC2206
	EXTRA=(${PIP_ARGS})
fi

echo "Ensuring pip is up to date (needed for editable pyproject.toml installs)..."
python3 -m pip install -q --upgrade pip "${EXTRA[@]}" || true

echo "Installing pas-core (PyYAML, rich, pydantic, keyring) in editable mode..."
python3 -m pip install -q -e ./libs/pas-core "${EXTRA[@]}"

if [ -f requirements.txt ]; then
	echo "Installing Python dependencies from requirements.txt..."
	python3 -m pip install -q -r requirements.txt "${EXTRA[@]}"
fi

if [ -d libs/ppui ]; then
	echo "Installing internal ppui library in editable mode..."
	python3 -m pip install -q -e ./libs/ppui "${EXTRA[@]}"
fi
