#!/usr/bin/env bash
# Descobre novas empresas InHire (crt.sh + Wayback), valida via API pública e
# atualiza data/inhire_companies.json. Agende 1x/dia no cron.
#
# USAGE:
#   discover_inhire.sh                 # descobre + valida (recomendado)
#   discover_inhire.sh --no-validate   # rápido, sem validar (lista maior/ruidosa)
set -uo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT" || { echo "Falha ao acessar $PROJECT"; exit 3; }

echo "[$(date)] Garantindo stack CareerOps..."
docker compose up -d >/dev/null 2>&1 || { echo "Falha ao subir docker compose"; exit 4; }

echo "[$(date)] Descobrindo tenants InHire..."
docker compose run --rm app uv run python -m careerops.collector.inhire_discovery "$@"
echo "[$(date)] Concluído."
