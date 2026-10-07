"""Monitor de consumo da Anthropic para o Luca.

Usa exclusivamente a Usage & Cost Admin API. Nenhuma chamada ao Claude e feita
por este modulo; portanto, consultar o monitor nao gera consumo de tokens.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os

import requests

BASE_URL = "https://api.anthropic.com/v1/organizations"
VERSION = "2023-06-01"
TIMEOUT = 25


def _admin_key() -> str:
    key = os.environ.get("ANTHROPIC_ADMIN_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_ADMIN_KEY nao configurada no luca-reports")
    return key


def _headers() -> dict[str, str]:
    return {
        "x-api-key": _admin_key(),
        "anthropic-version": VERSION,
        "User-Agent": "Lucralize-Luca-Reports/1.0",
    }


def _get_all(path: str, params: list[tuple[str, str]]) -> list[dict]:
    data: list[dict] = []
    page = None
    while True:
        query = list(params)
        if page:
            query.append(("page", page))
        r = requests.get(BASE_URL + path, headers=_headers(), params=query, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        data.extend(payload.get("data", []))
        if not payload.get("has_more"):
            break
        page = payload.get("next_page")
        if not page:
            raise RuntimeError("Anthropic informou has_more sem next_page")
    return data


def _window(days: int = 9) -> tuple[str, str]:
    # Buckets diarios da Admin API sao alinhados em UTC.
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days)
    return (
        datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
        datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def _usage(days: int = 9) -> list[dict]:
    start, end = _window(days)
    return _get_all(
        "/usage_report/messages",
        [
            ("starting_at", start),
            ("ending_at", end),
            ("bucket_width", "1d"),
            ("group_by[]", "model"),
            ("limit", "31"),
        ],
    )


def _cost(days: int = 9) -> list[dict]:
    start, end = _window(days)
    return _get_all(
        "/cost_report",
        [
            ("starting_at", start),
            ("ending_at", end),
            ("group_by[]", "description"),
            ("limit", "31"),
        ],
    )


def _date_from_bucket(bucket: dict) -> str:
    return str(bucket.get("starting_at", ""))[:10]


def _aggregate_usage(buckets: list[dict]) -> dict[str, dict]:
    daily: dict[str, dict] = {}
    for bucket in buckets:
        day = _date_from_bucket(bucket)
        if not day:
            continue
        d = daily.setdefault(day, {
            "input": 0,
            "cache_creation": 0,
            "cache_read": 0,
            "output": 0,
            "requests": 0,
            "models": defaultdict(lambda: {
                "input": 0, "cache_creation": 0, "cache_read": 0, "output": 0, "requests": 0
            }),
        })
        for row in bucket.get("results", []):
            model = row.get("model") or "unknown"
            cache_creation = row.get("cache_creation") or {}
            cache_created = int(cache_creation.get("ephemeral_5m_input_tokens") or 0) + int(
                cache_creation.get("ephemeral_1h_input_tokens") or 0
            )
            values = {
                "input": int(row.get("uncached_input_tokens") or 0),
                "cache_creation": cache_created,
                "cache_read": int(row.get("cache_read_input_tokens") or 0),
                "output": int(row.get("output_tokens") or 0),
                "requests": int(row.get("requests") or 0),
            }
            for key, value in values.items():
                d[key] += value
                d["models"][model][key] += value
    return daily


def _aggregate_cost(buckets: list[dict]) -> dict[str, dict]:
    daily: dict[str, dict] = {}
    for bucket in buckets:
        day = _date_from_bucket(bucket)
        if not day:
            continue
        d = daily.setdefault(day, {"usd": Decimal("0"), "models": defaultdict(Decimal)})
        for row in bucket.get("results", []):
            if (row.get("currency") or "USD") != "USD":
                continue
            # Documentacao da Anthropic: amount e decimal em centavos (menor unidade).
            amount_usd = Decimal(str(row.get("amount") or "0")) / Decimal("100")
            model = row.get("model") or "other"
            d["usd"] += amount_usd
            d["models"][model] += amount_usd
    return daily


def _round_money(value: Decimal | float) -> float:
    return round(float(value), 4)


def gerar_resumo_consumo(days: int = 9) -> dict:
    usage = _aggregate_usage(_usage(days))
    cost = _aggregate_cost(_cost(days))
    all_days = sorted(set(usage) | set(cost))
    if not all_days:
        raise RuntimeError("Anthropic nao retornou dados de uso/custo")

    rows = []
    for day in all_days:
        u = usage.get(day, {})
        c = cost.get(day, {})
        input_tokens = int(u.get("input", 0))
        cache_creation = int(u.get("cache_creation", 0))
        cache_read = int(u.get("cache_read", 0))
        output = int(u.get("output", 0))
        total = input_tokens + cache_creation + cache_read + output
        models = {}
        model_names = set((u.get("models") or {}).keys()) | set((c.get("models") or {}).keys())
        for model in sorted(model_names):
            mu = (u.get("models") or {}).get(model, {})
            mc = (c.get("models") or {}).get(model, Decimal("0"))
            mtokens = sum(int(mu.get(k, 0)) for k in ("input", "cache_creation", "cache_read", "output"))
            models[model] = {
                "tokens": mtokens,
                "cost_usd": _round_money(mc),
            }
        rows.append({
            "date": day,
            "tokens": total,
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "output_tokens": output,
            "requests": int(u.get("requests", 0)),
            "cost_usd": _round_money(c.get("usd", Decimal("0"))),
            "models": models,
        })

    latest = rows[-1]
    previous = rows[-8:-1]
    avg_cost = sum(r["cost_usd"] for r in previous) / len(previous) if previous else 0.0
    avg_tokens = sum(r["tokens"] for r in previous) / len(previous) if previous else 0.0

    cost_delta = ((latest["cost_usd"] / avg_cost) - 1) * 100 if avg_cost else None
    token_delta = ((latest["tokens"] / avg_tokens) - 1) * 100 if avg_tokens else None

    spike_threshold = float(os.environ.get("ANTHROPIC_SPIKE_THRESHOLD_PCT", "50"))
    spike = bool(
        (cost_delta is not None and cost_delta >= spike_threshold)
        or (token_delta is not None and token_delta >= spike_threshold)
    )

    return {
        "source": "anthropic_admin_api",
        "bucket_timezone": "UTC",
        "latest_complete_day": latest,
        "baseline": {
            "days": len(previous),
            "avg_cost_usd": round(avg_cost, 4),
            "avg_tokens": round(avg_tokens),
        },
        "variation_vs_baseline_pct": {
            "cost": None if cost_delta is None else round(cost_delta, 1),
            "tokens": None if token_delta is None else round(token_delta, 1),
        },
        "spike": spike,
        "spike_threshold_pct": spike_threshold,
        "history": rows,
    }
