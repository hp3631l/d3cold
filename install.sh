#!/usr/bin/env bash
# d3cold standalone user installer

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
SHARE_DIR="${HOME}/.local/share/d3cold"

echo "=== Installing d3cold & power-profile-switch ==="

mkdir -p "${BIN_DIR}" "${SHARE_DIR}"

# Install python package to ~/.local/share/d3cold
cp -r "${SCRIPT_DIR}/dgpu_doctor" "${SHARE_DIR}/"

# Maintain backwards compatibility for ~/.local/share/dgpu-doctor
if [ "${HOME}/.local/share/dgpu-doctor" != "${SHARE_DIR}" ]; then
    rm -rf "${HOME}/.local/share/dgpu-doctor"
    ln -sf "${SHARE_DIR}" "${HOME}/.local/share/dgpu-doctor"
fi

# Install launcher script for d3cold
cat << 'EOF' > "${BIN_DIR}/d3cold"
#!/usr/bin/env bash
if [ -d "${HOME}/.local/share/d3cold" ]; then
    PYTHONPATH="${HOME}/.local/share/d3cold" exec python3 -m dgpu_doctor.cli "$@"
elif [ -d "${HOME}/.local/share/dgpu-doctor" ]; then
    PYTHONPATH="${HOME}/.local/share/dgpu-doctor" exec python3 -m dgpu_doctor.cli "$@"
else
    exec python3 -m dgpu_doctor.cli "$@"
fi
EOF
chmod +x "${BIN_DIR}/d3cold"

# Maintain backwards compatibility for dgpu-doctor command
ln -sf "${BIN_DIR}/d3cold" "${BIN_DIR}/dgpu-doctor"

# Install prime-run
if [ -f "${SCRIPT_DIR}/bin/prime-run" ]; then
    cp "${SCRIPT_DIR}/bin/prime-run" "${BIN_DIR}/prime-run"
    chmod +x "${BIN_DIR}/prime-run"
fi

# Install power-profile-switch if present
if [ -f "${SCRIPT_DIR}/bin/power-profile-switch" ]; then
    cp "${SCRIPT_DIR}/bin/power-profile-switch" "${BIN_DIR}/power-profile-switch"
    chmod +x "${BIN_DIR}/power-profile-switch"
    echo "✓ Installed power-profile-switch to ${BIN_DIR}/power-profile-switch"
fi

# Install systemd auto-watcher service if present
USER_SYSTEMD_DIR="${HOME}/.config/systemd/user"
if [ -f "${SCRIPT_DIR}/systemd/power-profile-watcher.service" ]; then
    mkdir -p "${USER_SYSTEMD_DIR}"
    cp "${SCRIPT_DIR}/systemd/power-profile-watcher.service" "${USER_SYSTEMD_DIR}/power-profile-watcher.service"
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now power-profile-watcher.service 2>/dev/null || true
    echo "✓ Enabled power-profile-watcher.service for automatic AC/Battery switching"
fi

echo "✓ Installed d3cold to ${BIN_DIR}/d3cold (alias: dgpu-doctor)"
echo "✓ Installed prime-run to ${BIN_DIR}/prime-run"
echo ""
echo "Make sure ${BIN_DIR} is in your PATH. You can now run:"
echo "  d3cold audit           # Check health of NVIDIA dGPU power sleep"
echo "  d3cold fix             # Automatically remediate issues"
echo "  d3cold watch           # Real-time passive sleep monitor"
echo "  power-profile-switch   # Smart battery/AC/gaming profile switcher"
echo ""
