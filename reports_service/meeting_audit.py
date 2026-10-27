"""Contagem por atividade real e negócio vinculado ao Funil Comercial."""
from collections import defaultdict
from datetime import date
from .agendor import _get
from .config import FUNIL_COMERCIAL_ID
from .metrics import parse_dt

def meeting_counts(start: date, end: date) -> dict:
    counts = defaultdict(lambda: {"reunioes_agendadas": 0, "agenda_de_reunioes": 0})
    seen, cache, uncertain = set(), {}, []
    for page in range(1, 31):
        result = _get("/tasks", params={"per_page": 100, "page": page, "typesIn": "meeting"})
        for task in result.get("data") or []:
            ident = task.get("id")
            if ident is None:
                raise RuntimeError("Reunião sem identificador")
            if ident in seen:
                continue
            seen.add(ident)
            kind = task.get("type")
            if isinstance(kind, dict):
                kind = kind.get("name") or kind.get("label")
            if str(kind).casefold() not in ("meeting", "reunião", "reuniao"):
                continue
            created_ts = parse_dt(task.get("createdAt"))
            due_ts = parse_dt(task.get("dueDate"))
            created = created_ts.date() if created_ts else None
            due = due_ts.date() if due_ts else None
            if not ((created and start <= created <= end) or (due and start <= due <= end)):
                continue
            linked = task.get("deal")
            deal_id = linked.get("id") if isinstance(linked, dict) else linked
            if not deal_id:
                uncertain.append(ident)
                continue
            if deal_id not in cache:
                payload = _get(f"/deals/{deal_id}")
                deal = payload.get("data", payload)
                cache[deal_id] = (((deal.get("dealStage") or {}).get("funnel") or {}).get("id"))
            if cache[deal_id] is None:
                uncertain.append(ident)
                continue
            if str(cache[deal_id]) != str(FUNIL_COMERCIAL_ID):
                continue
            if created and start <= created <= end:
                counts[created.isoformat()]["reunioes_agendadas"] += 1
            if due and start <= due <= end:
                counts[due.isoformat()]["agenda_de_reunioes"] += 1
        if not (result.get("links") or {}).get("next"):
            break
    else:
        raise RuntimeError("Paginação de reuniões incompleta")
    if uncertain:
        raise RuntimeError(f"Vínculo de funil inconclusivo para {len(uncertain)} reuniões")
    return dict(counts)
