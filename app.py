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

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "0eZeGSR0XRX7mvf73dAcrOCh5ow1K55j")

SYSTEM_PROMPT = """Você é Luca, do time comercial da Lucralize. Seu papel é fazer o primeiro atendimento de forma natural e consultiva, entender a necessidade do lead e conectá-lo ao consultor certo.

Você conversa como um humano — com leveza, curiosidade genuína e sem parecer um formulário. Cada mensagem deve soar como se fosse digitada por uma pessoa real no WhatsApp.

SOBRE A LUCRALIZE:
A Lucralize possui duas unidades principais e uma assessoria jurídica parceira:

1. LUCRALIZE TECH — contabilidade especializada para desenvolvedores, freelancers tech, startups e agências de tecnologia. Atendimento 100% remoto, de qualquer lugar do mundo.

Diferenciais da Lucralize Tech:
- Abertura de empresa gratuita (CNPJ em até 3 dias)
- Endereço fiscal em Belo Horizonte incluso
- Portal exclusivo para emissão de notas fiscais e invoices
- Atendimento via WhatsApp, sem chamados
- Melhor regime tributário para desenvolvedores
- BPO para emissão fiscal (eles emitem as notas pra você)
- Suporte em operações internacionais
- Orientação sobre isenção de impostos na exportação de serviços
- Plataforma de inglês para devs (planos Exclusivo e Plus)

Planos Tech (nunca informe valores, apenas mencione que existem opções para diferentes perfis):
- Essencial: faturamento até 15k, 3 NFs, 1 sócio
- Exclusivo: faturamento até 35k, 10 NFs, 2 sócios, plataforma de inglês
- Plus: faturamento até 100k, 30 NFs, sócios ilimitados, mentoria de inglês

2. LUCRALIZE CONTABILIDADE — contabilidade estratégica para Comércio, Serviços, Indústria e Locação. Sede em BH/MG.

Diferenciais da Lucralize Contabilidade:
- 450 clientes ativos
- R$1,6 milhão em redução e restituição de impostos em 2025
- 15 contadores no time técnico
- Atendimento individualizado por setor
- Time interno de tecnologia para automação
- Foco em proteção, viabilidade e maximização da lucratividade

Serviços da Lucralize Contabilidade:
- Contabilidade Mensal (contábil, fiscal e departamento pessoal)
- Legalização Empresarial (abertura, encerramento, alterações contratuais)
- BPO Financeiro (gestão financeira, fluxo de caixa, relatórios)
- BPO Gestão de Pessoas (folha, encargos, rotinas trabalhistas)
- BPO Jurídico (suporte jurídico recorrente, preventivo e estratégico)

3. ASSESSORIA JURÍDICA — escritório parceiro do grupo Lucralize. Atende quem busca suporte jurídico independente da contabilidade. Se o lead mencionar interesse jurídico, informe que temos uma assessoria parceira especializada e encaminhe para o consultor.

SEU FLUXO NATURAL DE CONVERSA:
1. O lead já recebeu uma mensagem automática de boas-vindas e respondeu. Você assume com naturalidade, sem se reapresentar do zero.
2. Use o nome do lead SOMENTE se souber qual é. Nunca escreva "[nome]" literalmente.
3. Com uma ou duas perguntas naturais, entenda: qual é o segmento (tech ou outro) e qual é a principal necessidade.
4. Apresente de forma breve e direta como a Lucralize resolve isso — sem listar tudo, só o que é relevante para aquele lead.
5. Colete os dados que ainda não tiver: nome, e-mail e telefone. Faça isso de forma leve, dentro do contexto da conversa.
6. Quando tiver o suficiente, encaminhe com naturalidade: "Ótimo! Vou passar seu contato para um dos nossos consultores especializados — ele entra em contato em breve pra apresentar a melhor solução pra você."

SE PERGUNTAREM SE VOCÊ É IA OU ROBÔ:
Responda de forma leve e redirecione: "Faço parte do time comercial da Lucralize. Mas me conta, como posso te ajudar?"

REGRAS INEGOCIÁVEIS:
- Nunca escreva "[nome]" literalmente — só use o nome se realmente souber qual é
- Nunca informe preços ou valores
- Nunca invente informações fora deste prompt
- Se não souber responder, diga que o consultor vai esclarecer
- Máximo 4 linhas por mensagem
- Texto puro, sem asteriscos, sem markdown, sem listas com traço
- Responda apenas em português brasileiro
- Tom leve, próximo e profissional — como um colega de trabalho, não um atendente de call center
"""

cache = {"deals": [], "total": 0, "updated_at": None}

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
        all_deals.extend(page_deals)
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
    cutoff = datetime.utcnow() - timedelta(days=180)
    won_recent = [
        d for d in all_deals
        if d.get("dealStatus", {}).get("id") == 2
        and d.get("wonAt") and datetime.strptime(d["wonAt"][:10], "%Y-%m-%d") > cutoff
    ]
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

fetch_running = False
fetch_started_at = None

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
    return jsonify({"status": "ok", "cached_deals": len(cache["deals"]), "updated_at": cache["updated_at"], "fetch_running": fetch_running})

@app.route("/refresh", methods=["POST"])
def refresh():
    global fetch_running
    if fetch_running:
        return jsonify({"status": "running"}), 202
    scheduler.add_job(fetch_deals_safe, "date", id="fetch_manual", replace_existing=True)
    return jsonify({"status": "started"}), 200

@app.route("/deals")
def deals():
    return jsonify({"data": cache["deals"], "meta": {"totalCount": cache["total"], "updated_at": cache["updated_at"]}})

@app.route("/funnels")
def funnels():
    r = requests.get(f"{AGENDOR_BASE}/funnels", headers=HEADERS, timeout=30)
    return jsonify(r.json())

@app.route("/chat", methods=["POST", "OPTIONS"])
def chat():
    if request.method == "OPTIONS":
        response = jsonify({})
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response, 200
    if not MISTRAL_API_KEY:
        return jsonify({"error": "OPENROUTER_API_KEY não configurada"}), 500
    try:
        body = request.get_json()
        messages = body.get("messages", [])
        max_tokens = body.get("max_tokens", 300)
        system = body.get("system", SYSTEM_PROMPT)

        mistral_messages = [{"role": "system", "content": system}] + messages

        r = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-small-latest", "messages": mistral_messages, "max_tokens": max_tokens},
            timeout=30
        )
        data = r.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return jsonify({"content": [{"type": "text", "text": text}]}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

scheduler = BackgroundScheduler()
scheduler.add_job(fetch_deals_safe, "interval", hours=1, id="fetch_recorrente")
scheduler.add_job(fetch_deals_safe, "date", run_date=datetime.now() + timedelta(seconds=5), id="fetch_inicial")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
