#!/usr/bin/env bash
# Install CareerOps cron jobs.
#
# Timing is NO LONGER hardcoded here: it lives in config.json -> schedule
# (single source of truth, editable from the dashboard). This script only
# delegates to `python -m careerops.schedule`.
#
# Usage: ./install_cron_jobs.sh [install|remove|list|render|test [digest]]

set -uo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="$PROJECT/scripts/run_careerops.sh"
LOGDIR="$PROJECT/logs"
mkdir -p "$LOGDIR"

run_py() {
    if [ -x "$PROJECT/.venv/bin/python" ]; then
        (cd "$PROJECT" && PYTHONPATH="$PROJECT/src" "$PROJECT/.venv/bin/python" "$@")
    elif command -v uv >/dev/null 2>&1; then
        (cd "$PROJECT" && PYTHONPATH="$PROJECT/src" uv run python "$@")
    else
        (cd "$PROJECT" && PYTHONPATH="$PROJECT/src" python3 "$@")
    fi
}

test_job() {
    echo "Testing a manual run (wrapper reads config.json for countries)..."
    if [ "${1:-}" = "digest" ]; then
        "$WRAPPER" digest
    else
        "$WRAPPER"
    fi
}

case "${1:-install}" in
    install|remove|list|render)
        run_py -m careerops.schedule "$1" --project "$PROJECT" --config "$PROJECT/config.json"
        ;;
    test)
        test_job "${2:-}"
        ;;
    *)
        echo "Usage: $0 {install|remove|list|render|test [digest]}"
        exit 1
        ;;
esac
