#!/usr/bin/env bash
# Sincroniza workflows do n8n com n8n/workflows/*.json (versionável no Git).
#
# USAGE:
#   scripts/n8n_sync.sh export   # n8n -> projeto (após editar na UI)
#   scripts/n8n_sync.sh import   # projeto -> n8n (aplica os JSONs)
#   scripts/n8n_sync.sh list     # lista os arquivos versionados
#
# Observações:
# - Credenciais NÃO são exportadas (apenas IDs/nomes). Para outra máquina,
#   recrie as credenciais no n8n.
# - import usa --activeState=fromJson (ativa conforme campo "active").
# - Faça export antes de import (backup) se tiver dúvidas.
set -uo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WF_DIR="$PROJECT/n8n/workflows"
TMP="$WF_DIR/.tmp"

ensure_stack() {
    (cd "$PROJECT" && docker compose up -d n8n >/dev/null 2>&1)
}

export_wf() {
    ensure_stack
    mkdir -p "$WF_DIR" "$TMP"
    docker exec careerops-n8n n8n export:workflow --all --separate --pretty --output=/home/node/workflows/.tmp
    python3 - "$WF_DIR" "$TMP" <<'PY'
import json, re, sys
from pathlib import Path
wf_dir = Path(sys.argv[1]); tmp = Path(sys.argv[2])
keep = set()
for f in sorted(tmp.glob("*.json")):
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        continue
    name = str(data.get("name") or f.stem)
    if name.lower().startswith("test"):
        continue
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or f.stem
    out = wf_dir / f"{slug}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    keep.add(out.name)
for f in wf_dir.glob("*.json"):
    if f.name not in keep:
        f.unlink()
PY
    rm -rf "$TMP"
    echo "Exportado para $WF_DIR:"
    ls -1 "$WF_DIR"/*.json 2>/dev/null | xargs -n1 basename
}

import_wf() {
    ensure_stack
    if [ -d "$TMP" ]; then rm -rf "$TMP"; fi
    echo "Importando workflows de $WF_DIR ..."
    docker exec careerops-n8n n8n import:workflow --separate --input=/home/node/workflows
    local ids
    ids=$(python3 - "$WF_DIR" <<'PY'
import json, sys
from pathlib import Path
for f in sorted(Path(sys.argv[1]).glob("*.json")):
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        continue
    if d.get("active") and d.get("id"):
        print(d["id"])
PY
)
    if [ -n "$ids" ]; then
        for wid in $ids; do
            docker exec careerops-n8n n8n update:workflow --id="$wid" --active=true >/dev/null 2>&1 || true
        done
        echo "Reativados: $(echo "$ids" | tr '\n' ' ')"
    fi
    echo "Import concluído. Verifique no n8n."
}

case "${1:-export}" in
    export) export_wf ;;
    import) import_wf ;;
    list) ls -1 "$WF_DIR"/*.json 2>/dev/null | xargs -n1 basename ;;
    *) echo "Usage: $0 {export|import|list}"; exit 1 ;;
esac
