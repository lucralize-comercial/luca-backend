from datetime import date

from reports_service.metrics import daily_metrics, month_metrics, origins_for_day


def deal(i, start, won=None, lost=None, end=None, origin=None):
    d = {
        "id": i,
        "startTime": start,
        "wonAt": won,
        "lostAt": lost,
        "endTime": end,
        "customFields": {},
    }
    if origin is not None:
        d["customFields"]["origem_do_negocio"] = origin
    return d


def test_daily_snapshot_and_endtime_priority():
    deals = [
        deal(1, "2026-10-05T12:00:00Z", won="2026-10-05T18:00:00Z"),
        deal(2, "2026-10-05T13:00:00Z"),
        # lostAt é hoje, mas endTime indica encerramento histórico ontem.
        deal(3, "2026-10-04T10:00:00Z", lost="2026-10-06T11:00:00Z", end="2026-10-05T20:00:00Z"),
        # estava aberto em 05/10 e só ganhou em 06/10.
        deal(4, "2026-10-01T10:00:00Z", won="2026-10-06T12:00:00Z"),
    ]
    m = daily_metrics(deals, date(2026, 10, 5))
    assert m == {"leads": 2, "ganhos": 1, "perdidos": 1, "em_andamento": 2}


def test_month_open_is_stock_not_sum():
    deals = [
        deal(1, "2026-10-01T12:00:00Z"),
        deal(2, "2026-10-02T12:00:00Z", won="2026-10-03T12:00:00Z"),
        deal(3, "2026-09-20T12:00:00Z"),
    ]
    m = month_metrics(deals, date(2026, 10, 5))
    assert m["leads"] == 2
    assert m["ganhos"] == 1
    assert m["em_andamento"] == 2


def test_origins():
    deals = [
        deal(1, "2026-10-05T12:00:00Z", origin="Google Ads"),
        deal(2, "2026-10-05T13:00:00Z", origin="calculadora-impostos-desenvolvedores"),
        deal(3, "2026-10-05T14:00:00Z", origin="whatsapp_pagina"),
    ]
    o = origins_for_day(deals, date(2026, 10, 5))
    assert o["Google Ads"] == 1
    assert o["Calculadora"] == 1
    assert o["WhatsApp/Site"] == 1
