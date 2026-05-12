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
        params = {"per_page": 100, "page": page, "withCustomFields": "true"}
        r = requests.get(f"{AGENDOR_BASE}/deals", headers=HEADERS, params=params)
        data = r.json()

        page_deals = data.get("data", [])
        if total_count is None:
            total_count = data.get("meta", {}).get("totalCount", 0)

        all_deals.extend(page_deals)

        next_link = data.get("links", {}).get("next")
        if not next_link or len(page_deals) == 0:
            break
        page += 1

    return jsonify({"data": all_deals, "meta": {"totalCount": total_count}})


@app.route("/funnels")
def funnels():
    r = requests.get(f"{AGENDOR_BASE}/funnels", headers=HEADERS)
    return jsonify(r.json())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
