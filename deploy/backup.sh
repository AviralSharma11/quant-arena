#!/usr/bin/env bash
# Nightly backup of the public deployment, run from cron on the VM (RUNBOOK.md §9):
#
#     deploy/backup.sh            # writes into ~/backups, keeps 7 of each
#
# Both stores, because neither alone restores the exchange. PostgreSQL holds `users` — logins and
# the id sequence that numbers every account — which exist nowhere else. Redis holds the order
# log and every process's checkpoint (Open Issue 020); since the stream is trimmed below the
# checkpoints, those checkpoints are the only record of balances older than a minute.
#
# These land on the VM's own disk. Copy them off it (RUNBOOK.md §9): if the student credit lapses,
# Azure disables the VM, and a backup on a disabled disk is not a backup.
set -euo pipefail

cd "$(dirname "$0")/.."
dir="${QA_BACKUP_DIR:-$HOME/backups}"
keep="${QA_BACKUP_KEEP:-7}"
stamp="$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "$dir"

QA_BACKUP_DIR="$dir" QA_BACKUP_KEEP="$keep" scripts/backup_postgres.sh

# A point-in-time RDB alongside the live AOF: one self-contained file, cheap to copy off the VM.
before="$(docker compose exec -T redis redis-cli LASTSAVE)"
docker compose exec -T redis redis-cli BGSAVE >/dev/null
until [ "$(docker compose exec -T redis redis-cli LASTSAVE)" != "$before" ]; do sleep 1; done
docker compose cp redis:/data/dump.rdb "$dir/redis_${stamp}.rdb"
gzip -f "$dir/redis_${stamp}.rdb"

if [ "$keep" -gt 0 ]; then
    ls -1t "$dir"/redis_*.rdb.gz | tail -n +"$((keep + 1))" | xargs -r rm --
fi
echo "backed up to $dir"
