from datetime import datetime

from .agendor import (
    buscar_mapa_campos_personalizados,
    listar_leads_dia,
    metricas_dia,
    metricas_mes,
)
from .comercial import build_comercial
from .gestao import build_gestao
from .metrics import BRT, reference_days


def gerar_preview(now: datetime | None = None) -> dict:
    yesterday, before = reference_days(now)

    ontem = metricas_dia(yesterday)
    antes = metricas_dia(before)
    mes = metricas_mes(yesterday)

    # Só os leads de ontem precisam ser carregados linha a linha, porque o
    # report comercial precisa classificar a origem de cada um.
    leads_ontem = listar_leads_dia(yesterday)
    fields_map = buscar_mapa_campos_personalizados()

    comercial_text, comercial_data = build_comercial(ontem, leads_ontem, yesterday, fields_map)
    gestao_text, gestao_data = build_gestao(ontem, antes, mes, yesterday, before)

    return {
        "modo": "TESTE_SEM_ENVIO",
        "gerado_em": (now or datetime.now(BRT)).astimezone(BRT).isoformat(),
        "funil": "Funil Comercial",
        "deals_detalhados_lidos": len(leads_ontem),
        "comercial": {"indicadores": comercial_data, "mensagem": comercial_text},
        "gestao": {"indicadores": gestao_data, "mensagem": gestao_text},
    }
