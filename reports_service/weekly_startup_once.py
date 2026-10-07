from __future__ import annotations

import json
import os

from .weekly_analysis import gerar_relatorio_semanal

# build marker v3


def _dump(label, value):
    print(label + "=" + json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def main():
    result = gerar_relatorio_semanal(usar_ia=True)
    pacote = result["pacote"]
    resumo = {
        "periodo": pacote["periodo"],
        "indicadores": pacote["indicadores"],
        "taxas": pacote["taxas"],
        "origens_semana_atual": pacote["origens_coorte_semana_atual"],
        "perdas_semana_atual": pacote["perdas_semana_atual"],
        "funil_aberto_agora": pacote["funil_aberto_agora"],
    }
    _dump("WEEKLY_DATA", resumo)
    _dump("WEEKLY_AI", result["ia"]["analise"])
    _dump("WEEKLY_USAGE", result["ia"]["uso"])
    print("WEEKLY_MODEL=" + result["ia"]["modelo"], flush=True)
    os.execvp("gunicorn", ["gunicorn", "reports_service.app:app"])


if __name__ == "__main__":
    main()
