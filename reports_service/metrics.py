from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

BRT = ZoneInfo("America/Sao_Paulo")
UTC = ZoneInfo("UTC")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        # Agendor usa timestamps ISO; quando não vier offset, tratamos como UTC.
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(BRT)


def start_dt(deal: dict) -> datetime | None:
    return parse_dt(deal.get("startTime") or deal.get("createdAt"))


def won_dt(deal: dict) -> datetime | None:
    return parse_dt(deal.get("wonAt"))


def lost_dt(deal: dict) -> datetime | None:
    # O Luca já grava endTime para preservar a data histórica real da perda.
    return parse_dt(deal.get("endTime")) or parse_dt(deal.get("lostAt"))


def close_dt(deal: dict) -> datetime | None:
    return won_dt(deal) or lost_dt(deal)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    ini = datetime.combine(day, time.min, tzinfo=BRT)
    fim = datetime.combine(day, time.max, tzinfo=BRT)
    return ini, fim


def is_between(dt: datetime | None, start: datetime, end: datetime) -> bool:
    return dt is not None and start <= dt <= end


def snapshot_open_at(deals: list[dict], day: date) -> int:
    """Quantidade de negócios que permaneciam abertos no fim do dia."""
    _, cutoff = day_bounds(day)
    total = 0
    for deal in deals:
        entered = start_dt(deal)
        if not entered or entered > cutoff:
            continue
        closed = close_dt(deal)
        if closed is None or closed > cutoff:
            total += 1
    return total


def daily_metrics(deals: list[dict], day: date) -> dict[str, int]:
    ini, fim = day_bounds(day)
    return {
        "leads": sum(1 for d in deals if is_between(start_dt(d), ini, fim)),
        "ganhos": sum(1 for d in deals if is_between(won_dt(d), ini, fim)),
        "perdidos": sum(1 for d in deals if is_between(lost_dt(d), ini, fim)),
        "em_andamento": snapshot_open_at(deals, day),
    }


def month_metrics(deals: list[dict], through_day: date) -> dict[str, int]:
    first = through_day.replace(day=1)
    ini, _ = day_bounds(first)
    _, fim = day_bounds(through_day)
    return {
        "leads": sum(1 for d in deals if is_between(start_dt(d), ini, fim)),
        "ganhos": sum(1 for d in deals if is_between(won_dt(d), ini, fim)),
        "perdidos": sum(1 for d in deals if is_between(lost_dt(d), ini, fim)),
        # Estoque ao fim do período, não soma diária.
        "em_andamento": snapshot_open_at(deals, through_day),
    }


def _flatten(value: Any, option_map: dict[Any, str] | None = None) -> list[str]:
    option_map = option_map or {}
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        resolved = option_map.get(value, option_map.get(str(value), value))
        return [str(resolved)]
    if isinstance(value, dict):
        preferred = []
        for key in ("name", "label", "value", "text", "title", "id"):
            if key in value:
                preferred.extend(_flatten(value[key], option_map))
        return preferred or [str(value)]
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_flatten(item, option_map))
        return out
    return [str(value)]


def classify_origin(deal: dict, fields_map: dict[str, dict[Any, str]] | None = None) -> str:
    fields_map = fields_map or {}
    custom = deal.get("customFields") or {}
    values: list[str] = []
    for key in (
        "origem_do_negocio", "origem", "campanha", "grupo_de_anuncio",
        "anuncio", "meta_ads_source_id",
    ):
        values.extend(_flatten(custom.get(key), fields_map.get(key)))

    # Alguns deals antigos podem ter informação útil fora de customFields.
    values.extend(_flatten(deal.get("description")))
    text = " ".join(values).lower()

    if "calculadora" in text:
        return "Calculadora"
    if any(x in text for x in ("meta ads", "facebook", "instagram", "fbads", "meta_ads", "meta-ads")):
        return "Meta Ads"
    if any(x in text for x in ("google ads", "googleads", "adwords", "gads", "google")):
        return "Google Ads"
    if any(x in text for x in ("whatsapp", "whatsapp_pagina", "site", "página", "pagina")):
        return "WhatsApp/Site"
    return "Outros"


def origins_for_day(deals: list[dict], day: date, fields_map=None) -> dict[str, int]:
    """Classifica os mesmos leads do indicador, pela data bruta de startTime.

    O Agendor serializa startTime à meia-noite UTC mesmo para uma data de
    negócio. Converter para Brasília deslocaria o lead para o dia anterior.
    A ausência de origem identificável é contabilizada em "Outros".
    """
    counter = Counter()
    seen_ids = set()
    for deal in deals:
        raw_start = deal.get("startTime")
        if not raw_start:
            continue
        try:
            start_date = date.fromisoformat(str(raw_start)[:10])
        except (TypeError, ValueError):
            continue
        if start_date != day:
            continue
        deal_id = deal.get("id")
        if deal_id is not None:
            if deal_id in seen_ids:
                continue
            seen_ids.add(deal_id)
        counter[classify_origin(deal, fields_map)] += 1
    return {name: counter.get(name, 0) for name in (
        "Google Ads", "Meta Ads", "Calculadora", "WhatsApp/Site", "Outros"
    )}


def comparison_pct(current: int, previous: int) -> tuple[str, str]:
    if previous == 0:
        if current == 0:
            return "→", "0,0%"
        return "↑", "novo"
    pct = ((current - previous) / previous) * 100
    arrow = "↑" if pct > 0 else "↓" if pct < 0 else "→"
    return arrow, f"{abs(pct):.1f}%".replace(".", ",")


def reference_days(now: datetime | None = None) -> tuple[date, date]:
    current = (now or datetime.now(BRT)).astimezone(BRT)
    yesterday = current.date() - timedelta(days=1)
    before = yesterday - timedelta(days=1)
    return yesterday, before
