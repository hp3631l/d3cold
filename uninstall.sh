#!/usr/bin/env bash
# dgpu-doctor standalone uninstaller

set -euo pipefail

echo "=== Uninstalling dgpu-doctor ==="

rm -f "${HOME}/.local/bin/dgpu-doctor"
rm -rf "${HOME}/.local/share/dgpu-doctor"

echo "✓ Removed dgpu-doctor binaries and libraries."
echo "Note: If you wish to revert /etc/udev/rules.d/80-nvidia-pm.rules or ~/.config/environment.d/10-hyprland-gpu.conf, you can delete them manually."
