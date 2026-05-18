from flask import Flask, jsonify
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import os
import time
import threading

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

def fetch_products_for_deal(deal_id):
    try:
        r = requests.get(
            f"{AGENDOR_BASE}/deals/{deal_id}/products",
            headers=HEADERS,
            timeout=15
        )
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception as e:
        print(f"Erro produtos negócio {deal_id}: {e}")
    return []

def fetch_deals():
    print("⏰ Buscando negócios do Agendor...")
    all_deals = []
    page = 1
    total_count = None

    while True:
        try:
            params = {
                "per_page": 100,
                "page": page,
                "withCustomFields": "true"
            }
            r = requests.get(
                f"{AGENDOR_BASE}/deals",
                headers=HEADERS,
                params=params,
                timeout=30
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"Erro na página {page}: {e}")
            break

        page_deals = data.get("data", [])
        if total_count is None:
            total_count = data.get("meta", {}).get("totalCount", 0)
            print(f"Total na API: {total_count}")

        all_deals.extend(page_deals)
        print(f"Página {page}: {len(page_deals)} negócios ({len(all_deals)}/{total_count})")

        next_link = data.get("links", {}).get("next")
        if not next_link or len(page_deals) == 0:
            break
        page += 1

    # Atualiza cache antes de buscar produtos (servidor já responde)
    cache["deals"] = all_deals
    cache["total"] = total_count or len(all_deals)
    cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"✅ Cache atualizado: {len(all_deals)} negócios às {cache['updated_at']}")

    # Busca produtos dos ganhos em background
    won_deals = [d for d in all_deals if d.get("dealStatus", {}).get("id") == 2]
    print(f"🔍 Buscando produtos de {len(won_deals)} negócios ganhos...")
    for i, deal in enumerate(won_deals):
        deal_id = deal.get("id")
        products = fetch_products_for_deal(deal_id)
        if products:
            deal["products_entities"] = products
        time.sleep(0.1)
    print(f"✅ Produtos carregados.")

@app.route("/")
def index():
    return jsonify({
        "status": "ok",
        "message": "Proxy Agendor rodando!",
        "cached_deals": len(cache["deals"]),
        "updated_at": cache["updated_at"]
    })

@app.route("/deals")
def deals():
    return jsonify({
        "data": cache["deals"],
        "meta": {
            "totalCount": cache["total"],
            "updated_at": cache["updated_at"]
        }
    })

@app.route("/funnels")
def funnels():
    r = requests.get(f"{AGENDOR_BASE}/funnels", headers=HEADERS, timeout=30)
    return jsonify(r.json())

# Busca inicial em thread separada para não travar o servidor
threading.Thread(target=fetch_deals, daemon=True).start()

scheduler = BackgroundScheduler()
scheduler.add_job(fetch_deals, "interval", hours=1)
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
