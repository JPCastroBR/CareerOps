#!/usr/bin/env bash
# Wrapper: sobe o stack CareerOps (se necessário) e roda a varredura.
#
# USAGE:
#   run_careerops.sh                 # varre os países definidos em config.json
#   run_careerops.sh digest          # igual acima + dispara digest diário
#   run_careerops.sh <pais>          # varre UM país isolado (ex: Brazil, holanda)
#   run_careerops.sh <pais> digest   # país isolado + digest
#   run_careerops.sh banks           # SÓ portais de bancos BR (Gupy)
#   run_careerops.sh Brazil banks    # Brazil (JobSpy) + bancos
#   run_careerops.sh Brazil banks digest
#
# Sem país: o main.py lê config.json e varre os targets definidos lá.
# Com país: força varredura isolada (ignora config.json para a escolha do país).
set -uo pipefail

PROJECT=/home/castro/Projects/CareerOps
LOGDIR="$PROJECT/logs"
mkdir -p "$LOGDIR"

ARG1="${1:-}"
ARG2="${2:-}"
ARG3="${3:-}"

# Resolve country + digest + source from args (order-independent-ish)
COUNTRY=""
DIGEST_FLAG=""
SOURCE_FLAG=""
for a in "$ARG1" "$ARG2" "$ARG3"; do
    case "$a" in
        digest)      DIGEST_FLAG="--digest" ;;
        banks)       SOURCE_FLAG="--source banks" ;;
        "")          ;;
    esac
done
case "$ARG1" in
    digest|banks) ;;
    *)            [ -n "$ARG1" ] && COUNTRY="$ARG1" ;;
esac

# Log file (no country in name when scanning config.json)
if [ -n "$COUNTRY" ]; then
    LOG="$LOGDIR/careerops-$(echo "$COUNTRY" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')-$(date +%Y%m%d).log"
    MODE_ARGS="--country $COUNTRY"
else
    LOG="$LOGDIR/careerops-$(date +%Y%m%d).log"
    MODE_ARGS=""   # no --country => main.py uses config.json targets
fi
[ -n "$SOURCE_FLAG" ] && MODE_ARGS="$MODE_ARGS $SOURCE_FLAG"

cd "$PROJECT" || { echo "Falha ao acessar $PROJECT"; exit 3; }

echo "[$(date)] Garantindo stack CareerOps (n8n + app)..."
docker compose up -d >/dev/null 2>&1 || { echo "Falha ao subir docker compose"; exit 4; }

echo "[$(date)] >>> Iniciando varredura (modo: ${COUNTRY:-config.json}) digest=${DIGEST_FLAG:-off}" | tee -a "$LOG"

# shellcheck disable=SC2086
docker compose run --rm app uv run src/careerops/main.py $MODE_ARGS $DIGEST_FLAG 2>&1 | tee -a "$LOG"

echo "[$(date)] <<< Fim da varredura (modo: ${COUNTRY:-config.json})."
