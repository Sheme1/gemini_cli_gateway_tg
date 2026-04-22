#!/usr/bin/env bash

set -Eeuo pipefail

SERVICE_NAME="telegram-gateway.service"
SYSTEMD_UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"
DEFAULT_SYSTEM_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
TEMPLATE_PATH="${PROJECT_DIR}/telegram-gateway.service"
ENV_PATH="${PROJECT_DIR}/.env"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"
CURRENT_USER="$(id -un)"
CURRENT_HOME="${HOME}"

TMP_SERVICE="$(mktemp "${TMPDIR:-/tmp}/telegram-gateway.XXXXXX.service")"
trap 'rm -f "${TMP_SERVICE}"' EXIT

print_header() {
    echo "================================================"
    echo "  Telegram Gateway for Gemini CLI - systemd setup"
    echo "================================================"
    echo
}

fail() {
    echo "ERROR: $1" >&2
    echo >&2
    echo "Troubleshooting:" >&2
    echo "  sudo systemctl status telegram-gateway --no-pager -l" >&2
    echo "  sudo journalctl -u telegram-gateway -n 200 --no-pager" >&2
    echo "  cd '${PROJECT_DIR}' && ${VENV_PYTHON} -m gateway.main --check-runtime" >&2
    exit 1
}

escape_sed_replacement() {
    printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'
}

confirm() {
    local prompt="$1"
    read -r -p "${prompt} [y/N]: " reply
    [[ "${reply}" =~ ^[Yy]$ ]]
}

require_command() {
    local cmd="$1"
    local hint="$2"
    command -v "${cmd}" >/dev/null 2>&1 || fail "${hint}"
}

print_header

[[ "$(uname -s)" == "Linux" ]] || fail "install.sh supports only Linux with systemd."
[[ -d /run/systemd/system ]] || fail "systemd does not appear to be PID 1 on this host."

require_command systemctl "systemctl was not found. Install and use systemd on the target host."
require_command sudo "sudo is required because the script installs a system service."
require_command sed "sed is required to render the systemd template."
require_command install "install is required to copy the systemd unit."

if [[ "${EUID}" -eq 0 ]]; then
    fail "Run install.sh as the deployment user, not as root. The service will use the current account."
fi

[[ -f "${TEMPLATE_PATH}" ]] || fail "Service template not found: ${TEMPLATE_PATH}"
[[ -f "${ENV_PATH}" ]] || fail "Missing ${ENV_PATH}. Copy .env.example to .env and configure the bot first."
[[ -x "${VENV_PYTHON}" ]] || fail "Missing ${VENV_PYTHON}. Create .venv and install Python dependencies first."

GEMINI_CMD="$( (grep -E '^GEMINI_BIN=' "${ENV_PATH}" || true) | tail -n1 | cut -d= -f2-)"
GEMINI_CMD="${GEMINI_CMD%\"}"
GEMINI_CMD="${GEMINI_CMD#\"}"
GEMINI_CMD="${GEMINI_CMD%\'}"
GEMINI_CMD="${GEMINI_CMD#\'}"
GEMINI_CMD="${GEMINI_CMD:-gemini}"

require_command "${GEMINI_CMD}" "${GEMINI_CMD} was not found in PATH. Install @google/gemini-cli for this user first."
require_command node "node was not found in PATH. Gemini CLI requires Node.js to run."

GEMINI_BIN="$(command -v "${GEMINI_CMD}")"
NODE_BIN="$(command -v node)"
GEMINI_DIR="$(cd -- "$(dirname -- "${GEMINI_BIN}")" && pwd)"
NODE_DIR="$(cd -- "$(dirname -- "${NODE_BIN}")" && pwd)"
SERVICE_PATH="${GEMINI_DIR}:${NODE_DIR}:${DEFAULT_SYSTEM_PATH}"

echo "Detected deployment settings:"
echo "  User:              ${CURRENT_USER}"
echo "  Project directory: ${PROJECT_DIR}"
echo "  Env file:          ${ENV_PATH}"
echo "  Python:            ${VENV_PYTHON}"
echo "  Gemini CLI:        ${GEMINI_BIN}"
echo "  Node.js:           ${NODE_BIN}"
echo "  Unit file:         ${SYSTEMD_UNIT_PATH}"
echo

sed \
    -e "s|__SERVICE_USER__|$(escape_sed_replacement "${CURRENT_USER}")|g" \
    -e "s|__PROJECT_DIR__|$(escape_sed_replacement "${PROJECT_DIR}")|g" \
    -e "s|__ENV_FILE__|$(escape_sed_replacement "${ENV_PATH}")|g" \
    -e "s|__HOME_DIR__|$(escape_sed_replacement "${CURRENT_HOME}")|g" \
    -e "s|__SERVICE_PATH__|$(escape_sed_replacement "${SERVICE_PATH}")|g" \
    -e "s|__PYTHON_BIN__|$(escape_sed_replacement "${VENV_PYTHON}")|g" \
    "${TEMPLATE_PATH}" > "${TMP_SERVICE}"

if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "${TMP_SERVICE}" >/dev/null
fi

echo "Running runtime smoke check..."
if ! (cd "${PROJECT_DIR}" && "${VENV_PYTHON}" -m gateway.main --check-runtime); then
    fail "Runtime smoke check failed. Fix the diagnostics above and rerun install.sh."
fi
echo

echo "Rendered systemd unit:"
echo "----------------------------------------"
cat "${TMP_SERVICE}"
echo "----------------------------------------"
echo

confirm "Install or update ${SERVICE_NAME}?" || {
    echo "Cancelled."
    exit 0
}

sudo install -m 0644 "${TMP_SERVICE}" "${SYSTEMD_UNIT_PATH}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}" >/dev/null

if sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
    sudo systemctl restart "${SERVICE_NAME}"
else
    sudo systemctl start "${SERVICE_NAME}"
fi

echo
echo "Service installed successfully."
echo
sudo systemctl status "${SERVICE_NAME}" --no-pager -l
echo
echo "Useful commands:"
echo "  sudo systemctl restart telegram-gateway"
echo "  sudo systemctl stop telegram-gateway"
echo "  sudo systemctl status telegram-gateway"
echo "  sudo journalctl -u telegram-gateway -f"
