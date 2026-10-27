"""Leitura de indicadores por dia, sem envio nem alteração no Agendor."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from .agendor import _get, metricas_dia, listar_leads_dia, buscar_mapa_campos_personalizados
from .metrics import origins_for_day
from .config import FUNIL_COMERCIAL_ID

BRT = ZoneInfo("America/Sao_Paulo")
UTC = ZoneInfo("UTC")

def _day(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.replace(tzinfo=UTC).astimezone(BRT).date() if dt.tzinfo is None else dt.astimezone(BRT).date()
    except (ValueError, TypeError):
        return None

def _deal_id(task):
    obj = task.get("deal")
    return obj.get("id") if isinstance(obj, dict) else obj

def _funil(deal):
    stage = deal.get("dealStage") or {}
    funnel = stage.get("funnel") or {}
    return funnel.get("id")

def conferir_periodo(inicio: date, fim: date):
    if fim < inicio or (fim-inicio).days > 13:
        raise ValueError("Periodo invalido: maximo 14 dias")
    dates = [inicio+timedelta(days=i) for i in range((fim-inicio).days+1)]
    day_set = set(dates)
    daily = {d.isoformat(): {"reunioes_agendadas": 0, "agenda_de_reunioes": 0} for d in dates}
    tasks = []
    seen = set()
    for page in range(1, 31):
        resp = _get("/tasks", params={"per_page":100,"page":page,"typesIn":"meeting"})
        current = resp.get("data") or []
        for task in current:
            tid = task.get("id")
            if tid in seen:
                continue
            seen.add(tid)
            kind = task.get("type")
            if isinstance(kind,dict):
                kind = kind.get("name") or kind.get("label")
            if str(kind).casefold() not in ("reunião","reuniao","meeting"):
                continue
            created = _day(task.get("createdAt"))
            due = _day(task.get("dueDate"))
            if created in day_set or due in day_set:
                tasks.append((task,created,due))
        if not (resp.get("links") or {}).get("next"):
            break
    else:
        raise RuntimeError("Paginacao de tarefas incompleta; resultado bloqueado")

    funnel_cache = {}
    inconclusive = []
    for task,created,due in tasks:
        deal_id = _deal_id(task)
        if not deal_id:
            inconclusive.append(task.get("id"))
            continue
        if deal_id not in funnel_cache:
            resp = _get(f"/deals/{deal_id}")
            deal = resp.get("data",resp)
            funnel_cache[deal_id] = _funil(deal)
        funnel_id = funnel_cache[deal_id]
        if funnel_id is None:
            inconclusive.append(task.get("id"))
            continue
        if str(funnel_id) != str(FUNIL_COMERCIAL_ID):
            continue
        if created in day_set:
            daily[created.isoformat()]["reunioes_agendadas"] += 1
        if due in day_set:
            daily[due.isoformat()]["agenda_de_reunioes"] += 1

    if inconclusive:
        raise RuntimeError(f"Vinculo de funil inconclusivo para {len(inconclusive)} atividades; dados nao publicados")

    fields = buscar_mapa_campos_personalizados()
    for d in dates:
        metrics = metricas_dia(d)
        leads = listar_leads_dia(d)
        daily[d.isoformat()].update(metrics)
        daily[d.isoformat()]["origens"] = origins_for_day(leads,d,fields)
    return {"periodo":{"inicio":inicio.isoformat(),"fim":fim.isoformat()},
            "funil":FUNIL_COMERCIAL_ID,"dados":daily,
            "reunioes_candidatas":len(tasks),"negocios_verificados":len(funnel_cache),
            "observacao":"Dados lidos em tempo real; 27/10 parcial se consultado durante o dia."}
