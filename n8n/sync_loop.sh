#!/bin/sh
# Serviço n8n-sync: aplica n8n/workflows/*.json quando solicitado e,
# na primeira subida com banco vazio, carrega o "padrão" versionado no repo.
set -u
STATUS=/sync/import.status
SEED_MARKER=/home/node/.n8n/.careerops-seeded

apply_repo_workflows() {
  out=$(n8n import:workflow --separate --input=/workflows 2>&1); rc=$?
  ids=$(node -e 'const fs=require("fs");for(const f of fs.readdirSync("/workflows")){if(!f.endsWith(".json"))continue;try{const d=JSON.parse(fs.readFileSync("/workflows/"+f,"utf8"));if(d.active&&d.id)console.log(d.id)}catch(e){}}')
  for id in $ids; do
    n8n update:workflow --id="$id" --active=true >/dev/null 2>&1 || true
  done
  printf '%s' "$out" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{require("fs").writeFileSync(process.argv[1],JSON.stringify({ts:new Date().toISOString(),ok:process.argv[2]==="0",output:s}))})' "$STATUS" "$rc"
  return $rc
}

seed_defaults() {
  [ -f "$SEED_MARKER" ] && return 0
  tries=0
  while [ $tries -lt 30 ]; do
    if n8n export:workflow --all --output=/tmp/careerops-check.json >/dev/null 2>&1; then
      break
    fi
    tries=$((tries + 1))
    sleep 2
  done
  [ -f /tmp/careerops-check.json ] || return 1
  count=$(node -e 'try{const d=require("/tmp/careerops-check.json");console.log(Array.isArray(d)?d.length:0)}catch(e){console.log(0)}')
  if [ "$count" -eq 0 ]; then
    apply_repo_workflows || { rm -f /tmp/careerops-check.json; return 1; }
  fi
  rm -f /tmp/careerops-check.json
  touch "$SEED_MARKER"
}

seed_defaults

while true; do
  if [ -f /sync/import.request ]; then
    rm -f /sync/import.request
    apply_repo_workflows
  fi
  [ -f "$SEED_MARKER" ] || seed_defaults
  sleep 3
done
