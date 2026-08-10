#!/usr/bin/env bash
# Dispara o resumo diário (careerops-digest) uma vez por dia, de forma leve.
# Lê o Google Sheets (Jobs) e envia UMA mensagem consolidada via Telegram.
set -uo pipefail

PROJECT=/home/castro/Projects/CareerOps
cd "$PROJECT" || { echo "Falha ao acessar $PROJECT"; exit 3; }

echo "[$(date)] Garantindo n8n no ar..."
docker compose up -d n8n >/dev/null 2>&1 || { echo "Falha ao subir n8n"; exit 4; }

echo "[$(date)] Disparando digest diario (n8n -> Telegram)..."
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST http://localhost:5678/webhook/careerops-digest \
  -H 'Content-Type: application/json' \
  -d '{"trigger":"cron_digest"}' --max-time 15 \
  || echo "Falha ao disparar digest (verifique se o n8n está ativo)."
