"""Envio diário isolado via Railway Cron (08h America/Sao_Paulo).

Uso em cron: python -m reports_service.daily_sender
Uso em simulação: python -m reports_service.daily_sender --dry-run

Requer um volume persistente montado em /data, dois webhooks e uma flag de
ativação independente. Não é importado pelo web service.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import requests

from .teams_preview import WEBHOOKS, montar_cartao

BRT = ZoneInfo("America/Sao_Paulo")
LOGGER = logging.getLogger("luca-reports-daily")
STORE_DEFAULT = "/data/luca_reports_dispatch.sqlite3"


class DispatchLedger:
    """Reserva durável antes do POST. Uma entrega incerta NÃO é reenviada automaticamente."""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS sends (
            delivery_key TEXT PRIMARY KEY,
            destination TEXT NOT NULL,
            reference_date TEXT NOT NULL,
            reserved_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('reserved','accepted','uncertain')),
            http_status INTEGER,
            updated_at TEXT NOT NULL
        )""")

    def reserve(self, key: str, destination: str, reference_date: str, timestamp: str) -> bool:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = self.db.execute("""INSERT OR IGNORE INTO sends
                (delivery_key, destination, reference_date, reserved_at, state, updated_at)
                VALUES (?, ?, ?, ?, 'reserved', ?)""",
                (key, destination, reference_date, timestamp, timestamp))
            self.db.execute("COMMIT")
            return result.rowcount == 1
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def finish(self, key: str, state: str, http_status: int | None, timestamp: str):
        if state not in ("accepted", "uncertain"):
            raise ValueError("Invalid state")
        self.db.execute("UPDATE sends SET state=?, http_status=?, updated_at=? WHERE delivery_key=?",
                        (state, http_status, timestamp, key))

    def close(self):
        self.db.close()


def _mount_verified(path: str) -> bool:
    """Evita falsa idempotência com o sistema de arquivos efêmero do container."""
    volume = os.environ.get("REPORT_VOLUME_MOUNT", "/data")
    absolute_path = os.path.abspath(path)
    volume = os.path.abspath(volume)
    return (absolute_path.startswith(volume + os.sep)
            and os.path.isdir(volume)
            and os.path.ismount(volume))


def _now() -> datetime:
    return datetime.now(BRT)


def _validate_preview(preview: dict, reference_date: str):
    c = preview["comercial"]["indicadores"]
    g = preview["gestao"]["indicadores"]
    if c["data"] != reference_date or g["ontem"]["data"] != reference_date:
        raise RuntimeError("Datas dos relatórios não correspondem ao fechamento esperado")
    if sum(c["origens"].values()) != c["leads"]:
        raise RuntimeError("Total das origens não confere com Leads recebidos")
    for key in ("leads", "reunioes", "ganhos", "perdidos", "em_andamento"):
        if c[key] != g["ontem"][key]:
            raise RuntimeError("Comercial e gestão não conferem em " + key)


def run(*, dry_run: bool = False, now: datetime | None = None,
        ledger_path: str | None = None, session=None, verify_mount: bool = True,
        preview_provider=None) -> dict:
    current = (now or _now()).astimezone(BRT)
    period = (current.date() - timedelta(days=1)).isoformat()
    if not dry_run:
        if os.environ.get("REPORT_CRON_ENABLED", "false").lower() != "true":
            raise RuntimeError("Envio diário bloqueado: REPORT_CRON_ENABLED não está habilitado")
        if os.environ.get("REPORT_SEND_ENABLED", "false").lower() != "true":
            raise RuntimeError("Envio diário bloqueado: REPORT_SEND_ENABLED não está habilitado")
        if current.hour != 8:
            raise RuntimeError("Fora da janela permitida: 08:00–08:59, America/Sao_Paulo")
        for env in WEBHOOKS.values():
            if not os.environ.get(env, "").startswith("https://"):
                raise RuntimeError("Webhook não configurado: " + env)

    if preview_provider is None:
        from .service import gerar_preview
        preview_provider = gerar_preview
    preview = preview_provider(now=current)
    _validate_preview(preview, period)
    result = {"reference_date": period, "mode": "dry_run" if dry_run else "real", "destinations": {}}
    if dry_run:
        for dest in WEBHOOKS:
            card = montar_cartao(preview, dest)
            result["destinations"][dest] = {"status": "prepared", "blocks": len(card["attachments"][0]["content"]["body"])}
        return result

    db_path = ledger_path or os.environ.get("REPORT_STORE_PATH", STORE_DEFAULT)
    if verify_mount and not _mount_verified(db_path):
        raise RuntimeError("Volume persistente em /data ausente: envio bloqueado")

    ledger = DispatchLedger(db_path)
    http = session or requests
    failed = False
    try:
        for dest, env in WEBHOOKS.items():
            key = f"v1:{dest}:{period}"
            if not ledger.reserve(key, dest, period, current.isoformat()):
                result["destinations"][dest] = {"status": "already_reserved"}
                continue
            try:
                response = http.post(os.environ[env], json=montar_cartao(preview, dest), timeout=25)
                status = int(response.status_code)
                if status < 200 or status >= 300:
                    raise RuntimeError("HTTP status " + str(status))
            except Exception:
                # Pode ter chegado ao Teams antes de ocorrer o erro de rede.
                # Mantém reserva e exige inspeção manual antes de qualquer reenvio.
                ledger.finish(key, "uncertain", None, _now().isoformat())
                result["destinations"][dest] = {"status": "uncertain_review_required"}
                LOGGER.exception("Entrega incerta ao destino %s na data %s", dest, period)
                failed = True
            else:
                ledger.finish(key, "accepted", status, _now().isoformat())
                result["destinations"][dest] = {"status": "accepted", "http_status": status}
    finally:
        ledger.close()
    if failed:
        result["attention_required"] = True
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Gera e valida sem enviar nem gravar no banco")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    result = run(dry_run=args.dry_run)
    print("REPORT_DAILY_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
    if result.get("attention_required"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
