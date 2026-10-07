import threading
import time
from functools import lru_cache
from datetime import date, datetime, time as dt_time, timedelta
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

    Como os filtros de data de conclusão do endpoint não reproduziram de forma
    confiável a visão do Sumário, buscamos somente dealStatus=2 e aplicamos
    a mesma prioridade de data da aba Contratos Ganhos localmente. O universo de
    ganhos é muito menor que o funil inteiro.
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


def _data_ganho_raw(deal: dict[str, Any]) -> str | None:
    """Replica a prioridade usada na aba Contratos Ganhos do dashboard.

    Ordem: endTime -> wonAt -> finishedAt -> closedAt.
    O valor bruto é preservado porque endTime é uma data de negócio
    representada como meia-noite UTC; convertê-la para o fuso de Brasília
    mudaria indevidamente o dia usado nos relatórios.
    """
    for field in ("endTime", "wonAt", "finishedAt", "closedAt"):
        value = deal.get(field)
        if value:
            return str(value)
    return None


def _data_ganho_date(deal: dict[str, Any]) -> date | None:
    value = _data_ganho_raw(deal)
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def _eh_funil_comercial(deal: dict[str, Any]) -> bool:
    funnel = ((deal.get("dealStage") or {}).get("funnel") or {})
    try:
        return int(funnel.get("id")) == int(FUNIL_COMERCIAL_ID)
    except (TypeError, ValueError):
        return False


def contar_ganhos_por_data_ganho(start_day: date, end_day: date) -> int:
    """Conta ganhos pela data de conclusão, sem conversão de fuso horário."""
    return sum(
        1
        for deal in listar_ganhos_cacheados()
        if _eh_funil_comercial(deal)
        and (data_ganho := _data_ganho_date(deal)) is not None
        and start_day <= data_ganho <= end_day
    )


def _data_perda_raw(deal: dict[str, Any]) -> str | None:
    """Data de conclusão de negócio perdido.

    A data de negócio (`endTime`) tem prioridade. `lostAt` é apenas fallback
    para registros antigos ou incompletos. O valor bruto é preservado para
    evitar deslocamento de dia por fuso horário.
    """
    for field in ("endTime", "lostAt", "finishedAt", "closedAt"):
        value = deal.get(field)
        if value:
            return str(value)
    return None


def _data_perda_date(deal: dict[str, Any]) -> date | None:
    value = _data_perda_raw(deal)
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def contar_perdidos_por_data_conclusao(start_day: date, end_day: date) -> int:
    """Conta perdidos pela data de conclusão, sem conversão de fuso.

    O recorte enviado à API é propositalmente ligeiramente mais largo porque
    `endTime` é uma data de negócio serializada à meia-noite UTC. A seleção
    definitiva é feita localmente por YYYY-MM-DD e pelo Funil Comercial.
    """
    query_start = datetime.combine(start_day, dt_time.min, tzinfo=UTC) - timedelta(seconds=1)
    query_end = datetime.combine(end_day + timedelta(days=1), dt_time.min, tzinfo=UTC) + timedelta(seconds=1)
    deals = listar_deals(
        dealStatus=3,
        endAtGt=_iso(query_start),
        endAtLt=_iso(query_end),
    )
    return sum(
        1
        for deal in deals
        if _eh_funil_comercial(deal)
        and (data_perda := _data_perda_date(deal)) is not None
        and start_day <= data_perda <= end_day
    )


def _data_inicio_date(deal: dict[str, Any]) -> date | None:
    """Data de entrada do negócio no funil.

    `startTime` é uma data de negócio serializada à meia-noite UTC, então deve
    ser comparada como YYYY-MM-DD. Se não existir, `createdAt` é um timestamp
    real e é convertido para Brasília antes de extrair a data.
    """
    start_time = deal.get("startTime")
    if start_time:
        try:
            return date.fromisoformat(str(start_time)[:10])
        except (TypeError, ValueError):
            pass

    created_at = _parse_iso(deal.get("createdAt"))
    if created_at is None:
        return None
    return created_at.astimezone(BRT).date()


def _snapshot_open(day: date) -> int:
    """Estoque no fim do dia pela data de negócio, sem deslocamento de fuso.

    Um negócio conta como em andamento no fechamento de `day` quando:
    - já havia iniciado até essa data; e
    - continua aberto hoje, ou só foi concluído em data posterior a `day`.

    Isso evita interpretar `startTime`/`endTime` à meia-noite UTC como horário
    de Brasília, o que deslocava negócios do dia seguinte para o dia anterior.
    """
    # Negócios que continuam abertos hoje e já tinham iniciado até o corte.
    ongoing = sum(
        1
        for deal in listar_deals(dealStatus=1)
        if _eh_funil_comercial(deal)
        and (data_inicio := _data_inicio_date(deal)) is not None
        and data_inicio <= day
    )

    # Ganhos concluídos depois do corte ainda estavam abertos naquele dia.
    won_after = sum(
        1
        for deal in listar_ganhos_cacheados()
        if _eh_funil_comercial(deal)
        and (data_inicio := _data_inicio_date(deal)) is not None
        and data_inicio <= day
        and (data_ganho := _data_ganho_date(deal)) is not None
        and data_ganho > day
    )

    # Para perdas, pedimos à API apenas as conclusões posteriores ao corte e
    # fazemos a seleção definitiva localmente por YYYY-MM-DD.
    query_start = datetime.combine(day + timedelta(days=1), dt_time.min, tzinfo=UTC) - timedelta(seconds=1)
    lost_candidates = listar_deals(
        dealStatus=3,
        endAtGt=_iso(query_start),
    )
    lost_after = sum(
        1
        for deal in lost_candidates
        if _eh_funil_comercial(deal)
        and (data_inicio := _data_inicio_date(deal)) is not None
        and data_inicio <= day
        and (data_perda := _data_perda_date(deal)) is not None
        and data_perda > day
    )

    return ongoing + won_after + lost_after


def _period_metrics(
    start_iso: str,
    end_iso: str,
    snapshot_day: date,
    gains_start_day: date,
    gains_end_day: date,
) -> dict[str, int]:
    return {
        "leads": contar_deals(startAtGt=start_iso, startAtLt=end_iso),
        "ganhos": contar_ganhos_por_data_ganho(gains_start_day, gains_end_day),
        "perdidos": contar_perdidos_por_data_conclusao(gains_start_day, gains_end_day),
        "em_andamento": _snapshot_open(snapshot_day),
    }


def metricas_dia(day: date) -> dict[str, int]:
    start_iso, end_iso = _bounds(day)
    return _period_metrics(start_iso, end_iso, day, day, day)


def metricas_mes(through_day: date) -> dict[str, int]:
    start_iso, end_iso = _month_bounds(through_day)
    return _period_metrics(
        start_iso,
        end_iso,
        through_day,
        through_day.replace(day=1),
        through_day,
    )


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
