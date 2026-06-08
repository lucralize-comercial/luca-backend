from flask import Flask, jsonify, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import requests
import os
import time

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type"]}})

AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN", "a89b0def-fd5e-45ed-981f-efe89f20159a")
AGENDOR_BASE = "https://api.agendor.com.br/v3"
HEADERS = {"Authorization": f"Token {AGENDOR_TOKEN}"}

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

cache = {"deals": [], "total": 0, "updated_at": None}
stage_history_cache = {}  # { deal_id: [ {stage_id, stage_name, entered_at}, ... ] }

def fetch_page(page):
    for attempt in range(3):
        try:
            r = requests.get(
                f"{AGENDOR_BASE}/deals",
                headers=HEADERS,
                params={"per_page": 100, "page": page, "withCustomFields": "true", "order_by": "updatedAt", "order_dir": "desc"},
                timeout=60
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"Tentativa {attempt+1}/3 falhou na pagina {page}: {e}", flush=True)
            if attempt < 2:
                time.sleep(5)
    return None

def fetch_deal_history(deal_id):
    """Busca o histórico de movimentação de um deal entre etapas."""
    for attempt in range(2):
        try:
            r = requests.get(
                f"{AGENDOR_BASE}/deals/{deal_id}/history",
                headers=HEADERS,
                timeout=15
            )
            if r.status_code == 200:
                data = r.json().get("data", [])
                # Filtra apenas eventos de mudança de etapa
                stage_events = []
                for event in data:
                    if event.get("type") in ("dealStageChanged", "deal_stage_changed", "stageChanged"):
                        stage_events.append({
                            "stage_id": event.get("dealStage", {}).get("id") or event.get("stageId"),
                            "stage_name": event.get("dealStage", {}).get("name") or event.get("stageName"),
                            "entered_at": event.get("createdAt") or event.get("date")
                        })
                return stage_events
            elif r.status_code == 404:
                return []
        except Exception as e:
            print(f"Erro histórico deal {deal_id}: {e}", flush=True)
            if attempt < 1:
                time.sleep(2)
    return []

def fetch_deals():
    print("Buscando negocios do Agendor...", flush=True)
    all_deals = []
    page = 1
    total_count = None

    while True:
        print(f"Buscando pagina {page}...", flush=True)
        data = fetch_page(page)
        if data is None:
            print(f"Pagina {page} falhou, encerrando.", flush=True)
            break

        page_deals = data.get("data", [])
        if total_count is None:
            total_count = data.get("meta", {}).get("totalCount", 0)
            print(f"Total na API: {total_count}", flush=True)

        all_deals.extend(page_deals)
        print(f"Pagina {page}: {len(page_deals)} negocios ({len(all_deals)}/{total_count})", flush=True)

        if page % 10 == 0:
            cache["deals"] = list(all_deals)
            cache["total"] = total_count or len(all_deals)
            cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        next_link = data.get("links", {}).get("next")
        if not next_link or len(page_deals) == 0:
            break
        page += 1
        time.sleep(0.2)

    cache["deals"] = all_deals
    cache["total"] = total_count or len(all_deals)
    cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"Cache atualizado: {len(all_deals)} negocios as {cache['updated_at']}", flush=True)

    # Busca produtos dos ganhos recentes
    cutoff = datetime.utcnow() - timedelta(days=180)
    won_recent = [
        d for d in all_deals
        if d.get("dealStatus", {}).get("id") == 2
        and d.get("wonAt") and datetime.strptime(d["wonAt"][:10], "%Y-%m-%d") > cutoff
    ]
    print(f"Buscando produtos de {len(won_recent)} negocios ganhos recentes...", flush=True)
    for deal in won_recent:
        try:
            r = requests.get(
                f"{AGENDOR_BASE}/deals/{deal['id']}/products",
                headers=HEADERS, timeout=15
            )
            if r.status_code == 200:
                products = r.json().get("data", [])
                if products:
                    deal["products_entities"] = products
        except Exception as e:
            print(f"Erro produtos {deal['id']}: {e}", flush=True)
        time.sleep(0.1)

    cache["deals"] = all_deals
    cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"Cache final: {len(all_deals)} negocios", flush=True)

fetch_running = False
fetch_started_at = None

def fetch_deals_safe():
    global fetch_running, fetch_started_at
    if fetch_running:
        print("Fetch ja em andamento, ignorando.", flush=True)
        return
    fetch_running = True
    fetch_started_at = time.time()
    try:
        fetch_deals()
    finally:
        fetch_running = False

@app.route("/")
def index():
    return jsonify({
        "status": "ok",
        "cached_deals": len(cache["deals"]),
        "updated_at": cache["updated_at"],
        "fetch_running": fetch_running,
        "stage_history_cached": len(stage_history_cache)
    })

@app.route("/refresh", methods=["POST"])
def refresh():
    global fetch_running
    if fetch_running:
        return jsonify({"status": "running", "message": "Fetch já em andamento"}), 202
    scheduler.add_job(fetch_deals_safe, "date", id="fetch_manual", replace_existing=True)
    return jsonify({"status": "started", "message": "Atualização iniciada"}), 200

@app.route("/deals")
def deals():
    return jsonify({"data": cache["deals"], "meta": {"totalCount": cache["total"], "updated_at": cache["updated_at"]}})

@app.route("/funnels")
def funnels():
    r = requests.get(f"{AGENDOR_BASE}/funnels", headers=HEADERS, timeout=30)
    return jsonify(r.json())

@app.route("/deals/<int:deal_id>/history")
def deal_history(deal_id):
    """Retorna histórico de movimentação de um deal, com cache."""
    if deal_id not in stage_history_cache:
        stage_history_cache[deal_id] = fetch_deal_history(deal_id)
    return jsonify({"data": stage_history_cache[deal_id], "deal_id": deal_id})

@app.route("/funnel-history")
def funnel_history():
    """
    Retorna para cada deal do funil especificado, as etapas que ele passou
    e quando entrou em cada uma — baseado no histórico da API.
    Parâmetros: funnel_name (obrigatório), date_from, date_to (YYYY-MM-DD)
    """
    funnel_name = request.args.get("funnel_name", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    deals_do_funil = [
        d for d in cache["deals"]
        if d.get("dealStage", {}).get("funnel", {}).get("name") == funnel_name
        or any(
            (d.get("startTime", "") or "")[:10] >= date_from
            for _ in [1] if date_from
        )
    ]

    # Filtra por data de início se informado
    if date_from:
        deals_do_funil = [d for d in deals_do_funil if (d.get("startTime") or "")[:10] >= date_from]
    if date_to:
        deals_do_funil = [d for d in deals_do_funil if (d.get("startTime") or "")[:10] <= date_to]

    result = []
    for deal in deals_do_funil[:200]:  # limita a 200 deals por chamada
        deal_id = deal["id"]
        if deal_id not in stage_history_cache:
            stage_history_cache[deal_id] = fetch_deal_history(deal_id)
            time.sleep(0.15)
        result.append({
            "deal_id": deal_id,
            "title": deal.get("title"),
            "startTime": deal.get("startTime"),
            "history": stage_history_cache[deal_id]
        })

    return jsonify({"data": result, "total": len(result)})

@app.route("/chat", methods=["POST"])
def chat():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY não configurada"}), 500
    try:
        payload = request.get_json()
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01"
            },
            json=payload,
            timeout=30
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/deals/<int:deal_id>/history-raw")
def deal_history_raw(deal_id):
    """Retorna histórico bruto de um deal para diagnóstico."""
    r = requests.get(f"{AGENDOR_BASE}/deals/{deal_id}/history", headers=HEADERS, timeout=15)
    if r.status_code == 200:
        return jsonify(r.json())
    return jsonify({"error": r.status_code, "text": r.text[:500]}), r.status_code

scheduler = BackgroundScheduler()
scheduler.add_job(fetch_deals_safe, "interval", hours=1, id="fetch_recorrente")
scheduler.add_job(fetch_deals_safe, "date", run_date=datetime.now() + timedelta(seconds=5), id="fetch_inicial")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
