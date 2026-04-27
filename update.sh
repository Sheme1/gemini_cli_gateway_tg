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

TMP_SERVICE="$(mktemp "${TMPDIR:-/tmp}/telegram-gateway-update.XXXXXX.service")"
trap 'rm -f "${TMP_SERVICE}"' EXIT

print_header() {
    echo "================================================"
    echo "  Telegram Gateway for Gemini CLI - update"
    echo "================================================"
    echo
}

fail() {
    echo "ERROR: $1" >&2
    echo >&2
    echo "Troubleshooting:" >&2
    echo "  sudo systemctl status telegram-gateway --no-pager -l" >&2
    echo "  sudo journalctl -u telegram-gateway -n 200 --no-pager" >&2
    echo "  cd '${PROJECT_DIR}' && ${VENV_PYTHON} -m gateway.main --doctor" >&2
    exit 1
}

require_command() {
    local cmd="$1"
    local hint="$2"
    command -v "${cmd}" >/dev/null 2>&1 || fail "${hint}"
}

escape_sed_replacement() {
    printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'
}

read_env_value() {
    local key="$1"
    local value
    value="$( (grep -E "^${key}=" "${ENV_PATH}" || true) | tail -n1 | cut -d= -f2-)"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    printf '%s' "${value}"
}

render_unit_for_compare() {
    local gemini_cmd gemini_bin node_bin gemini_dir node_dir service_path

    gemini_cmd="$(read_env_value "GEMINI_BIN")"
    gemini_cmd="${gemini_cmd:-gemini}"

    require_command "${gemini_cmd}" "${gemini_cmd} was not found in PATH. Install @google/gemini-cli for this user first."
    require_command node "node was not found in PATH. Gemini CLI requires Node.js to run."

    gemini_bin="$(command -v "${gemini_cmd}")"
    node_bin="$(command -v node)"
    gemini_dir="$(cd -- "$(dirname -- "${gemini_bin}")" && pwd)"
    node_dir="$(cd -- "$(dirname -- "${node_bin}")" && pwd)"
    service_path="${gemini_dir}:${node_dir}:${DEFAULT_SYSTEM_PATH}"

    sed \
        -e "s|__SERVICE_USER__|$(escape_sed_replacement "${CURRENT_USER}")|g" \
        -e "s|__PROJECT_DIR__|$(escape_sed_replacement "${PROJECT_DIR}")|g" \
        -e "s|__ENV_FILE__|$(escape_sed_replacement "${ENV_PATH}")|g" \
        -e "s|__HOME_DIR__|$(escape_sed_replacement "${CURRENT_HOME}")|g" \
        -e "s|__SERVICE_PATH__|$(escape_sed_replacement "${service_path}")|g" \
        -e "s|__PYTHON_BIN__|$(escape_sed_replacement "${VENV_PYTHON}")|g" \
        "${TEMPLATE_PATH}" > "${TMP_SERVICE}"
}

print_header

[[ "$(uname -s)" == "Linux" ]] || fail "update.sh supports only Linux with systemd."
[[ -d /run/systemd/system ]] || fail "systemd does not appear to be PID 1 on this host."

require_command git "git was not found. Install git or update the project manually."
require_command systemctl "systemctl was not found. Install and use systemd on the target host."
require_command sudo "sudo is required to restart the system service."
require_command sed "sed is required to render the systemd template for comparison."

if [[ "${EUID}" -eq 0 ]]; then
    fail "Run update.sh as the deployment user, not as root."
fi

[[ -f "${ENV_PATH}" ]] || fail "Missing ${ENV_PATH}. Copy .env.example to .env and configure the bot first."
[[ -x "${VENV_PYTHON}" ]] || fail "Missing ${VENV_PYTHON}. Create .venv and install Python dependencies first."
[[ -f "${PROJECT_DIR}/requirements.txt" ]] || fail "Missing requirements.txt."
[[ -f "${TEMPLATE_PATH}" ]] || fail "Service template not found: ${TEMPLATE_PATH}"

echo "Updating repository with fast-forward only..."
git -C "${PROJECT_DIR}" pull --ff-only
echo

echo "Installing Python dependencies..."
"${VENV_PYTHON}" -m pip install -r "${PROJECT_DIR}/requirements.txt"
echo

echo "Running local doctor..."
(cd "${PROJECT_DIR}" && "${VENV_PYTHON}" -m gateway.main --doctor)
echo

echo "Checking installed systemd unit..."
render_unit_for_compare
if [[ ! -f "${SYSTEMD_UNIT_PATH}" ]]; then
    echo "WARNING: ${SYSTEMD_UNIT_PATH} is missing. Run ./install.sh before using update.sh for this service."
elif ! cmp -s "${TMP_SERVICE}" "${SYSTEMD_UNIT_PATH}"; then
    echo "WARNING: Installed systemd unit differs from the current rendered template."
    echo "Run ./install.sh to refresh the unit. It will call systemctl daemon-reload."
    echo "For ordinary Python code updates, daemon-reload is not required."
else
    echo "Systemd unit matches the current rendered template."
fi
echo

echo "Restarting ${SERVICE_NAME}..."
sudo systemctl restart "${SERVICE_NAME}"
echo

sudo systemctl status "${SERVICE_NAME}" --no-pager -l
echo
echo "Follow logs:"
echo "  sudo journalctl -u telegram-gateway -f"
