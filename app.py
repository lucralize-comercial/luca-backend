from flask import Flask, jsonify
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import requests
import os
import time
import sys

app = Flask(__name__)
CORS(app)

AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN", "a89b0def-fd5e-45ed-981f-efe89f20159a")
AGENDOR_BASE = "https://api.agendor.com.br/v3"
HEADERS = {"Authorization": f"Token {AGENDOR_TOKEN}"}

cache = {
    "deals": [],
    "total": 0,
    "updated_at": None
}

def log(msg):
    print(msg, flush=True)  # flush=True garante que o log aparece imediatamente no Render

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
            log(f"Tentativa {attempt+1}/3 falhou na pagina {page}: {e}")
            if attempt < 2:
                time.sleep(10)
    return None

def fetch_deals():
    log("Buscando negocios do Agendor...")
    all_deals = []
    page = 1
    total_count = None

    while True:
        log(f"Buscando pagina {page}...")
        data = fetch_page(page)
        if data is None:
            log(f"Pagina {page} falhou 3 vezes, encerrando.")
            break

        page_deals = data.get("data", [])
        if total_count is None:
            total_count = data.get("meta", {}).get("totalCount", 0)
            log(f"Total na API: {total_count}")

        all_deals.extend(page_deals)
        log(f"Pagina {page}: {len(page_deals)} negocios ({len(all_deals)}/{total_count})")

        if page % 10 == 0:
            cache["deals"] = list(all_deals)
            cache["total"] = total_count or len(all_deals)
            cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            log(f"Cache parcial salvo: {len(all_deals)} negocios")

        next_link = data.get("links", {}).get("next")
        if not next_link or len(page_deals) == 0:
            break
        page += 1
        time.sleep(0.2)

    cache["deals"] = all_deals
    cache["total"] = total_count or len(all_deals)
    cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log(f"Cache atualizado: {len(all_deals)} negocios as {cache['updated_at']}")

@app.route("/")
def index():
    return jsonify({
        "status": "ok",
        "cached_deals": len(cache["deals"]),
        "updated_at": cache["updated_at"]
    })

@app.route("/deals")
def deals():
    return jsonify({
        "data": cache["deals"],
        "meta": {"totalCount": cache["total"], "updated_at": cache["updated_at"]}
    })

@app.route("/funnels")
def funnels():
    r = requests.get(f"{AGENDOR_BASE}/funnels", headers=HEADERS, timeout=30)
    return jsonify(r.json())

scheduler = BackgroundScheduler()
scheduler.add_job(fetch_deals, "interval", hours=1, id="fetch_recorrente")
scheduler.add_job(fetch_deals, "date", run_date=datetime.now() + timedelta(seconds=5), id="fetch_inicial")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
