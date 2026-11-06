#!/usr/bin/env bash
# After the Tuesday deadline: fail unless this week's round is in the hub, or sits in an open PR
# that passed validation (the hub merges by hand). Needs GH_TOKEN.
set -euo pipefail

UPSTREAM=InsightNet-US/BDBV-Modeling-Hub
MODEL=pedro-zonemix
source "$(dirname "$0")/checks.sh"

REF=${REF:-$(TZ=Africa/Kinshasa date -d "+$(( (6 - $(TZ=Africa/Kinshasa date +%u) + 7) % 7 )) days" +%F)}
FILE="model-output/$MODEL/$REF-$MODEL.csv"

if in_hub "$FILE"; then echo "[check] round $REF is in the hub"; exit 0; fi
pr=$(gh pr list --repo "$UPSTREAM" --head "$MODEL-$REF" --state open --json number --jq '.[0].number')
[[ -n "$pr" ]] || { echo "[check] round $REF: no file in the hub and no open PR" >&2; exit 1; }
state=$(pr_state "$pr")
if [[ "$state" != pass ]]; then
  echo "[check] round $REF: PR #$pr validation is '$state': https://github.com/$UPSTREAM/pull/$pr" >&2; exit 1
fi
echo "[check] round $REF: PR #$pr passed validation, waiting for the hub to merge"
