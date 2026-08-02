#!/bin/bash
# Setup host symlinks for git config
# Runs at container startup (supervisord priority=750)

set -e

# Symlink .gitconfig for git delegation (allows Manager to commit with correct author)
gitconfig_host="/host-share/.gitconfig"
gitconfig_container="/root/.gitconfig"
if [ -f "$gitconfig_host" ]; then
    if [ -L "$gitconfig_container" ]; then
        echo "[setup-host-symlinks] ~/.gitconfig already symlinked, skipping"
    else
        rm -f "$gitconfig_container" 2>/dev/null || true
        ln -sf "$gitconfig_host" "$gitconfig_container"
        echo "[setup-host-symlinks] Linked ~/.gitconfig -> /host-share/.gitconfig"
    fi
else
    echo "[setup-host-symlinks] /host-share/.gitconfig not found, skipping"
fi

echo "[setup-host-symlinks] Done"
