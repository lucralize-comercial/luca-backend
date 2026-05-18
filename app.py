from flask import Flask, jsonify
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import requests
import os
import time

app = Flask(__name__)
CORS(app)

AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN", "a89b0def-fd5e-45ed-981f-efe89f20159a")
AGENDOR_BASE = "https://api.agendor.com.br/v3"
HEADERS = {"Authorization": f"Token {AGENDOR_TOKEN}"}

cache = {"deals": [], "total": 0, "updated_at": None}

def fetch_page(page):
    for attempt in range(3):
        try:
            r = requests.get(
                f"{AGENDOR_BASE}/deals",
                headers=HEADERS,
                params={"per_page": 100, "page": page, "withCustomFields": "true"},
                timeout=60
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"Tentativa {attempt+1}/3 falhou na pagina {page}: {e}", flush=True)
            if attempt < 2:
                time.sleep(5)
    return None

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

    # Busca produtos só dos ganhos dos últimos 90 dias
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
        "fetch_running": fetch_running
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

scheduler = BackgroundScheduler()
scheduler.add_job(fetch_deals_safe, "interval", hours=1, id="fetch_recorrente")
scheduler.add_job(fetch_deals_safe, "date", run_date=datetime.now() + timedelta(seconds=5), id="fetch_inicial")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
