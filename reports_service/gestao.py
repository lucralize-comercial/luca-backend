from datetime import date

from .metrics import comparison_pct


def _line(label: str, previous: int, current: int) -> str:
    arrow, pct = comparison_pct(current, previous)
    return f"{label}: **{previous} | {current} {arrow} {pct}**"


def build_gestao(y: dict, b: dict, month: dict, yesterday: date, before: date) -> tuple[str, dict]:
    text = (
        "**ACOMPANHAMENTO COMERCIAL**\n\n"
        f"**Antes de ontem ({before.strftime('%d/%m')}) | Ontem ({yesterday.strftime('%d/%m')})**\n\n"
        f"{_line('Leads recebidos', b['leads'], y['leads'])}\n"
        f"{_line('Reuniões', b['reunioes'], y['reunioes'])}\n"
        f"{_line('Ganhos', b['ganhos'], y['ganhos'])}\n"
        f"{_line('Perdidos', b['perdidos'], y['perdidos'])}\n"
        f"{_line('Em andamento', b['em_andamento'], y['em_andamento'])}\n\n"
        "\u200e\n\n"
        "**Acumulado do mês**\n\n"
        f"Leads recebidos: **{month['leads']}**\n"
        f"Reuniões: **{month['reunioes']}**\n"
        f"Ganhos: **{month['ganhos']}**\n"
        f"Perdidos: **{month['perdidos']}**\n"
        f"Em andamento: **{month['em_andamento']}**\n\n"
        "*Relatório automático — Luca*"
    )
    return text, {
        "ontem": {"data": yesterday.isoformat(), **y},
        "antes_de_ontem": {"data": before.isoformat(), **b},
        "acumulado_mes": month,
    }
