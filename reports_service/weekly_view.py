"""Renderização HTML segura e independente do relatório comercial semanal."""
from __future__ import annotations

from html import escape
from typing import Any


def _text(value: Any) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _items(values: Any, *, ordered: bool = False) -> str:
    if not isinstance(values, list):
        return ""
    tag = "ol" if ordered else "ul"
    body = "".join(f"<li>{_text(item)}</li>" for item in values if isinstance(item, str))
    return f"<{tag}>{body}</{tag}>" if body else "<p>Nenhum destaque registrado.</p>"


def renderizar_semanal_html(apresentacao: dict[str, Any]) -> str:
    """Retorna uma página completa; escapa todo conteúdo originado da IA."""
    cards = apresentacao.get("cards") or []
    if len(cards) != 3:
        raise ValueError("A apresentação semanal exige exatamente três cards.")
    periodo = apresentacao.get("periodo") or {}
    cards_html = "".join(
        '<article class="metric"><div class="metric-title">{}</div>'
        '<div class="metric-value">{}</div><div class="metric-delta">{}</div>'
        '<div class="previous">Semana anterior: {}</div></article>'.format(
            _text(card.get("titulo")), _text(card.get("valor")),
            _text(card.get("comparacao")), _text(card.get("anterior"))
        ) for card in cards
    )
    return """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Relatório semanal comercial</title>
<style>
:root{{font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:#17223b;background:#f4f6f9}}
*{{box-sizing:border-box}}body{{margin:0;padding:32px 18px}}
main{{max-width:950px;margin:auto;background:#fff;padding:42px;border-radius:18px;box-shadow:0 6px 25px #12223b12}}
header{{border-bottom:1px solid #e7ebf0;padding-bottom:24px;margin-bottom:24px}}
h1{{font-size:24px;letter-spacing:.03em;margin:0 0 8px}}
.period{{color:#697386;font-size:14px}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:20px 0 30px}}
.metric{{background:#f4f6f9;border-radius:12px;padding:20px}}
.metric-title,.previous{{color:#667084;font-size:13px}}
.metric-value{{font-size:38px;font-weight:750;margin:9px 0}}
.metric-delta{{font-weight:650;color:#273a66}}.previous{{margin-top:5px}}
section{{border-top:1px solid #e7ebf0;padding:20px 0}}
h2{{font-size:18px;margin:0 0 13px}}p,li{{font-size:15px;line-height:1.65}}
li{{margin:7px 0}}ul,ol{{padding-left:23px}}
footer{{color:#697386;font-size:12px;margin-top:20px}}
@media(max-width:640px){{main{{padding:23px}}body{{padding:10px}}.cards{{grid-template-columns:1fr}}.metric-value{{font-size:30px}}}}
@media print{{body{{padding:0;background:#fff}}main{{box-shadow:none;padding:0;max-width:none}}section{{break-inside:avoid}}}}
</style></head><body><main>
<header><h1>{title}</h1><div class="period">{inicio} a {fim}</div></header>
<div class="cards">{cards}</div>
<section><h2>Leitura do gestor</h2><p>{leitura}</p></section>
<section><h2>O que está performando</h2>{performando}</section>
<section><h2>O que está prejudicando</h2>{prejudicando}</section>
<section><h2>Ações recomendadas</h2>{acoes}</section>
<section><h2>Sinal para próxima semana</h2><p>{sinal}</p></section>
<footer>Confiança da análise: {confianca}. {ressalvas}</footer>
</main></body></html>""".format(
        title=_text(apresentacao.get("titulo", "ACOMPANHAMENTO COMERCIAL")),
        inicio=_text(periodo.get("inicio")), fim=_text(periodo.get("fim")),
        cards=cards_html,
        leitura=_text(apresentacao.get("leitura_gestor")),
        performando=_items(apresentacao.get("performando")),
        prejudicando=_items(apresentacao.get("prejudicando")),
        acoes=_items(apresentacao.get("acoes_recomendadas"), ordered=True),
        sinal=_text(apresentacao.get("sinal_proxima_semana")),
        confianca=_text(apresentacao.get("confianca")),
        ressalvas=" | ".join(_text(s) for s in apresentacao.get("ressalvas", []) if isinstance(s, str)),
    )
