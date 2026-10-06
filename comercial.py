from datetime import date

from .metrics import daily_metrics, origins_for_day


def build_comercial(deals: list[dict], day: date, fields_map=None) -> tuple[str, dict]:
    m = daily_metrics(deals, day)
    origins = origins_for_day(deals, day, fields_map)
    dd = day.strftime("%d/%m/%Y")

    text = (
        "📊 **REPORT COMERCIAL — LUCRALIZE**\n\n"
        f"**Fechamento de ontem — {dd}**\n\n"
        f"👤 **Leads recebidos:** {m['leads']}\n"
        f"🟢 **Negócios ganhos:** {m['ganhos']}\n"
        f"🔴 **Negócios perdidos:** {m['perdidos']}\n"
        f"🟡 **Em andamento:** {m['em_andamento']}\n\n"
        "\u200e\n\n"
        "**📍 Origem dos leads**\n\n"
        f"• Google Ads: **{origins['Google Ads']}**\n"
        f"• Meta Ads: **{origins['Meta Ads']}**\n"
        f"• Calculadora: **{origins['Calculadora']}**\n"
        f"• WhatsApp/Site: **{origins['WhatsApp/Site']}**\n"
        f"• Outros: **{origins['Outros']}**\n\n"
        "\u200e\n\n"
        "**📝 Resumo do dia**\n\n"
        f"Entraram **{m['leads']} novos leads** no Funil Comercial.\n"
        f"**{m['ganhos']} negócios foram ganhos** e **{m['perdidos']} foram encerrados como perdidos**.\n\n"
        "*Relatório automático — Luca*"
    )
    return text, {"data": day.isoformat(), **m, "origens": origins}
