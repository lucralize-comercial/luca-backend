"""Validação privada, temporária e sem envio do relatório semanal real.

Executar somente de forma explícita em pre-deploy controlado. Não grava dados
do Agendor nem texto da IA em logs; apenas resumo técnico e tokens.
"""
import json
import os
from .weekly_analysis import gerar_relatorio_semanal
from .weekly_view import renderizar_semanal_html


def main():
    if os.environ.get("REPORT_REAL_SMOKE_ONCE") != "enabled":
        print("WEEKLY_REAL_SMOKE=SKIPPED (not enabled)")
        return
    report = gerar_relatorio_semanal(usar_ia=True)
    presentation = report["apresentacao"]
    html = renderizar_semanal_html(presentation)
    if len(presentation["cards"]) != 3 or "<html" not in html:
        raise RuntimeError("Apresentação semanal real incompleta")
    usage = report["ia"]["uso"]
    print("WEEKLY_REAL_SMOKE=" + json.dumps({
        "ok": True,
        "periodo": presentation["periodo"],
        "cards": len(presentation["cards"]),
        "acoes": len(presentation["acoes_recomendadas"]),
        "confianca": presentation["confianca"],
        "modelo": report["ia"]["modelo"],
        "tentativas": report["ia"]["tentativas"],
        "uso": usage,
        "html_chars": len(html),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
