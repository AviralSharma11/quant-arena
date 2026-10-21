#!/usr/bin/env bash
# Redeploy the public stack to one commit (Task 3.3, Success Criterion 3):
#
#     deploy/redeploy.sh <commit-sha>
#
# Run on the VM, from anywhere. It checks the repository out at that commit — so the compose
# files match the images — pulls the images CI published for it, and recreates whatever changed.
# Volumes are untouched, so accounts and the order log survive; every process resumes from its
# checkpoint (Open Issue 020). Secrets come from `.env` in the repository root (RUNBOOK.md).
set -euo pipefail

cd "$(dirname "$0")/.."
target="${1:?usage: deploy/redeploy.sh <commit-sha>}"

git fetch --quiet origin
sha="$(git rev-parse --verify "${target}^{commit}")"
git checkout --quiet --detach "$sha"

# CI tags images with the full SHA. A commit that has not finished publishing fails here, at
# `pull`, before anything running is touched.
export QA_IMAGE_TAG="$sha"
compose=(docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml --profile bots)

"${compose[@]}" pull
"${compose[@]}" up -d --no-build --remove-orphans
"${compose[@]}" ps
echo "deployed ${sha}"
