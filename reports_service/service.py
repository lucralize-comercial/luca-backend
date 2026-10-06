from datetime import datetime

from .agendor import buscar_mapa_campos_personalizados, listar_deals_funil_comercial
from .comercial import build_comercial
from .gestao import build_gestao
from .metrics import BRT, reference_days


def gerar_preview(now: datetime | None = None) -> dict:
    deals = listar_deals_funil_comercial()
    fields_map = buscar_mapa_campos_personalizados()
    yesterday, before = reference_days(now)

    comercial_text, comercial_data = build_comercial(deals, yesterday, fields_map)
    gestao_text, gestao_data = build_gestao(deals, yesterday, before)

    return {
        "modo": "TESTE_SEM_ENVIO",
        "gerado_em": (now or datetime.now(BRT)).astimezone(BRT).isoformat(),
        "funil": "Funil Comercial",
        "total_deals_lidos_funil": len(deals),
        "comercial": {"indicadores": comercial_data, "mensagem": comercial_text},
        "gestao": {"indicadores": gestao_data, "mensagem": gestao_text},
    }
