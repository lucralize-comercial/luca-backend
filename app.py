from flask import Flask, jsonify
from flask_cors import CORS
import requests
import os

app = Flask(__name__)
CORS(app)

AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN", "a89b0def-fd5e-45ed-981f-efe89f20159a")
AGENDOR_BASE = "https://api.agendor.com.br/v3"
HEADERS = {"Authorization": f"Token {AGENDOR_TOKEN}"}


@app.route("/")
def index():
    return jsonify({"status": "ok", "message": "Proxy Agendor rodando!"})


@app.route("/deals")
def deals():
    all_deals = []
    page = 1
    total_count = None

    while True:
        try:
            params = {"per_page": 100, "page": page, "withCustomFields": "true"}
            r = requests.get(
                f"{AGENDOR_BASE}/deals",
                headers=HEADERS,
                params=params,
                timeout=30
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"Erro na pagina {page}: {e}")
            break

        page_deals = data.get("data", [])
        if total_count is None:
            total_count = data.get("meta", {}).get("totalCount", 0)
            print(f"Total na API: {total_count}")

        all_deals.extend(page_deals)
        print(f"Pagina {page}: {len(page_deals)} negocios ({len(all_deals)}/{total_count})")

        next_link = data.get("links", {}).get("next")
        if not next_link or len(page_deals) == 0:
            break
        page += 1

    print(f"Total carregado: {len(all_deals)}")
    return jsonify({"data": all_deals, "meta": {"totalCount": total_count or len(all_deals)}})


@app.route("/funnels")
def funnels():
    r = requests.get(f"{AGENDOR_BASE}/funnels", headers=HEADERS, timeout=30)
    return jsonify(r.json())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
