#!/usr/bin/env bash
# dgpu-doctor standalone user installer

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"

echo "=== Installing dgpu-doctor & power-profile-switch ==="

mkdir -p "${BIN_DIR}"

# Install launcher script
cat << 'EOF' > "${BIN_DIR}/dgpu-doctor"
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../share/dgpu-doctor" 2>/dev/null || cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${HOME}/.local/share/dgpu-doctor" ]; then
    PYTHONPATH="${HOME}/.local/share/dgpu-doctor" exec python3 -m dgpu_doctor.cli "$@"
else
    exec python3 -m dgpu_doctor.cli "$@"
fi
EOF
chmod +x "${BIN_DIR}/dgpu-doctor"

# Copy python package to ~/.local/share/dgpu-doctor
SHARE_DIR="${HOME}/.local/share/dgpu-doctor"
mkdir -p "${SHARE_DIR}"
cp -r "${SCRIPT_DIR}/dgpu_doctor" "${SHARE_DIR}/"

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

echo "✓ Installed dgpu-doctor to ${BIN_DIR}/dgpu-doctor"
echo "✓ Installed prime-run to ${BIN_DIR}/prime-run"
echo ""
echo "Make sure ${BIN_DIR} is in your PATH. You can now run:"
echo "  dgpu-doctor audit           # Check health of NVIDIA dGPU power sleep"
echo "  dgpu-doctor fix             # Automatically remediate issues"
echo "  dgpu-doctor watch           # Real-time passive sleep monitor"
echo "  power-profile-switch        # Smart battery/AC/gaming profile switcher"
echo ""
