#!/usr/bin/env bash
# ==============================================================================
# Quant Arena - PostgreSQL Backup Script
#
# ARCHITECTURAL NOTE:
# In Quant Arena, PostgreSQL is strictly a derived read model (see ARCHITECTURE.md
# and Open Issue 004). The source of truth for the exchange state is the Redis
# append-only event stream. Relational tables (accounts, positions, open_orders,
# house_fees) are projected from outbound stream events by the ledger service.
# If PostgreSQL is lost or reset, the state can be completely reconstructed by
# replaying the retained stream.
#
# Consequently, this backup script provides an operational convenience for fast
# local restores, testing, and disaster recovery baselines — it is NOT a primary
# durability requirement for the exchange core.
# ==============================================================================
set -euo pipefail

# ------------------------------------------------------------------------------
# Default Settings and Environment Variable Overrides
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODE="docker"                                     # 'docker' (default) or 'direct'
BACKUP_DIR="${QA_BACKUP_DIR:-./backups}"          # Configurable backup folder
BACKUP_KEEP="${QA_BACKUP_KEEP:-7}"                # Number of backups to retain
DB_USER="${QA_POSTGRES_USER:-quant}"              # PostgreSQL user
DB_PASSWORD="${QA_POSTGRES_PASSWORD:-quant}"      # PostgreSQL password
DB_NAME="${QA_POSTGRES_DB:-quant_arena}"          # PostgreSQL database name
DB_HOST="${QA_POSTGRES_HOST:-localhost}"          # Database host for direct mode
DB_PORT="${QA_POSTGRES_PORT:-5432}"               # Database port for direct mode

COMPOSE_SERVICE="postgres"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"

# ------------------------------------------------------------------------------
# Help and Usage
# ------------------------------------------------------------------------------
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

PostgreSQL backup script for Quant Arena.

Modes:
  docker (default)   Execute pg_dump inside the 'postgres' Docker Compose container.
  direct             Execute host pg_dump connecting directly to the database.

Options:
  -m, --mode MODE        Backup mode: 'docker' (default) or 'direct'
  -d, --dir DIR          Output directory for backups (default: ./backups or \$QA_BACKUP_DIR)
  -k, --keep NUM         Number of backups to retain (default: 7 or \$QA_BACKUP_KEEP; 0 to retain all)
  -H, --host HOST        Database host for direct mode (default: localhost or \$QA_POSTGRES_HOST)
  -p, --port PORT        Database port for direct mode (default: 5432 or \$QA_POSTGRES_PORT)
  -U, --user USER        Database user (default: quant or \$QA_POSTGRES_USER)
  -D, --dbname DBNAME    Database name (default: quant_arena or \$QA_POSTGRES_DB)
  -h, --help             Show this help message and exit

Environment Variables:
  QA_POSTGRES_PASSWORD   PostgreSQL password (default: quant)
  QA_BACKUP_DIR          Default backup directory (default: ./backups)
  QA_BACKUP_KEEP         Default backup retention count (default: 7)
  QA_POSTGRES_USER       Default database user (default: quant)
  QA_POSTGRES_DB         Default database name (default: quant_arena)
  QA_POSTGRES_HOST       Default database host for direct mode (default: localhost)
  QA_POSTGRES_PORT       Default database port for direct mode (default: 5432)

Architectural Note:
  PostgreSQL is a derived read model rebuildable from the Redis event stream.
  Backups are an operational convenience, not a primary durability requirement.
EOF
}

# ------------------------------------------------------------------------------
# Parse Command-Line Arguments
# ------------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--mode)
            MODE="${2:-}"
            shift 2
            ;;
        -d|--dir)
            BACKUP_DIR="${2:-}"
            shift 2
            ;;
        -k|--keep)
            BACKUP_KEEP="${2:-}"
            shift 2
            ;;
        -H|--host)
            DB_HOST="${2:-}"
            shift 2
            ;;
        -p|--port)
            DB_PORT="${2:-}"
            shift 2
            ;;
        -U|--user)
            DB_USER="${2:-}"
            shift 2
            ;;
        -D|--dbname)
            DB_NAME="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: Unknown option '$1'" >&2
            echo "Use -h or --help for usage information." >&2
            exit 1
            ;;
    esac
done

# ------------------------------------------------------------------------------
# Argument Validation
# ------------------------------------------------------------------------------
if [[ "${MODE}" != "docker" && "${MODE}" != "direct" ]]; then
    echo "Error: Invalid mode '${MODE}'. Supported modes are 'docker' or 'direct'." >&2
    exit 1
fi

if ! [[ "${BACKUP_KEEP}" =~ ^[0-9]+$ ]]; then
    echo "Error: Retention count must be a non-negative integer, got '${BACKUP_KEEP}'." >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# Pre-Flight Checks
# ------------------------------------------------------------------------------
if ! command -v gzip >/dev/null 2>&1; then
    echo "Error: 'gzip' utility is required but not found in PATH." >&2
    exit 1
fi

COMPOSE_CMD=()
if [[ "${MODE}" == "docker" ]]; then
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD=(docker compose)
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CMD=(docker-compose)
    else
        echo "Error: Neither 'docker compose' nor 'docker-compose' was found in PATH." >&2
        echo "Please install Docker Compose or switch to direct mode (-m direct)." >&2
        exit 1
    fi

    if [[ ! -f "${COMPOSE_FILE}" ]]; then
        echo "Error: Docker compose file not found at: ${COMPOSE_FILE}" >&2
        exit 1
    fi

    # Verify that the PostgreSQL service container is running and responding
    if ! "${COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" exec -T "${COMPOSE_SERVICE}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
        echo "Error: PostgreSQL service '${COMPOSE_SERVICE}' is not running or not ready." >&2
        echo "Start it with: ${COMPOSE_CMD[*]} -f \"${COMPOSE_FILE}\" up -d ${COMPOSE_SERVICE}" >&2
        exit 1
    fi
else
    # Direct mode checks
    if ! command -v pg_dump >/dev/null 2>&1; then
        echo "Error: 'pg_dump' utility not found in PATH for direct mode." >&2
        echo "Please install PostgreSQL client tools or switch to Docker Compose mode (-m docker)." >&2
        exit 1
    fi

    if command -v pg_isready >/dev/null 2>&1; then
        if ! pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
            echo "Error: PostgreSQL server at ${DB_HOST}:${DB_PORT} is not ready or not accepting connections." >&2
            exit 1
        fi
    fi
fi

# ------------------------------------------------------------------------------
# Prepare Paths and Traps
# ------------------------------------------------------------------------------
mkdir -p "${BACKUP_DIR}"
BACKUP_DIR_ABS="$(cd "${BACKUP_DIR}" && pwd)"

TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
BACKUP_FILENAME="quant_arena_${TIMESTAMP}.sql.gz"
BACKUP_PATH="${BACKUP_DIR_ABS}/${BACKUP_FILENAME}"
TEMP_BACKUP_PATH="${BACKUP_PATH}.tmp.$$"

# Clean up temporary file on failure or interruption
cleanup() {
    local exit_code=$?
    if [[ -f "${TEMP_BACKUP_PATH}" ]]; then
        rm -f "${TEMP_BACKUP_PATH}"
    fi
    exit "${exit_code}"
}
trap cleanup EXIT INT TERM

# ------------------------------------------------------------------------------
# Execute Backup
# ------------------------------------------------------------------------------
echo "Starting PostgreSQL backup for '${DB_NAME}' (mode: ${MODE})..."

if [[ "${MODE}" == "docker" ]]; then
    "${COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" exec -T \
        -e PGPASSWORD="${DB_PASSWORD}" \
        "${COMPOSE_SERVICE}" \
        pg_dump -U "${DB_USER}" -d "${DB_NAME}" --clean --if-exists --no-owner \
        | gzip -c > "${TEMP_BACKUP_PATH}"
else
    PGPASSWORD="${DB_PASSWORD}" pg_dump \
        -h "${DB_HOST}" \
        -p "${DB_PORT}" \
        -U "${DB_USER}" \
        -d "${DB_NAME}" \
        --clean --if-exists --no-owner \
        | gzip -c > "${TEMP_BACKUP_PATH}"
fi

# Ensure output file is non-empty
if [[ ! -s "${TEMP_BACKUP_PATH}" ]]; then
    echo "Error: Backup completed but produced an empty file." >&2
    exit 1
fi

# Atomically move temp file to target backup file
mv "${TEMP_BACKUP_PATH}" "${BACKUP_PATH}"
trap - EXIT INT TERM

# ------------------------------------------------------------------------------
# Retention Management
# ------------------------------------------------------------------------------
deleted_count=0
if [[ "${BACKUP_KEEP}" -gt 0 ]]; then
    shopt -s nullglob
    backups=( "${BACKUP_DIR_ABS}"/quant_arena_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9].sql.gz )
    shopt -u nullglob

    total_backups=${#backups[@]}
    if (( total_backups > BACKUP_KEEP )); then
        num_to_delete=$(( total_backups - BACKUP_KEEP ))
        for (( i=0; i<num_to_delete; i++ )); do
            rm -f "${backups[i]}"
            deleted_count=$(( deleted_count + 1 ))
        done
    fi
fi

# ------------------------------------------------------------------------------
# Summary Output
# ------------------------------------------------------------------------------
FILE_SIZE="$(ls -lh "${BACKUP_PATH}" | awk '{print $5}')"

shopt -s nullglob
remaining_backups=( "${BACKUP_DIR_ABS}"/quant_arena_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9].sql.gz )
shopt -u nullglob
remaining_count=${#remaining_backups[@]}

echo ""
echo "=============================================================================="
echo "Quant Arena - PostgreSQL Backup Summary"
echo "=============================================================================="
echo "Status:          SUCCESS"
echo "Timestamp:       $(date +"%Y-%m-%d %H:%M:%S")"
echo "Mode:            ${MODE}"
if [[ "${MODE}" == "docker" ]]; then
    echo "Compose Service: ${COMPOSE_SERVICE}"
else
    echo "Host/Port:       ${DB_HOST}:${DB_PORT}"
fi
echo "Database:        ${DB_NAME}"
echo "User:            ${DB_USER}"
echo "Backup File:     ${BACKUP_PATH}"
echo "Backup Size:     ${FILE_SIZE}"
if [[ "${BACKUP_KEEP}" -gt 0 ]]; then
    echo "Retention Policy: Keep ${BACKUP_KEEP} latest (deleted: ${deleted_count})"
else
    echo "Retention Policy: Retain all (rotation disabled)"
fi
echo "Total Backups:   ${remaining_count} backup(s) in ${BACKUP_DIR_ABS}"
echo "=============================================================================="
