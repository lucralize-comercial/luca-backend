from datetime import datetime

from .agendor import (
    buscar_mapa_campos_personalizados,
    listar_leads_dia,
    metricas_dia,
    metricas_mes,
    reunioes_dia,
    reunioes_mes,
)
from .comercial import build_comercial
from .gestao import build_gestao
from .metrics import BRT, reference_days, top_origins_comparison
from .meeting_audit import meeting_counts


def gerar_preview(now: datetime | None = None) -> dict:
    yesterday, before = reference_days(now)

    ontem = metricas_dia(yesterday)
    antes = metricas_dia(before)
    mes = metricas_mes(yesterday)

    meetings = meeting_counts(min(before, yesterday.replace(day=1)), yesterday)
    for day, metrics in ((yesterday, ontem), (before, antes)):
        found = meetings.get(day.isoformat(), {})
        metrics["reunioes_agendadas"] = found.get("reunioes_agendadas", 0)
        metrics["agenda_de_reunioes"] = found.get("agenda_de_reunioes", 0)
    mes["reunioes_agendadas"] = sum(v["reunioes_agendadas"] for v in meetings.values())
    mes["agenda_de_reunioes"] = sum(v["agenda_de_reunioes"] for v in meetings.values())

    # Origem detalhada para os dois dias comparados.
    leads_ontem = listar_leads_dia(yesterday)
    leads_antes = listar_leads_dia(before)
    fields_map = buscar_mapa_campos_personalizados()
    all_origins = top_origins_comparison(leads_antes, leads_ontem, fields_map)

    comercial_text, comercial_data = build_comercial(ontem, leads_ontem, yesterday, fields_map, all_origins)
    gestao_text, gestao_data = build_gestao(ontem, antes, mes, yesterday, before, all_origins)

    return {
        "modo": "TESTE_SEM_ENVIO",
        "gerado_em": (now or datetime.now(BRT)).astimezone(BRT).isoformat(),
        "funil": "Funil Comercial",
        "deals_detalhados_lidos": len(leads_ontem) + len(leads_antes),
        "comercial": {"indicadores": comercial_data, "mensagem": comercial_text},
        "gestao": {"indicadores": gestao_data, "mensagem": gestao_text},
    }
