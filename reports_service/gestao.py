from datetime import date

from .metrics import comparison_pct


def _line(label: str, current: int, previous: int, icon: str) -> str:
    arrow, pct = comparison_pct(current, previous)
    return f"{icon} **{label}:** {current} | {previous} {arrow} {pct}"


def build_gestao(y: dict, b: dict, month: dict, yesterday: date, before: date) -> tuple[str, dict]:
    text = (
        "📈 **REPORT DE GESTÃO — COMERCIAL**\n\n"
        f"**Ontem ({yesterday.strftime('%d/%m')}) | Antes de ontem ({before.strftime('%d/%m')})**\n\n"
        f"{_line('Leads recebidos', y['leads'], b['leads'], '👤')}\n"
        f"{_line('Ganhos', y['ganhos'], b['ganhos'], '🟢')}\n"
        f"{_line('Perdidos', y['perdidos'], b['perdidos'], '🔴')}\n"
        f"{_line('Em andamento', y['em_andamento'], b['em_andamento'], '🟡')}\n\n"
        "\u200e\n\n"
        "**📅 Acumulado do mês**\n\n"
        f"👤 Leads recebidos: **{month['leads']}**\n"
        f"🟢 Ganhos: **{month['ganhos']}**\n"
        f"🔴 Perdidos: **{month['perdidos']}**\n"
        f"🟡 Em andamento: **{month['em_andamento']}**\n\n"
        "*Relatório automático — Luca*"
    )
    return text, {
        "ontem": {"data": yesterday.isoformat(), **y},
        "antes_de_ontem": {"data": before.isoformat(), **b},
        "acumulado_mes": month,
    }
