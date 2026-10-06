from datetime import date

from .metrics import origins_for_day


def build_comercial(metrics: dict, leads_do_dia: list[dict], day: date, fields_map=None) -> tuple[str, dict]:
    origins = origins_for_day(leads_do_dia, day, fields_map)
    dd = day.strftime("%d/%m/%Y")

    text = (
        f"**RESUMO COMERCIAL — {dd}**\n\n"
        f"Leads recebidos: **{metrics['leads']}**\n"
        f"Reuniões: **{metrics['reunioes']}**\n"
        f"Negócios ganhos: **{metrics['ganhos']}**\n"
        f"Negócios perdidos: **{metrics['perdidos']}**\n"
        f"Em andamento: **{metrics['em_andamento']}**\n\n"
        "\u200e\n\n"
        "**Origem dos leads**\n\n"
        f"• Google Ads: **{origins['Google Ads']}**\n"
        f"• Meta Ads: **{origins['Meta Ads']}**\n"
        f"• Calculadora: **{origins['Calculadora']}**\n"
        f"• WhatsApp/Site: **{origins['WhatsApp/Site']}**\n"
        f"• Outros: **{origins['Outros']}**\n\n"
        "\u200e\n\n"
        "**Resumo do dia**\n\n"
        f"Entraram **{metrics['leads']} novos leads** no Funil Comercial.\n"
        f"Foram registradas **{metrics['reunioes']} reuniões**.\n"
        f"**{metrics['ganhos']} negócios foram ganhos** e **{metrics['perdidos']} foram encerrados como perdidos**.\n\n"
        "*Relatório automático — Luca*"
    )
    return text, {"data": day.isoformat(), **metrics, "origens": origins}
