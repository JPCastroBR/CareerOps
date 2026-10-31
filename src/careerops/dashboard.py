"""CareerOps live dashboard (stdlib only).

Serves a small web UI next to n8n (default ``http://localhost:8080``):

- Live: current run state, source progress, jobs being seen, recent events
- Analisadas: CSV of jobs sent for analysis (date, company, title, url…)
- Config: read/write ``config.json`` from the browser
- Agenda: read/write ``config.json["schedule"]`` + rendered crontab

Run: ``python -m careerops.dashboard`` (``DASHBOARD_PORT`` overrides 8080).

Security: no auth — it can edit local config files. Keep it on localhost/LAN
and do not expose it to the internet.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
import requests
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from careerops import telemetry as tel
from careerops import sheets as sheets_mod
from careerops.schedule import DEFAULT_CONFIG, get_schedule, load_config, next_runs, render_crontab

BASE_DIR = Path(__file__).resolve().parents[2]          # filesystem (/app no container)
PROJECT_DIR = Path(os.environ.get("CAREEROPS_PROJECT_DIR") or BASE_DIR)  # apenas p/ exibição/crontab
CONFIG_PATH = BASE_DIR / DEFAULT_CONFIG
PROFILE_PATH = BASE_DIR / "profile.yaml"
PROFILE_VERSIONS_DIR = BASE_DIR / "data" / "telemetry" / "profile_versions"
SECRETS_PATH = BASE_DIR / "secrets" / "api_keys.json"
SECRETS_DIR = BASE_DIR / "secrets"
SA_PATH = SECRETS_DIR / "google-sa.json"
SHEETS_PATH = SECRETS_DIR / "sheets.json"
CREDENTIALS_PATH = SECRETS_DIR / "credentials.json"
N8N_DIR = BASE_DIR / "n8n"
WORKFLOWS_DIR = N8N_DIR / "workflows"
IMPORT_REQUEST = N8N_DIR / "import.request"
IMPORT_STATUS = N8N_DIR / "import.status"

SECRET_FIELDS = [
    {"key": "GEMINI_API_KEY", "label": "Google Gemini (IA)", "help": "Chave usada pela IA (n8n)."},
    {"key": "OPENAI_API_KEY", "label": "OpenAI (IA)", "help": "Chave da OpenAI (n8n)."},
    {"key": "DEEPSEEK_API_KEY", "label": "DeepSeek (IA)", "help": "Chave do DeepSeek (n8n)."},
    {"key": "TELEGRAM_BOT_TOKEN", "label": "Telegram Bot", "help": "Token do bot para alertas/digest."},
    {"key": "GOOGLE_SA_FILE", "label": "Service Account Google", "help": "Caminho do JSON (ex.: secrets/google-sa.json)."},
    {"key": "N8N_API_KEY", "label": "n8n API Key", "help": "Chave da API do n8n."},
    {"key": "N8N_WEBHOOK_URL", "label": "Webhook n8n", "help": "Ex.: http://n8n:5678/webhook/careerops-jobs"},
]

PAGE = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>CareerOps</title>
  <style>/* ===================== CareerOps Design System v2 ===================== */
:root {
  --bg: #0a0e27;
  --bg-elev: #0e1231;
  --panel: #0d1130;
  --panel-2: #121740;
  --panel-3: #1a2053;
  --line: #1f2750;
  --line-2: #2c3669;
  --fg: #e8f4f8;
  --muted: #9fb2d4;
  --dim: #6d80a8;
  --accent: #00d9ff;
  --accent-2: #7cebff;
  --accent-soft: #0b3342;
  --ok: #00ff88;
  --ok-soft: #07331f;
  --err: #ff4d7e;
  --err-soft: #3a0f24;
  --warn: #ffb454;
  --warn-soft: #33240a;
  --violet: #b16cf0;
  --violet-soft: #2a1745;
  --cyan: #50e3c2;
  --radius-sm: 8px;
  --radius: 10px;
  --radius-lg: 16px;
  --gap: 18px;
  --shadow-sm: 0 1px 0 rgba(255, 255, 255, .04), 0 6px 20px rgba(0, 0, 0, .35);
  --shadow: 0 18px 48px rgba(0, 0, 0, .5);
  --font: "Inter", "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --icon: 18px;
}
html { color-scheme: dark; }
    * { box-sizing: border-box; }
    html, body { margin: 0; }

body {
  font-family: var(--font);
  font-size: 14px;
  color: var(--fg);
  background: var(--bg);
  min-height: 100vh;
}
body::before {
  content: "";
  position: fixed; inset: -20%; z-index: -2; pointer-events: none;
  background:
    radial-gradient(45% 55% at 12% 18%, rgba(0, 217, 255, .30), transparent 60%),
    radial-gradient(50% 60% at 88% 12%, rgba(121, 40, 202, .34), transparent 60%),
    radial-gradient(42% 52% at 72% 82%, rgba(0, 255, 136, .16), transparent 60%),
    radial-gradient(46% 56% at 22% 90%, rgba(255, 0, 110, .15), transparent 60%),
    radial-gradient(50% 50% at 50% 50%, rgba(0, 201, 255, .08), transparent 70%);
  filter: blur(60px);
  animation: auroraDrift 16s ease-in-out infinite alternate;
}
body::after {
  content: "";
  position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background-image:
    radial-gradient(1.2px 1.2px at 12% 22%, rgba(255, 255, 255, .65), transparent 55%),
    radial-gradient(1px 1px at 48% 68%, rgba(255, 255, 255, .4), transparent 55%),
    radial-gradient(1.4px 1.4px at 76% 38%, rgba(0, 217, 255, .6), transparent 55%),
    radial-gradient(1px 1px at 90% 82%, rgba(255, 255, 255, .45), transparent 55%),
    radial-gradient(1.2px 1.2px at 28% 56%, rgba(0, 255, 136, .5), transparent 55%),
    radial-gradient(1px 1px at 62% 12%, rgba(255, 255, 255, .4), transparent 55%);
  background-size: 340px 340px;
  opacity: .55;
}
@keyframes auroraDrift {
  0% { transform: translate(-3%, -2%) rotate(-2deg) scale(1); opacity: .75; }
  50% { transform: translate(1%, 2%) rotate(2deg) scale(1.06); opacity: 1; }
  100% { transform: translate(3%, -1%) rotate(-1deg) scale(1.02); opacity: .9; }
}
a { color: var(--accent-2); }
a:hover { color: #8ef2ff; }

/* ---------------- topbar ---------------- */
.topbar {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 26px;
  padding: 0 30px;
  height: 72px;
  background: rgba(8, 12, 22, .82);
  backdrop-filter: blur(18px) saturate(140%);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  z-index: 50;
}
.brand { display: flex; align-items: center; gap: 11px; text-decoration: none; color: var(--fg); font-weight: 800; font-size: 16px; white-space: nowrap; }
.brand:hover { text-decoration: none; color: var(--fg); }
.brand small { display: block; color: var(--dim); font-size: 10px; font-weight: 600; letter-spacing: .8px; text-transform: uppercase; margin-top: 1px; }
.logo-mark { width: 36px; height: 36px; border-radius: 11px; display: inline-flex; align-items: center; justify-content: center; background: linear-gradient(145deg, #00d9ff, #7928ca); color: #fff; box-shadow: 0 0 18px rgba(0, 217, 255, .55), 0 8px 22px rgba(121, 40, 202, .4); flex: none; }
.logo-mark svg { width: 19px; height: 19px; }
.brand-text { line-height: 1.1; }
.mainnav { display: flex; align-items: center; gap: 4px; justify-content: center; flex-wrap: wrap; }
.mainnav button { background: transparent; border: 1px solid transparent; color: var(--muted); padding: 8px 14px; border-radius: 10px; font-weight: 600; font-size: 13px; cursor: pointer; transition: .15s; }
.mainnav button:hover { color: var(--fg); background: rgba(255, 255, 255, .04); }
.mainnav button.active { color: #fff; background: linear-gradient(180deg, rgba(0, 217, 255, .30), rgba(0, 217, 255, .15)); border-color: rgba(0, 217, 255, .35); box-shadow: inset 0 1px 0 rgba(255, 255, 255, .08); }
.mainnav .nav-link { color: var(--accent-2); font-weight: 700; font-size: 13px; padding: 8px 10px; border-radius: 10px; }
.mainnav .nav-link:hover { background: rgba(0, 217, 255, .08); text-decoration: none; }
.header-right { display: flex; align-items: center; gap: 12px; justify-content: flex-end; }
.status-chip { display: inline-flex; align-items: center; gap: 7px; padding: 6px 12px; border: 1px solid var(--line); border-radius: 999px; background: rgba(17, 26, 46, .7); font-size: 11.5px; color: var(--muted); white-space: nowrap; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--dim); box-shadow: 0 0 0 3px rgba(95, 113, 148, .15); }
.status-dot.running { background: var(--ok); box-shadow: 0 0 0 3px rgba(47, 212, 139, .18); animation: pulse 1.6s infinite; }
.status-dot.error { background: var(--err); box-shadow: 0 0 0 3px rgba(251, 113, 133, .18); }
.status-dot.finished { background: var(--accent); }
.status-sep { opacity: .45; }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(47, 212, 139, .45); } 70% { box-shadow: 0 0 0 8px rgba(47, 212, 139, 0); } 100% { box-shadow: 0 0 0 0 rgba(47, 212, 139, 0); } }
.settings { position: relative; display: flex; align-items: center; gap: 8px; }
.settings .ghost { background: rgba(255, 255, 255, .03); border: 1px solid var(--line); color: var(--muted); padding: 8px 12px; border-radius: 10px; font-weight: 600; font-size: 12.5px; cursor: pointer; }
.settings .ghost:hover { color: var(--fg); border-color: var(--line-2); }
.settings .ghost.active { color: var(--accent-2); border-color: rgba(0, 217, 255, .45); }
.settings .menu { position: absolute; right: 0; top: calc(100% + 8px); background: var(--panel-2); border: 1px solid var(--line-2); border-radius: 12px; padding: 6px; display: flex; flex-direction: column; min-width: 190px; z-index: 60; box-shadow: var(--shadow); }
.settings .menu button { text-align: left; background: transparent; border: 1px solid transparent; color: var(--muted); padding: 9px 12px; border-radius: 8px; font-weight: 600; font-size: 13px; cursor: pointer; }
.settings .menu button:hover { background: rgba(255, 255, 255, .04); color: var(--fg); }
.settings .menu button.active { background: rgba(0, 217, 255, .16); color: var(--accent-2); border-color: rgba(0, 217, 255, .25); }

/* ---------------- layout ---------------- */
main { width: 100%; max-width: 1760px; margin: 0 auto; padding: clamp(20px, 2.4vw, 40px); }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; margin-bottom: 20px; flex-wrap: wrap; }
.page-head h1 { margin: 0 0 4px; font-size: 26px; font-weight: 800; letter-spacing: -.5px; }
.page-head p { margin: 0; color: var(--muted); font-size: 13px; }
.head-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.period-control { display: flex; align-items: center; gap: 8px; background: rgba(17, 26, 46, .7); border: 1px solid var(--line); border-radius: 10px; padding: 4px 6px 4px 12px; }
.period-label { font-size: 10px; text-transform: uppercase; letter-spacing: .7px; color: var(--dim); font-weight: 700; }
.period-control select { border: none; background: transparent; padding: 6px 8px; font-size: 12.5px; color: var(--fg); width: auto; }
.icon-btn { background: rgba(255, 255, 255, .03); border: 1px solid var(--line); color: var(--muted); width: 34px; height: 34px; border-radius: 10px; cursor: pointer; font-size: 15px; display: inline-flex; align-items: center; justify-content: center; }
.icon-btn:hover { color: var(--fg); border-color: var(--line-2); }
.sources-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 0 0 18px; }
.source-chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 11px; border-radius: 999px; background: rgba(0, 217, 255, .10); border: 1px solid rgba(0, 217, 255, .22); color: var(--accent-2); font-weight: 700; font-size: 10.5px; letter-spacing: .5px; text-transform: uppercase; }
.source-chip::before { content: ""; width: 5px; height: 5px; border-radius: 50%; background: currentColor; opacity: .8; }

/* ---------------- cards / grid ---------------- */
.dash-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: var(--gap); }
.span-2 { grid-column: span 2; }
.span-5 { grid-column: span 5; }
.span-6 { grid-column: span 6; }
.span-7 { grid-column: span 7; }
.span-12 { grid-column: span 12; }
@media (max-width: 1500px) {
  .span-6 { grid-column: span 12; }
}
@media (max-width: 1400px) {
  .span-2 { grid-column: span 4; }
  .span-5, .span-7 { grid-column: span 12; }
}
@media (max-width: 760px) { .dash-grid > * { grid-column: span 12 !important; } }
.card, .section {
  background: linear-gradient(180deg, var(--panel-2), var(--panel));
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm), 0 0 0 1px rgba(0, 217, 255, .05), 0 0 34px rgba(0, 217, 255, .06);
  padding: 18px;
}
.card-title { display: flex; align-items: center; gap: 9px; font-size: 12.5px; font-weight: 700; letter-spacing: .2px; margin-bottom: 14px; }
.card-title::before { content: ""; width: 3px; height: 14px; border-radius: 2px; background: linear-gradient(180deg, var(--accent), var(--violet)); }
.card .k, .section .k { color: var(--muted); font-size: 11.5px; }
.card .v { font-size: 28px; font-weight: 800; letter-spacing: -.6px; }
.section h3 { margin: 0 0 10px; font-size: 14px; font-weight: 700; }

/* ---------------- KPIs ---------------- */
.kpi { position: relative; overflow: hidden; display: flex; flex-direction: column; gap: 6px; padding: 16px 17px 14px; min-height: 118px; }
.kpi::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--c); }
.kpi::after { content: ""; position: absolute; right: -32px; top: -32px; width: 110px; height: 110px; border-radius: 50%; background: var(--c); opacity: .07; filter: blur(12px); pointer-events: none; }
.kpi-top { display: flex; align-items: center; gap: 9px; }
.kpi-icon { width: 28px; height: 28px; border-radius: 8px; display: inline-flex; align-items: center; justify-content: center; background: rgba(255, 255, 255, .05); color: var(--c); border: 1px solid rgba(255, 255, 255, .04); }
.kpi-icon svg { width: 16px; height: 16px; }
.kpi .k { font-size: 10.5px; color: var(--muted); font-weight: 700; letter-spacing: .7px; text-transform: uppercase; }
.kpi .v { font-size: 30px; font-weight: 800; letter-spacing: -1px; line-height: 1.05; }
.kpi .sub { font-size: 11.5px; color: var(--dim); }
.kpi.c-blue { --c: #00d9ff; }
.kpi.c-green { --c: #2fd48b; }
.kpi.c-red { --c: #fb7185; }
.kpi.c-violet { --c: #b16cf0; }
.kpi.c-amber { --c: #f5b942; }
.kpi.c-cyan { --c: #2dd4bf; }

/* ---------------- charts ---------------- */
.chart { display: flex; align-items: flex-end; gap: 5px; height: 190px; margin-top: 4px; padding: 14px 14px 22px; overflow-x: auto; border: 1px solid var(--line); border-radius: var(--radius); background: repeating-linear-gradient(180deg, rgba(255, 255, 255, .025) 0 1px, transparent 1px 38px), rgba(8, 12, 22, .35); }
.chart .bar { flex: 1 0 9px; min-width: 9px; background: linear-gradient(180deg, #00d9ff, #7928ca); border-radius: 5px 5px 0 0; position: relative; box-shadow: 0 0 16px rgba(0, 217, 255, .16); }
.chart .bar span { position: absolute; bottom: -16px; left: 50%; transform: translateX(-50%); font-size: 9.5px; color: var(--dim); white-space: nowrap; }
.chart-empty { display: flex; align-items: center; justify-content: center; height: 160px; color: var(--dim); font-size: 12.5px; border: 1px dashed var(--line); border-radius: var(--radius); }
.chart-h { display: flex; flex-direction: column; gap: 11px; margin-top: 2px; }
.hbar { display: grid; grid-template-columns: 130px minmax(0, 1fr) 92px; gap: 12px; align-items: center; font-size: 12px; }
.hbar > div:first-child { color: var(--muted); }
.hbar .track { height: 9px; background: rgba(255, 255, 255, .05); border-radius: 999px; overflow: hidden; }
.hbar .track i { display: block; height: 100%; border-radius: 999px; }
.hbar .val { text-align: right; font-weight: 800; color: var(--fg); }
.hbar .val small { color: var(--dim); font-weight: 600; font-size: 10px; margin-left: 4px; }
.funnel-row { display: grid; grid-template-columns: 118px minmax(0, 1fr) 100px; gap: 14px; align-items: center; margin: 9px 0; }
.funnel-row .lbl { color: var(--muted); font-size: 12px; font-weight: 600; }
.funnel-row .bar { height: 10px; background: rgba(255, 255, 255, .05); border: none; border-radius: 999px; overflow: hidden; }
.funnel-row .bar > i { display: block; height: 100%; border-radius: 999px; }
.funnel-row .val { text-align: right; font-weight: 800; font-size: 13px; }
.funnel-row .val small { color: var(--dim); font-weight: 600; font-size: 10.5px; margin-left: 5px; }
.donut-wrap { display: flex; align-items: center; justify-content: center; gap: 22px; flex-wrap: wrap; padding: 4px 0; }
.donut { width: 166px; height: 166px; border-radius: 50%; display: flex; align-items: center; justify-content: center; position: relative; }
.donut::after { content: ""; position: absolute; width: 104px; height: 104px; border-radius: 50%; background: var(--panel-2); box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .04); }
.donut .lbl { position: relative; z-index: 1; text-align: center; color: var(--muted); font-size: 11px; }
.donut .lbl b { display: block; font-size: 24px; color: var(--fg); line-height: 1.1; }
.legend { display: flex; flex-direction: column; gap: 9px; font-size: 12px; color: var(--muted); }
.legend .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 8px; }
.legend b { color: var(--fg); margin-left: 6px; }

/* ---------------- ops status ---------------- */
.ops-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.ops-item { background: rgba(8, 12, 22, .45); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.ops-label { font-size: 10px; letter-spacing: .8px; text-transform: uppercase; color: var(--dim); font-weight: 700; }
.ops-value { font-size: 13px; font-weight: 700; color: var(--fg); word-break: break-all; }
.ops-value.running { color: var(--ok); }
.ops-value.error { color: var(--err); }
.ops-value.mono { font-family: var(--mono); font-size: 12px; font-weight: 600; }

/* ---------------- tables ---------------- */
.table-wrap { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: auto; box-shadow: var(--shadow-sm); }
.table-wrap a { color: var(--accent-2); }
.table-wrap a:hover { color: #aef6ff; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 11px 14px; text-align: left; border-bottom: 1px solid rgba(255, 255, 255, .045); vertical-align: middle; }
th { color: var(--dim); font-size: 10.5px; text-transform: uppercase; letter-spacing: .7px; background: rgba(10, 15, 28, .92); position: sticky; top: 0; z-index: 1; font-weight: 700; }
tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: rgba(0, 217, 255, .045); }
td { font-size: 12.5px; color: var(--fg); }
.mono { font-family: var(--mono); font-size: 11.5px; color: var(--muted); }
.trunc { display: inline-block; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom; cursor: help; }
.trunc.wide { max-width: 520px; }
@media (min-width: 1600px) { .trunc { max-width: 440px; } .trunc.wide { max-width: 720px; } }
.empty { color: var(--dim); text-align: center; padding: 28px 16px; font-size: 12.5px; }
.pager { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; font-size: 12px; color: var(--muted); }
.pager .pager-info { margin-right: auto; color: var(--dim); }
.pager .pager-num { color: var(--dim); font-weight: 700; }
.pager button { padding: 6px 12px; }
.pager button:disabled { opacity: .4; pointer-events: none; }

/* ---------------- chips / scores ---------------- */
.score { display: inline-flex; align-items: center; justify-content: center; min-width: 42px; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 800; }
.score.hi { background: rgba(47, 212, 139, .14); color: #5ff0b0; }
.score.mid { background: rgba(0, 217, 255, .16); color: #8fe9ff; }
.score.low { background: rgba(245, 185, 66, .14); color: #ffd479; }
.score.bad { background: rgba(251, 113, 133, .14); color: #ff9bab; }
.chip { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 7px; background: rgba(255, 255, 255, .045); color: var(--muted); font-size: 11px; font-weight: 600; }
.chip.yes { background: rgba(47, 212, 139, .13); color: #5ff0b0; }
.chip.no { background: rgba(255, 255, 255, .045); color: var(--dim); }
.chip.tag { background: rgba(0, 217, 255, .12); color: var(--accent-2); }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 11px; border-radius: 999px; font-size: 10.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .5px; }
.pill.running { background: var(--ok-soft); color: var(--ok); }
.pill.finished { background: rgba(0, 217, 255, .14); color: var(--accent-2); }
.pill.error { background: var(--err-soft); color: var(--err); }
.pill.idle { background: rgba(255, 255, 255, .05); color: var(--dim); }

/* ---------------- buttons / inputs ---------------- */
button.act { background: linear-gradient(135deg, #00d9ff, #7928ca); border: 1px solid rgba(0, 217, 255, .35); color: #fff; padding: 9px 16px; border-radius: 10px; font-weight: 700; font-size: 12.5px; cursor: pointer; transition: .15s; box-shadow: 0 6px 24px rgba(0, 217, 255, .35); }
button.act:hover { transform: translateY(-1px); box-shadow: 0 10px 24px rgba(0, 217, 255, .35); }
button.ghost { background: rgba(255, 255, 255, .035); border: 1px solid var(--line); color: var(--fg); padding: 8px 13px; border-radius: 10px; cursor: pointer; font-weight: 600; font-size: 12px; transition: .15s; }
button.ghost:hover { border-color: var(--line-2); background: rgba(255, 255, 255, .06); }
button.danger { background: rgba(251, 113, 133, .1); border: 1px solid rgba(251, 113, 133, .25); color: #ff9bab; padding: 5px 10px; border-radius: 8px; cursor: pointer; font-size: 11.5px; font-weight: 700; }
button.danger:hover { background: rgba(251, 113, 133, .18); }
input[type=text], input[type=number], input[type=search], input[type=password], select, textarea { width: 100%; background: rgba(8, 12, 22, .55); border: 1px solid var(--line); color: var(--fg); border-radius: 10px; padding: 9px 11px; font-size: 13px; font-family: inherit; transition: .15s; }
input::placeholder, textarea::placeholder { color: var(--dim); }
input:focus, select:focus, textarea:focus { outline: none; border-color: rgba(0, 217, 255, .6); box-shadow: 0 0 0 3px rgba(0, 217, 255, .12); }
textarea { font-family: var(--mono); font-size: 12px; line-height: 1.5; }
:focus-visible { outline: 2px solid rgba(0, 217, 255, .55); outline-offset: 1px; }
.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 12px 0; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: rgba(8, 12, 22, .4); }
.seg button { background: transparent; color: var(--muted); border: none; padding: 8px 14px; cursor: pointer; font-weight: 700; font-size: 12px; }
.seg button.active { background: rgba(0, 217, 255, .18); color: var(--accent-2); }
.msg { color: var(--dim); font-size: 11.5px; }
.actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
pre.preview { background: rgba(8, 12, 22, .65); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px; color: var(--muted); max-height: 360px; overflow: auto; font-size: 11.5px; font-family: var(--mono); }

/* ---------------- forms / sections ---------------- */
.section { margin: 14px 0; }
.section-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 6px; }
.section-num { width: 24px; height: 24px; border-radius: 50%; background: rgba(0, 217, 255, .16); color: var(--accent-2); font-size: 11px; font-weight: 800; display: inline-flex; align-items: center; justify-content: center; flex: none; }
.section-head h3 { margin: 0; font-size: 14px; }
.section-desc { color: var(--dim); font-size: 11.5px; margin: 0 0 14px 34px; }
.field { display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 16px; align-items: start; padding: 12px 0; border-top: 1px solid rgba(255, 255, 255, .045); }
.field:first-of-type { border-top: none; }
.field-label { padding-top: 8px; }
.field-label .name { font-weight: 700; font-size: 13px; }
.field-label .help { color: var(--dim); font-size: 11.5px; margin-top: 3px; line-height: 1.45; }
.field-input { min-width: 0; }
@media (max-width: 640px) { .field { grid-template-columns: 1fr; gap: 6px; } .field-label { padding-top: 0; } }
.checks { display: flex; gap: 18px; flex-wrap: wrap; padding-top: 6px; }
.checks label { display: inline-flex; align-items: center; gap: 7px; color: var(--fg); font-weight: 600; font-size: 13px; }
details.block { border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); margin: 14px 0; }
details.block > summary { cursor: pointer; padding: 13px 16px; font-weight: 700; color: var(--fg); }
details.block > .body { padding: 0 16px 16px; }
.lbl { font-size: 11.5px; color: var(--dim); margin-bottom: 4px; }

/* ---------------- legacy components (skills/creds/exp) ---------------- */
.company { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 10.5px; font-weight: 700; margin-right: 4px; }
.company.c0 { background: rgba(0, 217, 255, .15); color: #8fe9ff; }
.company.c1 { background: rgba(47, 212, 139, .15); color: #5ff0b0; }
.company.c2 { background: rgba(167, 139, 250, .16); color: #c8b6ff; }
.company.c3 { background: rgba(245, 185, 66, .15); color: #ffd479; }
.company.c4 { background: rgba(251, 113, 133, .14); color: #ff9bab; }
.company.c5 { background: rgba(45, 212, 191, .14); color: #6ee7db; }
.company.c6 { background: rgba(244, 114, 182, .13); color: #f9a8d4; }
.company.c7 { background: rgba(163, 230, 53, .13); color: #bef264; }
.skill-group { display: flex; align-items: center; gap: 8px; margin: 16px 0 6px; }
.skill-group .gname { padding: 3px 11px; border-radius: 999px; font-weight: 800; font-size: 10.5px; letter-spacing: .3px; }
.skill-group.hard .gname, .skill-group.edu .gname { background: rgba(0, 217, 255, .14); color: #8fe9ff; }
.skill-group.soft .gname, .skill-group.idioma .gname { background: rgba(47, 212, 139, .14); color: #5ff0b0; }
.skill-group.cert .gname { background: rgba(245, 185, 66, .14); color: #ffd479; }
.skill-group .count { color: var(--dim); font-size: 10.5px; font-weight: 700; }
.skill-items { padding-left: 16px; margin-left: 6px; border-left: 2px solid var(--line); }
.skill-items.hard, .skill-items.edu { border-left-color: rgba(0, 217, 255, .5); }
.skill-items.soft, .skill-items.idioma { border-left-color: rgba(47, 212, 139, .5); }
.skill-items.cert { border-left-color: rgba(245, 185, 66, .5); }
.skill-row { display: flex; gap: 8px; align-items: center; padding: 7px 0; border-top: 1px solid rgba(255, 255, 255, .04); }
.skill-row > input[type=text] { flex: 1 1 0; min-width: 0; }
.skill-row > select { flex: 0 0 165px; }
.skill-row > button { flex: 0 0 auto; white-space: nowrap; }
.skill-row > .period { flex: 1.6 1 0; min-width: 0; }
.skill-row > input[type=number] { flex: 0 0 96px; }
.skill-extra { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; padding: 8px 0 4px; }
.skill-extra textarea { height: 56px; min-height: 56px; resize: vertical; }
@media (max-width: 700px) { .skill-row { flex-wrap: wrap; } .skill-row > select { flex: 1 1 130px; } .skill-extra { grid-template-columns: 1fr; } }
.groups-editor { display: flex; flex-direction: column; gap: 8px; }
.group-row { display: flex; gap: 8px; align-items: center; }
.group-row > input { flex: 1 1 0; min-width: 0; }
.group-row > select { flex: 0 0 150px; }
.group-row.hard { border-left: 3px solid rgba(0, 217, 255, .5); padding-left: 10px; border-radius: 4px; }
.group-row.soft { border-left: 3px solid rgba(47, 212, 139, .5); padding-left: 10px; border-radius: 4px; }
.exp-grid { display: grid; grid-template-columns: 1fr; gap: 14px; }
.exp-card { background: rgba(8, 12, 22, .35); border: 1px solid var(--line); border-radius: 12px; padding: 14px; }
.exp-card .exp-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.exp-card .grid3 { display: grid; grid-template-columns: 2fr 1.3fr 1.6fr; gap: 12px; }
.exp-card .lbl { font-size: 11.5px; color: var(--dim); margin: 8px 0 4px; }
.cred-row { display: grid; grid-template-columns: 1fr 160px auto; gap: 12px; align-items: center; padding: 10px 0; border-top: 1px solid rgba(255, 255, 255, .045); }
.cred-name { font-weight: 800; font-size: 13px; }
.cred-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.combo { position: relative; flex: 1 1 0; min-width: 150px; }
.combo-btn { width: 100%; display: flex; flex-wrap: wrap; gap: 4px; align-items: center; background: rgba(8, 12, 22, .55); border: 1px solid var(--line); border-radius: 10px; padding: 6px 8px; color: var(--fg); cursor: pointer; text-align: left; }
.combo-btn .caret { margin-left: auto; color: var(--dim); }
.combo-menu { position: absolute; z-index: 70; top: calc(100% + 4px); left: 0; right: 0; background: var(--panel-2); border: 1px solid var(--line-2); border-radius: 10px; padding: 8px; display: flex; flex-direction: column; gap: 6px; max-height: 220px; overflow: auto; box-shadow: var(--shadow); }
.combo-menu label { display: flex; align-items: center; gap: 8px; color: var(--fg); font-size: 12px; }
.combo-menu input { width: auto; }
.secret-reveal { display: block; margin-top: 5px; padding: 5px 7px; background: rgba(8, 12, 22, .65); border: 1px solid var(--line); border-radius: 6px; font-family: var(--mono); font-size: 11px; color: var(--fg); word-break: break-all; max-height: 80px; overflow: auto; }
    .checks input { width: auto; transform: scale(1.15); }
    .group-row > button { flex: 0 0 auto; white-space: nowrap; }
    .skill-block h4 { margin: 18px 0 6px; }
    .skill-row > select.lvl { flex: 0 0 190px; }
    .edu-block { margin: 10px 0; }
    @media (max-width: 900px) { .exp-card .grid3 { grid-template-columns: 1fr; } }
    @media (max-width: 700px) { .funnel-row { grid-template-columns: 96px minmax(0, 1fr) 62px; gap: 8px; } }
    #toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%) translateY(10px); background: var(--panel-2); border: 1px solid var(--line-2); color: var(--fg); padding: 10px 16px; border-radius: 12px; opacity: 0; pointer-events: none; transition: .2s; z-index: 100; box-shadow: var(--shadow); }
    #toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }

.hidden { display: none !important; }

@media (max-width: 1100px) {
  .topbar { grid-template-columns: 1fr auto; height: auto; padding: 12px 18px; gap: 10px 14px; }
  .mainnav { grid-column: 1 / -1; order: 3; justify-content: flex-start; overflow-x: auto; flex-wrap: nowrap; padding-bottom: 2px; }
  .mainnav button, .mainnav .nav-link { white-space: nowrap; }
  .brand { order: 1; }
  .header-right { order: 2; }
}
@media (max-width: 640px) {
  .topbar { grid-template-columns: 1fr; }
  .header-right { justify-content: space-between; width: 100%; }
  .status-chip { font-size: 10.5px; }
  .page-head h1 { font-size: 22px; }
  .head-actions { width: 100%; }
  .period-control { flex: 1; justify-content: space-between; }
  .trunc { max-width: 200px; }
  main { padding: 16px; }
}
.insight { font-size: 11.5px; color: var(--dim); border-top: 1px solid rgba(255, 255, 255, .05); padding-top: 10px; margin-top: 14px; display: flex; gap: 14px; flex-wrap: wrap; }
.insight b { color: var(--fg); font-weight: 700; }

.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin: 14px 0 18px; }
.cards .card { padding: 14px 16px; }
.cards .card .v { font-size: 24px; }
.card { display: flex; flex-direction: column; }
#funnel { flex: 1; display: flex; flex-direction: column; justify-content: center; }
#donut-wrap { flex: 1; }
#chart-bands { flex: 1; justify-content: center; }
.insight { margin-top: auto; }
.security-list { display: flex; flex-direction: column; }
.security-item { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid rgba(255, 255, 255, .045); font-size: 12px; color: var(--muted); }
.security-item:last-child { border-bottom: none; }
.security-item .mark { width: 18px; height: 18px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 800; flex: none; }
.security-item.ok .mark { background: rgba(47, 212, 139, .14); color: var(--ok); }
.security-item.bad .mark { background: rgba(251, 113, 133, .14); color: var(--err); }
.security-item .path { font-family: var(--mono); font-size: 11px; color: var(--dim); }

.funnel-foot { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: auto; padding-top: 12px; border-top: 1px solid rgba(255, 255, 255, .05); }
.funnel-foot > div { background: rgba(8, 12, 22, .4); border: 1px solid var(--line); border-radius: 10px; padding: 9px 11px; }
.funnel-foot span { display: block; font-size: 9.5px; letter-spacing: .6px; text-transform: uppercase; color: var(--dim); font-weight: 700; }
.funnel-foot b { display: block; font-size: 16px; font-weight: 800; color: var(--fg); margin-top: 2px; }
@media (max-width: 560px) { .funnel-foot { grid-template-columns: 1fr; } }
    td a { white-space: nowrap; }
    td.mono:first-child { white-space: nowrap; }
    .ops-value.finished { color: var(--accent-2); }
    .ops-value.idle { color: var(--muted); }

@media (max-width: 900px) {
  #view-vagas th:nth-child(5), #view-vagas td:nth-child(5),
  #view-vagas th:nth-child(6), #view-vagas td:nth-child(6),
  #view-vagas th:nth-child(7), #view-vagas td:nth-child(7),
  #view-vagas th:nth-child(8), #view-vagas td:nth-child(8),
  #view-vagas th:nth-child(9), #view-vagas td:nth-child(9),
  #view-vagas th:nth-child(10), #view-vagas td:nth-child(10) { display: none; }
  #view-analisadas th:nth-child(4), #view-analisadas td:nth-child(4),
  #view-analisadas th:nth-child(5), #view-analisadas td:nth-child(5) { display: none; }
  #view-overview th:nth-child(3), #view-overview td:nth-child(3),
  #view-overview th:nth-child(4), #view-overview td:nth-child(4),
  #view-overview th:nth-child(6), #view-overview td:nth-child(6) { display: none; }
}

.app-footer { max-width: 1760px; margin: 0 auto; padding: 18px 30px 32px; display: flex; justify-content: space-between; gap: 14px; flex-wrap: wrap; color: var(--dim); font-size: 11.5px; border-top: 1px solid rgba(255, 255, 255, .045); }
.app-footer b { color: var(--muted); font-weight: 700; }
.app-footer a { color: var(--accent-2); }
.mainnav .n8n-link { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line-2); border-radius: 10px; padding: 6px 11px; background: rgba(255, 255, 255, .03); font-size: 12px; }
.mainnav .n8n-link .n8n-badge { background: rgba(0, 217, 255, .18); color: var(--accent-2); border-radius: 6px; padding: 1px 6px; font-size: 10px; font-weight: 800; letter-spacing: .3px; }
.mainnav .n8n-link:hover { background: rgba(0, 217, 255, .12); border-color: rgba(0, 217, 255, .4); text-decoration: none; }
.hero { padding: 46px 8px 26px; text-align: center; }
.hero-badge { display: inline-block; padding: 5px 14px; border-radius: 999px; background: rgba(0, 217, 255, .12); border: 1px solid rgba(0, 217, 255, .25); color: var(--accent-2); font-size: 11px; font-weight: 700; letter-spacing: .6px; text-transform: uppercase; }
.hero h1 { font-size: 46px; font-weight: 900; letter-spacing: -1.5px; margin: 18px 0 10px; background: linear-gradient(90deg, #e8f4f8, #00d9ff 45%, #b16cf0); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
.hero-sub { max-width: 660px; margin: 0 auto; color: var(--muted); font-size: 15px; line-height: 1.65; }
.hero-cta { margin-top: 22px; display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 10px 18px; border-radius: 10px; font-weight: 700; font-size: 13px; text-decoration: none; background: linear-gradient(180deg, #00d9ff, #0095e0); color: #fff; border: 1px solid rgba(124, 176, 255, .35); }
.btn.ghost { background: rgba(255, 255, 255, .04); color: var(--fg); border-color: var(--line-2); }
.btn:hover { text-decoration: none; filter: brightness(1.06); }
.home-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-top: 34px; }
.home-card { background: linear-gradient(180deg, var(--panel-2), var(--panel)); border: 1px solid var(--line); border-radius: var(--radius-lg); padding: 18px; }
.hc-icon { font-size: 22px; }
.home-card h3 { margin: 10px 0 6px; font-size: 15px; }
.home-card p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.55; }
.home-how { max-width: 760px; margin: 30px auto 0; }
.home-steps { color: var(--muted); font-size: 13.5px; line-height: 1.9; padding-left: 20px; margin: 0; }
.home-steps b { color: var(--fg); }
.modal-backdrop { position: fixed; inset: 0; z-index: 200; background: rgba(4, 7, 14, .72); backdrop-filter: blur(4px); display: flex; align-items: center; justify-content: center; padding: 20px; }
.modal { width: 100%; max-width: 520px; background: var(--panel-2); border: 1px solid var(--line-2); border-radius: var(--radius-lg); padding: 20px; box-shadow: var(--shadow); }
.modal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.modal-head b { font-size: 15px; }
.modal-desc { color: var(--muted); font-size: 13px; line-height: 1.55; margin: 0 0 14px; }
.modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 14px; }
.market-note { background: rgba(8, 12, 22, .4); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; font-size: 12.5px; color: var(--muted); line-height: 1.65; margin-bottom: 16px; }
.market-note b { color: var(--fg); }
.market-note code { font-family: var(--mono); font-size: 11px; color: var(--accent-2); }
.mini-bar { height: 8px; background: rgba(255, 255, 255, .06); border-radius: 999px; overflow: hidden; min-width: 120px; }
.mini-bar i { display: block; height: 100%; border-radius: 999px; }
.chip.tf { background: rgba(45, 212, 191, .14); color: #6ee7db; }
.fab { position: fixed; right: 24px; bottom: 24px; z-index: 90; display: inline-flex; align-items: center; gap: 9px; padding: 12px 18px; border-radius: 999px; background: var(--panel-2); border: 1px solid var(--line-2); color: var(--fg); font-weight: 700; font-size: 13px; cursor: pointer; box-shadow: var(--shadow); font-family: var(--font); }
.fab:hover { border-color: rgba(0, 217, 255, .5); transform: translateY(-1px); }
.fab .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--dim); flex: none; }
.fab .dot.running { background: var(--ok); box-shadow: 0 0 0 4px rgba(47, 212, 139, .18); animation: pulse 1.6s infinite; }
.fab .dot.error { background: var(--err); }
.fab .dot.finished { background: var(--accent); }
.fab .fab-status { font-size: 11px; color: var(--muted); font-weight: 600; }
  </style>
</head>
<body>
  <header class="topbar">
    <a class="brand" href="#" onclick="show('inicio');return false;">
      <span class="logo-mark">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 7-7"/><path d="M14 8h6v6"/></svg>
      </span>
      <span class="brand-text">CareerOps<small>job intelligence</small></span>
    </a>
    <nav class="mainnav">
      <button data-view="inicio" class="active">Início</button>
      <button data-view="overview">Visão geral</button>
      <button data-view="vagas">Vagas</button>
      <button data-view="analisadas">Analisadas</button>
      <button data-view="mercado">Mercado</button>
      <a href="http://localhost:5678" target="_blank" class="nav-link n8n-link" title="Abrir o n8n (orquestração + IA)">n8n <span class="n8n-badge">:5678</span> ↗</a>
    </nav>
    <div class="header-right">
      <div class="settings">
        <button class="ghost" id="settings-btn">Configurações ▾</button>
        <div class="menu hidden" id="settings-menu">
          <button data-view="config">Geral</button>
          <button data-view="perfil">Perfil</button>
          <button data-view="agenda">Agendamento</button>
          <button data-view="workflows">Workflows</button>
          <button data-view="secrets">Segredos</button>
        </div>
      </div>
    </div>
  </header>

    <main>
      <!-- ===================== INÍCIO (landing) ===================== -->
      <section id="view-inicio">
        <div class="hero">
          <div class="hero-badge">pipeline de busca de vagas</div>
          <h1>CareerOps</h1>
          <p class="hero-sub">Sua busca de emprego de forma automática e observável: coleta, filtra, analisa com IA e registra cada vaga. Config no navegador, sem mexer em código.</p>
          <div class="hero-cta">
            <a class="btn" href="#overview" onclick="show('overview')">Ver dashboard</a>
            <a class="btn ghost" href="#mercado" onclick="show('mercado')">Mercado & gaps</a>
            <a class="btn ghost" href="http://localhost:5678" target="_blank">Abrir n8n ↗</a>
            <a class="btn ghost" href="https://github.com/JPCastroBR/CareerOps" target="_blank">GitHub ↗</a>
          </div>
        </div>
        <div class="home-grid">
          <div class="home-card"><div class="hc-icon">🔎</div><h3>Coleta</h3><p>JobSpy (LinkedIn/Indeed), portais de bancos (Gupy/Workday) e InHire — com descoberta automática de empresas.</p></div>
          <div class="home-card"><div class="hc-icon">🧭</div><h3>Filtra</h3><p>Blocklist, dedup normalizado, modalidade (remoto/híbrido) e pré-filtro por título antes de gastar IA.</p></div>
          <div class="home-card"><div class="hc-icon">🤖</div><h3>Analisa</h3><p>IA (DeepSeek/OpenAI) compara cada vaga com seu perfil e gera score 0–100.</p></div>
          <div class="home-card"><div class="hc-icon">📊</div><h3>Registra</h3><p>Aprovadas (>71) vão pra planilha e Telegram; rejeitadas ficam em auditoria. Dashboard vivo acompanha tudo.</p></div>
        </div>
        <div class="home-how">
          <div class="card-title">Como funciona</div>
          <ol class="home-steps">
            <li><b>config.json</b> define o que buscar — países, modalidade, query e fontes.</li>
            <li><b>profile.yaml</b> é a fonte de verdade: skills por nível, cargos-alvo, preferências.</li>
            <li>O <b>scheduler</b> dispara nos horários e envia cada vaga ao <b>n8n</b> (webhook).</li>
            <li>A <b>IA</b> pontua e o resultado cai na planilha + Telegram; o digest resume o dia às 21h.</li>
          </ol>
        </div>
      </section>

      <!-- ===================== VISÃO GERAL ===================== -->
      <section id="view-overview">
        <div class="page-head">
          <div>
            <h1>Visão geral</h1>
            <p>Desempenho da coleta e das vagas analisadas.</p>
          </div>
          <div class="head-actions">
            <div class="period-control">
              <span class="period-label">Período</span>
              <select id="ov-days">
                <option value="3">Últimos 3 dias</option>
                <option value="7">Últimos 7 dias</option>
                <option value="30" selected>Últimos 30 dias</option>
                <option value="90">Últimos 90 dias</option>
                <option value="0">Todos</option>
              </select>
            </div>
            <button class="icon-btn" onclick="tick()" title="Atualizar">⟳</button>
            <button class="ghost" onclick="openManualModal()">＋ Vaga por URL</button>
          </div>
        </div>
        <div class="sources-row">
          <div id="ov-sources" style="display:flex;gap:8px;flex-wrap:wrap"></div>
          <span class="msg" id="ov-status" style="margin-left:auto"></span>
        </div>
        <div class="dash-grid">
          <div class="kpi span-2 c-blue">
            <div class="kpi-top"><span class="kpi-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/></svg></span><span class="k">Pegas</span></div>
            <div class="v" id="k-pegas">—</div><div class="sub" id="k-pegas-sub">no período</div>
          </div>
          <div class="kpi span-2 c-green">
            <div class="kpi-top"><span class="kpi-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/></svg></span><span class="k">Compatíveis</span></div>
            <div class="v" id="k-aprovadas">—</div><div class="sub" id="k-aprovadas-sub">na planilha</div>
          </div>
          <div class="kpi span-2 c-red">
            <div class="kpi-top"><span class="kpi-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/></svg></span><span class="k">Rejeitadas</span></div>
            <div class="v" id="k-rejeitadas">—</div><div class="sub" id="k-rejeitadas-sub">na planilha</div>
          </div>
          <div class="kpi span-2 c-violet">
            <div class="kpi-top"><span class="kpi-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg></span><span class="k">Candidaturas</span></div>
            <div class="v" id="k-aplicadas">—</div><div class="sub" id="k-aplicadas-sub">Apply = Sim</div>
          </div>
          <div class="kpi span-2 c-amber">
            <div class="kpi-top"><span class="kpi-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/></svg></span><span class="k">Entrevistas</span></div>
            <div class="v" id="k-entrevistas">—</div><div class="sub" id="k-entrevistas-sub">ao menos uma</div>
          </div>
          <div class="kpi span-2 c-cyan">
            <div class="kpi-top"><span class="kpi-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 13l4-4"/><path d="M20.5 12a8.5 8.5 0 11-17 0 8.5 8.5 0 0117 0z"/></svg></span><span class="k">Score médio</span></div>
            <div class="v" id="k-score">—</div><div class="sub" id="k-score-sub">média 0–100</div>
          </div>

          <div class="card span-7"><div class="card-title">Funil de coleta</div><div id="funnel"></div>
            <div class="funnel-foot" id="funnel-note"></div></div>
          <div class="card span-5"><div class="card-title">Compatíveis × rejeitadas</div><div id="donut-wrap"></div>
            <div class="insight" id="donut-note"></div></div>
          <div class="card span-5"><div class="card-title">Faixas de score (70–100)</div><div id="chart-bands" class="chart-h"></div>
            <div class="insight" id="bands-note"></div></div>
          <div class="card span-7"><div class="card-title">Origem por plataforma</div><div id="chart-platform" class="chart-h"></div></div>
          <div class="card span-12"><div class="card-title">Evolução no período</div><div id="chart-day" class="chart"></div></div>
          <div class="card span-12">
            <div class="card-title">Status operacional</div>
            <div class="ops-grid">
              <div class="ops-item"><span class="ops-label">Status</span><span class="ops-value" id="ops-status">—</span></div>
              <div class="ops-item"><span class="ops-label">Execução</span><span class="ops-value mono" id="ops-run">—</span></div>
              <div class="ops-item"><span class="ops-label">Fonte</span><span class="ops-value" id="ops-source">—</span></div>
              <div class="ops-item"><span class="ops-label">Atualizado</span><span class="ops-value" id="ops-updated">—</span></div>
              <div class="ops-item"><span class="ops-label">Início</span><span class="ops-value" id="ops-started">—</span></div>
              <div class="ops-item"><span class="ops-label">Erro</span><span class="ops-value" id="ops-error">—</span></div>
            </div>
          </div>
          <details class="block span-12">
            <summary>Vagas sendo vistas (período)</summary>
            <div class="body">
              <p class="msg">Enviada = aceita pelo n8n. Analisada = presente na planilha (IA).</p>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Empresa</th><th>Título</th><th>Local</th><th>Fonte</th><th>Enviada (n8n)</th><th>Enviado em</th><th>Analisada (IA)</th><th>Ação</th></tr></thead>
                  <tbody id="seen-body"><tr><td colspan="8" class="empty">aguardando execução…</td></tr></tbody>
                </table>
              </div>
            </div>
          </details>
          <details class="block span-12">
            <summary>Eventos recentes (debug)</summary>
            <div class="body"><div id="events" style="max-height:260px;overflow:auto"></div></div>
          </details>
        </div>
      </section>

      <!-- ===================== COLETA (LIVE) ===================== -->
      <section id="view-coleta" class="hidden">
        <div class="page-head">
          <div>
            <h1>Coleta</h1>
            <p>Acompanhe a execução em tempo real: fontes, filtros e vagas descartadas (com o motivo).</p>
          </div>
          <div class="head-actions">
            <button class="ghost" onclick="loadCollect()" title="Atualizar agora">⟳</button>
            <button class="ghost" onclick="clearCollect()" title="Apaga eventos e descartadas (faça a cada 2-3 dias)">Limpar logs</button>
            <span class="msg" id="collect-msg"></span>
          </div>
        </div>
        <div class="dash-grid">
          <div class="card span-12">
            <div class="card-title">Status da execução</div>
            <div class="ops-grid">
              <div class="ops-item"><span class="ops-label">Status</span><span class="ops-value" id="coll-status">—</span></div>
              <div class="ops-item"><span class="ops-label">Execução</span><span class="ops-value mono" id="coll-run">—</span></div>
              <div class="ops-item"><span class="ops-label">Fonte atual</span><span class="ops-value" id="coll-source">—</span></div>
              <div class="ops-item"><span class="ops-label">Início</span><span class="ops-value" id="coll-started">—</span></div>
              <div class="ops-item"><span class="ops-label">Atualizado</span><span class="ops-value" id="coll-updated">—</span></div>
              <div class="ops-item"><span class="ops-label">Vistas</span><span class="ops-value" id="coll-seen">—</span></div>
              <div class="ops-item"><span class="ops-label">Enviadas</span><span class="ops-value" id="coll-sent">—</span></div>
              <div class="ops-item"><span class="ops-label">Filtradas</span><span class="ops-value" id="coll-filtered">—</span></div>
            </div>
            <div class="sources-row" style="margin-top:14px" id="coll-sources"></div>
          </div>

          <div class="card span-6">
            <div class="card-title">Filtros aplicados (o que cada estágio descarta)</div>
            <div class="table-wrap"><table>
              <thead><tr><th>Estágio</th><th>Descartadas</th><th>Explicação</th></tr></thead>
              <tbody id="coll-stages"></tbody>
            </table></div>
          </div>

          <div class="card span-6">
            <div class="card-title">Vagas descartadas (últimas)</div>
            <div class="table-wrap"><table>
              <thead><tr><th>Estágio</th><th>Empresa</th><th>Título</th><th>Motivo</th></tr></thead>
              <tbody id="coll-discarded"></tbody>
            </table></div>
          </div>

          <details class="block span-12">
            <summary>Eventos recentes (debug)</summary>
            <div class="body"><div id="coll-events" style="max-height:320px;overflow:auto"></div></div>
          </details>
        </div>
      </section>

      <!-- ===================== VAGAS (SHEETS) ===================== -->
      <section id="view-vagas" class="hidden">
        <div class="page-head">
          <h1>Vagas (planilha)</h1>
          <p>Dados da planilha Jobsearch-spy, vindos da análise da IA.</p>
        </div>
        <div class="toolbar">
          <div class="seg" id="sheet-seg">
            <button data-sheet="all" class="active">Todos</button>
            <button data-sheet="jobs">Compatíveis</button>
            <button data-sheet="rejected">Rejeitadas</button>
          </div>
          <select id="sheet-days" style="width:auto">
            <option value="0">Todos os dias</option>
            <option value="1">Hoje</option>
            <option value="3">Últimos 3 dias</option>
            <option value="7">Últimos 7 dias</option>
            <option value="30">Últimos 30 dias</option>
          </select>
          <select id="sheet-entrevista" style="width:auto" title="Filtrar por situação da entrevista">
            <option value="">Entrevista: todas</option>
          </select>
          <input type="search" id="sheet-q" placeholder="Filtrar por título ou empresa…" autocomplete="off" style="max-width:280px" />
          <button class="ghost" onclick="loadSheets(true)">Atualizar</button>
          <span class="msg" id="sheet-msg"></span>
        </div>
        <div class="toolbar">
          <button class="ghost" onclick="openManualModal()">＋ Adicionar vaga por URL</button>
        </div>
        <div class="cards" id="sheet-cards"></div>
        <div class="table-wrap">
          <table>
            <thead id="sheet-head"></thead>
            <tbody id="sheet-body"><tr><td class="empty">carregando…</td></tr></tbody>
          </table>
        </div>
        <div class="pager" id="sheet-pager"></div>
      </section>

      <!-- ===================== ANALISADAS (CSV LOCAL) ===================== -->
      <section id="view-analisadas" class="hidden">
        <div class="page-head">
          <h1>Analisadas (local)</h1>
          <p>Registro local gerado pelo coletor. <a href="/api/jobs.csv">Baixar CSV</a></p>
        </div>
        <div class="toolbar"><input type="search" id="jobs-q" placeholder="Filtrar por título ou empresa…" autocomplete="off" style="max-width:280px" /><button class="ghost" onclick="loadJobsCSV()">Atualizar</button><span class="msg" id="jobs-msg"></span></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Data</th><th>Empresa</th><th>Título</th><th>Local</th><th>Fonte</th><th>Score Local</th><th>Score IA</th><th></th></tr></thead>
            <tbody id="jobs-body"><tr><td colspan="8" class="empty">nenhuma vaga registrada ainda</td></tr></tbody>
          </table>
        </div>
        <div class="pager" id="jobs-pager"></div>
      </section>

      <!-- ===================== MERCADO ===================== -->
      <section id="view-mercado" class="hidden">
        <div class="page-head">
          <div>
            <h1>Mercado & Gaps</h1>
            <p>O que as vagas pedem × o que seu perfil cobre — onde priorizar estudo.</p>
          </div>
          <div class="head-actions">
            <div class="period-control">
              <span class="period-label">Período</span>
              <select id="gap-days">
                <option value="0" selected>Todos</option>
                <option value="7">Últimos 7 dias</option>
                <option value="30">Últimos 30 dias</option>
                <option value="90">Últimos 90 dias</option>
              </select>
            </div>
            <button class="icon-btn" onclick="loadGaps()" title="Atualizar">⟳</button>
          </div>
        </div>
        <div class="market-note">
          <b>De onde vem isto?</b> Cada vaga na planilha tem o campo <b>SKILLS_DA_VAGA</b>, preenchido pela IA — o que a vaga pede, classificado em
          <span class="chip yes">domina</span> (evidência forte no perfil) · <span class="chip tag">parcial</span> (conhecimento/estudo) · <span class="chip no">faltando</span> (sem evidência) e <span class="chip tf">transferível</span> (outra skill do mesmo grupo você já domina — ex.: PostgreSQL forte transfere conceitos para MySQL).
          Cruzamos com o <b>profile.yaml</b> (apelidos via <code>skill_aliases</code>). <b>"Skills a melhorar"</b> = pedidas em muitas vagas mas com pouca/nenhuma evidência sua — priorize por frequência.
        </div>
        <div class="cards" id="gap-cards"></div>
        <div class="dash-grid">
          <div class="card span-6">
            <div class="card-title">Mais pedidas pelo mercado</div>
            <div class="table-wrap"><table>
              <thead><tr><th>Skill</th><th>Pedida em</th><th>Demanda</th><th>Situação</th></tr></thead>
              <tbody id="gap-demand"></tbody>
            </table></div>
          </div>
          <div class="card span-6">
            <div class="card-title">Gaps prioritários (o que melhorar)</div>
            <div class="table-wrap"><table>
              <thead><tr><th>Skill</th><th>Pedida em</th><th>Sem evidência</th><th>Prioridade</th></tr></thead>
              <tbody id="gap-missing"></tbody>
            </table></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">Priorização (IA)</div>
          <p class="msg" style="margin:0 0 10px">Envia esta análise para a IA (via n8n) e recebe o que priorizar para estudar.</p>
          <button class="ghost" onclick="analyzeGaps()">Pedir análise</button>
          <div id="gap-ai-out" style="white-space:pre-wrap;font-size:13px;color:var(--fg);margin-top:12px;line-height:1.6"></div>
        </div>
        <p class="msg" id="gap-msg"></p>
      </section>

      <!-- ===================== CONFIGURAÇÃO ===================== -->
      <section id="view-config" class="hidden">
        <div class="page-head">
          <h1>Configuração</h1>
          <p>O que buscar, onde buscar e o que descartar. Campos com exemplos; a prévia mostra o arquivo final.</p>
        </div>
        <div class="toolbar">
          <div class="seg" id="cfg-mode">
            <button data-mode="guided" class="active">Guiado</button>
            <button data-mode="raw">Texto puro (JSON)</button>
          </div>
          <button class="ghost" onclick="reloadGuided()">Recarregar</button>
          <button class="ghost" onclick="expandAll(true)">Expandir</button>
          <button class="ghost" onclick="expandAll(false)">Recolher</button>
          <span class="msg" id="cfg-msg"></span>
        </div>
        <div id="cfg-guided">
          <div id="cfg-form"></div>
          <details class="block">
            <summary>Prévia do config.json</summary>
            <div class="body"><pre class="preview" id="cfg-preview"></pre></div>
          </details>
          <div class="actions">
            <button class="act" onclick="saveGuided()">Salvar configuração</button>
            <span class="msg" id="cfg-save-msg"></span>
          </div>
        </div>
        <div id="cfg-raw" class="hidden">
          <textarea id="cfg-raw-text" style="min-height:520px"></textarea>
          <div class="actions">
            <button class="act" onclick="saveRaw('config.json','cfg-raw-text','cfg-raw-msg')">Salvar JSON</button>
            <span class="msg" id="cfg-raw-msg"></span>
          </div>
        </div>
        <details class="block">
          <summary>Lista de empresas InHire (texto puro)</summary>
          <div class="body">
            <p class="msg">Uma empresa (slug) por linha. Ex.: nubank, magalu, xp</p>
            <textarea id="seed-text" style="min-height:260px"></textarea>
            <div class="actions">
              <button class="act" onclick="saveRaw('data/inhire_seed.txt','seed-text','seed-msg')">Salvar lista</button>
              <span class="msg" id="seed-msg"></span>
            </div>
          </div>
        </details>
      </section>

      <!-- ===================== PERFIL ===================== -->
      <section id="view-perfil" class="hidden">
        <div class="page-head">
          <h1>Perfil</h1>
          <p>Seu currículo estruturado. Edite objetivo, cargos, experiências e tecnologias (ex.: VMware).</p>
        </div>
        <div class="toolbar">
          <div class="seg" id="profile-mode">
            <button data-mode="guided" class="active">Guiado</button>
            <button data-mode="raw">Texto puro (YAML)</button>
          </div>
          <button class="ghost" onclick="reloadGuided()">Recarregar</button>
          <button class="ghost" onclick="expandAll(true)">Expandir</button>
          <button class="ghost" onclick="expandAll(false)">Recolher</button>
          <span class="msg" id="profile-msg"></span>
        </div>
        <div id="profile-guided">
          <div id="profile-form"></div>
          <details class="block">
            <summary>Prévia do profile.yaml</summary>
            <div class="body"><pre class="preview" id="profile-preview"></pre></div>
          </details>
          <div class="actions">
            <button class="act" onclick="saveGuided()">Salvar perfil</button>
            <span class="msg" id="profile-save-msg"></span>
          </div>
        </div>
        <div id="profile-raw" class="hidden">
          <textarea id="profile-raw-text" style="min-height:520px"></textarea>
          <div class="actions">
            <button class="act" onclick="saveRaw('profile.yaml','profile-raw-text','profile-raw-msg')">Salvar YAML</button>
            <span class="msg" id="profile-raw-msg"></span>
          </div>
        </div>
        <div class="section">
          <div class="section-head"><span class="section-num">9</span><h3>Versões do perfil</h3></div>
          <p class="msg" style="margin:0 0 12px">Backups automáticos. Sempre que o perfil é salvo (IA ou manual), a versão anterior é arquivada aqui.</p>
          <div class="table-wrap"><table>
            <thead><tr><th>Versão</th><th>Data</th><th>Tamanho</th><th>Ação</th></tr></thead>
            <tbody id="pf-versions"><tr><td colspan="4" class="empty">nenhuma versão ainda</td></tr></tbody>
          </table></div>
          <div class="actions">
            <button class="ghost" onclick="loadProfileVersions()">Atualizar</button>
            <span class="msg" id="pf-versions-msg"></span>
          </div>
        </div>
      </section>

      <!-- ===================== AGENDA ===================== -->
      <section id="view-agenda" class="hidden">
        <div class="page-head">
          <h1>Agenda</h1>
          <p>O container <span class="mono">scheduler</span> dispara sozinho nos horários abaixo — não precisa de cron.</p>
        </div>
        <div class="section" style="max-width:820px">
          <div class="field">
            <div class="field-label"><div class="name">Ativo</div><div class="help">Liga/desliga os disparos automáticos.</div></div>
            <div class="field-input"><div class="checks"><label><input type="checkbox" id="sch-enabled" /> Ativo</label></div></div>
          </div>
          <div class="field">
            <div class="field-label"><div class="name">Horários</div><div class="help">Separe por vírgula (24h). Ex.: 06:00, 11:00, 16:00, 21:00</div></div>
            <div class="field-input"><input type="text" id="sch-times" placeholder="06:00, 11:00, 16:00, 21:00" /></div>
          </div>
          <div class="field">
            <div class="field-label"><div class="name">Digest (Telegram)</div><div class="help">Horário do resumo diário. Ex.: 21:00</div></div>
            <div class="field-input"><input type="text" id="sch-digest" placeholder="21:00" /></div>
          </div>
          <div class="field">
            <div class="field-label"><div class="name">Tolerância (min)</div><div class="help">Se o PC estava desligado, ainda roda um horário atrasado dentro desse limite.</div></div>
            <div class="field-input"><input type="number" id="sch-grace" placeholder="30" /></div>
          </div>
          <div class="field">
            <div class="field-label"><div class="name">Fonte por frequência</div><div class="help">LinkedIn/Indeed a cada execução; Gupy/Workday/InHire 1x por dia (menos atualização). Aplicado só às fontes habilitadas em Config → Fontes.</div></div>
            <div class="field-input" id="sch-sources"></div>
          </div>
          <div class="actions">
            <button class="act" onclick="saveSchedule()">Salvar agenda</button>
            <span class="msg" id="sch-msg"></span>
          </div>
        </div>
        <h2 class="section-title">Próximos disparos</h2>
        <div class="card" id="sch-next"></div>
      </section>
      <section id="view-workflows" class="hidden">
        <div class="page-head">
          <h1>Workflows do n8n</h1>
          <p>Arquivos em <span class="mono">n8n/workflows</span>. Importar aplica ao n8n e reativa os ativos.</p>
        </div>
        <div class="toolbar">
          <button class="act" onclick="importWorkflows()">Importar para o n8n</button>
          <button class="ghost" onclick="loadWorkflows()">Atualizar</button>
          <span class="msg" id="wf-status"></span>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Nome</th><th>ID</th><th>Ativo</th><th>Tamanho</th><th>Atualizado</th><th>Ação</th></tr></thead>
            <tbody id="wf-body"><tr><td colspan="6" class="empty">carregando…</td></tr></tbody>
          </table>
        </div>
        <details class="block">
          <summary>Prévia do JSON</summary>
          <div class="body"><pre id="wf-preview" class="preview"></pre></div>
        </details>
      </section>
      <section id="view-secrets" class="hidden">
        <div class="page-head">
          <h1>Segredos</h1>
          <p>Chaves e credenciais locais em <span class="mono">secrets/</span> (fora do Git, permissão 600, exibidas mascaradas).</p>
        </div>
        <div class="section">
          <h3>Credenciais (para configurar no n8n)</h3>
          <p class="msg">Salve usuário/chave de cada provedor para não perder. As chaves ficam mascaradas; use “Copiar” para levar ao n8n.</p>
          <div id="cred-form">
            <input type="hidden" id="cred-id" />
            <div class="field"><div class="field-label"><div class="name">Nome</div><div class="help">Ex.: DeepSeek</div></div><div class="field-input"><input id="cred-name" type="text" placeholder="DeepSeek" /></div></div>
            <div class="field"><div class="field-label"><div class="name">Chave</div><div class="help">Mascarada. Em branco mantém a atual ao editar.</div></div><div class="field-input"><input id="cred-secret" type="password" placeholder="cole a chave" /></div></div>
            <div class="field"><div class="field-label"><div class="name">Observação</div><div class="help">Ex.: teste</div></div><div class="field-input"><input id="cred-notes" type="text" placeholder="teste" /></div></div>
          </div>
          <div class="actions">
            <button class="act" onclick="saveCredential()">Salvar credencial</button>
            <button class="ghost" onclick="clearCredForm()">Limpar</button>
            <span class="msg" id="cred-msg"></span>
          </div>
          <div id="cred-list" class="chart-h" style="margin-top:16px"></div>
        </div>
        <div class="section">
          <h3>Google Sheets</h3>
          <p class="msg">Service account e planilha usados para ler/gravar vagas.</p>
          <div class="field"><div class="field-label"><div class="name">Email (client_email)</div></div><div class="field-input"><input id="google-email" type="text" placeholder="n8n-bot@projeto.iam.gserviceaccount.com" /></div></div>
          <div class="field"><div class="field-label"><div class="name">Private key (PEM)</div><div class="help">Em branco mantém a atual.</div></div><div class="field-input"><textarea id="google-key" style="min-height:120px" placeholder="-----BEGIN PRIVATE KEY-----"></textarea></div></div>
          <div class="field"><div class="field-label"><div class="name">Spreadsheet ID</div></div><div class="field-input"><input id="google-spreadsheet" type="text" /></div></div>
          <div class="field"><div class="field-label"><div class="name">Aba compatível</div><div class="help">Ex.: Jobs</div></div><div class="field-input"><input id="google-jobs" type="text" /></div></div>
          <div class="field"><div class="field-label"><div class="name">Aba incompatível</div><div class="help">Ex.: Rejected-Jobs</div></div><div class="field-input"><input id="google-rejected" type="text" /></div></div>
          <div id="google-info" class="msg" style="margin:8px 0"></div>
          <div class="actions">
            <button class="act" onclick="saveGoogle()">Salvar</button>
            <button class="ghost" onclick="testSheets()">Testar conexão</button>
            <button class="ghost" id="google-key-btn" onclick="toggleGoogleKey()">Mostrar private key</button>
            <button class="ghost" onclick="copyPrivateKey()">Copiar private key</button>
            <span class="msg" id="google-msg"></span>
          </div>
        </div>
        <div class="section">
          <h3>Segurança</h3>
          <div id="security-list" class="chart-h"></div>
        </div>
      </section>
    </main>

  <div class="modal-backdrop hidden" id="manual-modal" onclick="if(event.target===this)closeManualModal()">
    <div class="modal">
      <div class="modal-head"><b>Adicionar vaga por URL</b><button class="ghost" onclick="closeManualModal()">✕</button></div>
      <p class="modal-desc">Cole o link de uma vaga que achou interessante. O CareerOps <b>baixa a página</b>, extrai título e descrição e envia para a <b>IA</b> analisar (score + skills). O resultado entra no fluxo normal da planilha: <b>Jobs</b> se score &gt; 71, senão <b>Rejected-Jobs</b>.</p>
      <input type="url" id="manual-url" placeholder="https://…" autocomplete="off" spellcheck="false" />
      <div class="modal-actions">
        <button class="ghost" onclick="closeManualModal()">Cancelar</button>
        <button class="act" onclick="manualAdd()">Enviar para análise</button>
      </div>
      <span class="msg" id="manual-msg"></span>
    </div>
  </div>

  <footer class="app-footer">
    <span><b>CareerOps</b> · pipeline de busca de vagas</span>
    <a href="https://github.com/JPCastroBR/CareerOps" target="_blank">github.com/JPCastroBR/CareerOps ↗</a>
  </footer>

  <button class="fab" id="fab" onclick="show('coleta')" title="Coleta ao vivo (clique para abrir)">
    <span class="dot idle" id="fab-dot"></span>
    <span>Coleta</span>
    <span class="fab-status" id="fab-status">ocioso</span>
  </button>

  <button class="fab hidden" id="pf-fab" onclick="openProfileAIModal()" title="Preencher perfil com IA" style="bottom:88px">
    <span>✨</span><span>Preencher com IA</span>
  </button>

  <div class="modal-backdrop hidden" id="pf-modal" onclick="if(event.target===this)closeProfileAIModal()">
    <div class="modal" style="max-width:680px">
      <div class="modal-head"><b>Preencher perfil com IA</b><button class="ghost" onclick="closeProfileAIModal()">✕</button></div>
      <p class="modal-desc">A IA monta o <span class="mono">profile.yaml</span> com base no currículo, LinkedIn e respostas. <b>Ao concluir, será feito um backup do perfil anterior e ele será substituído pelo gerado.</b></p>
      <input type="text" id="pf-linkedin" placeholder="LinkedIn (URL, opcional)" />
      <textarea id="pf-curriculo" rows="6" placeholder="Cole o texto do currículo (opcional)…" style="margin-top:10px"></textarea>
      <textarea id="pf-answers" rows="12" style="margin-top:10px"></textarea>
      <div class="modal-actions">
        <button class="ghost" onclick="closeProfileAIModal()">Cancelar</button>
        <button class="act" onclick="fillProfileAI()">Gerar perfil</button>
      </div>
      <span class="msg" id="pf-msg"></span>
    </div>
  </div>

  <div class="modal-backdrop hidden" id="pf-view-modal" onclick="if(event.target===this)closeProfileView()">
    <div class="modal" style="max-width:720px">
      <div class="modal-head"><b id="pf-view-title">Versão</b><button class="ghost" onclick="closeProfileView()">✕</button></div>
      <pre class="preview" id="pf-view-content" style="min-height:240px"></pre>
    </div>
  </div>

  <div class="modal-backdrop hidden" id="scan-modal" onclick="if(event.target===this)closeScanModal()">
    <div class="modal">
      <div class="modal-head"><b>Disparar scraping</b><button class="ghost" onclick="closeScanModal()">✕</button></div>
      <p class="modal-desc" id="scan-desc">Iniciar a busca manualmente?</p>
      <div class="modal-actions">
        <button class="ghost" onclick="closeScanModal()">Cancelar</button>
        <button class="act" onclick="triggerScan()">Disparar</button>
      </div>
      <span class="msg" id="scan-msg"></span>
    </div>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const j = async (url, opts) => (await fetch(url, opts)).json();
    function fmtDate(iso) {
      if (!iso) return "—";
      const d = new Date(iso);
      if (isNaN(d)) return esc(iso);
      return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
    }
    async function resendJob(url, title) {
      if (!confirm('Reenviar "' + (title || url) + '" para o n8n?')) return;
      const r = await fetch("/api/resend", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ url }) });
      const jr = await r.json();
      toast(jr.ok ? ("Reenviado ✓ (" + (jr.stored ? "payload completo" : "sem descrição armazenada") + ")") : ("Falha: " + (jr.error || jr.status_code)));
      tick();
    }
    function openManualModal() { $("manual-url").value = ""; $("manual-msg").textContent = ""; $("manual-modal").classList.remove("hidden"); setTimeout(() => $("manual-url").focus(), 30); }
    function closeManualModal() { $("manual-modal").classList.add("hidden"); }
    const PROFILE_QUESTIONS = `1. Cargo atual e empresa?
2. Anos de experiência na sua área?
3. Tecnologias com uso profissional (hands-on) — liste e diga onde usou:
4. Tecnologias que estudou ou tem certificação, mas sem prática profissional:
5. Certificações (nome + ano):
6. Idiomas e nível (ex.: inglês B2):
7. Cidade/estado + disponibilidade (remoto / híbrido / presencial):
8. Cargos-alvo que busca (ex.: Engenheiro de Software Sênior):
9. Principais atividades do dia a dia (top 5):
10. O que NÃO quer fazer (ex.: suporte, vendas):`;
    function openProfileAIModal() {
      if (!$("pf-answers").value.trim()) $("pf-answers").value = PROFILE_QUESTIONS;
      $("pf-msg").textContent = "";
      $("pf-modal").classList.remove("hidden");
    }
    function closeProfileAIModal() { $("pf-modal").classList.add("hidden"); }
    function closeProfileView() { $("pf-view-modal").classList.add("hidden"); }
    async function fillProfileAI() {
      $("pf-msg").textContent = "gerando… (pode levar ~30s)";
      const payload = {
        linkedin: $("pf-linkedin").value.trim(),
        curriculo: $("pf-curriculo").value.trim(),
        answers: $("pf-answers").value.trim(),
      };
      try {
        const r = await fetch("/api/profile/ai", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });
        const jr = await r.json();
        if (jr.ok) {
          $("pf-msg").textContent = "perfil preenchido ✓";
          toast("Perfil gerado pela IA ✓");
          closeProfileAIModal();
          loadGuided("profile.yaml");
          loadProfileVersions();
        } else {
          $("pf-msg").textContent = "erro: " + (jr.error || "?");
          toast("Falha: " + (jr.error || "?"));
        }
      } catch (e) {
        $("pf-msg").textContent = "erro de rede";
      }
    }
    async function loadProfileVersions() {
      const box = $("pf-versions");
      if (!box) return;
      try {
        const r = await j("/api/profile/versions");
        box.innerHTML = (r.versions || []).map((v) =>
          `<tr><td class="mono">${esc(v.name)}</td><td class="mono">${fmtShort(v.mtime)}</td><td class="mono">${(v.size / 1024).toFixed(1)} KB</td><td><button class="ghost" onclick="viewProfileVersion('${esc(v.name)}')">Ver</button> <button class="ghost" onclick="restoreProfileVersion('${esc(v.name)}')">Restaurar</button></td></tr>`
        ).join("") || `<tr><td colspan="4" class="empty">nenhuma versão ainda</td></tr>`;
      } catch (e) {}
    }
    async function viewProfileVersion(name) {
      const r = await j("/api/profile/version?name=" + encodeURIComponent(name));
      if (!r.ok) { toast("erro: " + r.error); return; }
      $("pf-view-title").textContent = name;
      $("pf-view-content").textContent = r.content;
      $("pf-view-modal").classList.remove("hidden");
    }
    async function restoreProfileVersion(name) {
      if (!confirm("Restaurar esta versão? O perfil atual será arquivado como backup.")) return;
      const r = await fetch("/api/profile/restore", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name }) });
      const jr = await r.json();
      toast(jr.ok ? "versão restaurada ✓" : ("erro: " + (jr.error || "?")));
      if (jr.ok) { loadGuided("profile.yaml"); loadProfileVersions(); }
    }
    let scanSource = "";
    function openScanModal(source) {
      scanSource = source;
      $("scan-desc").textContent = "Deseja disparar o scraping de " + (SOURCE_LABELS[source] || source) + " agora?";
      $("scan-msg").textContent = "";
      $("scan-modal").classList.remove("hidden");
    }
    function closeScanModal() { $("scan-modal").classList.add("hidden"); }
    async function triggerScan() {
      const source = scanSource;
      $("scan-msg").textContent = "iniciando…";
      try {
        const r = await fetch("/api/scan", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ source }) });
        const jr = await r.json();
        $("scan-msg").textContent = jr.ok ? ("disparado ✓ (" + (jr.sources || [source]).join(", ") + ")") : ("erro: " + (jr.error || "?"));
        toast(jr.ok ? "Scraping iniciado ✓" : ("Falha: " + (jr.error || "?")));
        if (jr.ok) { closeScanModal(); show("coleta"); }
      } catch (e) {
        $("scan-msg").textContent = "erro de rede";
      }
    }
    async function manualAdd() {
      const url = ($("manual-url").value || "").trim();
      if (!url) { toast("Cole a URL da vaga primeiro"); return; }
      $("manual-msg").textContent = "validando…";
      try {
        const r = await fetch("/api/manual", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ url }) });
        const jr = await r.json();
        $("manual-msg").textContent = jr.ok ? ("enviada ✓ — " + (jr.title || url)) : ("falha: " + (jr.error || jr.status_code));
        toast(jr.ok ? "Vaga enviada para análise ✓" : ("Falha: " + (jr.error || jr.status_code)));
        if (jr.ok) closeManualModal();
      } catch (e) {
        $("manual-msg").textContent = "erro de rede";
      }
    }
    let view = "inicio", sheetTab = "all", cfgMode = "guided", sheetPage = 0, jobsPage = 0;

    function show(v) {
      view = v;
      try { history.replaceState(null, "", "#" + v); } catch (e) {}
      document.querySelectorAll(".mainnav button, #settings-menu button").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
      $("settings-btn").classList.toggle("active", ["config", "perfil", "agenda", "workflows", "secrets"].includes(v));
      $("settings-menu").classList.add("hidden");
      document.querySelectorAll("main > section").forEach((s) => s.classList.toggle("hidden", s.id !== "view-" + v));
      if (v === "overview") tick();
      if (v === "coleta") loadCollect();
      if (v === "vagas") { const sq = $("sheet-q"); if (sq) sq.value = ""; const se = $("sheet-entrevista"); if (se) se.value = ""; sheetPage = 0; loadSheets(); }
      if (v === "analisadas") { const jq = $("jobs-q"); if (jq) jq.value = ""; jobsPage = 0; loadJobsCSV(); }
      if (v === "config") loadGuided("config.json");
      if (v === "perfil") { loadGuided("profile.yaml"); loadProfileVersions(); }
      if (v === "agenda") loadSchedule();
      if (v === "secrets") loadSecrets();
      if (v === "workflows") loadWorkflows();
      if (v === "mercado") loadGaps();
      $("pf-fab").classList.toggle("hidden", v !== "perfil");
    }
    document.querySelectorAll(".mainnav button, #settings-menu button").forEach((b) => (b.onclick = () => show(b.dataset.view)));
    $("settings-btn").onclick = (e) => { e.stopPropagation(); $("settings-menu").classList.toggle("hidden"); };
    document.addEventListener("click", (e) => { if (!e.target.closest(".settings")) $("settings-menu").classList.add("hidden"); });
    document.addEventListener("click", (e) => {
      const b = e.target.closest("[data-resend]");
      if (b) resendJob(b.dataset.resend, b.dataset.title);
    });
    document.addEventListener("click", (e) => {
      const chip = e.target.closest("#ov-sources .source-chip");
      if (chip && chip.dataset.source) openScanModal(chip.dataset.source);
    });

    // ---------------------------------------------------------------- overview
    async function tick() {
      try {
        const s = await j("/api/state");
        updateStatus(s);
        const days = $("ov-days").value || "30";
        const data = await j(`/api/overview?days=${days}`);
        renderOverview(data);
        const seen = await j(`/api/seen?days=${days}&limit=100`);
        $("seen-body").innerHTML = (seen.rows || []).map((r) =>
          `<tr><td>${esc(r.company)}</td><td>${r.url ? `<a href="${esc(r.url)}" target="_blank">${esc(r.title)}</a>` : esc(r.title)}</td><td>${esc(r.location)}</td><td class="mono">${esc(r.source)}</td><td>${r.sent ? '<span class="chip yes">Sim</span>' : '<span class="chip no">Não</span>'}</td><td class="mono">${fmtDate(r.sent_at)}</td><td>${r.analyzed ? '<span class="chip yes">Sim</span>' : '<span class="chip no">Não</span>'}</td><td>${r.url ? `<button class="ghost" data-resend="${esc(r.url)}" data-title="${esc(r.title)}">Reenviar</button>` : ""}</td></tr>`
        ).join("") || `<tr><td colspan="8" class="empty">nada ainda — rode a coleta ou <button class="ghost" onclick="openManualModal()">＋ adicione uma vaga por URL</button></td></tr>`;
        const ev = await j("/api/events?limit=40");
        $("events").innerHTML = ev.slice().reverse().map((e) =>
          `<div class="mono" style="padding:4px 0;border-bottom:1px dashed #20242e"><span class="chip tag">${esc(e.kind)}</span> ${esc(e.ts)} ${esc(e.source || e.stage || e.status || "")} ${e.found !== undefined ? "found=" + e.found : ""} ${e.discarded !== undefined ? "discarded=" + e.discarded : ""}</div>`
        ).join("") || `<div class="empty">sem eventos</div>`;
      } catch (e) {}
    }
    function fmtShort(iso) {
      if (!iso) return "—";
      const d = new Date(iso);
      if (isNaN(d)) return esc(iso);
      return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    }
    function updateStatus(s) {
      const status = s.status || "idle";
      const labels = { running: "coletando", finished: "concluída", error: "erro", idle: "ocioso" };
      const label = labels[status] || status;
      const fabDot = $("fab-dot"), fabStatus = $("fab-status");
      if (fabDot) fabDot.className = "dot " + status;
      if (fabStatus) fabStatus.textContent = label;
      const put = (id, value, cls) => {
        const el = $(id); if (!el) return;
        el.textContent = value || "—";
        if (cls !== undefined) el.className = "ops-value" + (cls ? " " + cls : "");
      };
      put("ops-status", label, status);
      put("ops-run", s.run_id, "mono");
      put("ops-source", s.current_source || (s.meta && s.meta.source), "");
      put("ops-updated", s.updated_at ? fmtShort(s.updated_at) : "", "");
      put("ops-started", s.started_at ? fmtShort(s.started_at) : "", "");
      put("ops-error", s.last_error, s.last_error ? "error" : "");
    }
    async function pollRun() {
      try { updateStatus(await j("/api/state")); } catch (e) {}
    }
    setInterval(pollRun, 4000);
    async function loadCollect() {
      try {
        const d = await j("/api/collect");
        const s = d.state || {};
        const labels = { running: "coletando", finished: "concluída", error: "erro", idle: "ocioso" };
        const put = (id, v, cls) => { const el = $(id); if (!el) return; el.textContent = v || "—"; el.className = "ops-value" + (cls ? " " + cls : ""); };
        put("coll-status", labels[s.status] || s.status || "ocioso", s.status);
        put("coll-run", s.run_id, "mono");
        put("coll-source", s.current_source || (s.meta && s.meta.source), "");
        put("coll-started", s.started_at ? fmtShort(s.started_at) : "", "");
        put("coll-updated", s.updated_at ? fmtShort(s.updated_at) : "", "");
        const c = s.counters || {};
        put("coll-seen", c.seen, "");
        put("coll-sent", c.sent, "");
        put("coll-filtered", c.filtered, "");
        const sources = s.sources || {};
        $("coll-sources").innerHTML = Object.keys(sources).map((k) => {
          const v = sources[k] || {};
          const done = v.finished_at != null;
          return `<span class="source-chip">${esc(k)}${done ? " · " + (v.found || 0) : " · rodando…"}</span>`;
        }).join("") || `<span class="msg">nenhuma fonte registrada ainda</span>`;
        $("coll-stages").innerHTML = (d.stages || []).map((st) =>
          `<tr><td><b>${esc(st.label)}</b><div class="mono" style="font-size:10px;color:var(--dim)">${esc(st.stage)}</div></td><td class="mono">${st.discarded || 0}</td><td class="msg">${esc(st.desc)}</td></tr>`
        ).join("") || `<tr><td colspan="3" class="empty">nenhum filtro registrado</td></tr>`;
        $("coll-discarded").innerHTML = (d.discarded || []).map((r) =>
          `<tr><td><span class="chip tag">${esc(r.stage_label || r.stage)}</span></td><td>${esc(r.company)}</td><td>${r.url ? `<a href="${esc(r.url)}" target="_blank">${esc(r.title)}</a>` : esc(r.title)}</td><td class="msg">${esc(r.reason)}</td></tr>`
        ).join("") || `<tr><td colspan="4" class="empty">nenhuma vaga descartada registrada ainda</td></tr>`;
        $("coll-events").innerHTML = (d.events || []).slice().reverse().map((e) =>
          `<div class="mono" style="padding:4px 0;border-bottom:1px dashed #20242e"><span class="chip tag">${esc(e.kind)}</span> ${esc(e.ts)} ${esc(e.source || e.stage || e.status || "")} ${e.found !== undefined ? "found=" + e.found : ""} ${e.discarded !== undefined ? "discarded=" + e.discarded : ""}</div>`
        ).join("") || `<div class="empty">sem eventos</div>`;
      } catch (e) {}
    }
    async function clearCollect() {
      if (!confirm("Apagar eventos e vagas descartadas registradas?")) return;
      const r = await fetch("/api/collect/clear", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
      const jr = await r.json();
      toast(jr.ok ? "logs limpos ✓" : ("erro: " + (jr.error || "?")));
      loadCollect();
    }
    setInterval(() => { if (view === "coleta") loadCollect(); }, 5000);
    const SOURCE_LABELS = { linkedin: "LinkedIn", indeed: "Indeed", jobspy: "JobSpy", gupy: "Gupy", workday: "Workday", inhire: "InHire", banks: "Gupy/Workday", other: "Outros" };
    function renderOverview(d) {
      $("ov-status").textContent = d.ok ? "dados da planilha" : "erro: " + (d.error || "?");
      $("ov-sources").innerHTML = (d.sources || []).map((s) => `<span class="source-chip" data-source="${esc(s)}" style="cursor:pointer" title="Clique para disparar o scraping de ${esc(SOURCE_LABELS[s] || s)}">${esc(SOURCE_LABELS[s] || s)}</span>`).join("") || `<span class="msg">nenhuma fonte ativa</span>`;
      const pegas = d.pegas || 0;
      const pctOf = (v) => (pegas ? Math.round((v / pegas) * 100) : 0);
      const set = (id, v) => { const el = $(id); if (el) el.textContent = (v === null || v === undefined) ? "—" : v; };
      set("k-pegas", pegas);
      set("k-aprovadas", d.aprovadas || 0);
      set("k-rejeitadas", d.rejeitadas || 0);
      set("k-aplicadas", d.aplicadas || 0);
      set("k-entrevistas", d.entrevistas || 0);
      set("k-score", d.avg_score);
      set("k-pegas-sub", "no período");
      set("k-aprovadas-sub", pctOf(d.aprovadas || 0) + "% de pegas");
      set("k-rejeitadas-sub", pctOf(d.rejeitadas || 0) + "% de pegas");
      set("k-aplicadas-sub", (d.aprovadas ? Math.round(((d.aplicadas || 0) / d.aprovadas) * 100) : 0) + "% dos compatíveis");
      set("k-entrevistas-sub", (d.aprovadas ? Math.round(((d.entrevistas || 0) / d.aprovadas) * 100) : 0) + "% dos compatíveis");
      set("k-score-sub", "média 0–100");

      const stages = [
        ["Pegas", pegas, "#4c8dff"],
        ["Compatíveis", d.aprovadas || 0, "#2fd48b"],
        ["Rejeitadas", d.rejeitadas || 0, "#fb7185"],
        ["Entrevistas", d.entrevistas || 0, "#f5b942"],
      ];
      const maxVal = Math.max(1, pegas);
      $("funnel").innerHTML = stages.map(([label, val, color]) => {
        const pct = pegas ? Math.round((val / pegas) * 100) : 0;
        return `<div class="funnel-row"><div class="lbl">${label}</div><div class="bar"><i style="width:${Math.round((val / maxVal) * 100)}%;background:linear-gradient(90deg, ${color}, ${color}aa)"></i></div><div class="val">${val}<small>${pct}%</small></div></div>`;
      }).join("");
      $("funnel-note").innerHTML = `<div><span>Compatibilidade</span><b>${pctOf(d.aprovadas || 0)}%</b></div><div><span>Rejeição</span><b>${pctOf(d.rejeitadas || 0)}%</b></div><div><span>Entrevistas</span><b>${pctOf(d.entrevistas || 0)}%</b></div>`;

      const total = (d.aprovadas || 0) + (d.rejeitadas || 0);
      const pct = total ? Math.round((d.aprovadas / total) * 100) : 0;
      $("donut-wrap").innerHTML = `<div class="donut" style="background:conic-gradient(#2fd48b 0 ${pct}%, #fb7185 ${pct}% 100%)"><div class="lbl"><b>${pct}%</b>compatíveis</div></div>
        <div class="legend">
          <div><span class="dot" style="background:#2fd48b"></span>Compatíveis <b>${d.aprovadas || 0}</b></div>
          <div><span class="dot" style="background:#fb7185"></span>Rejeitadas <b>${d.rejeitadas || 0}</b></div>
        </div>`;
      $("donut-note").innerHTML = `<span>Total analisado <b>${total}</b></span><span>Compatíveis <b>${pct}%</b></span>`;

      const by = d.by_day || [];
      const byTotal = by.reduce((a, x) => a + (x.count || 0), 0);
      const dayBox = $("chart-day");
      if (by.length < 2 || byTotal === 0) {
        dayBox.innerHTML = `<div class="chart-empty">Sem dados suficientes no período.</div>`;
      } else {
        const maxDay = Math.max(1, ...by.map((x) => x.count));
        const showCounts = by.length <= 20;
        dayBox.innerHTML = by.map((x, i) => {
          const showLabel = by.length <= 12 || i % Math.ceil(by.length / 8) === 0;
          const label = showLabel ? (x.date.length === 7 ? x.date.slice(5) + "/" + x.date.slice(0, 4) : x.date.slice(5).replace("-", "/")) : "";
          const h = Math.max(2, Math.round((x.count / maxDay) * 120));
          return `<div class="bar" style="height:${h}px" title="${x.date}: ${x.count}"><span>${label}</span>${showCounts ? `<b style="position:absolute;top:-15px;left:0;right:0;text-align:center;font-size:9px;color:var(--fg)">${x.count}</b>` : ""}</div>`;
        }).join("");
      }

      const bands = d.score_bands || {};
      const bandList = [["70–80", bands["70-80"] || 0, "#7cb0ff"], ["80–90", bands["80-90"] || 0, "#a78bfa"], ["90–100", bands["90-100"] || 0, "#f5b942"]];
      const bandTotal = d.score_total || bandList.reduce((a, b) => a + b[1], 0);
      const maxBand = Math.max(1, ...bandList.map((b) => b[1]));
      $("chart-bands").innerHTML = bandList.map(([label, val, color]) => {
        const pctB = bandTotal ? Math.round((val / bandTotal) * 100) : 0;
        return `<div class="hbar"><div>${label}</div><div class="track"><i style="width:${Math.round((val / maxBand) * 100)}%;background:linear-gradient(90deg, ${color}, ${color}aa)"></i></div><div class="val">${val}<small>${pctB}%</small></div></div>`;
      }).join("");
      $("bands-note").innerHTML = `<span>Base pontuada <b>${bandTotal}</b></span><span>Faixas de 70 a 100</span>`;

      const COLORS = { LinkedIn: "#4c8dff", Indeed: "#a78bfa", Gupy: "#2fd48b", Workday: "#f5b942", InHire: "#2dd4bf", Outros: "#8ea2c4" };
      const plats = d.platforms || [];
      const maxP = Math.max(1, ...plats.map((p) => p.count));
      $("chart-platform").innerHTML = plats.map((p) => {
        const c = COLORS[p.name] || "#8ea2c4";
        const pctP = pegas ? Math.round((p.count / pegas) * 100) : 0;
        return `<div class="hbar"><div>${esc(p.name)}</div><div class="track"><i style="width:${Math.round((p.count / maxP) * 100)}%;background:linear-gradient(90deg, ${c}, ${c}aa)"></i></div><div class="val">${p.count}<small>${pctP}%</small></div></div>`;
      }).join("") || `<div class="msg">sem dados</div>`;
    }
    async function loadWorkflows() {
      const r = await j("/api/n8n/workflows");
      $("wf-body").innerHTML = (r.workflows || []).map((w) =>
        `<tr><td>${esc(w.name)}</td><td class="mono">${esc(w.id)}</td><td>${w.active ? '<span class="chip yes">Sim</span>' : '<span class="chip no">Não</span>'}</td><td class="mono">${Math.round(w.size / 1024)} KB</td><td class="mono">${esc(w.mtime)}</td><td><button class="ghost" data-view-wf="${esc(w.file)}">Ver</button></td></tr>`
      ).join("") || `<tr><td colspan="6" class="empty">nenhum workflow</td></tr>`;
      $("wf-body").querySelectorAll("button[data-view-wf]").forEach((b) => (b.onclick = () => viewWorkflow(b.dataset.viewWf)));
      const st = await j("/api/n8n/status");
      $("wf-status").textContent = st.status ? `última importação: ${st.status.ts} (${st.status.ok ? "ok" : "erro"})` : "";
    }
    async function viewWorkflow(file) {
      const r = await j("/api/n8n/workflow?file=" + encodeURIComponent(file));
      const pre = $("wf-preview");
      pre.textContent = r.ok ? r.content : "erro: " + r.error;
      pre.closest("details").open = true;
      pre.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    async function importWorkflows() {
      $("wf-status").textContent = "solicitando importação…";
      const r = await fetch("/api/n8n/import", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
      const jr = await r.json();
      if (!jr.ok) { $("wf-status").textContent = "erro: " + jr.error; return; }
      $("wf-status").textContent = "importando… (aguarde ~20s)";
      const started = Date.now();
      const timer = setInterval(async () => {
        const st = await j("/api/n8n/status");
        if (st.status && st.status.ts) {
          clearInterval(timer);
          $("wf-status").textContent = st.status.ok ? `importado ✓ (${st.status.ts})` : "erro na importação";
          if (!st.status.ok) { $("wf-preview").textContent = st.status.output || ""; $("wf-preview").closest("details").open = true; }
          loadWorkflows();
        } else if (Date.now() - started > 45000) {
          clearInterval(timer);
          $("wf-status").textContent = "tempo esgotado; verifique o container n8n-sync";
        }
      }, 2000);
    }
    async function loadSecrets() {
      const r = await j("/api/secrets");
      loadCredentials();

      loadGoogle();

      const sec = r.security || {};
      const items = [["secrets/ ignorado no Git", !!sec.gitignored], ["Permissões 600", !!sec.perms_ok]];
      (sec.files || []).forEach((f) => items.push([`${f.path} — ${f.exists ? "existe" : "ausente"} (${f.perms || "—"})`, (!f.exists) || f.perms === "0o600"]));
      $("security-list").innerHTML = `<div class="security-list">${items.map(([label, ok]) =>
        `<div class="security-item ${ok ? "ok" : "bad"}"><span class="mark">${ok ? "✓" : "✕"}</span><span>${esc(label)}</span></div>`
      ).join("")}</div>`;
    }
    let creds = [];
    const revealed = {};
    async function loadCredentials() {
      const r = await j("/api/credentials");
      creds = r.credentials || [];
      const box = $("cred-list");
      box.innerHTML = creds.map((c) => `
        <div class="cred-row">
          <div>
            <div class="cred-name">${esc(c.name)}</div>
            <div class="msg">${esc(c.notes || "sem observação")}</div>
          </div>
          <div class="mono">${c.has_secret ? esc(c.masked) : "—"}${revealed[c.id] ? `<span class="secret-reveal">${esc(revealed[c.id])}</span>` : ""}</div>
          <div class="cred-actions">
            ${c.has_secret ? `<button class="ghost" onclick="toggleReveal('${c.id}')">${revealed[c.id] ? "Ocultar" : "Mostrar"}</button>
            <button class="ghost" onclick="copySecret('${c.id}')">Copiar chave</button>
            <button class="ghost" onclick="copyCredentialJson('${c.id}')">Copiar JSON</button>` : ""}
            <button class="ghost" onclick="editCredential('${c.id}')">Editar</button>
            <button class="danger" onclick="deleteCredential('${c.id}')">Remover</button>
          </div>
        </div>`).join("") || `<div class="msg">nenhuma credencial salva</div>`;
    }
    function clearCredForm() {
      $("cred-id").value = ""; $("cred-name").value = "";
      $("cred-secret").value = ""; $("cred-secret").placeholder = "cole a chave"; $("cred-notes").value = "";
      $("cred-msg").textContent = "";
    }
    function editCredential(id) {
      const c = creds.find((x) => x.id === id); if (!c) return;
      $("cred-id").value = c.id; $("cred-name").value = c.name;
      $("cred-secret").value = "";
      $("cred-secret").placeholder = c.has_secret ? "definido (" + c.masked + ") — em branco mantém" : "cole a chave";
      $("cred-notes").value = c.notes || "";
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
    async function saveCredential() {
      const credential = {
        id: $("cred-id").value || "",
        name: $("cred-name").value.trim(),
        secret: $("cred-secret").value,
        notes: $("cred-notes").value.trim(),
      };
      const r = await fetch("/api/credentials", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "save", credential }) });
      const jr = await r.json();
      $("cred-msg").textContent = jr.ok ? "salvo ✓" : "erro: " + jr.error;
      if (jr.ok) { clearCredForm(); loadCredentials(); }
    }
    async function deleteCredential(id) {
      if (!confirm("Remover esta credencial?")) return;
      delete revealed[id];
      await fetch("/api/credentials", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "delete", id }) });
      toast("Removida"); loadCredentials();
    }
    async function toggleReveal(id) {
      if (revealed[id]) { delete revealed[id]; loadCredentials(); return; }
      const r = await j("/api/credentials/reveal?id=" + encodeURIComponent(id));
      if (!r.ok) { toast("erro: " + r.error); return; }
      revealed[id] = r.secret;
      loadCredentials();
    }
    async function copyText(text) {
      try { await navigator.clipboard.writeText(text); }
      catch (e) {
        const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); } catch (err) {}
        ta.remove();
      }
      toast("copiado ✓");
    }
    async function copySecret(id) {
      const r = await j("/api/credentials/reveal?id=" + encodeURIComponent(id));
      if (!r.ok) { toast("erro: " + r.error); return; }
      copyText(r.secret);
    }
    async function copyCredentialJson(id) {
      const c = creds.find((x) => x.id === id); if (!c) return;
      const r = await j("/api/credentials/reveal?id=" + encodeURIComponent(id));
      if (!r.ok) { toast("erro: " + r.error); return; }
      const payload = { name: r.name, key: r.secret, notes: c.notes || "" };
      copyText(JSON.stringify(payload, null, 2));
    }
    let googleKeyVisible = false;
    async function loadGoogle() {
      const r = await j("/api/google");
      $("google-email").value = r.email || "";
      googleKeyVisible = false;
      $("google-key").value = "";
      $("google-key").placeholder = r.has_private_key ? "definida — em branco mantém" : "-----BEGIN PRIVATE KEY-----";
      $("google-key-btn").textContent = "Mostrar private key";
      $("google-spreadsheet").value = r.spreadsheet_id || "";
      $("google-jobs").value = r.jobs_tab || "Jobs";
      $("google-rejected").value = r.rejected_tab || "Rejected-Jobs";
      $("google-info").innerHTML = r.email
        ? `projeto <b>${esc(r.project_id || "—")}</b> · private key <b>${r.has_private_key ? "definida" : "ausente"}</b>`
        : "service account não definido";
      $("google-msg").textContent = "";
    }
    async function saveGoogle() {
      const payload = {
        email: $("google-email").value.trim(),
        private_key: $("google-key").value.trim(),
        spreadsheet_id: $("google-spreadsheet").value.trim(),
        jobs_tab: $("google-jobs").value.trim(),
        rejected_tab: $("google-rejected").value.trim(),
      };
      const r = await fetch("/api/google", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });
      const jr = await r.json();
      $("google-msg").textContent = jr.ok ? "salvo ✓" : "erro: " + jr.error;
      if (jr.ok) { loadGoogle(); loadSecrets(); }
    }
    async function toggleGoogleKey() {
      const btn = $("google-key-btn");
      if (googleKeyVisible) {
        googleKeyVisible = false; $("google-key").value = "";
        $("google-key").placeholder = "definida — em branco mantém";
        btn.textContent = "Mostrar private key";
        return;
      }
      const r = await j("/api/google/reveal-key");
      if (!r.ok) { toast("erro: " + r.error); return; }
      googleKeyVisible = true; $("google-key").value = r.private_key; $("google-key").placeholder = "";
      btn.textContent = "Ocultar private key";
    }
    async function copyPrivateKey() {
      const r = await j("/api/google/reveal-key");
      if (!r.ok) { toast("erro: " + r.error); return; }
      copyText(r.private_key);
    }
    async function testSheets() {
      $("sheets-msg").textContent = "testando…";
      const r = await fetch("/api/sheets/test", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
      const jr = await r.json();
      $("sheets-msg").textContent = jr.ok ? `OK — ${jr.rows} linhas em ${jr.tab}` : `erro: ${jr.error || "?"}`;
    }
    async function loadJobsCSV() {
      const rows = await j("/api/jobs?limit=300");
      window._jobs = rows;
      jobsPage = 0;
      $("jobs-msg").textContent = rows.length + " registro(s)";
      renderJobs();
    }
    function renderJobs() {
      let rows = window._jobs || [];
      const q = ($("jobs-q").value || "").toLowerCase().trim();
      if (q) rows = rows.filter((r) => `${r.title || ""} ${r.company || ""}`.toLowerCase().includes(q));
      const size = 25;
      const pages = Math.max(1, Math.ceil(rows.length / size));
      if (jobsPage > pages - 1) jobsPage = pages - 1;
      const slice = rows.slice(jobsPage * size, (jobsPage + 1) * size);
      $("jobs-body").innerHTML = slice.map((r) =>
        `<tr><td class="mono">${fmtShort(r.analyzed_at)}</td><td>${esc(r.company)}</td><td>${titleCell(r.title, r.url)}</td><td>${esc(r.location)}</td><td class="mono">${esc(r.source)}</td><td>${scorePill(r.det_score)}</td><td>${scorePill(r.ia_score)}</td><td>${r.url ? `<button class="ghost" data-resend="${esc(r.url)}" data-title="${esc(r.title)}">Reenviar</button>` : ""}</td></tr>`
      ).join("") || `<tr><td colspan="8" class="empty">nenhuma vaga registrada ainda</td></tr>`;
      $("jobs-pager").innerHTML = pagerHTML(rows.length, jobsPage, size, "jobsGo");
    }
    async function loadGaps() {
      const days = $("gap-days").value || "0";
      const d = await j(`/api/gaps?days=${days}`);
      if (!d || !d.ok) { $("gap-msg").textContent = "erro: " + (d && d.error ? d.error : "?"); return; }
      $("gap-msg").innerHTML = `${d.total_jobs} vagas · ${d.distinct_skills} skills pedidas · <span class="chip yes">domina</span> <span class="chip tag">parcial</span> <span class="chip tf">transferível</span> <span class="chip no">faltando</span> · cobertura <b>${d.coverage_pct}%</b>`;
      $("gap-cards").innerHTML = [
        ["Vagas na planilha", d.total_jobs],
        ["Skills pedidas", d.distinct_skills],
        ["Cobertura forte", d.coverage_pct + "%"],
        ["Skills a melhorar", d.gaps.length],
      ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
      $("gap-demand").innerHTML = (d.demand || []).map((s) => {
        const pct = d.total_jobs ? Math.round(s.count / d.total_jobs * 100) : 0;
        const chips = { strong: '<span class="chip yes">domina</span>', weak: '<span class="chip tag">parcial</span>', transferable: '<span class="chip tf">transferível</span>', missing: '<span class="chip no">faltando</span>' };
        const chip = chips[s.status] || chips.missing;
        return `<tr><td><b>${esc(s.label || s.name)}</b></td><td class="mono">${s.count} · ${pct}%</td><td><div class="mini-bar"><i style="width:${Math.min(100, pct)}%;background:linear-gradient(90deg,var(--accent),var(--violet))"></i></div></td><td>${chip}</td></tr>`;
      }).join("") || `<tr><td colspan="4" class="empty">sem skills registradas ainda</td></tr>`;
      $("gap-missing").innerHTML = (d.gaps || []).map((s) => {
        const pct = d.total_jobs ? Math.round(s.count / d.total_jobs * 100) : 0;
        return `<tr><td><b>${esc(s.label || s.name)}</b></td><td class="mono">${s.count}</td><td class="mono">${s.ausentes}×</td><td><div class="mini-bar"><i style="width:${Math.min(100, pct)}%;background:linear-gradient(90deg,var(--err),var(--warn))"></i></div></td></tr>`;
      }).join("") || `<tr><td colspan="4" class="empty">perfil cobre tudo 👏</td></tr>`;
    }
    async function analyzeGaps() {
      const days = $("gap-days").value || "0";
      $("gap-ai-out").textContent = "analisando…";
      try {
        const r = await fetch(`/api/gaps/ai?days=${days}`);
        const jr = await r.json();
        $("gap-ai-out").textContent = jr.ok ? jr.text : ("erro: " + (jr.error || "?"));
      } catch (e) {
        $("gap-ai-out").textContent = "erro de rede";
      }
    }

    // ---------------------------------------------------------------- planilha
    function scorePill(v) {
      const n = parseFloat(v);
      if (isNaN(n)) return `<span class="chip">—</span>`;
      const c = n >= 80 ? "hi" : n >= 60 ? "mid" : n >= 40 ? "low" : "bad";
      return `<span class="score ${c}">${n.toFixed(0)}</span>`;
    }
    function yesChip(v) {
      const y = ["sim", "yes", "true", "1"].includes(String(v ?? "").trim().toLowerCase());
      return `<span class="chip ${y ? "yes" : "no"}">${y ? "Sim" : "Não"}</span>`;
    }
    function truncCell(text, n) {
      const s = String(text ?? "").trim();
      if (!s || s.toLowerCase() === "n/a") return `<span class="chip">—</span>`;
      return `<span class="trunc" title="${esc(s)}">${esc(s.length > n ? s.slice(0, n) + "…" : s)}</span>`;
    }
    function skillsCell(raw) {
      const s = String(raw ?? "").trim();
      if (!s || s.toLowerCase() === "n/a") return `<span class="chip">—</span>`;
      try {
        const o = JSON.parse(s);
        const parts = [];
        for (const k of Object.keys(o)) if (Array.isArray(o[k]) && o[k].length) parts.push(k + ": " + o[k].join(", "));
        return truncCell(parts.length ? parts.join(" · ") : s, 90);
      } catch (e) { return truncCell(s, 90); }
    }
    function interviewChip(v) {
      const s = String(v ?? "").trim();
      if (!s) return `<span class="chip">—</span>`;
      const real = !["sem retorno", "não", "nao", "n/a", "-", "descartado", "reprovado", "rejeitado", "aguardando", "pendente", "default"].includes(s.toLowerCase());
      return `<span class="chip ${real ? "yes" : ""}">${esc(s)}</span>`;
    }
    function titleCell(title, url) {
      return url ? `<a href="${esc(url)}" target="_blank">${esc(title)}</a>` : esc(title);
    }
    function pagerHTML(total, page, size, go) {
      const pages = Math.max(1, Math.ceil(total / size));
      const start = total ? page * size + 1 : 0;
      const end = Math.min(total, (page + 1) * size);
      return `<span class="pager-info">${start}–${end} de ${total}</span>` +
        `<button class="ghost" ${page <= 0 ? "disabled" : ""} onclick="${go}(${page - 1})">‹ Anterior</button>` +
        `<span class="pager-num">${page + 1} / ${pages}</span>` +
        `<button class="ghost" ${page >= pages - 1 ? "disabled" : ""} onclick="${go}(${page + 1})">Próxima ›</button>`;
    }
    function sheetGo(p) { sheetPage = p; renderSheets(); }
    function jobsGo(p) { jobsPage = p; renderJobs(); }
    document.querySelectorAll("#sheet-seg button").forEach((b) => (b.onclick = () => {
      sheetTab = b.dataset.sheet;
      document.querySelectorAll("#sheet-seg button").forEach((x) => x.classList.toggle("active", x === b));
      loadSheets();
    }));
    $("sheet-days").onchange = () => loadSheets();
    $("sheet-entrevista").onchange = () => { sheetPage = 0; renderSheets(); };
    $("sheet-q").oninput = () => { sheetPage = 0; renderSheets(); };
    $("jobs-q").oninput = () => { jobsPage = 0; renderJobs(); };
    $("gap-days").onchange = () => loadGaps();
    async function loadSheets(force) {
      $("sheet-msg").textContent = "carregando…";
      const days = $("sheet-days").value || "0";
      const r = await j(`/api/sheets?tab=${sheetTab}&limit=400&days=${days}${force ? "&force=1" : ""}`);
      window._sheet = r;
      sheetPage = 0;
      populateInterviewFilter(r.rows || []);
      renderSheets();
    }
    const ENT_NONE = ["", "sem retorno", "não", "nao", "no", "n/a", "na", "-", "default", "descartado", "rejeitado", "reprovado", "aguardando", "pendente", "em análise", "em analise", "nenhuma", "não fiz", "nao fiz"];
    function entReal(v) { const s = String(v ?? "").trim().toLowerCase(); return !!s && !ENT_NONE.includes(s); }
    function populateInterviewFilter(rows) {
      const sel = $("sheet-entrevista");
      if (!sel) return;
      const cur = sel.value || "";
      const seen = new Set();
      (rows || []).forEach((r) => { const v = String(r.Entrevista ?? "").trim(); if (v) seen.add(v); });
      const values = [...seen].sort((a, b) => a.localeCompare(b, "pt-BR"));
      let html = `<option value="">Entrevista: todas</option>`;
      html += `<option value="__has__">Com entrevista</option>`;
      html += `<option value="__none__">Sem entrevista</option>`;
      values.forEach((v) => { html += `<option value="${esc(v)}">${esc(v)}</option>`; });
      sel.innerHTML = html;
      sel.value = cur;
    }
    function matchInterview(r, filter) {
      if (!filter) return true;
      const v = String(r.Entrevista ?? "").trim();
      if (filter === "__has__") return entReal(v);
      if (filter === "__none__") return !entReal(v);
      return v.toLowerCase() === filter.toLowerCase();
    }
    function renderSheets() {
      const d = window._sheet;
      if (!d) return;
      $("sheet-msg").textContent = d.ok ? `${d.tab_name || d.tab} • ${d.updated_at || ""}` : "erro: " + d.error;
      const s = d.summary || {};
      const cards = [["Total", s.total], ["Aplicadas", s.applied], ["Entrevistas", s.interview], ["Score médio", s.avg_score], ["Remotas", s.remote]];
      if (sheetTab === "rejected") cards.push(["Visto?", s.visa_block]);
      if (sheetTab === "all" && s.rejected !== undefined) cards.push(["Rejeitadas", s.rejected]);
      $("sheet-cards").innerHTML = cards.filter((c) => c[1] !== undefined && c[1] !== null)
        .map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
      let rows = d.rows || [];
      const q = ($("sheet-q").value || "").toLowerCase().trim();
      if (q) rows = rows.filter((r) => `${r.company || ""} ${r.title || ""}`.toLowerCase().includes(q));
      const entFilter = $("sheet-entrevista") ? ($("sheet-entrevista").value || "") : "";
      if (entFilter) rows = rows.filter((r) => matchInterview(r, entFilter));
      const size = 25;
      const pages = Math.max(1, Math.ceil(rows.length / size));
      if (sheetPage > pages - 1) sheetPage = pages - 1;
      const slice = rows.slice(sheetPage * size, (sheetPage + 1) * size);
      const tipoCell = (r) => `<span class="chip ${r._tab === "rejected" ? "no" : "yes"}">${r._tab === "rejected" ? "Rejeitada" : "Compatível"}</span>`;
      if (sheetTab === "rejected") {
        $("sheet-head").innerHTML = "<tr><th>Data</th><th>Empresa</th><th>Título</th><th>Score</th><th>Visto?</th><th>Remota</th><th>Skills</th></tr>";
        $("sheet-body").innerHTML = slice.map((r) =>
          `<tr><td class="mono">${esc(r.Date)}</td><td>${esc(r.company)}</td><td>${titleCell(r.title, r.job_url)}</td><td>${scorePill(r.Score)}</td><td>${truncCell(r["Visto?"], 60)}</td><td>${yesChip(r.is_remote)}</td><td>${skillsCell(r.SKILLS_DA_VAGA)}</td></tr>`
        ).join("") || `<tr><td colspan="7" class="empty">sem dados</td></tr>`;
      } else if (sheetTab === "all") {
        $("sheet-head").innerHTML = "<tr><th>Data</th><th>Tipo</th><th>Empresa</th><th>Título</th><th>Score</th><th>Aplicar</th><th>Entrevista</th><th>Remota</th><th>Local</th><th>OBS (IA)</th><th>Skills</th></tr>";
        $("sheet-body").innerHTML = slice.map((r) =>
          `<tr><td class="mono">${esc(r.Date)}</td><td>${tipoCell(r)}</td><td>${esc(r.company)}</td><td>${titleCell(r.title, r.job_url)}</td><td>${scorePill(r.Score)}</td><td>${yesChip(r.Apply)}</td><td>${interviewChip(r.Entrevista)}</td><td>${yesChip(r.is_remote)}</td><td>${truncCell(r.location, 40)}</td><td>${truncCell(r.OBS, 160)}</td><td>${skillsCell(r.SKILLS_DA_VAGA)}</td></tr>`
        ).join("") || `<tr><td colspan="11" class="empty">sem dados</td></tr>`;
      } else {
        $("sheet-head").innerHTML = "<tr><th>Data</th><th>Empresa</th><th>Título</th><th>Score</th><th>Aplicar</th><th>Entrevista</th><th>Remota</th><th>Local</th><th>OBS (IA)</th><th>Skills</th></tr>";
        $("sheet-body").innerHTML = slice.map((r) =>
          `<tr><td class="mono">${esc(r.Date)}</td><td>${esc(r.company)}</td><td>${titleCell(r.title, r.job_url)}</td><td>${scorePill(r.Score)}</td><td>${yesChip(r.Apply)}</td><td>${interviewChip(r.Entrevista)}</td><td>${yesChip(r.is_remote)}</td><td>${truncCell(r.location, 40)}</td><td>${truncCell(r.OBS, 160)}</td><td>${skillsCell(r.SKILLS_DA_VAGA)}</td></tr>`
        ).join("") || `<tr><td colspan="10" class="empty">sem dados</td></tr>`;
      }
      $("sheet-pager").innerHTML = pagerHTML(rows.length, sheetPage, size, "sheetGo");
    }
    setInterval(() => { if (view === "vagas") loadSheets(); }, 30000);

    // ---------------------------------------------------------------- guided editor
    let guidedFile = null, docOriginal = null, docWorking = null, previewTimer = null;

    const KEY_PT = {
  // config
  search_query: "Termos de busca", hours_old: "Janela de busca (horas)", results_per_country: "Vagas por país",
  bank_results_per_bank: "Vagas por banco", bank_include_keywords: "Palavras-chave dos bancos", banks: "Bancos",
  inhire: "InHire", targets: "Países", work_modes: "Modalidades", title_blocklist: "Bloquear cargos",
  company_blocklist: "Bloquear empresas", schedule: "Agenda", enabled: "Ativo", results_per_company: "Vagas por empresa",
  companies: "Empresas", companies_file: "Arquivo de empresas", include_keywords: "Palavras-chave incluídas",
  subdomain: "Subdomínio", source: "Origem", name: "Nome", workday: "Workday", tenant: "Tenant", site: "Site",
  results: "Resultados", timezone: "Fuso horário", times: "Horários", digest_at: "Horário do digest",
  misfire_grace_minutes: "Tolerância (min)", prompt: "System prompt da IA", max_description_chars: "Máx. de caracteres da descrição",
  // profile
  profile_version: "Versão do perfil", source_of_truth: "Fonte de verdade", identity: "Identidade",
  level_definitions: "Definições de nível", source_definitions: "Definições de origem",
  status_definitions: "Definições de status", classification_rules: "Regras de classificação",
  professional_objective: "Objetivo profissional", target_roles: "Cargos-alvo", seniority: "Senioridade",
  experience: "Experiência", skill_groups: "Grupos de competências", skills: "Competências",
  certifications: "Certificações", education: "Educação", languages: "Idiomas", location: "Localização",
  restrictions: "Restrições", desired_job_types: "Tipos desejados", undesired_job_types: "Tipos indesejados",
  matching_guidance: "Orientações de matching", title_matching: "Filtro de títulos", skill_aliases: "Apelidos de skills",
  work_preferences: "Preferências de trabalho",
  // campos comuns
  level: "Nível", evidence: "Evidências", limitations: "Limitações", notes: "Observação", label: "Rótulo",
  group: "Grupo", kind: "Tipo", period: "Período", focus: "Foco", responsibilities: "Atividades",
  technologies: "Tecnologias", company: "Empresa", role: "Cargo", degree: "Curso", institution: "Instituição",
  language: "Idioma", declared_proficiency: "Proficiência declarada", current_situation: "Situação atual",
  evidence_notes: "Notas de evidência", acceptable: "Aceitável", preferred: "Preferido", detail: "Detalhe",
  condition: "Condição", city: "Cidade", state: "Estado", country: "País", current: "Atual",
  open_to_levels: "Níveis abertos", evaluation_rule: "Regra de avaliação", primary: "Principais",
  adjacent: "Próximos", also_acceptable: "Também aceitáveis", target: "Alvo", hard: "Técnica", soft: "Comportamental",
  cv: "Currículo", user: "Usuário", project: "Projeto", confirmed: "Confirmado", not_assessed: "Não avaliado",
  hands_on: "Hands-on", working_knowledge: "Conhecimento prático", learning: "Aprendendo",
  conceptual: "Conceitual", none: "Nenhum",
};
function pt(key) { return KEY_PT[key] || key; }
const CONFIG_SECTIONS = [
      { n: 1, title: "Busca", desc: "O que procurar.", fields: [
        { key: "search_query", label: "Termos de busca", type: "textarea", rows: 5, help: 'Separe alternativas com OR. Ex.: "Engenheiro de Software" OR Developer OR Backend' },
        { key: "hours_old", label: "Janela de busca (horas)", type: "select", numeric: true, options: [24, 36, 48, 72, 168], help: "24, 36, 48, 72 ou 168 (1 semana)." },
        { key: "results_per_source", label: "Vagas por fonte", type: "number", help: "Por país (JobSpy), por banco e por empresa (InHire). Ex.: 500." },
      ]},
      { n: 2, title: "Países e modalidades", desc: "Onde buscar e o que aceitar.", type: "targets" },
      { n: 3, title: "Fontes", desc: "Plataformas e empresas (Gupy, Workday, InHire e outras).", type: "sources" },
      { n: 4, title: "Filtros", desc: "O que descartar antes da análise.", fields: [
        { key: "title_blocklist", label: "Bloquear cargos", type: "lines", rows: 10, help: "Um termo por linha. Ex.: estágio, gerente, frontend." },
        { key: "company_blocklist", label: "Bloquear empresas", type: "lines", rows: 6, help: "Uma empresa por linha. Ex.: jobgether." },
        { key: "bank_include_keywords", label: "Palavras-chave (Gupy/Workday)", type: "lines", rows: 6, help: "Um termo por linha. Ex.: engenharia, desenvolvimento, dados." },
      ]},
      { n: 5, title: "IA (prompt)", desc: "Instruções da IA. Editável sem republicar o n8n.", fields: [
        { key: "prompt", label: "System prompt", type: "textarea", rows: 18, help: "Mensagem de sistema enviada no payload (substitui a do n8n)." },
        { key: "max_description_chars", label: "Máx. de caracteres da descrição", type: "number", help: "Descrições maiores são cortadas antes de ir à IA (economia de tokens). Ex.: 4000." },
      ]},
    ];
    const CONFIG_CURATED = new Set(["search_query", "hours_old", "results_per_country", "targets", "work_modes",
      "bank_results_per_bank", "banks", "bank_include_keywords", "title_blocklist", "company_blocklist", "inhire", "schedule", "scan_sources", "other_sources", "results_per_source", "prompt", "max_description_chars"]);
    const PROFILE_SECTIONS = [
      { n: 1, title: "Identidade", desc: "Seus dados básicos e links.", fields: [
        { key: "identity.name", label: "Nome", type: "text", help: "Nome completo." },
        { key: "location.city", label: "Cidade", type: "text", help: "Ex.: São Paulo" },
        { key: "location.state", label: "Estado", type: "text", help: "Ex.: SP" },
        { key: "location.country", label: "País", type: "text", help: "Ex.: Brazil" },
        { key: "location.timezone", label: "Fuso horário", type: "text", help: "Ex.: America/Sao_Paulo" },
        { key: "identity.linkedin_public", label: "LinkedIn público", type: "bool", help: "Exibir link público do LinkedIn." },
        { key: "identity.github_public", label: "GitHub público", type: "bool", help: "Exibir link público do GitHub." },
      ]},
      { n: 2, title: "Objetivo", desc: "O que procura e cargos-alvo.", fields: [
        { key: "professional_objective", label: "Objetivo profissional", type: "textarea", rows: 6, help: "Resumo em 2-4 linhas." },
        { key: "target_roles.primary", label: "Cargos-alvo", type: "lines", rows: 5, help: "Um por linha. Ex.: Site Reliability Engineer" },
        { key: "target_roles.adjacent", label: "Cargos próximos", type: "lines", rows: 4, help: "Ex.: Arquiteto de Software" },
        { key: "target_roles.also_acceptable", label: "Também aceitáveis", type: "lines", rows: 4, help: "Ex.: Engenheiro Backend" },
        { key: "seniority.target", label: "Senioridade alvo", type: "text", help: "Ex.: Engenheiro de Software Sênior" },
        { key: "seniority.open_to_levels", label: "Níveis abertos", type: "lines", rows: 3 },
        { key: "seniority.evaluation_rule", label: "Regra de avaliação", type: "textarea", rows: 3 },
      ]},
      { n: 3, title: "Formação, Idiomas e Certificações", desc: "Educação, idiomas e certificações.", type: "education" },
      { n: 4, title: "Experiência", desc: "Empregos, atividades e tecnologias.", type: "experience", key: "experience" },
      { n: 5, title: "Competências", desc: "Grupos por você. Cada skill tem grupo e nível; informe as empresas onde usou.", type: "skills" },
      { n: 6, title: "Apelidos de skills", desc: "Nomes alternativos que as vagas usam para a mesma skill (ex.: kubernetes → k8s, eks, gke). Usados no Mercado & Gaps.", type: "aliases" },
      { n: 7, title: "Preferências de vaga", desc: "O que aceitar e evitar.", fields: [
        { key: "desired_job_types", label: "Tipos desejados", type: "lines", rows: 6, help: "Ex.: Backend, Frontend" },
        { key: "undesired_job_types", label: "Tipos indesejados", type: "lines", rows: 8, help: "Ex.: Full Stack, Front-end" },
        { key: "restrictions", label: "Restrições", type: "lines", rows: 5, help: "Ex.: presencial fora do escopo; visto." },
      ]},
    ];
    const PROFILE_CURATED = new Set(["identity", "location", "professional_objective", "target_roles", "seniority",
      "education", "languages", "certifications", "experience", "skill_groups", "skills",
      "desired_job_types", "undesired_job_types", "restrictions", "work_preferences", "skill_aliases"]);
    const get = (o, p) => p.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
    function set(o, p, v) {
      const parts = p.split("."); let cur = o;
      for (let i = 0; i < parts.length - 1; i++) { if (typeof cur[parts[i]] !== "object" || cur[parts[i]] === null) cur[parts[i]] = {}; cur = cur[parts[i]]; }
      cur[parts[parts.length - 1]] = v;
    }
    const clone = (v) => (typeof structuredClone === "function" ? structuredClone(v) : JSON.parse(JSON.stringify(v)));

    function readField(el, type) {
      if (type === "number") { const n = parseFloat(el.value); return isNaN(n) ? null : n; }
      if (type === "lines") return el.value.split("\n").map((s) => s.trim()).filter(Boolean);
      if (type === "targets") return el.value.split("\n").map((s) => s.trim()).filter(Boolean).map((l) => {
        const p = l.split(":").map((s) => s.trim());
        return { country: p[0], work_mode: p[1] || "any" };
      });
      return el.value;
    }

    function fieldRow(field, onValue) {
      const raw = get(docWorking, field.key);
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        const row = document.createElement("div"); row.className = "field";
        const lab = document.createElement("div"); lab.className = "field-label";
        lab.appendChild(Object.assign(document.createElement("div"), { className: "name", textContent: field.label }));
        const box = document.createElement("div"); box.className = "field-input";
        const ta = document.createElement("textarea"); ta.rows = 6; ta.value = JSON.stringify(raw, null, 2);
        ta.oninput = () => { try { set(docWorking, field.key, JSON.parse(ta.value)); schedulePreview(); } catch (e) {} };
        box.appendChild(ta); row.appendChild(lab); row.appendChild(box); return row;
      }
      const row = document.createElement("div"); row.className = "field";
      const lab = document.createElement("div"); lab.className = "field-label";
      const name = document.createElement("div"); name.className = "name"; name.textContent = field.label; lab.appendChild(name);
      if (field.help) { const h = document.createElement("div"); h.className = "help"; h.textContent = field.help; lab.appendChild(h); }
      const box = document.createElement("div"); box.className = "field-input";
      const val = get(docWorking, field.key);
      let el;
      if (field.type === "modes") {
        el = document.createElement("div"); el.className = "checks";
        const cur = Array.isArray(val) ? val : ["remote", "hybrid", "local"];
        [["remote", "Remoto"], ["hybrid", "Híbrido"], ["local", "Local"]].forEach(([k, lbl]) => {
          const l = document.createElement("label");
          const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = cur.includes(k);
          cb.onchange = () => onValue([...el.querySelectorAll("input")].filter((c) => c.checked).map((c) => c.dataset.mode));
          cb.dataset.mode = k; l.appendChild(cb); l.appendChild(document.createTextNode(" " + lbl)); el.appendChild(l);
        });
      } else if (field.type === "select") {
        el = document.createElement("select");
        const opts = (field.options || []).map((o) => (Array.isArray(o) ? o : [o, String(o)]));
        if (val !== undefined && val !== null && !opts.some(([v]) => String(v) === String(val))) opts.unshift([val, String(val)]);
        opts.forEach(([v, l]) => { const o = document.createElement("option"); o.value = v; o.textContent = l; if (String(val) === String(v)) o.selected = true; el.appendChild(o); });
        el.onchange = () => onValue(field.numeric ? Number(el.value) : el.value);
      } else if (field.type === "lines" || field.type === "targets" || field.type === "textarea") {
        el = document.createElement("textarea"); el.rows = field.rows || (field.type === "textarea" ? 5 : 8);
        el.value = field.type === "lines" ? (Array.isArray(val) ? val.join("\n") : "")
          : field.type === "targets" ? (Array.isArray(val) ? val.map((t) => `${t.country || ""}: ${t.work_mode || "any"}`).join("\n") : "")
          : (val || "");
        el.oninput = () => onValue(readField(el, field.type));
      } else {
        el = document.createElement("input"); el.type = field.type === "number" ? "number" : "text"; el.value = val ?? "";
        el.oninput = () => onValue(readField(el, field.type));
      }
      box.appendChild(el); row.appendChild(lab); row.appendChild(box); return row;
    }

    function textInput(value, onInput, placeholder) {
      const el = document.createElement("input"); el.type = "text"; el.value = value ?? "";
      if (placeholder) el.placeholder = placeholder;
      el.oninput = () => onInput(el.value);
      return el;
    }
    function linesInput(value, onChange, rows, placeholder) {
      const el = document.createElement("textarea"); el.rows = rows || 5;
      el.value = Array.isArray(value) ? value.join("\n") : (value ?? "");
      if (placeholder) el.placeholder = placeholder;
      el.oninput = () => onChange(el.value.split("\n").map((s) => s.trim()).filter(Boolean));
      return el;
    }
    function cefrSelect(value, onChange, cls) {
      const opts = ["", "A1 - Iniciante", "A2 - Básico", "B1 - Intermediário", "B2 - Avançado", "C1 - Fluente", "C2 - Proficiente", "Nativo"];
      const s = document.createElement("select"); if (cls) s.className = cls;
      opts.forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = v || "—"; if ((value || "") === v) o.selected = true; s.appendChild(o); });
      s.onchange = () => onChange(s.value); return s;
    }
    function labeled(label, node) {
      const d = document.createElement("div");
      const l = document.createElement("div"); l.className = "lbl"; l.textContent = label;
      d.appendChild(l); d.appendChild(node); return d;
    }
    function itemRemove(onRemove) {
      const head = document.createElement("div"); head.className = "exp-head";
      const rm = document.createElement("button"); rm.type = "button"; rm.className = "danger"; rm.textContent = "Remover";
      rm.onclick = onRemove; head.appendChild(rm);
      return head;
    }
    function cardHead(title, onRemove) {
      const head = document.createElement("div"); head.className = "exp-head";
      head.appendChild(Object.assign(document.createElement("strong"), { textContent: title }));
      const rm = document.createElement("button"); rm.type = "button"; rm.className = "danger"; rm.textContent = "Remover";
      rm.onclick = onRemove; head.appendChild(rm);
      return head;
    }
    function toMonth(v) {
      const s = String(v || "").trim();
      if (/^\d{4}$/.test(s)) return s + "-01";
      if (/^\d{4}-\d{2}$/.test(s)) return s;
      return "";
    }
    function periodInputs(value, onChange) {
      const parts = String(value || "").split("..");
      const start = parts[0] || "";
      const rawEnd = parts[1] || "";
      const isCurrent = ["present", "atual"].includes(rawEnd.trim().toLowerCase());
      const wrap = document.createElement("div"); wrap.style.display = "flex"; wrap.style.gap = "8px"; wrap.style.alignItems = "center"; wrap.style.flexWrap = "wrap";
      const s = document.createElement("input"); s.type = "month"; s.value = toMonth(start);
      const e = document.createElement("input"); e.type = "month"; e.value = isCurrent ? "" : toMonth(rawEnd); e.disabled = isCurrent;
      const cb = document.createElement("label"); cb.style.display = "flex"; cb.style.gap = "6px"; cb.style.alignItems = "center"; cb.style.fontSize = "12px"; cb.style.color = "var(--fg)";
      const cbox = document.createElement("input"); cbox.type = "checkbox"; cbox.checked = isCurrent;
      cb.appendChild(cbox); cb.appendChild(document.createTextNode("Atual"));
      const emit = () => onChange(`${s.value || ""}..${cbox.checked ? "present" : (e.value || "")}`);
      s.onchange = emit; e.onchange = emit; cbox.onchange = () => { e.disabled = cbox.checked; emit(); };
      wrap.appendChild(s); wrap.appendChild(document.createTextNode("→")); wrap.appendChild(e); wrap.appendChild(cb);
      return wrap;
    }

    function skillOptions() {
      const list = (docWorking.skills || []).map((s) => ({ value: s.name, label: s.label || s.name || "" }));
      const known = new Set(list.map((x) => x.value));
      (docWorking.experience || []).forEach((e) => (e.technologies || []).forEach((t) => {
        if (t && !known.has(t)) { known.add(t); list.push({ value: t, label: t }); }
      }));
      return list.sort((a, b) => a.label.localeCompare(b.label));
    }
    function experienceSkillsCombo(item) {
      const wrap = document.createElement("div"); wrap.className = "combo";
      const btn = document.createElement("button"); btn.type = "button"; btn.className = "combo-btn";
      const menu = document.createElement("div"); menu.className = "combo-menu hidden";
      const labelOf = (v) => { const o = skillOptions().find((x) => x.value === v); return o ? o.label : v; };
      function renderBtn() {
        btn.innerHTML = "";
        const sel = (item.technologies || []).slice().sort((a, b) => labelOf(a).localeCompare(labelOf(b)));
        if (!sel.length) btn.appendChild(Object.assign(document.createElement("span"), { className: "chip", textContent: "tecnologias ▾" }));
        else sel.forEach((v) => btn.appendChild(Object.assign(document.createElement("span"), { className: "chip", textContent: labelOf(v) })));
        btn.appendChild(Object.assign(document.createElement("span"), { className: "caret", textContent: "▾" }));
      }
      function renderMenu() {
        menu.innerHTML = "";
        skillOptions().forEach((opt) => {
          const lab = document.createElement("label");
          const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = (item.technologies || []).includes(opt.value);
          cb.onchange = () => {
            item.technologies = Array.isArray(item.technologies) ? item.technologies : [];
            if (cb.checked) { if (!item.technologies.includes(opt.value)) item.technologies.push(opt.value); }
            else item.technologies = item.technologies.filter((t) => t !== opt.value);
            renderBtn(); renderMenu(); recomputeSkillCompanies();
            if (window._skillsContainer) renderSkills(window._skillsContainer);
            schedulePreview();
          };
          lab.appendChild(cb); lab.appendChild(document.createTextNode(" " + opt.label)); menu.appendChild(lab);
        });
      }
      menu.addEventListener("click", (e) => e.stopPropagation());
      btn.onclick = (e) => { e.stopPropagation(); renderMenu(); menu.classList.toggle("hidden"); };
      renderBtn();
      wrap.appendChild(btn); wrap.appendChild(menu);
      return wrap;
    }

    function renderExperience(container) {
      window._expContainer = container;
      const arr = Array.isArray(docWorking.experience) ? docWorking.experience : (docWorking.experience = []);
      container.innerHTML = "";
      const grid = document.createElement("div"); grid.className = "exp-grid";
      arr.forEach((item, index) => {
        const c = document.createElement("div"); c.className = "exp-card";
        c.appendChild(cardHead(`Experiência ${index + 1}`, () => { arr.splice(index, 1); renderExperience(container); schedulePreview(); }));
        const row3 = document.createElement("div"); row3.className = "grid3";
        row3.appendChild(labeled("Empresa", textInput(item.company, (v) => { item.company = v; schedulePreview(); }, "Ex.: Empresa Exemplo")));
        row3.appendChild(labeled("Cargo", textInput(item.role, (v) => { item.role = v; schedulePreview(); }, "Ex.: Engenheiro de Software")));
        row3.appendChild(labeled("Período", periodInputs(item.period, (v) => { item.period = v; item.current = /present|atual/i.test(v); schedulePreview(); })));
        c.appendChild(row3);
        c.appendChild(labeled("Foco", textInput(item.focus, (v) => { item.focus = v; schedulePreview(); }, "Ex.: Backend, microsserviços")));
        c.appendChild(labeled("Atividades", linesInput(item.responsibilities, (v) => { item.responsibilities = v; schedulePreview(); }, 8, "Uma atividade por linha")));
        c.appendChild(labeled("Tecnologias", experienceSkillsCombo(item)));
        grid.appendChild(c);
      });
      container.appendChild(grid);
      const add = document.createElement("button"); add.type = "button"; add.className = "ghost"; add.textContent = "+ Adicionar experiência";
      add.onclick = () => { arr.push({ company: "", role: "", period: "", focus: "", responsibilities: [], technologies: [], current: false }); renderExperience(container); schedulePreview(); };
      container.appendChild(add);
    }

    function renderEducation(container) {
      container.innerHTML = "";
      const edu = Array.isArray(docWorking.education) ? docWorking.education : (docWorking.education = []);
      const langs = Array.isArray(docWorking.languages) ? docWorking.languages : (docWorking.languages = []);
      const certs = Array.isArray(docWorking.certifications) ? docWorking.certifications : (docWorking.certifications = []);

      function block(title, cls) {
        const wrap = document.createElement("div"); wrap.className = "edu-block";
        const header = document.createElement("div"); header.className = "skill-group " + cls;
        header.appendChild(Object.assign(document.createElement("span"), { className: "gname", textContent: title }));
        const count = document.createElement("span"); count.className = "count"; header.appendChild(count);
        wrap.appendChild(header);
        const items = document.createElement("div"); items.className = "skill-items " + cls;
        wrap.appendChild(items); container.appendChild(wrap);
        return { items, count };
      }
      function removeBtn(onRemove) {
        const rm = document.createElement("button"); rm.type = "button"; rm.className = "danger"; rm.textContent = "Remover";
        rm.onclick = onRemove; return rm;
      }
      function addBtn(label, onClick) {
        const b = document.createElement("button"); b.type = "button"; b.className = "ghost"; b.textContent = label;
        b.onclick = onClick; return b;
      }

      // Educação
      const E = block("Educação", "edu");
      const addE = addBtn("+ Adicionar formação", () => { edu.unshift({ degree: "", institution: "", period: "" }); renderEducation(container); schedulePreview(); });
      E.items.appendChild(addE);
      edu.forEach((item, index) => {
        const row = document.createElement("div"); row.className = "skill-row";
        row.appendChild(textInput(item.degree, (v) => { item.degree = v; schedulePreview(); }, "Curso — ex.: Ciência da Computação"));
        row.appendChild(textInput(item.institution, (v) => { item.institution = v; schedulePreview(); }, "Instituição — ex.: Universidade"));
        const per = periodInputs(item.period, (v) => { item.period = v; schedulePreview(); }); per.classList.add("period");
        row.appendChild(per);
        row.appendChild(removeBtn(() => { edu.splice(index, 1); renderEducation(container); schedulePreview(); }));
        E.items.appendChild(row);
      });
      E.items.insertBefore(addE, E.items.firstChild);
      E.count.textContent = edu.length + " formação(ões)";

      // Idiomas
      const L = block("Idiomas", "idioma");
      const addL = addBtn("+ Adicionar idioma", () => {
        langs.unshift({ language: "", cefr_level: "", current_situation: "", reading_level: "", writing_level: "", speaking_level: "", evidence_notes: "", declared_proficiency: "" });
        renderEducation(container); schedulePreview();
      });
      L.items.appendChild(addL);
      langs.forEach((item, index) => {
        const itemWrap = document.createElement("div");
        const row = document.createElement("div"); row.className = "skill-row";
        row.appendChild(textInput(item.language, (v) => { item.language = v; schedulePreview(); }, "Idioma"));
        row.appendChild(cefrSelect(item.cefr_level, (v) => { item.cefr_level = v; item.declared_proficiency = v; schedulePreview(); }, "lvl"));
        row.appendChild(textInput(item.current_situation, (v) => { item.current_situation = v; schedulePreview(); }, "Situação atual"));
        const det = document.createElement("button"); det.type = "button"; det.className = "ghost"; det.textContent = "Detalhes";
        row.appendChild(det);
        row.appendChild(removeBtn(() => { langs.splice(index, 1); renderEducation(container); schedulePreview(); }));
        const extra = document.createElement("div"); extra.className = "skill-extra hidden";
        const grid = document.createElement("div"); grid.style.display = "grid"; grid.style.gridTemplateColumns = "repeat(3, 1fr)"; grid.style.gap = "8px";
        grid.appendChild(labeled("Leitura", cefrSelect(item.reading_level, (v) => { item.reading_level = v; schedulePreview(); })));
        grid.appendChild(labeled("Escrita", cefrSelect(item.writing_level, (v) => { item.writing_level = v; schedulePreview(); })));
        grid.appendChild(labeled("Conversação", cefrSelect(item.speaking_level, (v) => { item.speaking_level = v; schedulePreview(); })));
        const notes = document.createElement("textarea"); notes.rows = 2; notes.placeholder = "Notas (opcional)";
        notes.value = Array.isArray(item.evidence_notes) ? item.evidence_notes.join(" ") : (item.evidence_notes || "");
        notes.oninput = () => { item.evidence_notes = notes.value; schedulePreview(); };
        extra.appendChild(grid); extra.appendChild(notes);
        det.onclick = () => extra.classList.toggle("hidden");
        itemWrap.appendChild(row); itemWrap.appendChild(extra); L.items.appendChild(itemWrap);
      });
      L.items.insertBefore(addL, L.items.firstChild);
      L.count.textContent = langs.length + " idioma(s)";

      // Certificações
      const C = block("Certificações", "cert");
      const addC = addBtn("+ Adicionar certificação", () => { certs.unshift({ name: "", year: new Date().getFullYear() }); renderEducation(container); schedulePreview(); });
      C.items.appendChild(addC);
      certs.forEach((item, index) => {
        const row = document.createElement("div"); row.className = "skill-row";
        row.appendChild(textInput(item.name, (v) => { item.name = v; schedulePreview(); }, "Nome da certificação"));
        const year = document.createElement("input"); year.type = "number"; year.value = item.year ?? "";
        year.oninput = () => { const n = parseInt(year.value, 10); item.year = isNaN(n) ? null : n; schedulePreview(); };
        row.appendChild(year);
        row.appendChild(removeBtn(() => { certs.splice(index, 1); renderEducation(container); schedulePreview(); }));
        C.items.appendChild(row);
      });
      C.items.insertBefore(addC, C.items.firstChild);
      C.count.textContent = certs.length + " certificação(ões)";
    }

    // editor genérico para campos não curados
    function hiddenTop(k, file) {
      if (k.startsWith("_")) return true;
      if (file === "config.json" && k === "schedule") return true;
      if (file === "profile.yaml" && k === "work_preferences") return true;
      return false;
    }
    function renderGeneric(key, value, path) {
      const wrap = document.createElement("div");
      if (value && typeof value === "object" && !Array.isArray(value)) {
        const keys0 = Object.keys(value).filter((k) => !k.startsWith("_"));
        if (!keys0.length) {
          const row = document.createElement("div"); row.className = "field";
          const lab = document.createElement("div"); lab.className = "field-label"; lab.appendChild(Object.assign(document.createElement("div"), { className: "name", textContent: pt(key), title: key }));
          const box = document.createElement("div"); box.className = "field-input";
          const ta = document.createElement("textarea"); ta.rows = 5; ta.value = "{}";
          ta.oninput = () => { try { set(docWorking, path, JSON.parse(ta.value || "{}")); schedulePreview(); } catch (e) {} };
          box.appendChild(ta); row.appendChild(lab); row.appendChild(box); wrap.appendChild(row); return wrap;
        }
        const det = document.createElement("details"); det.className = "block";
        det.appendChild(Object.assign(document.createElement("summary"), { textContent: pt(key), title: key }));
        const body = document.createElement("div"); body.className = "body"; det.appendChild(body);
        for (const k of Object.keys(value)) {
          if (k.startsWith("_")) continue;
          body.appendChild(renderGeneric(k, value[k], path + "." + k));
        }
        wrap.appendChild(det); return wrap;
      }
      if (Array.isArray(value)) {
        const row = document.createElement("div"); row.className = "field";
        const lab = document.createElement("div"); lab.className = "field-label"; lab.appendChild(Object.assign(document.createElement("div"), { className: "name", textContent: pt(key), title: key }));
        const box = document.createElement("div"); box.className = "field-input";
        const objs = value.length && value.every((v) => v && typeof v === "object" && !Array.isArray(v));
        if (!objs) {
          const ta = document.createElement("textarea"); ta.rows = 10;
          ta.value = value.map((v) => (v == null ? "" : String(v))).join("\n");
          ta.oninput = () => { set(docWorking, path, ta.value.split("\n").map((s) => s.trim()).filter(Boolean)); schedulePreview(); };
          box.appendChild(ta);
        } else {
          const pre = document.createElement("pre"); pre.className = "preview"; pre.textContent = JSON.stringify(value, null, 2);
          box.appendChild(pre);
          const note = document.createElement("div"); note.className = "msg"; note.textContent = "Para editar itens desta lista, use o modo Texto puro.";
          box.appendChild(note);
        }
        row.appendChild(lab); row.appendChild(box); wrap.appendChild(row); return wrap;
      }
      const row = document.createElement("div"); row.className = "field";
      const lab = document.createElement("div"); lab.className = "field-label"; lab.appendChild(Object.assign(document.createElement("div"), { className: "name", textContent: pt(key), title: key }));
      const box = document.createElement("div"); box.className = "field-input";
      let el;
      if (typeof value === "boolean") {
        el = document.createElement("input"); el.type = "checkbox"; el.checked = value;
        el.onchange = () => { set(docWorking, path, el.checked); schedulePreview(); };
      } else if (typeof value === "number") {
        el = document.createElement("input"); el.type = "number"; el.value = value;
        el.oninput = () => { const n = parseFloat(el.value); set(docWorking, path, isNaN(n) ? null : n); schedulePreview(); };
      } else {
        const s = String(value ?? "");
        if (s.length > 80 || s.includes("\n")) { el = document.createElement("textarea"); el.rows = 4; }
        else { el = document.createElement("input"); el.type = "text"; }
        el.value = s;
        el.oninput = () => { set(docWorking, path, el.value); schedulePreview(); };
      }
      box.appendChild(el); row.appendChild(lab); row.appendChild(box); wrap.appendChild(row); return wrap;
    }

    const LEVELS = [
  ["hands_on", "Hands-on (comprovado)"],
  ["working_knowledge", "Conhecimento prático"],
  ["learning", "Aprendendo"],
  ["conceptual", "Conceitual"],
  ["none", "Nenhum"]
];
function companyClass(name) {
  let h = 0; const s = String(name || "");
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return "c" + (h % 8);
}
function toast(text) {
  let el = document.getElementById("toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; document.body.appendChild(el); }
  el.textContent = text; el.classList.add("show");
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("show"), 2600);
}
function guessPlatform(value) {
  const h = String(value || "").toLowerCase();
  if (h.includes("myworkdayjobs.com")) return "Workday";
  if (h.includes("gupy.io")) return "Gupy";
  if (h.includes("inhire.app")) return "InHire";
  if (h.includes("icims.com")) return "iCIMS";
  if (h.includes("greenhouse.io")) return "Greenhouse";
  if (h.includes("lever.co")) return "Lever";
  if (h.includes("workable.com")) return "Workable";
  if (h.includes("smartrecruiters.com")) return "SmartRecruiters";
  if (h.includes("bamboohr.com")) return "BambooHR";
  if (h.includes("recruitee.com")) return "Recruitee";
  if (h.includes("personio")) return "Personio";
  return "Desconhecida";
}
function prettyName(v) {
  return String(v || "").replace(/[-_.]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).trim();
}
function parseSourceDomain(raw) {
  const s = String(raw || "").trim();
  if (!s) return null;
  let url; try { url = new URL(/^https?:\/\//i.test(s) ? s : "https://" + s); } catch (e) { return null; }
  const host = url.hostname.toLowerCase();
  const segs = url.pathname.split("/").filter(Boolean).filter((p) => !/^[a-z]{2}(-[a-z]{2})?$/i.test(p));
  if (host.endsWith(".gupy.io")) {
    const sub = host.slice(0, -".gupy.io".length);
    if (!sub) return null;
    return { kind: "gupy", subdomain: sub, name: prettyName(sub) };
  }
  if (host.includes("myworkdayjobs.com")) {
    const tenant = host.split(".")[0];
    return { kind: "workday", subdomain: host, name: prettyName(tenant), tenant, site: segs[0] || "" };
  }
  if (host.endsWith(".inhire.app")) {
    const tenant = host.slice(0, -".inhire.app".length).split(".").pop();
    return { kind: "inhire", tenant };
  }
  return null;
}
function sourceRow(opts) {
  const wrap = document.createElement("div"); wrap.className = "source-item";
  const row = document.createElement("div"); row.className = "skill-row"; row.style.padding = "5px 0";
  const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = opts.enabled !== false;
  row.appendChild(cb);
  const title = document.createElement("span"); title.textContent = opts.title || "—"; title.style.fontWeight = "700"; title.style.minWidth = "170px"; title.style.flex = "0 0 auto";
  row.appendChild(title);
  const link = document.createElement("a"); link.href = opts.href || "#"; link.target = "_blank"; link.className = "mono"; link.textContent = opts.domain || ""; link.style.flex = "1 1 auto"; link.style.minWidth = "0"; link.style.overflow = "hidden"; link.style.textOverflow = "ellipsis"; link.style.whiteSpace = "nowrap";
  row.appendChild(link);
  if (opts.badge) { const badge = document.createElement("span"); badge.className = "chip tag"; badge.textContent = opts.badge; row.appendChild(badge); }
  const chip = document.createElement("span"); chip.className = "chip " + (opts.enabled !== false ? "yes" : "no"); chip.textContent = opts.enabled !== false ? "habilitado" : "desabilitado";
  row.appendChild(chip);
  cb.onchange = () => opts.onToggle(cb.checked, chip);
  function setDisplay(d) {
    if (d.title !== undefined) title.textContent = d.title || "—";
    if (d.domain !== undefined) link.textContent = d.domain;
    if (d.href !== undefined) link.href = d.href || "#";
  }
  let editor = null;
  if (opts.editor) {
    const btn = document.createElement("button"); btn.type = "button"; btn.className = "ghost"; btn.textContent = "Editar";
    editor = document.createElement("div"); editor.className = "skill-extra hidden"; editor.appendChild(opts.editor(setDisplay));
    btn.onclick = () => editor.classList.toggle("hidden");
    row.appendChild(btn);
  }
  if (opts.onRemove) row.appendChild(removeButton(opts.onRemove));
  wrap.appendChild(row); if (editor) wrap.appendChild(editor);
  return wrap;
}
async function loadInhireData() {
  const r = await j("/api/file?name=" + encodeURIComponent("data/inhire_companies.json"));
  let data = { companies: [], disabled: [] };
  if (r.ok) { try { data = JSON.parse(r.content); } catch (e) { data = { companies: [], disabled: [] }; } }
  if (Array.isArray(data)) data = { companies: data, disabled: [] };
  data.companies = Array.isArray(data.companies) ? data.companies : [];
  data.disabled = Array.isArray(data.disabled) ? data.disabled : [];
  return data;
}
async function saveInhireData(data) {
  const res = await fetch("/api/file", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: "data/inhire_companies.json", content: JSON.stringify(data, null, 2) }) });
  return (await res.json()).ok;
}
async function addInhireTenant(tenant) {
  const data = await loadInhireData();
  if (!data.companies.includes(tenant)) data.companies.push(tenant);
  data.disabled = data.disabled.filter((x) => x !== tenant);
  return saveInhireData(data);
}
async function toggleInhire(slug, enabled) {
  const data = await loadInhireData();
  if (!data.companies.includes(slug)) data.companies.push(slug);
  if (enabled) data.disabled = data.disabled.filter((x) => x !== slug);
  else if (!data.disabled.includes(slug)) data.disabled.push(slug);
  return saveInhireData(data);
}
async function renameInhire(oldSlug, newSlug) {
  const data = await loadInhireData();
  data.companies = [...new Set(data.companies.map((s) => (s === oldSlug ? newSlug : s)))];
  data.disabled = [...new Set(data.disabled.map((s) => (s === oldSlug ? newSlug : s)))].filter((s) => data.companies.includes(s));
  return saveInhireData(data);
}
async function removeInhire(slug) {
  const data = await loadInhireData();
  data.companies = data.companies.filter((s) => s !== slug);
  data.disabled = data.disabled.filter((s) => s !== slug);
  return saveInhireData(data);
}
async function renderInhireCompanies(listEl, query, countEl) {
  const data = await loadInhireData();
  const companies = [...new Set(data.companies.map(String))].sort((a, b) => a.localeCompare(b));
  const disabled = new Set(data.disabled);
  if (countEl) countEl.textContent = companies.length + " empresa(s)";
  const q = String(query || "").toLowerCase().trim();
  const list = companies.filter((c) => !q || c.toLowerCase().includes(q));
  listEl.innerHTML = "";
  if (!list.length) { listEl.appendChild(Object.assign(document.createElement("div"), { className: "msg", textContent: "nenhuma empresa" })); return; }
  const refresh = () => renderInhireCompanies(listEl, query, countEl);
  list.forEach((slug) => {
    listEl.appendChild(sourceRow({
      enabled: !disabled.has(slug),
      title: slug,
      domain: slug + ".inhire.app",
      href: "https://" + slug + ".inhire.app",
      onToggle: async (checked, chip) => {
        const ok = await toggleInhire(slug, checked);
        if (!ok) { toast("Falha ao salvar"); return; }
        chip.textContent = checked ? "habilitado" : "desabilitado";
        chip.className = "chip " + (checked ? "yes" : "no");
      },
      editor: (setDisplay) => {
        const inp = document.createElement("input"); inp.type = "text"; inp.value = slug; inp.placeholder = "slug";
        inp.onchange = async () => {
          const nv = inp.value.trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
          if (!nv || nv === slug) { inp.value = slug; return; }
          const ok = await renameInhire(slug, nv);
          toast(ok ? "Renomeado: " + nv : "Falha ao renomear");
          if (ok) refresh();
        };
        return labeled("Slug", inp);
      },
      onRemove: async () => { const ok = await removeInhire(slug); toast(ok ? "Removido: " + slug : "Falha"); if (ok) refresh(); },
    }));
  });
}
function slug(v) {
  return String(v || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}
function evidenceText(v) {
  if (Array.isArray(v)) return v.join("\n");
  return v == null ? "" : String(v);
}
function experienceCompanies() { return [...new Set((docWorking.experience || []).map((e) => e.company).filter(Boolean))]; }
function shortCompany(c) {
  return String(c || "");
}
function matchCompany(v) {
  const list = experienceCompanies();
  return list.find((c) => c === v) || list.find((c) => c.includes(v)) || list.find((c) => v.includes(c)) || v;
}
function toggleSkillCompany(skill, company, selected) {
  const full = matchCompany(company);
  const exps = docWorking.experience || [];
  const exp = exps.find((e) => e.company === full) || exps.find((e) => e.company === company);
  skill.companies = [...new Set((skill.companies || []).map(matchCompany))];
  if (selected) {
    if (!skill.companies.includes(full)) skill.companies.push(full);
    if (exp) { exp.technologies = Array.isArray(exp.technologies) ? exp.technologies : []; if (!exp.technologies.includes(skill.name)) exp.technologies.push(skill.name); }
  } else {
    skill.companies = skill.companies.filter((c) => c !== full && c !== company);
    if (exp) exp.technologies = (exp.technologies || []).filter((t) => t !== skill.name);
  }
  if (!skill.companies.length) delete skill.companies;
}
function recomputeSkillCompanies() {
  const exps = docWorking.experience || [];
  (docWorking.skills || []).forEach((s) => {
    const comps = [];
    exps.forEach((e) => { if (e.company && (e.technologies || []).includes(s.name) && !comps.includes(e.company)) comps.push(e.company); });
    if (comps.length) s.companies = comps; else delete s.companies;
  });
}
function skillRow(skill, groups, kind, container) {
  const wrap = document.createElement("div");
  const row = document.createElement("div"); row.className = "skill-row";
  const name = document.createElement("input"); name.type = "text";
  name.placeholder = "Ex.: Acme Corp";
  name.value = skill.label || skill.name || "";
  name.oninput = () => { skill.label = name.value; if (!skill.name) skill.name = slug(name.value); schedulePreview(); };
  const grp = document.createElement("select");
  groups.filter((g) => g.kind === kind).sort((a, b) => a.name.localeCompare(b.name)).forEach((g) => {
    const o = document.createElement("option"); o.value = g.name; o.textContent = g.name;
    if (skill.group === g.name) o.selected = true; grp.appendChild(o);
  });
  grp.onchange = () => { skill.group = grp.value; renderSkills(container); schedulePreview(); };
  const lvl = document.createElement("select");
  LEVELS.forEach(([v, l]) => { const o = document.createElement("option"); o.value = v; o.textContent = l; if (skill.level === v) o.selected = true; lvl.appendChild(o); });
  lvl.onchange = () => { skill.level = lvl.value; schedulePreview(); };
  function mkCompanyChip(text, title) {
    const s = document.createElement("span");
    s.className = "company " + (title ? companyClass(title) : "c0");
    s.textContent = text; if (title) s.title = title;
    return s;
  }
  const combo = document.createElement("div"); combo.className = "combo";
  const comboBtn = document.createElement("button"); comboBtn.type = "button"; comboBtn.className = "combo-btn";
  const comboMenu = document.createElement("div"); comboMenu.className = "combo-menu hidden";
  function renderComboBtn() {
    comboBtn.innerHTML = "";
    const sel = [...new Set(skill.companies || [])].sort((a, b) => shortCompany(a).localeCompare(shortCompany(b)));
    if (!sel.length) comboBtn.appendChild(Object.assign(document.createElement("span"), { className: "chip", textContent: "empresas ▾" }));
    else sel.forEach((c) => comboBtn.appendChild(mkCompanyChip(shortCompany(c), c)));
    comboBtn.appendChild(Object.assign(document.createElement("span"), { className: "caret", textContent: "▾" }));
  }
  function renderComboMenu() {
    comboMenu.innerHTML = "";
    const all = experienceCompanies();
    if (!all.length) { comboMenu.appendChild(Object.assign(document.createElement("div"), { className: "msg", textContent: "Cadastre uma experiência primeiro." })); return; }
    all.forEach((c) => {
      const lab = document.createElement("label");
      const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = (skill.companies || []).includes(c);
      cb.onchange = () => { toggleSkillCompany(skill, c, cb.checked); renderComboBtn(); if (window._expContainer) renderExperience(window._expContainer); schedulePreview(); };
      lab.appendChild(cb); lab.appendChild(mkCompanyChip(shortCompany(c), c)); comboMenu.appendChild(lab);
    });
  }
  comboMenu.addEventListener("click", (e) => e.stopPropagation());
  comboBtn.onclick = (e) => { e.stopPropagation(); renderComboMenu(); comboMenu.classList.toggle("hidden"); };
  renderComboBtn();
  combo.appendChild(comboBtn); combo.appendChild(comboMenu);
  const det = document.createElement("button"); det.type = "button"; det.className = "ghost"; det.textContent = "Detalhes";
  const rm = document.createElement("button"); rm.type = "button"; rm.className = "danger"; rm.textContent = "Remover";
  row.appendChild(name); row.appendChild(grp); row.appendChild(lvl); row.appendChild(combo); row.appendChild(det); row.appendChild(rm);
  const extra = document.createElement("div"); extra.className = "skill-extra hidden";
  const ev = document.createElement("textarea"); ev.rows = 2; ev.placeholder = "Evidências (opcional)";
  ev.value = evidenceText(skill.evidence);
  ev.oninput = () => { skill.evidence = ev.value || undefined; schedulePreview(); };
  const lim = document.createElement("input"); lim.type = "text"; lim.placeholder = "Limitações (opcional)";
  lim.value = skill.limitations || "";
  lim.oninput = () => { skill.limitations = lim.value || undefined; schedulePreview(); };
  const notes = document.createElement("textarea"); notes.rows = 2;
  notes.placeholder = "Observação (opcional) — ex.: outras ferramentas";
  notes.value = skill.notes || "";
  notes.oninput = () => { skill.notes = notes.value || undefined; schedulePreview(); };
  extra.appendChild(ev); extra.appendChild(lim); extra.appendChild(notes);
  det.onclick = () => extra.classList.toggle("hidden");
  rm.onclick = () => { const i = docWorking.skills.indexOf(skill); if (i >= 0) docWorking.skills.splice(i, 1); renderSkills(container); schedulePreview(); };
  wrap.appendChild(row); wrap.appendChild(extra);
  return wrap;
}
function renderSkills(container) {
  window._skillsContainer = container;
  recomputeSkillCompanies();
  const groups = Array.isArray(docWorking.skill_groups) ? docWorking.skill_groups : (docWorking.skill_groups = []);
  const skills = Array.isArray(docWorking.skills) ? docWorking.skills : (docWorking.skills = []);
  container.innerHTML = "";

  container.appendChild(Object.assign(document.createElement("h4"), { textContent: "Grupos", style: "margin:4px 0 8px" }));
  const ge = document.createElement("div"); ge.className = "groups-editor";
  groups.forEach((g, idx) => {
    const row = document.createElement("div"); row.className = "group-row " + g.kind;
    const name = document.createElement("input"); name.type = "text"; name.value = g.name;
    name.onchange = () => {
      const old = g.name; const nv = slug(name.value) || old;
      g.name = nv; skills.forEach((s) => { if (s.group === old) s.group = nv; });
      renderSkills(container); schedulePreview();
    };
    const kind = document.createElement("select");
    [["hard", "Técnica"], ["soft", "Comportamental"]].forEach(([v, l]) => {
      const o = document.createElement("option"); o.value = v; o.textContent = l; if (g.kind === v) o.selected = true; kind.appendChild(o);
    });
    kind.onchange = () => { g.kind = kind.value; renderSkills(container); schedulePreview(); };
    const rm = document.createElement("button"); rm.type = "button"; rm.className = "danger"; rm.textContent = "Remover";
    rm.onclick = () => {
      if (skills.some((s) => s.group === g.name)) { alert("Mova ou remova as skills deste grupo antes."); return; }
      groups.splice(idx, 1); renderSkills(container); schedulePreview();
    };
    row.appendChild(name); row.appendChild(kind); row.appendChild(rm); ge.appendChild(row);
  });
  const addG = document.createElement("button"); addG.type = "button"; addG.className = "ghost"; addG.textContent = "+ Adicionar grupo";
  addG.onclick = () => {
    let i = 1, n = "novo_grupo";
    while (groups.some((g) => g.name === n)) n = "novo_grupo_" + (++i);
    groups.push({ name: n, kind: "hard" }); renderSkills(container); schedulePreview();
  };
  ge.appendChild(addG); container.appendChild(ge);

  ["hard", "soft"].forEach((kind) => {
    const block = document.createElement("div"); block.className = "skill-block";
    block.appendChild(Object.assign(document.createElement("h4"), { textContent: kind === "hard" ? "Competências técnicas" : "Competências comportamentais" }));
    const gs = [...new Set(groups.filter((g) => g.kind === kind).map((g) => g.name))].sort();
    if (!gs.length) { const p = document.createElement("div"); p.className = "msg"; p.textContent = "Nenhum grupo " + (kind === "hard" ? "técnico" : "comportamental") + "."; block.appendChild(p); }
    gs.forEach((gname) => {
      const items = skills.filter((s) => s.group === gname).sort((a, b) => String(a.label || a.name).localeCompare(String(b.label || b.name)));
      const header = document.createElement("div"); header.className = "skill-group " + kind;
      header.appendChild(Object.assign(document.createElement("span"), { className: "gname", textContent: gname }));
      header.appendChild(Object.assign(document.createElement("span"), { className: "count", textContent: items.length + " skill(s)" }));
      block.appendChild(header);
      const itemsWrap = document.createElement("div"); itemsWrap.className = "skill-items " + kind;
      const addBtn = document.createElement("button"); addBtn.type = "button"; addBtn.className = "ghost"; addBtn.textContent = "+ Adicionar skill";
      addBtn.onclick = () => { skills.push({ name: "", label: "", group: gname, level: "learning" }); renderSkills(container); schedulePreview(); };
      itemsWrap.appendChild(addBtn);
      if (!items.length) itemsWrap.appendChild(Object.assign(document.createElement("div"), { className: "msg", textContent: "—" }));
      items.forEach((s) => itemsWrap.appendChild(skillRow(s, groups, kind, container)));
      block.appendChild(itemsWrap);
    }); container.appendChild(block);
  });
}
function pillBlock(container, title, cls) {
  const wrap = document.createElement("div"); wrap.className = "edu-block";
  const header = document.createElement("div"); header.className = "skill-group " + cls;
  header.appendChild(Object.assign(document.createElement("span"), { className: "gname", textContent: title }));
  const count = document.createElement("span"); count.className = "count"; header.appendChild(count);
  wrap.appendChild(header);
  const items = document.createElement("div"); items.className = "skill-items " + cls;
  wrap.appendChild(items); container.appendChild(wrap);
  return { items, count };
}
function removeButton(onClick) {
  const b = document.createElement("button"); b.type = "button"; b.className = "danger"; b.textContent = "Remover";
  b.onclick = onClick; return b;
}
function addButton(label, onClick) {
  const b = document.createElement("button"); b.type = "button"; b.className = "ghost"; b.textContent = label;
  b.onclick = onClick; return b;
}
function renderTargets(container) {
  container.innerHTML = "";
  const targets = Array.isArray(docWorking.targets) ? docWorking.targets : (docWorking.targets = []);
  const B = pillBlock(container, "Alvos", "edu");
  const add = addButton("+ Adicionar país", () => { targets.unshift({ country: "", work_mode: "any" }); renderTargets(container); schedulePreview(); });
  B.items.appendChild(add);
  targets.forEach((t, index) => {
    const row = document.createElement("div"); row.className = "skill-row";
    row.appendChild(textInput(t.country, (v) => { t.country = v; schedulePreview(); }, "País — ex.: Brazil"));
    const mode = document.createElement("select"); mode.className = "lvl";
    [["remote", "Remoto"], ["hybrid", "Híbrido"], ["onsite", "Presencial"], ["any", "Qualquer"]].forEach(([v, l]) => {
      const o = document.createElement("option"); o.value = v; o.textContent = l; if ((t.work_mode || "any") === v) o.selected = true; mode.appendChild(o);
    });
    mode.onchange = () => { t.work_mode = mode.value; schedulePreview(); };
    row.appendChild(mode);
    row.appendChild(removeButton(() => { targets.splice(index, 1); renderTargets(container); schedulePreview(); }));
    B.items.appendChild(row);
  });
  B.count.textContent = targets.length + " alvo(s)";
  container.appendChild(fieldRow({ key: "work_modes", label: "Modalidades aceitas", type: "modes", help: "Remoto, Híbrido ou Local. Desmarque o que não aceita." }, (v) => { set(docWorking, "work_modes", v); schedulePreview(); }));
}
function renderSources(container) {
  container.innerHTML = "";
  let refreshIh = null;

  // Fontes usadas no comando de busca
  const scanWrap = document.createElement("div"); scanWrap.className = "skill-row"; scanWrap.style.marginBottom = "10px";
  scanWrap.appendChild(Object.assign(document.createElement("div"), { className: "msg", textContent: "Fontes ao rodar a busca:" }));
  const scan0 = Array.isArray(docWorking.scan_sources) ? docWorking.scan_sources : (docWorking.scan_sources = ["linkedin", "indeed", "gupy", "workday", "inhire"]);
  const scan = new Set(scan0);
  if (scan.has("banks")) { scan.delete("banks"); scan.add("gupy"); scan.add("workday"); }
  if (scan.has("jobspy")) { scan.delete("jobspy"); scan.add("linkedin"); scan.add("indeed"); }
  [["linkedin", "LinkedIn"], ["indeed", "Indeed"], ["gupy", "Gupy"], ["workday", "Workday"], ["inhire", "InHire"], ["other", "Outros (em testes)"]].forEach(([k, l]) => {
    const lab = document.createElement("label"); lab.style.display = "flex"; lab.style.alignItems = "center"; lab.style.gap = "6px"; lab.style.color = "var(--fg)"; lab.style.fontSize = "13px";
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = scan.has(k);
    cb.onchange = () => {
      let arr = Array.isArray(docWorking.scan_sources) ? docWorking.scan_sources : (docWorking.scan_sources = []);
      arr = arr.filter((x) => x !== "banks" && x !== "jobspy");
      if (cb.checked) { if (!arr.includes(k)) arr.push(k); } else arr = arr.filter((x) => x !== k);
      docWorking.scan_sources = arr;
      schedulePreview();
    };
    lab.appendChild(cb); lab.appendChild(document.createTextNode(" " + l)); scanWrap.appendChild(lab);
  });
  container.appendChild(scanWrap);

  // Adicionar por domínio
  const domainRow = document.createElement("div"); domainRow.className = "skill-row"; domainRow.style.marginBottom = "10px";
  const domainInput = textInput("", () => {}, "Cole o domínio: empresa.gupy.io, empresa.wd3.myworkdayjobs.com/Site, empresa.inhire.app");
  const detectBtn = addButton("Detectar e adicionar", async () => {
    const raw = domainInput.value.trim();
    const parsed = parseSourceDomain(raw);
    if (!parsed) {
      const withProto = /^https?:\/\//i.test(raw) ? raw : "https://" + raw;
      let host = "";
      try { host = new URL(withProto).hostname.toLowerCase(); } catch (e) { toast("Domínio inválido."); return; }
      if (!host.includes(".")) { toast("Domínio inválido."); return; }
      const platform = guessPlatform(host);
      docWorking.other_sources = Array.isArray(docWorking.other_sources) ? docWorking.other_sources : [];
      docWorking.other_sources.unshift({ name: prettyName(host.split(".")[0]), url: withProto, platform, enabled: true });
      toast("Adicionado em Outros (" + platform + ")");
      renderSources(container); schedulePreview();
      return;
    }
    if (parsed.kind === "gupy") {
      docWorking.banks = Array.isArray(docWorking.banks) ? docWorking.banks : [];
      docWorking.banks.unshift({ name: parsed.name, subdomain: parsed.subdomain, source: "gupy" });
      toast("Gupy adicionado: " + parsed.subdomain);
    } else if (parsed.kind === "workday") {
      docWorking.banks = Array.isArray(docWorking.banks) ? docWorking.banks : [];
      docWorking.banks.unshift({ name: parsed.name, subdomain: parsed.subdomain, source: "workday", workday: { tenant: parsed.tenant, site: parsed.site } });
      toast("Workday adicionado: " + parsed.tenant + (parsed.site ? " / " + parsed.site : ""));
    } else if (parsed.kind === "inhire") {
      const ok = await addInhireTenant(parsed.tenant);
      toast(ok ? "InHire adicionado: " + parsed.tenant : "Falha ao salvar InHire.");
      if (ok && refreshIh) refreshIh();
      return;
    }
    renderSources(container); schedulePreview();
  });
  domainRow.appendChild(domainInput); domainRow.appendChild(detectBtn); container.appendChild(domainRow);

  // InHire: todas as empresas, alfabética, domínio e habilitado
  const IH = pillBlock(container, "InHire", "idioma");
  const ihTools = document.createElement("div"); ihTools.className = "skill-row"; ihTools.style.marginBottom = "6px";
  const filter = document.createElement("input"); filter.type = "search"; filter.placeholder = "Filtrar empresas…";
  const addIn = textInput("", () => {}, "Adicionar tenant — ex.: nubank");
  const addBtn = addButton("+ Adicionar", async () => {
    const val = addIn.value.trim();
    if (!val) return;
    const parsed = parseSourceDomain(val) || { kind: "inhire", tenant: val.toLowerCase().replace(/\s+/g, "-") };
    if (parsed.kind !== "inhire") { toast("Informe um tenant ou domínio inhire."); return; }
    const ok = await addInhireTenant(parsed.tenant);
    toast(ok ? "InHire adicionado: " + parsed.tenant : "Falha ao salvar InHire.");
    if (ok) { addIn.value = ""; if (refreshIh) refreshIh(); }
  });
  ihTools.append(filter, addIn, addBtn); IH.items.appendChild(ihTools);
  const ihList = document.createElement("div"); ihList.style.maxHeight = "340px"; ihList.style.overflow = "auto";
  IH.items.appendChild(ihList);
  refreshIh = () => renderInhireCompanies(ihList, filter.value, IH.count);
  filter.oninput = refreshIh;
  refreshIh();

  // Gupy / Workday / Outros — mesmo padrão do InHire
  const banks = Array.isArray(docWorking.banks) ? docWorking.banks : (docWorking.banks = []);
  function banksGroup(title, cls, source) {
    const B = pillBlock(container, title, cls);
    const tools = document.createElement("div"); tools.className = "skill-row"; tools.style.marginBottom = "6px";
    const filter = document.createElement("input"); filter.type = "search"; filter.placeholder = "Filtrar…";
    const add = addButton("+ Adicionar", () => {
      banks.unshift(source === "workday"
        ? { name: "", subdomain: "", source: "workday", enabled: true, workday: { tenant: "", site: "" } }
        : { name: "", subdomain: "", source: "gupy", enabled: true });
      renderSources(container); schedulePreview();
    });
    tools.append(filter, add); B.items.appendChild(tools);
    const list = document.createElement("div"); list.style.maxHeight = "340px"; list.style.overflow = "auto"; B.items.appendChild(list);
    function renderList() {
      const q = String(filter.value || "").toLowerCase().trim();
      const arr = banks.filter((b) => (b.source || "gupy") === source)
        .slice().sort((a, b) => String(a.name || a.subdomain || "").localeCompare(String(b.name || b.subdomain || "")));
      const filtered = arr.filter((b) => !q || `${b.name} ${b.subdomain} ${(b.workday || {}).tenant || ""} ${(b.workday || {}).site || ""}`.toLowerCase().includes(q));
      B.count.textContent = arr.length + " banco(s)";
      list.innerHTML = "";
      if (!filtered.length) { list.appendChild(Object.assign(document.createElement("div"), { className: "msg", textContent: "nenhum banco" })); return; }
      filtered.forEach((b) => {
        const host = b.subdomain || "";
        const domain = source === "workday" ? host : (host ? host + ".gupy.io" : "");
        list.appendChild(sourceRow({
          enabled: b.enabled !== false,
          title: b.name || host || "(sem nome)",
          domain: domain,
          href: domain ? "https://" + domain : "#",
          onToggle: (checked, chip) => {
            b.enabled = checked;
            chip.textContent = checked ? "habilitado" : "desabilitado";
            chip.className = "chip " + (checked ? "yes" : "no");
            schedulePreview();
          },
          editor: (setDisplay) => {
            const box = document.createElement("div"); box.style.display = "grid"; box.style.gap = "8px";
            const name = document.createElement("input"); name.type = "text"; name.value = b.name || ""; name.placeholder = "Nome";
            name.onchange = () => { b.name = name.value; setDisplay({ title: b.name || b.subdomain }); schedulePreview(); };
            const sub = document.createElement("input"); sub.type = "text"; sub.value = b.subdomain || ""; sub.placeholder = source === "workday" ? "Host" : "Subdomínio";
            sub.onchange = () => { b.subdomain = sub.value; const d = source === "workday" ? sub.value : (sub.value ? sub.value + ".gupy.io" : ""); setDisplay({ domain: d, href: d ? "https://" + d : "#" }); schedulePreview(); };
            box.append(labeled("Nome", name), labeled(source === "workday" ? "Host" : "Subdomínio", sub));
            if (source === "workday") {
              b.workday = b.workday || { tenant: "", site: "" };
              const t = document.createElement("input"); t.type = "text"; t.value = b.workday.tenant || ""; t.placeholder = "Tenant"; t.onchange = () => { b.workday.tenant = t.value; schedulePreview(); };
              const s = document.createElement("input"); s.type = "text"; s.value = b.workday.site || ""; s.placeholder = "Site"; s.onchange = () => { b.workday.site = s.value; schedulePreview(); };
              box.append(labeled("Tenant", t), labeled("Site", s));
            }
            return box;
          },
          onRemove: () => { const i = banks.indexOf(b); if (i >= 0) banks.splice(i, 1); renderSources(container); schedulePreview(); },
        }));
      });
    }
    filter.oninput = renderList;
    renderList();
  }
  banksGroup("Gupy", "edu", "gupy");
  banksGroup("Workday", "cert", "workday");

  // Outros (em testes) — mesmo padrão
  const O = pillBlock(container, "Outros (em testes)", "idioma");
  O.items.appendChild(Object.assign(document.createElement("div"), { className: "msg", textContent: "Em testes: nem todas as plataformas são suportadas. Registre aqui para avaliar depois." }));
  const others = Array.isArray(docWorking.other_sources) ? docWorking.other_sources : (docWorking.other_sources = []);
  const oTools = document.createElement("div"); oTools.className = "skill-row"; oTools.style.marginBottom = "6px";
  const oFilter = document.createElement("input"); oFilter.type = "search"; oFilter.placeholder = "Filtrar…";
  const oAdd = addButton("+ Adicionar fonte (em testes)", () => { others.unshift({ name: "", url: "", enabled: true }); renderSources(container); schedulePreview(); });
  oTools.append(oFilter, oAdd); O.items.appendChild(oTools);
  const oList = document.createElement("div"); oList.style.maxHeight = "300px"; oList.style.overflow = "auto"; O.items.appendChild(oList);
  function renderOthers() {
    const q = String(oFilter.value || "").toLowerCase().trim();
    const arr = others.slice().sort((a, b) => String(a.name || a.url || "").localeCompare(String(b.name || b.url || "")));
    const filtered = arr.filter((o) => !q || `${o.name} ${o.url}`.toLowerCase().includes(q));
    O.count.textContent = arr.length + " fonte(s)";
    oList.innerHTML = "";
    if (!filtered.length) { oList.appendChild(Object.assign(document.createElement("div"), { className: "msg", textContent: "nenhuma fonte" })); return; }
    filtered.forEach((o) => {
      if (!o.platform) o.platform = guessPlatform(o.url);
      oList.appendChild(sourceRow({
        enabled: o.enabled !== false,
        title: o.name || "(sem nome)",
        domain: o.url || "",
        href: o.url || "#",
        badge: o.platform,
        onToggle: (checked, chip) => { o.enabled = checked; chip.textContent = checked ? "habilitado" : "desabilitado"; chip.className = "chip " + (checked ? "yes" : "no"); schedulePreview(); },
        editor: (setDisplay) => {
          const box = document.createElement("div"); box.style.display = "grid"; box.style.gap = "8px";
          const name = document.createElement("input"); name.type = "text"; name.value = o.name || ""; name.placeholder = "Nome";
          name.onchange = () => { o.name = name.value; setDisplay({ title: o.name || o.url }); schedulePreview(); };
          const url = document.createElement("input"); url.type = "text"; url.value = o.url || ""; url.placeholder = "URL";
          url.onchange = () => { o.url = url.value; o.platform = guessPlatform(o.url); setDisplay({ domain: o.url, href: o.url || "#" }); schedulePreview(); };
          const plat = document.createElement("input"); plat.type = "text"; plat.value = o.platform || ""; plat.placeholder = "Plataforma";
          plat.onchange = () => { o.platform = plat.value; schedulePreview(); };
          box.append(labeled("Nome", name), labeled("URL", url), labeled("Plataforma", plat));
          return box;
        },
        onRemove: () => { const i = others.indexOf(o); if (i >= 0) others.splice(i, 1); renderSources(container); schedulePreview(); },
      }));
    });
  }
  oFilter.oninput = renderOthers;
  renderOthers();
}

function renderAliases(box) {
  box.innerHTML = "";
  if (!docWorking.skill_aliases || typeof docWorking.skill_aliases !== "object" || Array.isArray(docWorking.skill_aliases)) docWorking.skill_aliases = {};
  const aliases = docWorking.skill_aliases;
  function draw() {
    box.innerHTML = "";
    Object.keys(aliases).sort((a, b) => a.localeCompare(b)).forEach((k) => {
      const row = document.createElement("div"); row.className = "skill-row";
      const nameIn = textInput(k, (v) => { const vals = aliases[k]; delete aliases[k]; const nv = (v || "").trim(); aliases[nv] = vals; schedulePreview(); }, "Skill (ex.: kubernetes)");
      nameIn.style.flex = "0 0 200px";
      const aliIn = textInput((aliases[k] || []).join(", "), (v) => { aliases[k] = v.split(",").map((s) => s.trim()).filter(Boolean); schedulePreview(); }, "apelidos separados por vírgula (ex.: k8s, eks, gke)");
      aliIn.style.flex = "1 1 auto";
      const rm = addButton("✕", () => { delete aliases[k]; draw(); schedulePreview(); });
      row.append(nameIn, aliIn, rm);
      box.appendChild(row);
    });
    box.appendChild(addButton("+ Adicionar apelido", () => { aliases[""] = []; draw(); schedulePreview(); }));
  }
  draw();
}

function renderSections(container, sections, curated) {
      container.innerHTML = "";
      for (const section of sections) {
        const sec = document.createElement("div"); sec.className = "section";
        if (["experience", "skills", "education", "targets", "sources", "aliases"].includes(section.type) || guidedFile === "profile.yaml") sec.classList.add("wide");
        const head = document.createElement("div"); head.className = "section-head";
        head.appendChild(Object.assign(document.createElement("span"), { className: "section-num", textContent: section.n }));
        head.appendChild(Object.assign(document.createElement("h3"), { textContent: section.title }));
        sec.appendChild(head);
        if (section.desc) sec.appendChild(Object.assign(document.createElement("p"), { className: "section-desc", textContent: section.desc }));
        if (section.type === "experience") {
          const box = document.createElement("div"); sec.appendChild(box); renderExperience(box);
        } else if (section.type === "skills") {
          const box = document.createElement("div"); sec.appendChild(box); renderSkills(box);
        } else if (section.type === "education") {
          const box = document.createElement("div"); sec.appendChild(box); renderEducation(box);
        } else if (section.type === "targets") {
          const box = document.createElement("div"); sec.appendChild(box); renderTargets(box);
        } else if (section.type === "sources") {
          const box = document.createElement("div"); sec.appendChild(box); renderSources(box);
        } else if (section.type === "aliases") {
          const box = document.createElement("div"); sec.appendChild(box); renderAliases(box);
        } else {
          for (const field of section.fields) {
            sec.appendChild(fieldRow(field, (v) => { set(docWorking, field.key, v); schedulePreview(); }));
          }
        }
        container.appendChild(sec);
      }
      const others = Object.keys(docWorking).filter((k) => !curated.has(k) && !hiddenTop(k, guidedFile));
      if (others.length) {
        const det = document.createElement("details"); det.className = "block";
        det.appendChild(Object.assign(document.createElement("summary"), { textContent: "Outros campos (avançado)" }));
        const body = document.createElement("div"); body.className = "body";
        for (const k of others) body.appendChild(renderGeneric(k, docWorking[k], k));
        det.appendChild(body); container.appendChild(det);
      }
    }

    async function loadGuided(file) {
      if (guidedFile !== file) { guidedFile = file; docWorking = null; }
      const r = await j("/api/guided?name=" + encodeURIComponent(file));
      if (!r.ok) { ($(file === "config.json" ? "cfg-msg" : "profile-msg")).textContent = "erro: " + r.error; return; }
      docOriginal = r.data; docWorking = clone(r.data);
      const form = $(file === "config.json" ? "cfg-form" : "profile-form");
      renderSections(form, file === "config.json" ? CONFIG_SECTIONS : PROFILE_SECTIONS, file === "config.json" ? CONFIG_CURATED : PROFILE_CURATED);
      $(file === "config.json" ? "cfg-msg" : "profile-msg").textContent = "";
      updatePreview();
      if (file === "config.json") loadSeed();
      else loadRaw(file, "profile-raw-text");
    }
    function reloadGuided() { if (guidedFile) loadGuided(guidedFile); }
    function expandAll(open) { document.querySelectorAll("main details.block").forEach((d) => (d.open = open)); }

    function schedulePreview() { clearTimeout(previewTimer); previewTimer = setTimeout(updatePreview, 350); }
    async function updatePreview() {
      if (!guidedFile) return;
      const r = await fetch("/api/guided/preview", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: guidedFile, data: docWorking }) });
      const jr = await r.json();
      const target = $(guidedFile === "config.json" ? "cfg-preview" : "profile-preview");
      if (target) target.textContent = jr.ok ? jr.preview : "erro: " + jr.error;
    }
    async function saveGuided() {
      if (!guidedFile) return;
      const r = await fetch("/api/guided/save", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: guidedFile, data: docWorking }) });
      const jr = await r.json();
      const msg = $(guidedFile === "config.json" ? "cfg-save-msg" : "profile-save-msg");
      msg.textContent = jr.ok ? "salvo ✓" : "erro: " + jr.error;
      if (jr.ok) loadGuided(guidedFile);
    }

    // modo texto puro
    function setEditorMode(which, mode) {
      const isCfg = which === "config";
      const guided = mode === "guided";
      $(isCfg ? "cfg-mode" : "profile-mode").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
      $(isCfg ? "cfg-guided" : "profile-guided").classList.toggle("hidden", !guided);
      $(isCfg ? "cfg-raw" : "profile-raw").classList.toggle("hidden", guided);
      if (!guided) loadRaw(isCfg ? "config.json" : "profile.yaml", isCfg ? "cfg-raw-text" : "profile-raw-text");
    }
    document.querySelectorAll("#cfg-mode button").forEach((b) => (b.onclick = () => setEditorMode("config", b.dataset.mode)));
    document.querySelectorAll("#profile-mode button").forEach((b) => (b.onclick = () => setEditorMode("profile", b.dataset.mode)));

    async function loadRaw(file, target) {
      const r = await j("/api/file?name=" + encodeURIComponent(file));
      if (r.ok && $(target)) $(target).value = r.content || "";
    }
    async function saveRaw(file, target, msgId) {
      const r = await fetch("/api/file", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: file, content: $(target).value }) });
      const jr = await r.json();
      $(msgId).textContent = jr.ok ? "salvo ✓" : "erro: " + jr.error;
    }
    async function loadSeed() {
      const r = await j("/api/file?name=" + encodeURIComponent("data/inhire_seed.txt"));
      if (r.ok) $("seed-text").value = r.content || "";
    }

    // ---------------------------------------------------------------- agenda
    const SOURCE_OPTS = [["linkedin", "LinkedIn"], ["indeed", "Indeed"], ["gupy", "Gupy"], ["workday", "Workday"], ["inhire", "InHire"]];
    function renderSourceFreq(scanSources, freq) {
      const box = $("sch-sources");
      if (!box) return;
      const scan = (scanSources && scanSources.length) ? scanSources : ["linkedin", "indeed", "gupy", "workday", "inhire"];
      box.innerHTML = SOURCE_OPTS.map(([k, l]) => {
        const v = (freq || {})[k] === "daily" ? "daily" : "every";
        const disabled = !scan.includes(k);
        return `<div class="cred-row" style="grid-template-columns:1fr 200px">
          <div><span>${l}</span> <span class="mono" style="font-size:10px;color:var(--dim)">${k}</span>${disabled ? ' <span class="chip no">desabilitada</span>' : ""}</div>
          <select data-src="${k}" ${disabled ? "disabled" : ""}>
            <option value="every" ${v === "every" ? "selected" : ""}>Toda execução</option>
            <option value="daily" ${v === "daily" ? "selected" : ""}>1x por dia</option>
          </select>
        </div>`;
      }).join("");
    }
    async function loadSchedule() {
      const r = await j("/api/schedule");
      const s = r.schedule || {};
      $("sch-enabled").checked = !!s.enabled;
      $("sch-times").value = (s.times || []).join(", ");
      $("sch-digest").value = s.digest_at || "";
      $("sch-grace").value = s.grace_minutes ?? 30;
      renderSourceFreq(r.scan_sources || [], s.source_frequency || {});
      renderNext(r.next_runs || []);
    }
    function renderNext(list) {
      $("sch-next").innerHTML = (list || []).map((x) =>
        `<div class="mono" style="padding:5px 0;border-bottom:1px dashed #20242e"><span class="chip tag">${x.digest ? "digest" : "varredura"}</span> ${esc(x.at)}</div>`
      ).join("") || `<div class="empty">sem horários</div>`;
    }
    async function saveSchedule() {
      const freq = {};
      document.querySelectorAll("#sch-sources select").forEach((sel) => { freq[sel.dataset.src] = sel.value; });
      const payload = {
        enabled: $("sch-enabled").checked,
        timezone: "America/Sao_Paulo",
        times: $("sch-times").value.split(",").map((s) => s.trim()).filter(Boolean),
        digest_at: $("sch-digest").value.trim(),
        misfire_grace_minutes: parseInt($("sch-grace").value || "30", 10),
        source_frequency: freq
      };
      const r = await fetch("/api/schedule", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });
      const jr = await r.json();
      $("sch-msg").textContent = jr.ok ? "salvo ✓" : "erro: " + jr.error;
      if (jr.ok) loadSchedule();
    }

    document.addEventListener("click", (e) => {
      if (e.target.closest(".combo-menu")) return;
      if (!e.target.closest(".combo")) document.querySelectorAll(".combo-menu").forEach((m) => m.classList.add("hidden"));
    });
    const initialView = (location.hash || "#inicio").replace("#", "");
    show(initialView);
    window.addEventListener("hashchange", () => show((location.hash || "#inicio").replace("#", "")));
  </script>
</body>
</html>
"""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_config(config: dict) -> None:
    if not isinstance(config, dict):
        raise ValueError("config deve ser um objeto JSON")
    backup = Path(tel.TELEMETRY_DIR) / "config.json.bak"
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        if CONFIG_PATH.exists():
            backup.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _iso_dt(value):
    try:
        return datetime.fromisoformat(str(value))
    except Exception:  # noqa: BLE001
        return None


def _sheet_date(value):
    text = str(value or "").strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


ALLOWED_FILES = [
    "config.json",
    "profile.yaml",
    "data/inhire_seed.txt",
    "data/inhire_bruteforce.txt",
    "data/inhire_companies.json",
]


def safe_file(name: str) -> Path | None:
    """Resolve an allowed editable file (no path traversal)."""
    name = str(name or "").strip().lstrip("/")
    if name not in ALLOWED_FILES:
        return None
    path = (BASE_DIR / name).resolve()
    try:
        path.relative_to(BASE_DIR.resolve())
    except ValueError:
        return None
    return path


def validate_content(name: str, content: str) -> tuple[bool, str]:
    """Validate JSON/YAML before writing. Returns (ok, error)."""
    if name.endswith(".json"):
        try:
            data = json.loads(content)
        except Exception as e:  # noqa: BLE001
            return False, f"JSON inválido: {e}"
        if not isinstance(data, dict):
            return False, "JSON deve ser um objeto"
        return True, ""
    if name.endswith((".yaml", ".yml")):
        try:
            import yaml

            data = yaml.safe_load(content)
        except Exception as e:  # noqa: BLE001
            return False, f"YAML inválido: {e}"
        if not isinstance(data, dict):
            return False, "YAML deve ser um mapeamento"
        return True, ""
    return True, ""


def write_allowed_file(path: Path, content: str) -> None:
    backup_dir = Path(tel.TELEMETRY_DIR) / "backups"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        if path.exists():
            (backup_dir / f"{path.name}.bak").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:  # in-place: preserva dono
        f.write(content)


def load_doc(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
    else:
        import yaml

        data = yaml.safe_load(text) or {}
    return data if isinstance(data, dict) else {}


def get_path(doc: dict, dotted: str):
    cur = doc
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def set_path(doc: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = doc
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    last = parts[-1]
    if value is None:
        cur.pop(last, None)
    else:
        cur[last] = value


def render_doc(doc: dict, path: Path) -> str:
    if path.suffix == ".json":
        return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    import yaml

    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def _overview_data(days: int) -> dict:
    from collections import Counter

    cfg = _read_json(CONFIG_PATH)
    sources = cfg.get("scan_sources") or []
    cutoff = date.today() - timedelta(days=days - 1) if days and days > 0 else None

    def rows_since(tab: str) -> list[dict]:
        data = sheets_mod.read_tab(tab)
        rows = data.get("rows") or []
        if not cutoff:
            return rows
        out = []
        for r in rows:
            d = _sheet_date(r.get("Date"))
            if d is None or d < cutoff:
                continue
            out.append(r)
        return out

    jobs = rows_since("jobs")
    rejected = rows_since("rejected")

    def score(value):
        try:
            return float(str(value).replace(",", "."))
        except Exception:  # noqa: BLE001
            return None

    scores = [s for s in (score(r.get("Score")) for r in jobs) if s is not None]
    buckets = [0, 0, 0, 0, 0]
    for s in scores:
        buckets[min(4, max(0, int(s // 20)))] += 1
    b70 = sum(1 for s in scores if 70 <= s < 80)
    b80 = sum(1 for s in scores if 80 <= s < 90)
    b90 = sum(1 for s in scores if s >= 90)

    def platform(url) -> str:
        u = str(url or "").lower()
        if "linkedin.com" in u: return "LinkedIn"
        if "indeed." in u: return "Indeed"
        if "gupy.io" in u: return "Gupy"
        if "myworkdayjobs.com" in u: return "Workday"
        if "inhire.app" in u: return "InHire"
        if "greenhouse.io" in u: return "Greenhouse"
        if "lever.co" in u: return "Lever"
        if "icims.com" in u: return "iCIMS"
        return "Outros"

    platforms: Counter = Counter()
    companies: Counter = Counter()
    by_day: Counter = Counter()
    for row in jobs + rejected:
        platforms[platform(row.get("job_url"))] += 1
        company = str(row.get("company") or "").strip()
        if company:
            companies[company] += 1
        d = _sheet_date(row.get("Date"))
        if d:
            by_day[d.isoformat()] += 1

    today = date.today()
    if cutoff:
        span_days = (today - cutoff).days + 1
    else:
        filled = sorted(by_day.keys())
        if filled:
            span_days = (date.fromisoformat(filled[-1]) - date.fromisoformat(filled[0])).days + 1
        else:
            span_days = 1
    if span_days <= 45:
        granularity, label = "dia", "por dia"
    elif span_days <= 180:
        granularity, label = "semana", "por semana"
    else:
        granularity, label = "mês", "por mês"

    def bucket_key(d: date) -> str:
        if granularity == "dia":
            return d.isoformat()
        if granularity == "semana":
            return (d - timedelta(days=d.weekday())).isoformat()
        return d.strftime("%Y-%m")

    agg: Counter = Counter()
    for iso, count in by_day.items():
        d = date.fromisoformat(iso)
        if cutoff and d < cutoff:
            continue
        agg[bucket_key(d)] += count

    keys: list[str] = []
    if cutoff:
        if granularity == "dia":
            keys = [(cutoff + timedelta(days=i)).isoformat() for i in range(span_days)]
        elif granularity == "semana":
            cur = cutoff - timedelta(days=cutoff.weekday())
            while cur <= today:
                keys.append(cur.isoformat())
                cur += timedelta(days=7)
        else:
            cur = cutoff.replace(day=1)
            while (cur.year, cur.month) <= (today.year, today.month):
                keys.append(cur.strftime("%Y-%m"))
                cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        keys = sorted(agg.keys())[-24:]

    by_day_list = [{"date": k, "count": agg.get(k, 0)} for k in keys]

    def count_true(rows, field):
        return sum(1 for r in rows if str(r.get(field, "")).strip().lower() in {"sim", "yes", "true", "1"})

    return {
        "ok": True,
        "days": days,
        "sources": sources,
        "pegas": len(jobs) + len(rejected),
        "aprovadas": len(jobs),
        "rejeitadas": len(rejected),
        "aplicadas": count_true(jobs, "Apply"),
        "entrevistas": sum(1 for r in jobs if sheets_mod.is_interview_value(r.get("Entrevista"))),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "t70": sum(1 for s in scores if s >= 70),
        "t80": sum(1 for s in scores if s >= 80),
        "t90": sum(1 for s in scores if s >= 90),
        "score_buckets": buckets,
        "score_bands": {"70-80": b70, "80-90": b80, "90-100": b90},
        "score_total": len(scores),
        "platforms": [{"name": k, "count": v} for k, v in platforms.most_common()],
        "top_companies": [{"name": k, "count": v} for k, v in companies.most_common(10)],
        "by_day": by_day_list,
        "by_day_label": label,
        "source": "sheets",
    }


def _gaps_data(days: int) -> dict:
    """Compara as skills pedidas nas vagas (SKILLS_DA_VAGA) com o profile.yaml.

    Fonte: campo ``SKILLS_DA_VAGA`` que o n8n grava por vaga na planilha
    (abas ``Jobs`` + ``Rejected-Jobs``). Cada skill é normalizada via
    ``profile.yaml -> skill_aliases`` antes de contar.
    """
    from collections import Counter, defaultdict

    rows: list = []
    for tab in ("jobs", "rejected"):
        d = sheets_mod.read_tab(tab)
        if d.get("ok") and isinstance(d.get("rows"), list):
            rows.extend(d["rows"])

    if days:
        cutoff_date = (datetime.now() - timedelta(days=int(days))).date()

        def recent(r) -> bool:
            d = _sheet_date(r.get("Date"))
            return d is not None and d >= cutoff_date

        rows = [r for r in rows if recent(r)]

    # canonical: termo da vaga -> nome da skill do perfil
    profile_skills: dict = {}
    alias_to: dict = {}
    try:
        from careerops.profile import load_profile
        p = load_profile()
        aliases = p.get("skill_aliases") or {}
        for s in (p.get("skills") or []):
            nm = str(s.get("name") or "").strip()
            if not nm:
                continue
            profile_skills[nm] = {
                "label": str(s.get("label") or nm).strip(),
                "level": str(s.get("level") or "").strip().lower(),
            }
            keys = [s.get("label"), nm] + [a for a in (aliases.get(nm) or [])]
            for k in keys:
                alias_to[str(k).strip().lower()] = nm
    except Exception:  # noqa: BLE001
        profile_skills = {}

    def canon(raw) -> str | None:
        s = re.sub(r"\(.*?\)", "", str(raw or "")).strip().strip("*").strip()
        low = s.lower()
        if not low or low == "n/a":
            return None
        return alias_to.get(low, low)

    demand: Counter = Counter()
    buckets = defaultdict(Counter)
    for r in rows:
        raw = r.get("SKILLS_DA_VAGA") or ""
        try:
            sk = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(sk, dict):
            continue
        for b in ("hands_on", "relacionadas", "conhecimento", "ausentes"):
            for s in (sk.get(b) or []):
                name = canon(s)
                if not name:
                    continue
                demand[name] += 1
                buckets[b][name] += 1

    strong = {"hands_on", "working_knowledge"}
    weak = {"learning", "conceptual", "none"}

    # Famílias transferíveis: dominar 1 item do grupo transfere os conceitos pros demais.
    FAMILIES = {
        "cloud": {"aws", "azure", "gcp", "oci", "cloud_architecture"},
        "observability": {"monitoring", "observability_practices", "new_relic", "elastic_apm"},
        "ci_cd": {"ci_cd", "gitops"},
        "iac": {"infrastructure_as_code", "ansible"},
        "containers": {"docker", "kubernetes"},
    }
    name_to_family: dict = {}
    for fam, names in FAMILIES.items():
        for n in names:
            name_to_family[n] = fam
    strong_families = set()
    for nm, ps in profile_skills.items():
        if ps["level"] in strong:
            f = name_to_family.get(nm)
            if f:
                strong_families.add(f)

    items = []
    for name, c in demand.items():
        ps = profile_skills.get(name)
        lv = ps["level"] if ps else None
        f = name_to_family.get(name)
        if lv in strong:
            status = "strong"
        elif f and f in strong_families and f == "cloud":
            # conceitos de nuvem transferem entre provedores (AWS/OCI fortes -> GCP/Azure transferíveis)
            status = "transferable"
        elif lv in weak:
            status = "weak"
        else:
            status = "missing"
        items.append({
            "name": name,
            "label": ps["label"] if ps else name,
            "count": c,
            "hands_on": buckets["hands_on"].get(name, 0),
            "relacionadas": buckets["relacionadas"].get(name, 0),
            "conhecimento": buckets["conhecimento"].get(name, 0),
            "ausentes": buckets["ausentes"].get(name, 0),
            "status": status,
            "level": lv,
        })
    items.sort(key=lambda x: -x["count"])
    strong_count = sum(1 for i in items if i["status"] == "strong")
    return {
        "ok": True,
        "total_jobs": len(rows),
        "distinct_skills": len(items),
        "coverage_pct": round(100 * strong_count / len(items)) if items else 0,
        "demand": items[:15],
        "gaps": sorted([i for i in items if i["status"] not in ("strong", "transferable")], key=lambda x: -x["ausentes"])[:15],
    }


def _gaps_prompt(data: dict) -> str:
    lines = [
        "Você é um coach de carreira em tecnologia. Analise os dados de mercado abaixo e responda em português, de forma direta, o que o candidato deve PRIORIZAR para estudar ou melhorar.",
        f"Vagas analisadas: {data.get('total_jobs')} | skills distintas: {data.get('distinct_skills')} | cobertura forte: {data.get('coverage_pct')}%.",
        "",
        "Mais pedidas (skill — nº de vagas — situação: domina/parcial/faltando):",
    ]
    for s in (data.get("demand") or [])[:15]:
        lines.append(f"- {s['label']}: {s['count']} vagas ({s['status']})")
    lines.append("")
    lines.append("Gaps (skill — pedida em N vagas — sem evidência no perfil em M):")
    for s in (data.get("gaps") or [])[:15]:
        lines.append(f"- {s['label']}: pedida em {s['count']}, sem evidência em {s['ausentes']}")
    lines.append("")
    lines.append("Formato da resposta: no máximo 8 tópicos, cada um com 1-2 frases. Priorize pelo impacto (frequência nas vagas × gap do candidato). Use apenas os números fornecidos; não invente.")
    return "\n".join(lines)


STAGE_LABELS: dict[str, tuple[str, str]] = {
    "title_or_description": ("Blocklist título/descrição", "Cargos fora do escopo (dev, QA, suporte, gestão) e barreiras (visto, idioma, clearance)."),
    "company_blocklist": ("Empresas bloqueadas", "Aggregators/spam que não são empregadores reais."),
    "already_processed": ("Já processadas", "Vagas já vistas em execuções anteriores (memória vagas_processadas.txt)."),
    "dedupe_batch": ("Duplicatas no lote", "Mesma URL ou empresa+título+localização entre fontes."),
    "brazil_onsite": ("Presencial no Brasil", "Presencial BR descartado (aceita remoto e híbrido)."),
    "work_modes": ("Modalidade", "Fora das modalidades habilitadas em config.json (remoto/híbrido)."),
    "prefilter_title": ("Pré-filtro por título", "Função fora do alvo (profile.yaml → title_matching)."),
}


def _collect_data() -> dict:
    """Live collection detail: state, filter stages (with counts + explanation),
    recently discarded jobs (with reason) and the event stream."""
    from collections import Counter

    state = tel.read_state()
    all_discarded = tel.read_discarded(limit=10000)
    counts: Counter = Counter(r.get("stage") or "?" for r in all_discarded)

    stage_order = [
        "title_or_description", "company_blocklist", "already_processed",
        "dedupe_batch", "brazil_onsite", "work_modes", "prefilter_title",
    ]
    stages = []
    seen_stages: set[str] = set()
    for stage in stage_order:
        label, desc = STAGE_LABELS.get(stage, (stage, ""))
        stages.append({"stage": stage, "label": label, "desc": desc, "discarded": counts.get(stage, 0)})
        seen_stages.add(stage)
    for stage, n in counts.items():
        if stage in seen_stages:
            continue
        label, desc = STAGE_LABELS.get(stage, (stage, ""))
        stages.append({"stage": stage, "label": label, "desc": desc, "discarded": n})

    recent = []
    for r in all_discarded[:300]:
        rec = dict(r)
        rec["stage_label"] = STAGE_LABELS.get(rec.get("stage") or "", (rec.get("stage") or "", ""))[0]
        recent.append(rec)

    return {
        "ok": True,
        "state": state,
        "stages": stages,
        "discarded": recent,
        "events": tel.read_events(limit=60),
    }


def _profile_ai_prompt(linkedin: str, curriculo: str, answers: str, current_yaml: str) -> str:
    lines = [
        "Você é um especialista em currículos e carreiras de tecnologia.",
        "Monte o arquivo profile.yaml do pipeline CareerOps a partir dos dados da pessoa.",
        "Responda SOMENTE com YAML válido, sem comentários, sem ```, sem texto fora do YAML.",
        "",
        "REGRAS:",
        "- Mantenha exatamente as MESMAS chaves e estrutura do profile.yaml de referência abaixo.",
        "- Substitua TODOS os valores pessoais pelos dados reais da pessoa (não copie o conteúdo de referência).",
        "- Preserve como estão as seções instrucionais: level_definitions, source_definitions, status_definitions, classification_rules, matching_guidance, title_matching e skill_aliases — a menos que o usuário peça mudança.",
        "- Nível de skill (hands_on/working_knowledge/learning/conceptual/none): curso ou certificação NÃO é hands_on; hands_on exige uso profissional declarado.",
        "- Não invente experiência, empresa, cargo ou tecnologia; use apenas o que a pessoa declarou.",
        "- Se faltar informação, deixe o campo vazio ou com lista vazia, não invente.",
        "",
        "LinkedIn: " + (linkedin or "(não informado)"),
        "",
        "Currículo:",
        curriculo or "(não informado)",
        "",
        "Respostas da pessoa:",
        answers or "(não informado)",
        "",
        "profile.yaml de referência (estrutura e chaves a seguir):",
        current_yaml,
    ]
    return "\n".join(lines)


def _version_profile() -> str | None:
    """Archive the current profile.yaml into a timestamped version file."""
    if not PROFILE_PATH.exists():
        return None
    try:
        PROFILE_VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = PROFILE_VERSIONS_DIR / f"profile-{stamp}.yaml"
        dest.write_text(PROFILE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        return dest.name
    except OSError:
        return None


def _list_profile_versions() -> list[dict]:
    if not PROFILE_VERSIONS_DIR.is_dir():
        return []
    out = []
    for f in sorted(PROFILE_VERSIONS_DIR.glob("profile-*.yaml"), reverse=True):
        try:
            st = f.stat()
            out.append({"name": f.name, "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
        except OSError:
            continue
    return out


def _load_secrets() -> dict:
    try:
        return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}

def _save_secrets(data: dict) -> None:
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        SECRETS_PATH.chmod(0o600)
    except OSError:
        pass


def _mask(value: str) -> str:
    value = str(value or "")
    if len(value) <= 4:
        return "••••" if value else ""
    return "••••" + value[-4:]


def _display_source(source, url) -> str:
    s = str(source or "")
    u = str(url or "").lower()
    if s.startswith("inhire"): return "InHire"
    if s.startswith("gupy"): return "Gupy"
    if s.startswith("workday"): return "Workday"
    if "linkedin.com" in u: return "LinkedIn"
    if "indeed." in u: return "Indeed"
    if "gupy.io" in u: return "Gupy"
    if "myworkdayjobs.com" in u: return "Workday"
    if "inhire.app" in u: return "InHire"
    if "greenhouse.io" in u: return "Greenhouse"
    if "lever.co" in u: return "Lever"
    if "icims.com" in u: return "iCIMS"
    if s and s not in ("jobspy", "banks"): return s
    return "Outros"


def _scrape_url(url: str) -> tuple[str, str, str]:
    """Best-effort: extrai <title>, site/company e meta description de uma URL."""
    title = company = desc = ""
    try:
        r = requests.get(url, timeout=8, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"})
        text = r.text[:200000]
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()[:300]
        m = (re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']*)', text, re.I)
             or re.search(r'<meta[^>]+name=["\']application-name["\'][^>]+content=["\']([^"\']*)', text, re.I))
        if m:
            company = m.group(1).strip()[:120]
        m = re.search(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']*)', text, re.I)
        if m:
            desc = m.group(1).strip()[:2000]
    except Exception:  # noqa: BLE001
        pass
    if not company:
        company = (urlparse(url).netloc or "").replace("www.", "")[:80]
    if not title:
        title = "Vaga manual"
    return title, company, desc


def _sheet_urls() -> set[str]:
    urls: set[str] = set()
    for tab in ("jobs", "rejected"):
        try:
            data = sheets_mod.read_tab(tab)
            for r in data.get("rows", []) or []:
                if r.get("job_url"):
                    urls.add(str(r["job_url"]))
        except Exception:  # noqa: BLE001
            continue
    return urls


def _sheet_score_map() -> dict[str, str]:
    """Map job_url -> IA score (Score column) across Jobs + Rejected-Jobs."""
    score_map: dict[str, str] = {}
    for tab in ("jobs", "rejected"):
        try:
            data = sheets_mod.read_tab(tab)
            for r in data.get("rows", []) or []:
                u = str(r.get("job_url") or "").strip()
                if u and str(r.get("Score") or "").strip():
                    score_map.setdefault(u, str(r.get("Score")).strip())
        except Exception:  # noqa: BLE001
            continue
    return score_map


def _all_summary(rows) -> dict:
    def count_true(field):
        return sum(1 for r in rows if str(r.get(field, "")).strip().lower() in {"sim", "yes", "true", "1"})

    scores = []
    for r in rows:
        try:
            scores.append(float(str(r.get("Score")).replace(",", ".")))
        except Exception:  # noqa: BLE001
            continue
    return {
        "total": len(rows),
        "applied": count_true("Apply"),
        "interview": sum(1 for r in rows if sheets_mod.is_interview_value(r.get("Entrevista"))),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "remote": count_true("is_remote"),
        "rejected": sum(1 for r in rows if r.get("_tab") == "rejected"),
    }


def _perms(path: Path):
    try:
        return oct(path.stat().st_mode & 0o777)
    except OSError:
        return None


def _sa_status() -> dict:
    data = {}
    if SA_PATH.is_file():
        try:
            data = json.loads(SA_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    return {
        "set": SA_PATH.is_file(),
        "path": str(SA_PATH.relative_to(BASE_DIR)),
        "client_email": data.get("client_email", ""),
        "project_id": data.get("project_id", ""),
        "has_private_key": bool(data.get("private_key")),
        "perms": _perms(SA_PATH),
    }


def _sheets_status() -> dict:
    data = {}
    if SHEETS_PATH.is_file():
        try:
            data = json.loads(SHEETS_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    return {
        "set": SHEETS_PATH.is_file(),
        "path": str(SHEETS_PATH.relative_to(BASE_DIR)),
        "spreadsheet_id": data.get("spreadsheet_id", ""),
        "jobs_tab": data.get("jobs_tab", "Jobs"),
        "rejected_tab": data.get("rejected_tab", "Rejected-Jobs"),
        "salary_tab": data.get("salary_tab", "Salario"),
        "cache_ttl": data.get("cache_ttl", 60),
        "perms": _perms(SHEETS_PATH),
    }


def _workflow_file(name: str) -> Path | None:
    name = str(name or "").strip()
    if not name.endswith(".json") or "/" in name or "\\" in name:
        return None
    path = (WORKFLOWS_DIR / name).resolve()
    if path.parent != WORKFLOWS_DIR.resolve() or not path.is_file():
        return None
    return path


def _load_credentials() -> list[dict]:
    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
        items = data.get("credentials", []) if isinstance(data, dict) else data
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save_credentials(items: list[dict]) -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(
        json.dumps({"credentials": items}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    CREDENTIALS_PATH.chmod(0o600)


def _cred_public(item: dict) -> dict:
    secret = str(item.get("secret") or "")
    return {
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "provider": item.get("provider", ""),
        "username": item.get("username", ""),
        "notes": item.get("notes", ""),
        "updated_at": item.get("updated_at", ""),
        "has_secret": bool(secret),
        "masked": _mask(secret),
    }


def _security_status() -> dict:
    try:
        gitignore = (BASE_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    except OSError:
        gitignore = []
    ignored = any(line.strip() == "secrets/" for line in gitignore)
    files = []
    for path in (SA_PATH, SHEETS_PATH, CREDENTIALS_PATH, SECRETS_DIR / "api_keys.json"):
        files.append({
            "path": str(path.relative_to(BASE_DIR)),
            "exists": path.is_file(),
            "perms": _perms(path),
        })
    perms_ok = all((not f["exists"]) or f["perms"] == "0o600" for f in files)
    return {"gitignored": ignored, "perms_ok": perms_ok, "files": files}


def _extract_json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    raw = str(text or "").lstrip()
    obj, _ = decoder.raw_decode(raw)
    if not isinstance(obj, dict):
        raise ValueError("JSON deve ser um objeto")
    return obj


def _reset_sheets_cache() -> None:
    try:
        sheets_mod._creds = None
        sheets_mod._cache.clear()
    except Exception:  # noqa: BLE001
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "CareerOpsDashboard/1.0"

    def log_message(self, fmt, *args):  # noqa: A003
        return

    def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/":
            return self._send(200, PAGE.encode("utf-8"), "text/html")
        if path == "/health":
            return self._json(200, {"ok": True})
        if path == "/api/overview":
            days = int((qs.get("days") or ["30"])[0])
            try:
                return self._json(200, _overview_data(days))
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/gaps":
            days = int((qs.get("days") or ["0"])[0])
            try:
                return self._json(200, _gaps_data(days))
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/gaps/ai":
            days = int((qs.get("days") or ["0"])[0])
            try:
                data = _gaps_data(days)
                webhook = os.environ.get("N8N_GAPS_WEBHOOK_URL") or _load_secrets().get("N8N_GAPS_WEBHOOK_URL") or "http://n8n:5678/webhook/careerops-gaps"
                r = requests.post(webhook, json={"prompt": _gaps_prompt(data)}, timeout=180)
                if r.status_code != 200:
                    return self._json(400, {"ok": False, "error": f"n8n HTTP {r.status_code}"})
                body = r.json()
                text = ""
                if isinstance(body, list) and body:
                    body = body[0]
                if isinstance(body, dict):
                    text = str(body.get("text") or body.get("response") or body.get("content") or "")
                text = text.strip()
                if not text:
                    return self._json(400, {"ok": False, "error": "resposta vazia do n8n"})
                return self._json(200, {"ok": True, "text": text})
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/n8n/workflows":
            files = []
            if WORKFLOWS_DIR.is_dir():
                for f in sorted(WORKFLOWS_DIR.glob("*.json")):
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        data = {}
                    st = f.stat()
                    files.append({
                        "file": f.name,
                        "name": data.get("name", f.stem),
                        "id": data.get("id", ""),
                        "active": bool(data.get("active")),
                        "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                    })
            return self._json(200, {"ok": True, "workflows": files})
        if path == "/api/n8n/workflow":
            name = (qs.get("file") or [""])[0]
            f = _workflow_file(name)
            if not f:
                return self._json(404, {"ok": False, "error": "arquivo inválido"})
            return self._json(200, {"ok": True, "file": name, "content": f.read_text(encoding="utf-8")})
        if path == "/api/n8n/status":
            pending = IMPORT_REQUEST.exists()
            if IMPORT_STATUS.is_file():
                try:
                    return self._json(200, {"ok": True, "pending": pending,
                                            "status": json.loads(IMPORT_STATUS.read_text(encoding="utf-8"))})
                except Exception:  # noqa: BLE001
                    pass
            return self._json(200, {"ok": True, "pending": pending, "status": None})
        if path == "/api/google":
            sa = _sa_status()
            sh = _sheets_status()
            return self._json(200, {
                "ok": True,
                "email": sa.get("client_email", ""),
                "project_id": sa.get("project_id", ""),
                "has_private_key": sa.get("has_private_key", False),
                "spreadsheet_id": sh.get("spreadsheet_id", ""),
                "jobs_tab": sh.get("jobs_tab", "Jobs"),
                "rejected_tab": sh.get("rejected_tab", "Rejected-Jobs"),
                "perms": sa.get("perms"),
            })
        if path == "/api/google/reveal-key":
            try:
                data = json.loads(SA_PATH.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return self._json(404, {"ok": False, "error": "service account não definido"})
            key = str(data.get("private_key") or "")
            if not key:
                return self._json(404, {"ok": False, "error": "private key ausente"})
            return self._json(200, {"ok": True, "private_key": key})
        if path == "/api/credentials":
            return self._json(200, {"ok": True, "credentials": [_cred_public(x) for x in _load_credentials()]})
        if path == "/api/credentials/reveal":
            cid = (qs.get("id") or [""])[0]
            item = next((x for x in _load_credentials() if x.get("id") == cid), None)
            if not item or not item.get("secret"):
                return self._json(404, {"ok": False, "error": "credencial sem segredo"})
            return self._json(200, {"ok": True, "name": item.get("name", ""),
                                    "provider": item.get("provider", ""),
                                    "username": item.get("username", ""),
                                    "secret": item.get("secret", "")})
        if path == "/api/secrets":
            data = _load_secrets()
            out = []
            for f in SECRET_FIELDS:
                value = str(data.get(f["key"], ""))
                out.append({**f, "set": bool(value), "masked": _mask(value)})
            return self._json(200, {
                "ok": True, "fields": out,
                "service_account": _sa_status(),
                "sheets": _sheets_status(),
                "security": _security_status(),
            })
        if path == "/api/seen":
            days = int((qs.get("days") or ["3"])[0])
            limit = int((qs.get("limit") or ["300"])[0])
            sent_urls = tel.read_job_urls()
            sheet_urls = _sheet_urls()
            sent_at = {}
            for rec in tel.read_jobs(limit=200000):
                u = rec.get("url")
                if u and (rec.get("status") or "sent") == "sent" and u not in sent_at:
                    sent_at[u] = rec.get("analyzed_at") or ""
            rows = tel.read_seen(days=days or None, limit=limit)
            out = []
            for r in rows:
                url = r.get("url") or ""
                out.append({
                    **r,
                    "source": _display_source(r.get("source"), url),
                    "sent": bool(url and url in sent_urls),
                    "sent_at": sent_at.get(url, ""),
                    "analyzed": bool(url and url in sheet_urls),
                })
            return self._json(200, {"ok": True, "days": days, "rows": out})
        if path == "/api/funnel":
            days = int((qs.get("days") or ["3"])[0])
            cutoff = datetime.now().astimezone() - timedelta(days=days) if days > 0 else None
            seen = len(tel.read_seen(days=days or None, limit=None))
            rows = tel.read_jobs(limit=200000)
            recent = []
            for r in rows:
                if cutoff is None:
                    recent.append(r)
                    continue
                ts = _iso_dt(r.get("analyzed_at"))
                if ts and ts.astimezone() >= cutoff:
                    recent.append(r)
            sent = len({r.get("url") for r in recent if r.get("url")})
            scores: list[float] = []
            source = "local"
            try:
                sdata = sheets_mod.read_tab("jobs")
                if sdata.get("ok"):
                    cutoff_date = cutoff.date() if cutoff else None
                    for r in sdata.get("rows", []):
                        d = _sheet_date(r.get("Date"))
                        if cutoff_date and (d is None or d < cutoff_date):
                            continue
                        try:
                            scores.append(float(str(r.get("Score")).replace(",", ".")))
                        except Exception:
                            continue
                    if scores:
                        source = "sheets"
            except Exception:  # noqa: BLE001
                pass
            if not scores:
                for r in recent:
                    try:
                        scores.append(float(r.get("det_score")))
                    except Exception:  # noqa: BLE001
                        continue
            return self._json(200, {
                "ok": True, "days": days, "seen": seen, "sent": sent,
                "t70": sum(1 for s in scores if s >= 70),
                "t80": sum(1 for s in scores if s >= 80),
                "t90": sum(1 for s in scores if s >= 90),
                "source": source,
            })
        if path == "/api/state":
            return self._json(200, tel.read_state())
        if path == "/api/collect":
            return self._json(200, _collect_data())
        if path == "/api/events":
            limit = int((qs.get("limit") or ["80"])[0])
            return self._json(200, tel.read_events(limit))
        if path == "/api/jobs":
            limit = int((qs.get("limit") or ["200"])[0])
            rows = tel.read_jobs(limit)
            score_map = _sheet_score_map()
            for r in rows:
                r["ia_score"] = score_map.get(str(r.get("url") or "").strip())
            return self._json(200, rows)
        if path == "/api/jobs.csv":
            csv_bytes = b""
            p = tel.jobs_csv_path()
            if p.exists():
                csv_bytes = p.read_bytes()
            return self._send(200, csv_bytes, "text/csv")
        if path == "/api/sheets":
            tab = (qs.get("tab") or ["all"])[0]
            if tab not in ("jobs", "rejected", "all"):
                tab = "all"
            force = (qs.get("force") or ["0"])[0] == "1"
            limit = int((qs.get("limit") or ["300"])[0])
            days = int((qs.get("days") or ["0"])[0])
            if tab == "all":
                merged: list[dict] = []
                error = None
                for t in ("jobs", "rejected"):
                    d = sheets_mod.read_tab(t, force=force)
                    if not d.get("ok"):
                        error = d.get("error")
                        continue
                    for r in (d.get("rows") or []):
                        rr = dict(r)
                        rr["_tab"] = t
                        merged.append(rr)
                if not merged and error:
                    return self._json(200, {"ok": False, "tab": "all", "tab_name": "Todos",
                                            "error": error, "headers": [], "rows": [], "summary": {}})
                rows = merged
                if days > 0:
                    cutoff = date.today() - timedelta(days=days - 1)
                    rows = [r for r in rows if (_sheet_date(r.get("Date")) or date.min) >= cutoff]
                rows = sorted(rows, key=lambda r: (_sheet_date(r.get("Date")) or date.min), reverse=True)
                total = len(rows)
                data = {"ok": True, "tab": "all", "tab_name": "Todos", "headers": [], "rows": rows[:limit],
                        "returned": min(limit, total), "filtered_total": total,
                        "summary": _all_summary(rows), "error": None}
                return self._json(200, data)
            data = sheets_mod.read_tab(tab, force=force)
            rows = data.get("rows") or []
            if days > 0:
                cutoff = date.today() - timedelta(days=days - 1)
                rows = [r for r in rows if (_sheet_date(r.get("Date")) or date.min) >= cutoff]
            rows = sorted(rows, key=lambda r: (_sheet_date(r.get("Date")) or date.min), reverse=True)
            total = len(rows)
            data = {**data, "rows": rows[:limit], "returned": min(limit, total),
                    "filtered_total": total, "summary": sheets_mod.summarize(tab, rows)}
            return self._json(200, data)
        if path == "/api/guided":
            name = (qs.get("name") or [""])[0]
            pth = safe_file(name)
            if not pth or not pth.exists() or pth.suffix not in (".json", ".yaml", ".yml"):
                return self._json(404, {"ok": False, "error": "arquivo não suportado"})
            try:
                return self._json(200, {"ok": True, "name": name, "data": load_doc(pth)})
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/files":
            files = []
            for name in ALLOWED_FILES:
                pth = safe_file(name)
                if pth and pth.exists():
                    files.append({"name": name, "size": pth.stat().st_size})
            return self._json(200, {"ok": True, "files": files})
        if path == "/api/file":
            name = (qs.get("name") or [""])[0]
            pth = safe_file(name)
            if not pth or not pth.exists():
                return self._json(404, {"ok": False, "error": "arquivo não permitido/inexistente"})
            return self._json(200, {"ok": True, "name": name,
                                    "content": pth.read_text(encoding="utf-8")})
        if path == "/api/config":
            return self._json(200, {"ok": True, "config": _read_json(CONFIG_PATH)})
        if path == "/api/profile/versions":
            return self._json(200, {"ok": True, "versions": _list_profile_versions()})
        if path == "/api/profile/version":
            name = (qs.get("name") or [""])[0]
            f = PROFILE_VERSIONS_DIR / name
            if not name.startswith("profile-") or "/" in name or "\\" in name or not f.is_file():
                return self._json(404, {"ok": False, "error": "versão inválida"})
            try:
                return self._json(200, {"ok": True, "name": name, "content": f.read_text(encoding="utf-8")})
            except OSError as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/schedule":
            cfg = _read_json(CONFIG_PATH)
            sched = get_schedule(cfg)
            return self._json(200, {"ok": True, "schedule": sched, "next_runs": next_runs(sched),
                                    "scan_sources": cfg.get("scan_sources") or []})
        if path == "/api/schedule/render":
            schedule = get_schedule(_read_json(CONFIG_PATH))
            return self._send(200, render_crontab(schedule, PROJECT_DIR).encode("utf-8"), "text/plain")
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        raw = self._body()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as e:  # noqa: BLE001
            return self._json(400, {"ok": False, "error": f"JSON inválido: {e}"})

        if path in ("/api/guided/preview", "/api/guided/save"):
            name = str(payload.get("name") or "")
            patch = payload.get("patch") or {}
            pth = safe_file(name)
            if not pth or not pth.exists() or pth.suffix not in (".json", ".yaml", ".yml"):
                return self._json(400, {"ok": False, "error": "arquivo não suportado"})
            try:
                if isinstance(payload.get("data"), dict):
                    doc = payload["data"]
                else:
                    doc = load_doc(pth)
                    if not isinstance(patch, dict):
                        return self._json(400, {"ok": False, "error": "patch inválido"})
                    for key, value in patch.items():
                        set_path(doc, str(key), value)
                text = render_doc(doc, pth)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            if path.endswith("/preview"):
                return self._json(200, {"ok": True, "preview": text})
            ok, err = validate_content(name, text)
            if not ok:
                return self._json(400, {"ok": False, "error": err})
            try:
                if pth.name == "profile.yaml":
                    _version_profile()
                write_allowed_file(pth, text)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True})
        if path == "/api/manual":
            url = str(payload.get("url") or "").strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                return self._json(400, {"ok": False, "error": "URL inválida (use http:// ou https://)"})
            title, company, desc = _scrape_url(url)
            try:
                cfg = load_config(CONFIG_PATH)
                prompt = str(cfg.get("prompt") or "")
            except Exception:  # noqa: BLE001
                prompt = ""
            profile_context = ""
            try:
                from careerops.profile import build_profile_context, load_profile
                profile_context = build_profile_context(load_profile())
            except Exception:  # noqa: BLE001
                profile_context = ""
            job = {
                "title": title, "company": company, "location": "",
                "description": desc, "job_url": url, "url": url,
                "source": "manual", "is_remote": "", "job_type": "", "job_level": "",
                "system_prompt": prompt, "profile_context": profile_context,
            }
            webhook = os.environ.get("N8N_WEBHOOK_URL") or _load_secrets().get("N8N_WEBHOOK_URL") or "http://n8n:5678/webhook/careerops-jobs"
            try:
                resp = requests.post(webhook, json=job, timeout=45)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            if resp.status_code == 200:
                try:
                    tel.record_sent(job)
                except Exception:  # noqa: BLE001
                    pass
            return self._json(200, {"ok": resp.status_code == 200, "status_code": resp.status_code,
                                    "title": job["title"], "company": job["company"]})
        if path == "/api/collect/clear":
            tel.clear_events()
            return self._json(200, {"ok": True})
        if path == "/api/scan":
            source = str(payload.get("source") or "").strip().lower()
            if source not in ("linkedin", "indeed", "gupy", "workday", "inhire", "banks", "jobspy", "all"):
                return self._json(400, {"ok": False, "error": "fonte inválida"})
            if source == "banks":
                sources = ["gupy", "workday"]
            elif source == "jobspy":
                sources = ["linkedin", "indeed"]
            elif source == "all":
                sources = []
            else:
                sources = [source]
            env = dict(os.environ)
            env.setdefault("PYTHONPATH", str(BASE_DIR / "src"))
            cmd = [sys.executable, "-m", "careerops.main"]
            if sources:
                cmd += ["--sources", ",".join(sources)]
            try:
                log_dir = BASE_DIR / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / f"careerops-manual-{datetime.now():%Y%m%d-%H%M%S}.log"
                with log_path.open("a", encoding="utf-8") as f:
                    subprocess.Popen(cmd, cwd=str(BASE_DIR), env=env, stdout=f, stderr=f, start_new_session=True)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True, "sources": sources or ["config"],
                                    "log": log_path.name})
        if path == "/api/profile/ai":
            linkedin = str(payload.get("linkedin") or "").strip()
            curriculo = str(payload.get("curriculo") or "").strip()
            answers = str(payload.get("answers") or "").strip()
            if not answers and not curriculo:
                return self._json(400, {"ok": False, "error": "informe as respostas ou o currículo"})
            import yaml
            try:
                current_yaml = PROFILE_PATH.read_text(encoding="utf-8")
            except OSError:
                current_yaml = ""
            prompt = _profile_ai_prompt(linkedin, curriculo, answers, current_yaml)
            webhook = os.environ.get("N8N_PERFIL_WEBHOOK_URL") or "http://n8n:5678/webhook/careerops-perfil"
            try:
                r = requests.post(webhook, json={"prompt": prompt}, timeout=180)
                if r.status_code != 200:
                    return self._json(400, {"ok": False, "error": f"n8n HTTP {r.status_code}"})
                body = r.json()
                if isinstance(body, list) and body:
                    body = body[0]
                if isinstance(body, dict):
                    text = str(body.get("text") or body.get("response") or body.get("content") or "")
                else:
                    text = str(body or "")
                text = text.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```(?:yaml|yml)?\s*", "", text)
                    text = re.sub(r"\s*```$", "", text)
                data = yaml.safe_load(text)
                if not isinstance(data, dict):
                    return self._json(400, {"ok": False, "error": "a IA não retornou um YAML válido de objeto"})
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            try:
                _version_profile()
                write_allowed_file(PROFILE_PATH, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True, "message": "profile.yaml atualizado"})
        if path == "/api/profile/restore":
            name = str(payload.get("name") or "")
            f = PROFILE_VERSIONS_DIR / name
            if not name.startswith("profile-") or "/" in name or "\\" in name or not f.is_file():
                return self._json(404, {"ok": False, "error": "versão inválida"})
            try:
                import yaml
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    return self._json(400, {"ok": False, "error": "versão inválida (YAML)"})
                _version_profile()
                write_allowed_file(PROFILE_PATH, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
                return self._json(200, {"ok": True})
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/resend":
            url = str(payload.get("url") or "")
            if not url:
                return self._json(400, {"ok": False, "error": "url ausente"})
            job = tel.read_sent_payload(url)
            stored = bool(job)
            if not job:
                row = next((r for r in tel.read_seen(days=None, limit=None) if r.get("url") == url), None)
                if not row:
                    return self._json(404, {"ok": False, "error": "vaga não encontrada no histórico"})
                job = {
                    "title": row.get("title") or "",
                    "company": row.get("company") or "",
                    "location": row.get("location") or "",
                    "job_url": url,
                    "description": "",
                    "is_remote": "",
                    "job_type": "",
                    "job_level": "",
                    "source": _display_source(row.get("source"), url),
                }
            webhook = os.environ.get("N8N_WEBHOOK_URL") or _load_secrets().get("N8N_WEBHOOK_URL") or "http://n8n:5678/webhook/careerops-jobs"
            try:
                resp = requests.post(webhook, json=job, timeout=45)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": resp.status_code == 200, "status_code": resp.status_code,
                                    "stored": stored, "webhook": webhook})
        if path == "/api/n8n/import":
            try:
                N8N_DIR.mkdir(parents=True, exist_ok=True)
                for stale in (IMPORT_STATUS, IMPORT_REQUEST):
                    try:
                        stale.unlink()
                    except OSError:
                        pass
                IMPORT_REQUEST.write_text(datetime.now().astimezone().isoformat(timespec="seconds") + "\n", encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True, "message": "importação solicitada"})
        if path == "/api/google":
            email = str(payload.get("email") or "").strip()
            private_key = str(payload.get("private_key") or "").strip().replace("\\n", "\n")
            try:
                sa = json.loads(SA_PATH.read_text(encoding="utf-8")) if SA_PATH.is_file() else {}
            except Exception:  # noqa: BLE001
                sa = {}
            if not isinstance(sa, dict):
                sa = {}
            if email:
                sa["client_email"] = email
            if private_key:
                sa["private_key"] = private_key
            if not sa.get("client_email") or not sa.get("private_key"):
                return self._json(400, {"ok": False, "error": "informe email e private key (ou mantenha a existente)"})
            sa.setdefault("type", "service_account")
            sa.setdefault("project_id", str(sa.get("client_email", "")).split("@")[-1].split(".")[0])
            sa.setdefault("token_uri", "https://oauth2.googleapis.com/token")
            try:
                SECRETS_DIR.mkdir(parents=True, exist_ok=True)
                SA_PATH.write_text(json.dumps(sa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                SA_PATH.chmod(0o600)
                sheet = {}
                if SHEETS_PATH.is_file():
                    try:
                        sheet = json.loads(SHEETS_PATH.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        sheet = {}
                sheet.update({
                    "service_account": "secrets/google-sa.json",
                    "spreadsheet_id": str(payload.get("spreadsheet_id") or sheet.get("spreadsheet_id") or ""),
                    "jobs_tab": str(payload.get("jobs_tab") or sheet.get("jobs_tab") or "Jobs"),
                    "rejected_tab": str(payload.get("rejected_tab") or sheet.get("rejected_tab") or "Rejected-Jobs"),
                })
                sheet.setdefault("cache_ttl", 60)
                SHEETS_PATH.write_text(json.dumps(sheet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                SHEETS_PATH.chmod(0o600)
                _reset_sheets_cache()
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True})
        if path == "/api/credentials":
            action = str(payload.get("action") or "save")
            items = _load_credentials()
            if action == "delete":
                cid = str(payload.get("id") or "")
                items = [x for x in items if x.get("id") != cid]
                _save_credentials(items)
                return self._json(200, {"ok": True})
            cred = payload.get("credential") if isinstance(payload.get("credential"), dict) else payload
            cid = str(cred.get("id") or "")
            existing = next((x for x in items if x.get("id") == cid), None) if cid else None
            if existing is None:
                cid = uuid.uuid4().hex[:12]
                existing = {"id": cid}
                items.append(existing)
            existing["name"] = str(cred.get("name") or existing.get("name") or cred.get("provider") or "credencial").strip()
            existing["provider"] = str(cred.get("provider") or existing.get("provider") or "").strip()
            existing["username"] = str(cred.get("username") or "")
            existing["notes"] = str(cred.get("notes") or "")
            secret = cred.get("secret")
            if secret not in (None, ""):
                existing["secret"] = str(secret)
            existing["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            try:
                _save_credentials(items)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True, "id": cid, "credential": _cred_public(existing)})
        if path == "/api/service-account":
            content = str(payload.get("content") or "")
            try:
                data = _extract_json_object(content)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": f"JSON inválido: {e}"})
            if not data.get("client_email") or not data.get("private_key"):
                return self._json(400, {"ok": False, "error": "service account sem client_email/private_key"})
            try:
                SECRETS_DIR.mkdir(parents=True, exist_ok=True)
                SA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                SA_PATH.chmod(0o600)
                _reset_sheets_cache()
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True, "service_account": _sa_status()})
        if path == "/api/sheets-config":
            config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
            current = {}
            if SHEETS_PATH.is_file():
                try:
                    current = json.loads(SHEETS_PATH.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    current = {}
            for key in ("spreadsheet_id", "service_account", "jobs_tab", "rejected_tab", "salary_tab", "cache_ttl"):
                if key in (config or {}):
                    current[key] = config[key]
            try:
                current.setdefault("service_account", "secrets/google-sa.json")
                current.setdefault("jobs_tab", "Jobs")
                current.setdefault("rejected_tab", "Rejected-Jobs")
                current.setdefault("salary_tab", "Salario")
                current.setdefault("cache_ttl", 60)
                SECRETS_DIR.mkdir(parents=True, exist_ok=True)
                SHEETS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                SHEETS_PATH.chmod(0o600)
                _reset_sheets_cache()
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True, "sheets": _sheets_status()})
        if path == "/api/sheets/test":
            _reset_sheets_cache()
            data = sheets_mod.read_tab("jobs", force=True)
            return self._json(200, {
                "ok": bool(data.get("ok")),
                "rows": len(data.get("rows") or []),
                "error": data.get("error"),
                "tab": data.get("tab_name"),
            })
        if path == "/api/secrets":
            values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
            data = _load_secrets()
            allowed = {f["key"] for f in SECRET_FIELDS}
            for key, value in (values or {}).items():
                if key not in allowed:
                    continue
                text = str(value or "")
                if text == "__clear__":
                    data.pop(key, None)
                elif text.strip():
                    data[key] = text.strip()
            try:
                _save_secrets(data)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True})
        if path == "/api/file":
            name = str(payload.get("name") or "")
            content = str(payload.get("content") or "")
            pth = safe_file(name)
            if not pth:
                return self._json(400, {"ok": False, "error": "arquivo não permitido"})
            ok, err = validate_content(name, content)
            if not ok:
                return self._json(400, {"ok": False, "error": err})
            try:
                if pth.name == "profile.yaml":
                    _version_profile()
                write_allowed_file(pth, content)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True})
        if path == "/api/config":
            config = payload.get("config", payload) if isinstance(payload, dict) else payload
            try:
                _write_config(config)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True})

        if path == "/api/schedule":
            schedule = payload.get("schedule", payload) if isinstance(payload, dict) else payload
            if not isinstance(schedule, dict) or not (schedule.get("times") or schedule.get("jobs")):
                return self._json(400, {"ok": False, "error": "informe 'times' (ex.: [\"06:00\", \"21:00\"])"})
            times = schedule.get("times") or [j.get("time") for j in schedule.get("jobs", [])]
            config = _read_json(CONFIG_PATH)
            config["schedule"] = {
                "_comment": "Agenda simples. O container scheduler dispara sozinho.",
                "enabled": bool(schedule.get("enabled", True)),
                "timezone": schedule.get("timezone") or "America/Sao_Paulo",
                "times": [str(t).strip() for t in times if str(t).strip()],
                "digest_at": str(schedule.get("digest_at") or "").strip(),
                "misfire_grace_minutes": int(schedule.get("misfire_grace_minutes", 120)),
            }
            freq = schedule.get("source_frequency")
            if isinstance(freq, dict) and freq:
                config["schedule"]["source_frequency"] = freq
            try:
                _write_config(config)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True})

        return self._json(404, {"ok": False, "error": "not found"})


def serve(port: int | None = None) -> None:
    port = port or int(os.environ.get("DASHBOARD_PORT", "8080"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"CareerOps dashboard em http://0.0.0.0:{port} (projeto: {PROJECT_DIR})")
    httpd.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    serve()
