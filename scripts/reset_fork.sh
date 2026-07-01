#!/usr/bin/env bash
# Reset the fork to a clean slate: delete the pipeline's issues, close Devin's
# PRs, delete its branches, and clear local run state. Destructive - asks first.
#
#   ./scripts/reset_fork.sh
#
# Reads TARGET_REPO / REMEDIATE_LABEL from .env if present.
set -euo pipefail

[ -f .env ] && set -a && . ./.env && set +a
REPO="${TARGET_REPO:-Pintrue/superset}"
LABEL="${REMEDIATE_LABEL:-devin-remediate}"

# Use gh's own login (repo scope, repo owner) rather than the limited pipeline
# PAT that .env just exported - deleting issues / branches needs more than the
# scan token has, so let gh fall back to your authenticated account.
unset GITHUB_TOKEN GH_TOKEN

read -r -p "Delete all '$LABEL' issues and Devin PRs/branches on $REPO? [y/N] " ans
[ "$ans" = "y" ] || { echo "aborted"; exit 1; }

echo "Deleting $LABEL issues ..."
for n in $(gh issue list --repo "$REPO" --label "$LABEL" --state all --json number -q '.[].number'); do
  gh issue delete "$n" --repo "$REPO" --yes
done

echo "Closing open PRs and deleting their branches ..."
for n in $(gh pr list --repo "$REPO" --state open --json number -q '.[].number'); do
  gh pr close "$n" --repo "$REPO" --delete-branch || true
done

echo "Deleting any leftover devin/* branches ..."
for b in $(gh api "repos/$REPO/branches" --paginate -q '.[].name' | grep '^devin/' || true); do
  gh api -X DELETE "repos/$REPO/git/refs/heads/$b" || true
done

echo "Clearing local state ..."
rm -f state.json report.md

echo "Done. Fork reset."
