"""Apresentação de relatórios no Teams via Workflows (Adaptive Cards).

Não modifica os cálculos do Agendor, não agenda envios e não envia ao importar.
"""
import os
from datetime import date
from typing import Any

import requests


WEBHOOKS = {
    "comercial": "TEAMS_WEBHOOK_COMERCIAL",
    "gestao": "TEAMS_WEBHOOK_GESTAO",
}


def _line(label: str, value: str, bold_value: bool = True) -> dict:
    txt = f"{label} **{value}**" if bold_value else f"{label} {value}"
    return {"type": "TextBlock", "text": txt, "wrap": True, "spacing": "None", "size": "Default"}


def _heading(label: str, *, first: bool = False) -> dict:
    return {
        "type": "TextBlock", "text": label, "weight": "Bolder", "wrap": True,
        "spacing": "None" if first else "Medium",
    }


def _pct(old: int, new: int) -> str:
    if old == 0 and new > 0:
        return "↑ novo"
    if old == 0 and new == 0:
        return "→ 0,0%"
    delta = (new - old) / old * 100
    arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
    return f"{arrow} {abs(delta):.1f}%".replace(".", ",")


def _date(iso: str, fmt: str) -> str:
    return date.fromisoformat(iso[:10]).strftime(fmt)


def _base(title: str) -> list[dict]:
    return [
        _heading(title, first=True),
        {"type": "TextBlock", "text": "Relatório automático — Luca", "isSubtle": True,
         "italic": True, "wrap": True, "spacing": "None"},
    ]


def montar_cartao(preview: dict[str, Any], destino: str) -> dict[str, Any]:
    if destino not in WEBHOOKS:
        raise ValueError("Destino inválido")

    if destino == "comercial":
        m = preview["comercial"]["indicadores"]
        d = _date(m["data"], "%d/%m/%Y")
        body = _base(f"RESUMO COMERCIAL — {d}")
        body.extend([
            _line("Leads recebidos:", str(m["leads"])),
            _line("Reuniões:", str(m["reunioes"])),
            _line("Negócios ganhos:", str(m["ganhos"])),
            _line("Negócios perdidos:", str(m["perdidos"])),
            _line("Em andamento:", str(m["em_andamento"])),
            _heading("Origem dos leads"),
        ])
        for origin in ("Google Ads", "Meta Ads", "Calculadora", "WhatsApp/Site", "Outros"):
            body.append(_line(f"• {origin}:", str(m["origens"][origin])))
        body.extend([
            _heading("Resumo do dia"),
            _line("Entraram", f"{m['leads']} novos leads", True),
        ])
        # Texto narrativo compacto, sem espaços extras entre as linhas
        body[-1]["text"] += " no Funil Comercial."
        body.append(_line("Foi registrada", f"{m['reunioes']} reunião" if m["reunioes"] == 1 else f"{m['reunioes']} reuniões"))
        body[-1]["text"] += "."
        body.append(_line("", f"{m['ganhos']} negócios foram ganhos"))
        body[-1]["text"] += f" e **{m['perdidos']} foram encerrados como perdidos**."
    else:
        g = preview["gestao"]["indicadores"]
        b, y, m = g["antes_de_ontem"], g["ontem"], g["acumulado_mes"]
        body = _base("ACOMPANHAMENTO COMERCIAL")
        body.append(_heading(f"Antes de ontem ({_date(b['data'], '%d/%m')}) | Ontem ({_date(y['data'], '%d/%m')})"))
        for label, key in (("Leads recebidos:", "leads"), ("Reuniões:", "reunioes"),
                           ("Ganhos:", "ganhos"), ("Perdidos:", "perdidos"),
                           ("Em andamento:", "em_andamento")):
            body.append(_line(label, f"{b[key]} | {y[key]} {_pct(b[key], y[key])}"))
        body.append(_heading("Acumulado do mês"))
        for label, key in (("Leads recebidos:", "leads"), ("Reuniões:", "reunioes"),
                           ("Ganhos:", "ganhos"), ("Perdidos:", "perdidos"),
                           ("Em andamento:", "em_andamento")):
            body.append(_line(label, str(m[key])))

    return {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
                         "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                                     "type": "AdaptiveCard", "version": "1.2", "body": body}}],
    }


def enviar_teste(preview: dict[str, Any], destino: str, *, confirmacao: str) -> int:
    """Somente envio de teste por chamada explícita, nunca agendamento."""
    if confirmacao != "CONFIRMAR_ENVIO_TESTE_TEAMS":
        raise PermissionError("Envio de teste exige confirmação explícita")
    var = WEBHOOKS.get(destino)
    if var is None:
        raise ValueError("Destino inválido")
    url = os.environ.get(var, "")
    if not url.startswith("https://"):
        raise RuntimeError(f"Webhook {var} ausente ou inválido")
    response = requests.post(url, json=montar_cartao(preview, destino), timeout=25)
    response.raise_for_status()
    return response.status_code
