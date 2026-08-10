#!/usr/bin/env bash
# Wrapper genérico: sobe o stack CareerOps (se necessário) e roda a varredura
# de UM país. Uso: run_careerops.sh <pais> [digest]
#   <pais>  : nome canônico (Brazil, Netherlands, ...) ou alias PT (brasil, holanda)
#   [digest]: se passado, dispara o resumo diário (n8n -> Telegram) ao final
set -uo pipefail

PROJECT=/home/castro/Projects/CareerOps
LOGDIR="$PROJECT/logs"
mkdir -p "$LOGDIR"

COUNTRY="${1:-}"
DIGEST_FLAG=""
[ "${2:-}" = "digest" ] && DIGEST_FLAG="--digest"

if [ -z "$COUNTRY" ]; then
  echo "Uso: run_careerops.sh <pais> [digest]"
  exit 2
fi

# Create dated log file at runtime
LOG="$LOGDIR/careerops-$(echo "$COUNTRY" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')-$(date +%Y%m%d).log"

cd "$PROJECT" || { echo "Falha ao acessar $PROJECT"; exit 3; }

echo "[$(date)] Garantindo stack CareerOps (n8n + app)..."
docker compose up -d >/dev/null 2>&1 || { echo "Falha ao subir docker compose"; exit 4; }

echo "[$(date)] >>> Iniciando varredura: pais=$COUNTRY digest=${DIGEST_FLAG:-off}" | tee -a "$LOG"

docker compose run --rm app uv run src/careerops/main.py --country "$COUNTRY" $DIGEST_FLAG 2>&1 | tee -a "$LOG"

echo "[$(date)] <<< Fim da varredura pais=$COUNTRY."
