"""Pacote semanal de dados comerciais e análise gerencial via Claude.

A coleta/cálculo é determinística; a IA recebe apenas agregados, nunca o funil bruto.
Nenhum envio ao Teams acontece neste módulo.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import json
import os
from typing import Any

import requests

from .agendor import (
    buscar_mapa_campos_personalizados,
    listar_abertos_detalhados,
    listar_ganhos_periodo,
    listar_leads_por_data_inicio,
    listar_perdidos_periodo,
    metricas_periodo,
    reunioes_periodo,
)
from .metrics import BRT, classify_origin, parse_dt

LOSS_REASON_NAMES = {
    3162041: "Contador / Parente Contador",
    3162042: "Curioso (sem intenção de compra)",
    3162049: "Satisfeito com o Contador Atual",
    3162043: "Sem retorno",
    3162045: "Prazo (momento inadequado)",
    3187920: "Contato inválido",
    3217904: "Sem WhatsApp",
    3162046: "Preço",
    3162047: "Produto/Serviço não atendeu",
    3162048: "Fechou com Concorrente",
    3162050: "Desistiu da negociação",
    3200168: "Lead parou de interagir",
    3265096: "Empresa Baixada/Em Processo de Baixa",
}

def _week_window(now: datetime | None = None) -> tuple[date, date, date, date]:
    current = (now or datetime.now(BRT)).astimezone(BRT)
    end = current.date() - timedelta(days=1)
    start = end - timedelta(days=6)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)
    return start, end, prev_start, prev_end

def _status_name(deal: dict[str, Any]) -> str:
    status = deal.get("dealStatus")
    if isinstance(status, dict):
        text = status.get("name") or status.get("text") or status.get("label")
        if text:
            return str(text).lower()
        status = status.get("id")
    text = str(status or "").lower()
    if text in {"1", "ongoing", "open", "andamento"}:
        return "aberto"
    if text in {"2", "won", "ganho"}:
        return "ganho"
    if text in {"3", "lost", "perdido"}:
        return "perdido"
    return text or "desconhecido"

def _reason_name(deal: dict[str, Any]) -> str:
    reason = deal.get("lossReason") or {}
    if isinstance(reason, dict):
        name = reason.get("name") or reason.get("label") or reason.get("text")
        if name:
            return str(name)
        rid = reason.get("id")
    else:
        rid = reason
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return "Não informado"
    return LOSS_REASON_NAMES.get(rid, f"Motivo #{rid}")

def _stage_name(deal: dict[str, Any]) -> str:
    stage = deal.get("dealStage") or {}
    if isinstance(stage, dict):
        return str(stage.get("name") or stage.get("title") or stage.get("label") or "Etapa não informada")
    return str(stage or "Etapa não informada")

def _pct(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 1)

def _delta(current: int, previous: int) -> dict[str, Any]:
    if previous == 0:
        return {"valor": current, "anterior": previous, "variacao_pct": None if current else 0.0}
    return {"valor": current, "anterior": previous, "variacao_pct": round((current - previous) / previous * 100, 1)}

def _cohort_by_origin(deals: list[dict[str, Any]], fields_map: dict) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = defaultdict(lambda: {"leads": 0, "abertos": 0, "ganhos": 0, "perdidos": 0})
    for deal in deals:
        origin = classify_origin(deal, fields_map)
        row = rows[origin]
        row["leads"] += 1
        status = _status_name(deal)
        if "won" in status or "ganh" in status:
            row["ganhos"] += 1
        elif "lost" in status or "perd" in status:
            row["perdidos"] += 1
        else:
            row["abertos"] += 1
    for row in rows.values():
        row["taxa_ganho_pct"] = _pct(row["ganhos"], row["leads"])
        row["taxa_perda_pct"] = _pct(row["perdidos"], row["leads"])
    return dict(sorted(rows.items(), key=lambda kv: (-kv[1]["leads"], kv[0])))

def _losses_breakdown(deals: list[dict[str, Any]], fields_map: dict) -> dict[str, Any]:
    reasons = Counter()
    origins = Counter()
    matrix: dict[str, Counter] = defaultdict(Counter)
    for deal in deals:
        reason = _reason_name(deal)
        origin = classify_origin(deal, fields_map)
        reasons[reason] += 1
        origins[origin] += 1
        matrix[origin][reason] += 1
    total = len(deals)
    detailed_reasons = []
    for reason, qty in reasons.most_common():
        by_origin = sorted(
            (
                {"origem": origin, "quantidade": counter.get(reason, 0)}
                for origin, counter in matrix.items()
                if counter.get(reason, 0) > 0
            ),
            key=lambda row: (-row["quantidade"], row["origem"]),
        )
        for row in by_origin:
            row["participacao_no_motivo_pct"] = _pct(row["quantidade"], qty)
        top2_qty = sum(row["quantidade"] for row in by_origin[:2])
        detailed_reasons.append({
            "motivo": reason,
            "quantidade": qty,
            "participacao_pct": _pct(qty, total),
            "por_origem": by_origin,
            "top2_origens_quantidade": top2_qty,
            "top2_origens_participacao_no_motivo_pct": _pct(top2_qty, qty),
        })
    return {
        "total": total,
        "motivos": [
            {"motivo": name, "quantidade": qty, "participacao_pct": _pct(qty, total)}
            for name, qty in reasons.most_common()
        ],
        "motivos_detalhados": detailed_reasons,
        "origens": [
            {"origem": name, "quantidade": qty, "participacao_pct": _pct(qty, total)}
            for name, qty in origins.most_common()
        ],
        "origem_x_motivo": {
            origin: dict(counter.most_common()) for origin, counter in matrix.items()
        },
    }

def _open_pipeline(deals: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    stages = Counter()
    stale5 = Counter()
    stale10 = Counter()
    unknown_update = 0
    for deal in deals:
        stage = _stage_name(deal)
        stages[stage] += 1
        updated = parse_dt(deal.get("updatedAt"))
        if not updated:
            unknown_update += 1
            continue
        days = max(0, (now.date() - updated.date()).days)
        if days >= 5:
            stale5[stage] += 1
        if days >= 10:
            stale10[stage] += 1
    return {
        "total_abertos_agora": len(deals),
        "por_etapa": [{"etapa": k, "quantidade": v} for k, v in stages.most_common()],
        "sem_atualizacao_5d_por_etapa": [{"etapa": k, "quantidade": v} for k, v in stale5.most_common()],
        "sem_atualizacao_10d_por_etapa": [{"etapa": k, "quantidade": v} for k, v in stale10.most_common()],
        "sem_data_atualizacao": unknown_update,
        "observacao": "Tempo parado = dias desde updatedAt do negócio; não é tempo exato dentro da etapa.",
    }

def montar_pacote_semanal(now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(BRT)).astimezone(BRT)
    start, end, prev_start, prev_end = _week_window(current)
    fields_map = buscar_mapa_campos_personalizados()

    current_metrics = metricas_periodo(start, end)
    previous_metrics = metricas_periodo(prev_start, prev_end)
    current_metrics["reunioes"] = reunioes_periodo(start, end)
    previous_metrics["reunioes"] = reunioes_periodo(prev_start, prev_end)

    leads_current = listar_leads_por_data_inicio(start, end, with_custom_fields=True)
    leads_previous = listar_leads_por_data_inicio(prev_start, prev_end, with_custom_fields=True)
    lost_current = listar_perdidos_periodo(start, end, with_custom_fields=True)
    lost_previous = listar_perdidos_periodo(prev_start, prev_end, with_custom_fields=True)
    won_current = listar_ganhos_periodo(start, end, with_custom_fields=True)
    open_now = listar_abertos_detalhados(with_custom_fields=True)

    indicators = {}
    for key in ("leads", "reunioes", "ganhos", "perdidos", "em_andamento"):
        indicators[key] = _delta(int(current_metrics.get(key, 0)), int(previous_metrics.get(key, 0)))

    losses_current = _losses_breakdown(lost_current, fields_map)
    losses_previous = _losses_breakdown(lost_previous, fields_map)
    origins_current = _cohort_by_origin(leads_current, fields_map)
    origins_previous = _cohort_by_origin(leads_previous, fields_map)

    origem_outros_leads = int((origins_current.get("Outros") or {}).get("leads", 0))
    origem_outros_perdas = next(
        (int(row["quantidade"]) for row in losses_current["origens"] if row["origem"] == "Outros"),
        0,
    )

    return {
        "periodo": {
            "semana_atual": {"inicio": start.isoformat(), "fim": end.isoformat()},
            "semana_anterior": {"inicio": prev_start.isoformat(), "fim": prev_end.isoformat()},
        },
        "indicadores": indicators,
        "taxas": {
            "lead_para_reuniao_pct": _pct(current_metrics["reunioes"], current_metrics["leads"]),
            "lead_para_ganho_pct": _pct(current_metrics["ganhos"], current_metrics["leads"]),
            "semana_anterior_lead_para_reuniao_pct": _pct(previous_metrics["reunioes"], previous_metrics["leads"]),
            "semana_anterior_lead_para_ganho_pct": _pct(previous_metrics["ganhos"], previous_metrics["leads"]),
        },
        "origens_coorte_semana_atual": origins_current,
        "origens_coorte_semana_anterior": origins_previous,
        "perdas_semana_atual": losses_current,
        "perdas_semana_anterior": losses_previous,
        "qualidade_dados": {
            "origem_outros_leads_pct": _pct(origem_outros_leads, len(leads_current)),
            "origem_outros_perdas_pct": _pct(origem_outros_perdas, len(lost_current)),
            "motivo_perda_nao_informado_pct": _pct(
                next((int(row["quantidade"]) for row in losses_current["motivos"] if row["motivo"] == "Não informado"), 0),
                len(lost_current),
            ),
            "regra": "Se 'Outros' for alto, reduzir confiança em conclusões por origem e priorizar melhoria de classificação.",
        },
        "ganhos_semana_atual": {
            "total": len(won_current),
            "por_origem": dict(Counter(classify_origin(d, fields_map) for d in won_current).most_common()),
        },
        "funil_aberto_agora": _open_pipeline(open_now, current),
        "notas_metodologicas": [
            "Conversão por origem da coorte usa o status atual dos leads que entraram na semana; leads recentes ainda podem estar em maturação.",
            "Perdas e ganhos da semana usam a data de conclusão, podendo incluir leads originados em semanas anteriores.",
            "Tempo sem atualização usa updatedAt e não deve ser descrito como tempo exato na etapa.",
        ],
    }

SYSTEM_PROMPT = """Você é um analista comercial sênior apoiando o gestor da Lucralize.
Receberá SOMENTE dados agregados e confiáveis do CRM. Seu trabalho é interpretar, não repetir tabela.

Regras:
- Separe claramente fatos de hipóteses. Nunca afirme causalidade que os dados não sustentam.
- Priorize relações entre origem, avanço, ganho, perda, motivo de perda e inatividade.
- Compare semana atual com anterior quando isso muda a decisão.
- NÃO faça contas novas nem invente denominadores. Só cite percentuais, totais e concentrações que já estejam explicitamente presentes nos dados.
- "por_etapa" significa quantidade atualmente naquela etapa; NÃO chame esses negócios de parados/inativos. Só use "parado", "inativo" ou equivalente para os blocos "sem_atualizacao_5d_por_etapa" e "sem_atualizacao_10d_por_etapa".
- Motivo "Sem Retorno" NÃO prova falha de follow-up nem baixa qualidade do lead. Pode indicar hipótese a investigar, nunca causa confirmada.
- Se "qualidade_dados.origem_outros_perdas_pct" estiver alta (>=30%), reduza a força de conclusões por origem nas perdas e diga que a classificação de origem limita a análise.
- Não trate leads recém-chegados como fracasso só porque ainda estão abertos.
- Use no máximo 3 achados em 'performando', 3 em 'prejudicando' e 4 ações.
- Cada ação deve dizer O QUE fazer e POR QUÊ, sustentada por algum dado explícito.
- Se não houver evidência suficiente, diga explicitamente.
- Seja direto, executivo e útil; não encha espaço.
- Responda em português do Brasil.
- Retorne APENAS JSON válido, sem markdown, com as chaves:
  leitura_gestor (string),
  performando (array de strings),
  prejudicando (array de strings),
  acoes_recomendadas (array de strings),
  sinal_proxima_semana (string),
  confianca ("alta"|"media"|"baixa"),
  ressalvas (array de strings).
"""

def _parse_ai_json(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("resposta sem bloco de texto")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("resposta sem JSON")
        return json.loads(raw[start:end + 1])


def analisar_com_ia(pacote: dict[str, Any]) -> dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY não configurada no luca-reports")
    model = os.environ.get("REPORT_AI_MODEL", "claude-sonnet-5").strip()
    payload_text = json.dumps(pacote, ensure_ascii=False, separators=(",", ":"))
    usage_total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    last_detail = ""
    for attempt, max_tokens in enumerate((1800, 2600), start=1):
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "thinking": {"type": "disabled"},
                "system": SYSTEM_PROMPT,
                "messages": [{
                    "role": "user",
                    "content": "Analise o relatório semanal a seguir e gere a leitura gerencial solicitada. "
                               "Seja objetivo e devolva obrigatoriamente o JSON final completo.\n\nDADOS:\n" + payload_text,
                }],
            },
            timeout=90,
        )
        r.raise_for_status()
        body = r.json()
        usage = body.get("usage") or {}
        for key in usage_total:
            usage_total[key] += int(usage.get(key) or 0)

        content = body.get("content") or []
        text_parts = [
            block.get("text", "") for block in content
            if block.get("type") == "text" and block.get("text")
        ]
        raw = "\n".join(text_parts).strip()
        try:
            analysis = _parse_ai_json(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            block_types = [str(block.get("type")) for block in content]
            last_detail = (
                f"tentativa={attempt}; stop_reason={body.get('stop_reason')}; "
                f"blocos={block_types}; erro={exc}"
            )
            continue

        return {
            "modelo": model,
            "tentativas": attempt,
            "analise": analysis,
            "uso": usage_total,
        }

    raise RuntimeError("IA não retornou JSON válido após retry: " + last_detail)

def gerar_relatorio_semanal(now: datetime | None = None, *, usar_ia: bool = True) -> dict[str, Any]:
    pacote = montar_pacote_semanal(now)
    result = {"pacote": pacote}
    if usar_ia:
        result["ia"] = analisar_com_ia(pacote)
    return result
