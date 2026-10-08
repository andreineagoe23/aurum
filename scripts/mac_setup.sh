#!/usr/bin/env bash
# Aurum macOS setup: uv, Python 3.12, venv, dev deps, hooks, checks.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

step() { printf '\n==> %s\n' "$1"; }

step "Ensure uv is installed"
if ! command -v uv >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        brew install uv
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # shellcheck disable=SC1091
        [ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env"
    fi
fi
uv --version

step "Install Python 3.12"
uv python install 3.12

step "Sync dependencies (.venv, incl. dev group)"
uv sync

step "Install pre-commit hooks"
uv run pre-commit install

step "Check .env"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example. Edit it as needed (MT5_* only matter on the Windows VM)."
else
    echo ".env already exists; leaving it untouched."
fi

step "ruff check"
uv run ruff check

step "ruff format --check"
uv run ruff format --check

step "mypy"
uv run mypy

step "pytest"
uv run pytest

step "aurum doctor"
uv run aurum doctor

echo
echo "Mac setup complete."
