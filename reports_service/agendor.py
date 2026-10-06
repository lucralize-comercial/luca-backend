import threading
import time
from typing import Any

import requests

from .config import AGENDOR_BASE, AGENDOR_TOKEN, FUNIL_COMERCIAL_ID

_MIN_INTERVAL = 0.36
_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


def _headers():
    if not AGENDOR_TOKEN:
        raise RuntimeError("AGENDOR_TOKEN não configurado no serviço de reports")
    return {"Authorization": f"Token {AGENDOR_TOKEN}"}


def _wait_slot():
    global _LAST_REQUEST_AT
    with _LOCK:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _LAST_REQUEST_AT)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST_AT = time.monotonic()


def _get(path: str, *, params=None, timeout=60):
    url = f"{AGENDOR_BASE}{path}"
    last_error = None
    for attempt in range(5):
        _wait_slot()
        try:
            r = requests.get(url, headers=_headers(), params=params, timeout=timeout)
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(2 ** attempt, 8)
                except (TypeError, ValueError):
                    delay = min(2 ** attempt, 8)
                time.sleep(max(1.0, delay))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"Falha consultando Agendor {path}: {last_error}")


def listar_deals_funil_comercial() -> list[dict[str, Any]]:
    """Busca todos os negócios e mantém apenas o Funil Comercial.

    Replica somente a paginação necessária ao report. Não depende do app.py do Luca.
    """
    deals = []
    page = 1
    while True:
        payload = _get(
            "/deals",
            params={
                "per_page": 100,
                "page": page,
                "withCustomFields": "true",
                "order_by": "updatedAt",
                "order_dir": "desc",
            },
        )
        page_deals = payload.get("data") or []
        for deal in page_deals:
            funnel_id = (((deal.get("dealStage") or {}).get("funnel") or {}).get("id"))
            if funnel_id == FUNIL_COMERCIAL_ID:
                deals.append(deal)
        if not (payload.get("links") or {}).get("next") or not page_deals:
            break
        page += 1
    return deals


def buscar_mapa_campos_personalizados() -> dict[str, dict[Any, str]]:
    """Retorna {slug_do_campo: {id_da_opcao: nome}} quando disponível.

    É tolerante às variações de schema do endpoint e serve somente para traduzir
    opções como Origem do Negócio em nomes legíveis para o classificador.
    """
    try:
        payload = _get("/custom_fields/deals", timeout=20)
    except Exception:
        return {}

    result: dict[str, dict[Any, str]] = {}
    for field in payload.get("data") or []:
        key = field.get("identifier") or field.get("key") or field.get("slug")
        if not key:
            continue
        mapping: dict[Any, str] = {}
        for opt in (field.get("options") or field.get("values") or []):
            oid = opt.get("id")
            name = opt.get("name") or opt.get("value") or opt.get("label")
            if oid is not None and name is not None:
                mapping[oid] = str(name)
                mapping[str(oid)] = str(name)
        result[str(key)] = mapping
    return result
