"""CV profile estruturado — Fase 8.

Estrutura o currículo do candidato para matching determinístico, evitando
dependência exclusiva de keywords nos títulos.

As constantes abaixo são a "fonte da verdade" para matching local; mantê-las
em sincronia com o currículo (vault Obsidian) é trabalho humano. Atualize este
arquivo quando o CV evoluir.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# -------------------------------------------------------- cargos-alvo

ROLE_KEYWORDS: list[str] = [
    # Ordem importa — primeira ocorrência na interseção vence (Fase 9).
    "site reliability engineer", "site reliability",
    "sre",
    "devops engineer", "devops",
    "infrastructure engineer", "infrastructure",
    "cloud engineer", "cloud infrastructure",
    "platform engineer", "platform",
    "reliability engineer",
    "production engineer",
]


# -------------------------------------------------------- skills (peso)

# Skill -> peso em skills_match (0..1). Skills mais raras/específicas pesam mais.
SKILLS_WEIGHT: dict[str, float] = {
    # Cloud
    "oci": 0.18,
    "aws": 0.16,
    "azure": 0.10,
    "gcp": 0.08,
    # Containers / orquestração
    "kubernetes": 0.16,
    "docker": 0.12,
    "docker swarm": 0.08,
    "helm": 0.06,
    # IaC
    "terraform": 0.16,
    "ansible": 0.14,
    "pulumi": 0.06,
    "cloudformation": 0.06,
    # OS / runtime
    "linux": 0.12,
    "windows server": 0.04,
    # Observabilidade
    "grafana": 0.10,
    "zabbix": 0.10,
    "graylog": 0.06,
    "new relic": 0.08,
    "elastic apm": 0.06,
    "elk": 0.06,
    "prometheus": 0.08,
    "loki": 0.04,
    # CI/CD
    "gitlab ci": 0.10,
    "github actions": 0.08,
    "jenkins": 0.06,
    "ci/cd": 0.10,
    # Scripting
    "bash": 0.08,
    "powershell": 0.06,
    "python": 0.06,
    # Automação / orquestração
    "n8n": 0.06,
    "airflow": 0.04,
    # Redes / AD
    "networking": 0.08,
    "active directory": 0.06,
    "vpn": 0.06,
    "haproxy": 0.06,
    # Práticas
    "gitops": 0.06,
    "devsecops": 0.06,
    "secops": 0.04,
}


# -------------------------------------------------------- senioridade

SENIORITY_ACCEPTED = {"senior", "staff", "principal"}


# --------------------------------------------------------- localização

# Aceita vagas remotas globais + Brasil remoto + países-alvo explícitos.
COUNTRY_PREFERRED = {"brazil", "netherlands", "united kingdom", "ireland",
                     "poland", "germany", "switzerland", "luxembourg",
                     "denmark", "norway", "canada", "sweden", "austria",
                     "finland", "france", "worldwide"}


@dataclass(frozen=True)
class CVProfile:
    roles: tuple[str, ...] = tuple(ROLE_KEYWORDS)
    skills: dict[str, float] = field(default_factory=lambda: dict(SKILLS_WEIGHT))
    seniority_accepted: set[str] = field(default_factory=lambda: set(SENIORITY_ACCEPTED))
    countries: set[str] = field(default_factory=lambda: set(COUNTRY_PREFERRED))
    # Match requisitos "extras" do candidato
    prefers_remote: bool = True
    min_years_experience: int = 5
    visa_required_ok: bool = True   # candidato busca realocação

    @property
    def max_skill_score(self) -> float:
        # Considera top-12 skills nas vagas para normalização
        return sum(sorted(self.skills.values(), reverse=True)[:12])
