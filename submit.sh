#!/usr/bin/env bash
# One weekly round: forecast the coming Saturday's round and open the submission PR from the fork.
# Safe to run many times: it does nothing once the round is in, or while the hub hasn't opened the
# round or published its data. An open PR is rebuilt if it failed validation, or if it was built
# from an older release and the new one is out. Needs GH_TOKEN (pedro-f20 classic token,
# public_repo scope) and a venv at .venv.
#   REF=YYYY-MM-DD  override the round (a Saturday)
#   DRY_RUN=1       forecast only, no push or PR
#   FINAL=1         a late run: no new release means forecast from the last one; a round the hub
#                   hasn't opened is an error
set -euo pipefail

UPSTREAM=InsightNet-US/BDBV-Modeling-Hub
FORK=pedro-f20/BDBV-Modeling-Hub
MODEL=pedro-zonemix
EXIT_NO_DATA=3
EXIT_NO_ROUND=4
STALE_MARK="older release"
source "$(dirname "$0")/checks.sh"

# Rounds run Sunday-Tuesday in Kinshasa; the round is the coming Saturday (date +%u: Saturday=6)
today=$(TZ=Africa/Kinshasa date +%F)
weekday=$(TZ=Africa/Kinshasa date +%u)
REF=${REF:-$(date -d "$today +$(( (6 - weekday + 7) % 7 )) days" +%F)}
FILE="model-output/$MODEL/$REF-$MODEL.csv"
BRANCH="$MODEL-$REF"
echo "[submit] round $REF (Kinshasa date $today)"

pr=""
pr_failed=""
refresh_only=""
if [[ -z "${DRY_RUN:-}" ]]; then
  [[ -n "${GH_TOKEN:-}" ]] || { echo "[submit] GH_TOKEN is empty: set the RESPICAST_TOKEN secret" >&2; exit 1; }
  if in_hub "$FILE"; then echo "[submit] $FILE is already in the hub, nothing to do"; exit 0; fi
  pr=$(gh pr list --repo "$UPSTREAM" --head "$BRANCH" --state open --json number --jq '.[0].number')
  if [[ -n "$pr" ]]; then
    state=$(pr_state "$pr")
    body=$(gh pr view "$pr" --repo "$UPSTREAM" --json body --jq .body)
    case $state in
      pending|none) wait_for_validation "$pr"; exit 0 ;;
      fail) echo "[submit] PR #$pr failed validation, rebuilding"; pr_failed=1 ;;
      pass)
        if [[ "$body" != *"$STALE_MARK"* ]]; then
          echo "[submit] PR #$pr passed validation, waiting for the hub to merge it"; exit 0
        fi
        echo "[submit] PR #$pr was built from an older release, looking for the new one"; refresh_only=1 ;;
    esac
  fi
  # The hub checks the window when validation runs, so a late PR can only fail
  if [[ "$weekday" != 7 && "$weekday" != 1 && "$weekday" != 2 ]]; then
    echo "[submit] round $REF window is closed (Kinshasa date $today) and the round isn't in" >&2; exit 1
  fi
fi

rm -rf hub out
git clone -q --depth 1 --filter=blob:none --sparse "https://github.com/$UPSTREAM.git" hub
git -C hub sparse-checkout set target-data auxiliary-data hub-config

run_forecast() {
  set +e
  BDBV_HUB=hub .venv/bin/python forecast.py "$REF" out
  status=$?
  set -e
}
run_forecast
stale=""
if [[ $status -eq $EXIT_NO_DATA && -z "$refresh_only" && ( -n "${FINAL:-}" || -n "$pr_failed" ) ]]; then
  echo "[submit] no complete release for this round: forecasting from the last one"
  stale=1
  STALE_OK=1 run_forecast
fi
if [[ $status -eq $EXIT_NO_DATA || $status -eq $EXIT_NO_ROUND ]]; then
  [[ -z "$refresh_only" ]] || { echo "[submit] no new release yet, PR #$pr stays as it is"; exit 0; }
  if [[ -n "${FINAL:-}" || -n "$pr_failed" ]]; then
    echo "[submit] round $REF can't be built (exit $status): the hub hasn't opened it" >&2; exit 1
  fi
  exit 0
fi
[[ $status -eq 0 ]] || exit $status
[[ -z "${DRY_RUN:-}" ]] || { echo "[submit] dry run, stopping before push"; exit 0; }

# Branch from the fork's own main, never synced (syncing needs the workflow scope).
# The PR adds one new file, so a stale base merges cleanly.
rm -rf fork
git clone -q --depth 1 --filter=blob:none --sparse "https://github.com/$FORK.git" fork
git -C fork sparse-checkout set "model-output/$MODEL"
mkdir -p "fork/model-output/$MODEL"
cp "out/$REF-$MODEL.csv" "fork/$FILE"
git -C fork switch -q -C "$BRANCH"
git -C fork add "$FILE"
git -C fork -c user.name=pedro-f20 -c user.email=250035667+pedro-f20@users.noreply.github.com \
  commit -q -m "Add $MODEL forecast for $REF"
# Force: a leftover branch or a PR being rebuilt must not block the push
git -C fork push -q -f "https://x-access-token:$GH_TOKEN@github.com/$FORK.git" "$BRANCH"

locations=$(cut -d, -f5 "out/$REF-$MODEL.csv" | tail -n +2 | sort -u | wc -l | tr -d ' ')
body="Weekly forecast for round $REF from $MODEL ($locations locations). Same model and settings as earlier rounds; see model-metadata/$MODEL.yml."
[[ -z "$stale" ]] || body="$body Built from an $STALE_MARK, because this round's release wasn't complete when this ran."
if [[ -n "$pr" ]]; then
  gh pr edit "$pr" --repo "$UPSTREAM" --body "$body" >/dev/null
  echo "[submit] updated PR #$pr"
else
  url=$(gh pr create --repo "$UPSTREAM" --head "pedro-f20:$BRANCH" --base main \
    --title "$MODEL forecast for $REF" --body "$body")
  pr=${url##*/}
  echo "[submit] opened $url"
fi
echo "$REF,$today,$locations,https://github.com/$UPSTREAM/pull/$pr" >> submissions.csv
wait_for_validation "$pr"
