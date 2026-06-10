from flask import Flask, jsonify, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import requests
import os
import time
import threading

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type"]}})

AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN", "a89b0def-fd5e-45ed-981f-efe89f20159a")
AGENDOR_BASE = "https://api.agendor.com.br/v3"
HEADERS = {"Authorization": f"Token {AGENDOR_TOKEN}"}
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

FUNIS_HISTORICO = ["Funil Comercial"]
HISTORICO_DIAS = 30

cache = {"deals": [], "total": 0, "updated_at": None}
history_cache = {"data": [], "updated_at": None, "total_processed": 0, "total_target": 0}
tasks_cache = {"data": [], "updated_at": None}

fetch_running = False
fetch_started_at = None
history_running = False

def fetch_page(page):
    for attempt in range(3):
        try:
            r = requests.get(
                f"{AGENDOR_BASE}/deals", headers=HEADERS,
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
    for attempt in range(2):
        try:
            r = requests.get(f"{AGENDOR_BASE}/deals/{deal_id}/history", headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.json().get("data", [])
            return []
        except Exception as e:
            print(f"Erro historico deal {deal_id}: {e}", flush=True)
            if attempt < 1:
                time.sleep(2)
    return []

def fetch_tasks_job():
    try:
        print("Buscando tasks do Agendor...", flush=True)
        all_tasks = []
        # Busca últimos 31 dias (limite da API)
        date_gt = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        page = 1
        while page <= 50:
            r = requests.get(
                f"{AGENDOR_BASE}/tasks", headers=HEADERS,
                params={"per_page": 100, "page": page, "createdDateGt": date_gt},
                timeout=60
            )
            if r.status_code != 200:
                print(f"Erro tasks pagina {page}: {r.status_code} {r.text[:100]}", flush=True)
                break
            data = r.json()
            page_data = data.get("data", [])
            if not page_data:
                break
            all_tasks.extend(page_data)
            print(f"Tasks pagina {page}: {len(page_data)} ({len(all_tasks)} total)", flush=True)
            if not data.get("links", {}).get("next") or len(page_data) < 100:
                break
            page += 1
            time.sleep(0.2)
        tasks_cache["data"] = all_tasks
        tasks_cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"Tasks: {len(all_tasks)} carregadas", flush=True)
    except Exception as e:
        print(f"Erro fetch tasks: {e}", flush=True)

def fetch_history_job():
    global history_running
    if history_running:
        return
    history_running = True
    try:
        all_deals = cache["deals"]
        if not all_deals:
            return
        cutoff = datetime.utcnow() - timedelta(days=HISTORICO_DIAS)
        deals_para_historico = [
            d for d in all_deals
            if d.get("dealStage", {}).get("funnel", {}).get("name") in FUNIS_HISTORICO
            and d.get("startTime")
            and datetime.strptime(d["startTime"][:10], "%Y-%m-%d") > cutoff
        ]
        total = len(deals_para_historico)
        history_cache["total_target"] = total
        history_cache["total_processed"] = 0
        print(f"Buscando histórico de {total} deals...", flush=True)
        hist_data = []
        for i, deal in enumerate(deals_para_historico):
            events = fetch_deal_history(deal["id"])
            hist_data.append({
                "deal_id": deal["id"], "title": deal.get("title", ""),
                "startTime": deal.get("startTime"), "wonAt": deal.get("wonAt"),
                "lostAt": deal.get("lostAt"), "dealStatus": deal.get("dealStatus", {}),
                "currentStage": deal.get("dealStage", {}), "owner": deal.get("owner", {}),
                "value": deal.get("value", 0), "events": events
            })
            history_cache["total_processed"] = i + 1
            if (i + 1) % 10 == 0:
                history_cache["data"] = list(hist_data)
                history_cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            time.sleep(0.15)
        history_cache["data"] = hist_data
        history_cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"Histórico completo: {len(hist_data)} deals.", flush=True)
    except Exception as e:
        print(f"Erro histórico: {e}", flush=True)
    finally:
        history_running = False

def fetch_deals():
    print("Buscando negocios do Agendor...", flush=True)
    all_deals = []
    page = 1
    total_count = None
    while True:
        data = fetch_page(page)
        if data is None:
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
        if not data.get("links", {}).get("next") or len(page_deals) == 0:
            break
        page += 1
        time.sleep(0.2)

    cache["deals"] = all_deals
    cache["total"] = total_count or len(all_deals)
    cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"Cache atualizado: {len(all_deals)} negocios", flush=True)

    cutoff = datetime.utcnow() - timedelta(days=180)
    won_recent = [
        d for d in all_deals
        if d.get("dealStatus", {}).get("id") == 2
        and d.get("wonAt") and datetime.strptime(d["wonAt"][:10], "%Y-%m-%d") > cutoff
    ]
    print(f"Buscando produtos de {len(won_recent)} ganhos recentes...", flush=True)
    for deal in won_recent:
        try:
            r = requests.get(f"{AGENDOR_BASE}/deals/{deal['id']}/products", headers=HEADERS, timeout=15)
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

    # Dispara histórico e tasks em threads separadas
    t1 = threading.Timer(5.0, fetch_history_job)
    t1.daemon = True
    t1.start()
    t2 = threading.Timer(10.0, fetch_tasks_job)
    t2.daemon = True
    t2.start()

def fetch_deals_safe():
    global fetch_running, fetch_started_at
    if fetch_running:
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
        "history_running": history_running,
        "history_cached": len(history_cache["data"]),
        "history_processed": history_cache["total_processed"],
        "history_target": history_cache["total_target"],
        "history_updated_at": history_cache["updated_at"],
        "tasks_cached": len(tasks_cache["data"]),
        "tasks_updated_at": tasks_cache["updated_at"]
    })

@app.route("/refresh", methods=["POST"])
def refresh():
    global fetch_running
    if fetch_running:
        return jsonify({"status": "running", "message": "Fetch já em andamento"}), 202
    scheduler.add_job(fetch_deals_safe, "date", id="fetch_manual", replace_existing=True)
    return jsonify({"status": "started", "message": "Atualização iniciada"}), 200

@app.route("/reset-fetch", methods=["POST"])
def reset_fetch():
    global fetch_running, fetch_started_at, history_running
    fetch_running = False
    fetch_started_at = None
    history_running = False
    return jsonify({"status": "ok", "message": "resetado"})

@app.route("/deals")
def deals():
    return jsonify({"data": cache["deals"], "meta": {"totalCount": cache["total"], "updated_at": cache["updated_at"]}})

@app.route("/tasks")
def tasks():
    return jsonify({
        "data": tasks_cache["data"],
        "total": len(tasks_cache["data"]),
        "updated_at": tasks_cache["updated_at"]
    })

@app.route("/funnels")
def funnels():
    r = requests.get(f"{AGENDOR_BASE}/funnels", headers=HEADERS, timeout=30)
    return jsonify(r.json())

@app.route("/history-cache")
def history_cache_route():
    return jsonify({
        "data": history_cache["data"],
        "total": len(history_cache["data"]),
        "updated_at": history_cache["updated_at"],
        "processing": history_running,
        "processed": history_cache["total_processed"],
        "target": history_cache["total_target"]
    })

@app.route("/chat", methods=["POST"])
def chat():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY nao configurada"}), 500
    try:
        payload = request.get_json()
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
            json=payload, timeout=30
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

scheduler = BackgroundScheduler()
scheduler.add_job(fetch_deals_safe, "interval", hours=1, id="fetch_recorrente")
scheduler.add_job(fetch_deals_safe, "date", run_date=datetime.now() + timedelta(seconds=5), id="fetch_inicial")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
