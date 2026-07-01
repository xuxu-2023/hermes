#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="${TMPDIR:-/tmp}/dockter-hermes-test.$$"
LOG_FILE="${TMP_DIR}/commands.log"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$TMP_DIR/bin" "$TMP_DIR/home/.hermes/workspace" "$TMP_DIR/hermes-agent"
touch "$LOG_FILE"
touch "$TMP_DIR/hermes-agent/Dockerfile"
touch "$TMP_DIR/hermes-agent/docker-compose.yml"
touch "$TMP_DIR/hermes-agent/docker-compose.extra.yml"

cat > "$TMP_DIR/home/.hermes/config.yaml" <<'YAML'
model:
  provider: test
YAML

cat > "$TMP_DIR/home/.hermes/.env" <<'ENV'
API_SERVER_KEY=secret-value
OPENROUTER_API_KEY=another-secret
ENV

cat > "$TMP_DIR/bin/docker" <<'SH'
#!/usr/bin/env bash
printf 'docker %q' "$1" >> "$DOCKTER_TEST_LOG"
shift || true
for arg in "$@"; do
  printf ' %q' "$arg" >> "$DOCKTER_TEST_LOG"
done
printf '\n' >> "$DOCKTER_TEST_LOG"
case "$*" in
  *"gateway status --deep"*)
    printf 'gateway ok\n'
    ;;
  *"--format"*)
    printf 'NAMES\tIMAGE\tSTATUS\tPORTS\nhermes\thermes-agent\tUp\t9119\n'
    ;;
  *)
    printf 'docker stub ok\n'
    ;;
esac
SH
chmod +x "$TMP_DIR/bin/docker"

cat > "$TMP_DIR/bin/git" <<'SH'
#!/usr/bin/env bash
printf 'git' >> "$DOCKTER_TEST_LOG"
for arg in "$@"; do
  printf ' %q' "$arg" >> "$DOCKTER_TEST_LOG"
done
printf '\n' >> "$DOCKTER_TEST_LOG"
printf 'git stub ok\n'
SH
chmod +x "$TMP_DIR/bin/git"

cat > "$TMP_DIR/bin/curl" <<'SH'
#!/usr/bin/env bash
printf 'curl' >> "$DOCKTER_TEST_LOG"
for arg in "$@"; do
  printf ' %q' "$arg" >> "$DOCKTER_TEST_LOG"
done
printf '\n' >> "$DOCKTER_TEST_LOG"
printf '{"status":"ok"}\n'
SH
chmod +x "$TMP_DIR/bin/curl"

cat > "$TMP_DIR/bin/open" <<'SH'
#!/usr/bin/env bash
printf 'open' >> "$DOCKTER_TEST_LOG"
for arg in "$@"; do
  printf ' %q' "$arg" >> "$DOCKTER_TEST_LOG"
done
printf '\n' >> "$DOCKTER_TEST_LOG"
printf 'open stub ok\n'
SH
chmod +x "$TMP_DIR/bin/open"

cat > "$TMP_DIR/bin/xdg-open" <<'SH'
#!/usr/bin/env bash
printf 'xdg-open' >> "$DOCKTER_TEST_LOG"
for arg in "$@"; do
  printf ' %q' "$arg" >> "$DOCKTER_TEST_LOG"
done
printf '\n' >> "$DOCKTER_TEST_LOG"
printf 'xdg-open stub ok\n'
SH
chmod +x "$TMP_DIR/bin/xdg-open"

export HOME="$TMP_DIR/home"
export PATH="$TMP_DIR/bin:$PATH"
export DOCKTER_TEST_LOG="$LOG_FILE"
export DOCKTER_HERMES_DIR="$TMP_DIR/hermes-agent"
export HERMES_HOME="$TMP_DIR/home/.hermes"
export DOCKTER_HERMES_DOCKER_BIN="$TMP_DIR/bin/docker"

# shellcheck source=/dev/null
source "$ROOT_DIR/dockter-hermes-helpers.sh"

run_case() {
  local name="$1"
  shift
  printf 'TEST %s\n' "$name"
  ( "$@" ) >/dev/null
}

run_failure_case() {
  local name="$1"
  shift
  printf 'TEST %s\n' "$name"
  if ( "$@" ) >/dev/null 2>&1; then
    printf 'Expected failure but command passed: %s\n' "$name" >&2
    return 1
  fi
}

run_case help dockter-hermes-help
run_case config dockter-hermes-config
run_case show-config dockter-hermes-show-config
run_case cd dockter-hermes-cd
run_case home dockter-hermes-home
run_case workspace dockter-hermes-workspace

run_case start-default dockter-hermes-start
run_case start-gateway dockter-hermes-start gateway
run_case restart-default dockter-hermes-restart
run_case restart-all dockter-hermes-restart all
run_case stop dockter-hermes-stop
run_case logs-default dockter-hermes-logs
run_case logs-dashboard dockter-hermes-logs dashboard --tail 5
run_case status dockter-hermes-status

run_case shell dockter-hermes-shell gateway
run_case exec dockter-hermes-exec gateway hermes --version
run_case exec-default-service dockter-hermes-exec hermes --help
run_failure_case exec-missing-command dockter-hermes-exec
run_case cli dockter-hermes-cli hermes --help
run_case chat dockter-hermes-chat
run_case setup dockter-hermes-setup

run_case dashboard dockter-hermes-dashboard
run_case health dockter-hermes-health

run_case rebuild dockter-hermes-rebuild
run_case update dockter-hermes-update
printf 'y\n' | dockter-hermes-clean >/dev/null
printf 'TEST clean\n'

printf '\nCaptured external commands:\n'
cat "$LOG_FILE"
