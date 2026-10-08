"""Envio avulso explicitamente autorizado referente a 28/09–04/10/2026."""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from .weekly_delivery import executar_envio_semanal

if __name__ == "__main__":
    if os.environ.get("REPORT_BACKFILL_APPROVED") != "2026-10-05":
        raise SystemExit("Envio retroativo não autorizado")
    result = executar_envio_semanal(
        now=datetime(2026, 10, 5, 8, 15, tzinfo=ZoneInfo("America/Sao_Paulo"))
    )
    print("WEEKLY_BACKFILL_RESULT=" + json.dumps(result, ensure_ascii=False))
