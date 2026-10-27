from datetime import date

from .metrics import origins_for_day


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def build_comercial(metrics: dict, leads_do_dia: list[dict], day: date, fields_map=None, all_origins=None) -> tuple[str, dict]:
    origins = all_origins['ontem'] if all_origins else origins_for_day(leads_do_dia, day, fields_map)
    dd = day.strftime("%d/%m/%Y")
    leads = metrics['leads']
    reunioes = metrics['reunioes_agendadas']
    ganhos = metrics['ganhos']
    perdidos = metrics['perdidos']

    text = (
        f"**RESUMO COMERCIAL — {dd}**\n\n"
        f"Leads recebidos: **{metrics['leads']}**\n"
        f"Reuniões agendadas: **{metrics['reunioes_agendadas']}**\n"
        f"Agenda de reuniões: **{metrics['agenda_de_reunioes']}**\n"
        f"Negócios ganhos: **{metrics['ganhos']}**\n"
        f"Negócios perdidos: **{metrics['perdidos']}**\n"
        f"Em andamento: **{metrics['em_andamento']}**\n\n"
        "\u200e\n\n"
        "**Origem dos leads**\n\n"
        + "".join(f"• {name}: **{count}**\n" for name, count in origins.items()) + "\n"
        + "\u200e\n\n"
        + "**Resumo do dia**\n\n"
        f"Entraram **{leads} {_plural(leads, 'novo lead', 'novos leads')}** no Funil Comercial.\n"
        f"{_plural(reunioes, 'Foi registrada', 'Foram registradas')} **{reunioes} {_plural(reunioes, 'reunião', 'reuniões')}**.\n"
        f"**{ganhos} {_plural(ganhos, 'negócio foi ganho', 'negócios foram ganhos')}** e **{perdidos} {_plural(perdidos, 'foi encerrado como perdido', 'foram encerrados como perdidos')}**.\n\n"
        "*Relatório automático — Luca*"
    )
    return text, {"data": day.isoformat(), **metrics, "origens": origins}
