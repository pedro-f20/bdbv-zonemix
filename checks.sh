# Shared by submit.sh and check.sh. Needs UPSTREAM set and GH_TOKEN.
CHECK=validate-submission

# True if the file is in the hub. Any error other than a 404 stops the run: carrying on could
# open a PR that breaks the hub's rule that existing rows are preserved.
in_hub() {
  local out
  if out=$(gh api "repos/$UPSTREAM/contents/$1" --silent 2>&1); then return 0; fi
  grep -q "HTTP 404" <<<"$out" && return 1
  echo "[checks] can't read $1 from the hub: $out" >&2; exit 1
}

# Prints pass, pending, none (no validation run yet, or awaiting a maintainer's approval) or fail.
pr_state() {
  gh pr view "$1" --repo "$UPSTREAM" --json statusCheckRollup --jq "
    [.statusCheckRollup[] | select(.name == \"$CHECK\")] as \$runs
    | if (\$runs | length) == 0 then \"none\"
      elif (\$runs | all(.conclusion == \"SUCCESS\")) then \"pass\"
      elif (\$runs | any(.status == \"QUEUED\" or .status == \"IN_PROGRESS\" or .status == \"PENDING\" or .status == \"WAITING\")) then \"pending\"
      else \"fail\" end"
}

# Returns only on a passing validation: a run that opens a PR isn't done until the hub accepts it.
wait_for_validation() {
  local state=none deadline=$(( SECONDS + ${VALIDATION_WAIT:-1800} ))
  while (( SECONDS < deadline )); do
    state=$(pr_state "$1")
    case $state in
      pass) echo "[checks] PR #$1 passed validation"; return 0 ;;
      fail) echo "[checks] PR #$1 failed validation: https://github.com/$UPSTREAM/pull/$1" >&2; exit 1 ;;
    esac
    sleep "${VALIDATION_POLL:-30}"
  done
  echo "[checks] PR #$1 validation still '$state' after waiting: https://github.com/$UPSTREAM/pull/$1" >&2
  exit 1
}
