"""Entrega semanal para Teams com marcação persistente por período.

Execução exclusivamente por job dedicado (não pelo servidor Flask).
Nunca reenvia automaticamente períodos com tentativa registrada:
uma falha incerta precisa de revisão manual para evitar duplicação.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import requests

from .weekly_analysis import gerar_relatorio_semanal

BRT = ZoneInfo("America/Sao_Paulo")


def _adaptive_card(presentation: dict) -> dict:
    cards = presentation["cards"]
    rows = [
        {"type": "TextBlock", "text": "ACOMPANHAMENTO COMERCIAL",
         "size": "Large", "weight": "Bolder"},
        {"type": "TextBlock",
         "text": f'{presentation["periodo"]["inicio"]} a {presentation["periodo"]["fim"]}',
         "isSubtle": True},
    ]
    for metric in cards:
        rows.append({"type": "TextBlock", "wrap": True,
                     "text": f'**{metric["titulo"]}: {metric["valor"]}**  '
                             f'({metric["comparacao"]}; anterior: {metric["anterior"]})'})
    for heading, key in (
        ("Leitura do gestor", "leitura_gestor"),
        ("O que está performando", "performando"),
        ("O que está prejudicando", "prejudicando"),
        ("Ações recomendadas", "acoes_recomendadas"),
        ("Sinal para próxima semana", "sinal_proxima_semana"),
    ):
        rows.append({"type": "TextBlock", "text": heading, "weight": "Bolder", "spacing": "Medium"})
        value = presentation[key]
        if isinstance(value, list):
            value = "\\n".join(f"{i}. {text}" for i, text in enumerate(value, 1))
        rows.append({"type": "TextBlock", "text": str(value), "wrap": True})
    rows.append({"type": "TextBlock", "text": "Análise Claude | Confiança: " + presentation["confianca"],
                 "isSubtle": True, "wrap": True})
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard", "version": "1.4", "body": rows},
    }]}


def executar_envio_semanal(*, now: datetime | None = None, generate=gerar_relatorio_semanal,
                           post=requests.post, db_path: str | None = None) -> dict:
    if os.environ.get("REPORT_SEND_ENABLED", "false").lower() != "true":
        return {"status": "disabled"}
    local = (now or datetime.now(BRT)).astimezone(BRT)
    if local.weekday() != 0 or (local.hour, local.minute) < (8, 15):
        return {"status": "outside_schedule"}
    if not os.environ.get("TEAMS_WEBHOOK_SEMANAL_PRIVADO"):
        raise RuntimeError("TEAMS_WEBHOOK_SEMANAL_PRIVADO ausente")
    path = Path(db_path or os.environ.get("REPORT_WEEKLY_DB_PATH", "/data/weekly_delivery.sqlite3"))
    if not path.parent.exists():
        raise RuntimeError("Diretório persistente indisponível; envio bloqueado")
    # O calendário referente ao período anterior é sempre identificado pela segunda atual.
    period_key = local.date().isoformat()
    with sqlite3.connect(path, timeout=30) as db:
        db.execute("CREATE TABLE IF NOT EXISTS weekly_dispatch "
                   "(week_key TEXT PRIMARY KEY, state TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.commit()
        # Reserva irreversível antes de chamar APIs externas: entrega no máximo uma vez.
        changed = db.execute(
            "INSERT OR IGNORE INTO weekly_dispatch VALUES (?, 'reserved', datetime('now'))",
            (period_key,),
        ).rowcount
        db.commit()
    if not changed:
        return {"status": "already_attempted", "week_key": period_key}
    try:
        report = generate(usar_ia=True, now=local)
        payload = _adaptive_card(report["apresentacao"])
        response = post(os.environ["TEAMS_WEBHOOK_SEMANAL_PRIVADO"],
                        json=payload, timeout=30)
        response.raise_for_status()
    except Exception:
        with sqlite3.connect(path) as db:
            db.execute("UPDATE weekly_dispatch SET state='needs_review' WHERE week_key=?", (period_key,))
            db.commit()
        raise
    with sqlite3.connect(path) as db:
        db.execute("UPDATE weekly_dispatch SET state='sent' WHERE week_key=?", (period_key,))
        db.commit()
    return {"status": "sent", "week_key": period_key}
