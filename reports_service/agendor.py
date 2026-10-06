import threading
import time
from functools import lru_cache
from datetime import date, datetime, time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .config import AGENDOR_BASE, AGENDOR_TOKEN, FUNIL_COMERCIAL_ID

_MIN_INTERVAL = 0.36
_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
BRT = ZoneInfo("America/Sao_Paulo")
UTC = ZoneInfo("UTC")


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


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _bounds(day: date) -> tuple[str, str]:
    start = datetime.combine(day, dt_time.min, tzinfo=BRT)
    end = datetime.combine(day, dt_time.max, tzinfo=BRT)
    return _iso(start), _iso(end)


def _month_bounds(day: date) -> tuple[str, str]:
    start = datetime.combine(day.replace(day=1), dt_time.min, tzinfo=BRT)
    end = datetime.combine(day, dt_time.max, tzinfo=BRT)
    return _iso(start), _iso(end)


def _base_params(**extra) -> dict[str, Any]:
    params: dict[str, Any] = {
        "funnels": FUNIL_COMERCIAL_ID,
        "per_page": 1,
        "page": 1,
    }
    params.update({k: v for k, v in extra.items() if v is not None})
    return params


def contar_deals(**filters) -> int:
    payload = _get("/deals", params=_base_params(**filters))
    meta = payload.get("meta") or {}
    total = meta.get("totalCount")
    if total is not None:
        return int(total)
    return len(payload.get("data") or [])


def listar_deals(**filters) -> list[dict[str, Any]]:
    """Lista apenas o recorte necessário ao report, nunca o funil inteiro."""
    deals: list[dict[str, Any]] = []
    page = 1
    while True:
        params = {
            "funnels": FUNIL_COMERCIAL_ID,
            "per_page": 100,
            "page": page,
            **filters,
        }
        payload = _get("/deals", params=params)
        page_deals = payload.get("data") or []
        deals.extend(page_deals)
        if not (payload.get("links") or {}).get("next") or not page_deals:
            break
        page += 1
    return deals


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


_WON_CACHE_LOCK = threading.Lock()
_WON_CACHE_AT = 0.0
_WON_CACHE: list[dict[str, Any]] = []
_WON_CACHE_TTL = 60.0


def listar_ganhos_cacheados() -> list[dict[str, Any]]:
    """Carrega apenas negócios ganhos e reaproveita por 60s.

    O Agendor expõe `wonAt` no negócio, mas atualmente ignora filtros
    `wonAtGt/wonAtLt` no endpoint de deals. Como o universo de ganhos é muito
    menor que o funil inteiro, buscamos somente dealStatus=2 e filtramos
    `wonAt` localmente.
    """
    global _WON_CACHE_AT, _WON_CACHE
    now = time.monotonic()
    with _WON_CACHE_LOCK:
        if _WON_CACHE and (now - _WON_CACHE_AT) < _WON_CACHE_TTL:
            return _WON_CACHE
        data = listar_deals(dealStatus=2)
        _WON_CACHE = data
        _WON_CACHE_AT = time.monotonic()
        return _WON_CACHE


def contar_ganhos_por_won_at(start_iso: str, end_iso: str) -> int:
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    if start is None or end is None:
        return 0
    total = 0
    for deal in listar_ganhos_cacheados():
        won_at = _parse_iso(deal.get("wonAt"))
        if won_at is not None and start < won_at < end:
            total += 1
    return total


def _snapshot_open(day: date) -> int:
    """Estoque no fim do dia sem baixar os 6.789 negócios do funil.

    Mantém a mesma lógica conceitual do cálculo anterior para negócios que hoje
    pertencem ao Funil Comercial: entrou até o corte e ainda não havia encerrado.
    """
    _, cutoff = _bounds(day)

    # Negócios que continuam abertos hoje e já existiam no corte.
    ongoing = contar_deals(dealStatus=1, startAtLt=cutoff)

    # Negócios que hoje estão fechados, mas só foram encerrados depois do corte.
    # Portanto, no fim daquele dia ainda estavam em andamento.
    won_after = contar_deals(dealStatus=2, startAtLt=cutoff, endAtGt=cutoff)
    lost_after = contar_deals(dealStatus=3, startAtLt=cutoff, endAtGt=cutoff)
    return ongoing + won_after + lost_after


def _period_metrics(start_iso: str, end_iso: str, snapshot_day: date) -> dict[str, int]:
    return {
        "leads": contar_deals(startAtGt=start_iso, startAtLt=end_iso),
        "ganhos": contar_ganhos_por_won_at(start_iso, end_iso),
        "perdidos": contar_deals(dealStatus=3, endAtGt=start_iso, endAtLt=end_iso),
        "em_andamento": _snapshot_open(snapshot_day),
    }


def metricas_dia(day: date) -> dict[str, int]:
    start_iso, end_iso = _bounds(day)
    return _period_metrics(start_iso, end_iso, day)


def metricas_mes(through_day: date) -> dict[str, int]:
    start_iso, end_iso = _month_bounds(through_day)
    return _period_metrics(start_iso, end_iso, through_day)


def listar_leads_dia(day: date) -> list[dict[str, Any]]:
    start_iso, end_iso = _bounds(day)
    return listar_deals(
        startAtGt=start_iso,
        startAtLt=end_iso,
        withCustomFields="true",
    )



def contar_reunioes(start_iso: str, end_iso: str) -> int:
    """Conta a visão de reuniões do Agendor pela data agendada (dueDate).

    Não tenta classificar reunião como realizada/cancelada: replica apenas a visão
    de atividades do tipo meeting existente no Agendor.
    """
    payload = _get(
        "/tasks",
        params={
            "per_page": 1,
            "page": 1,
            "typesIn": "meeting",
            "dueDateGt": start_iso,
            "dueDateLt": end_iso,
        },
    )
    meta = payload.get("meta") or {}
    total = meta.get("totalCount")
    if total is not None:
        return int(total)
    return len(payload.get("data") or [])


def reunioes_dia(day: date) -> int:
    start_iso, end_iso = _bounds(day)
    return contar_reunioes(start_iso, end_iso)


def reunioes_mes(through_day: date) -> int:
    start_iso, end_iso = _month_bounds(through_day)
    return contar_reunioes(start_iso, end_iso)

def buscar_mapa_campos_personalizados() -> dict[str, dict[Any, str]]:
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
