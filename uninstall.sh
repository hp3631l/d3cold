#!/usr/bin/env bash
# dgpu-doctor standalone uninstaller

set -euo pipefail

echo "=== Uninstalling d3cold ==="

rm -f "${HOME}/.local/bin/d3cold"
rm -f "${HOME}/.local/bin/dgpu-doctor"
rm -rf "${HOME}/.local/share/d3cold"
rm -rf "${HOME}/.local/share/dgpu-doctor"

echo "✓ Removed d3cold binaries and libraries."
echo "Note: If you wish to revert /etc/udev/rules.d/80-nvidia-pm.rules or ~/.config/environment.d/10-hyprland-gpu.conf, you can delete them manually."
