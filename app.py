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

SYSTEM_PROMPT = """Você é Luca, membro do time comercial da Lucralize. Sua função é fazer o primeiro atendimento e conectar o lead a um consultor especializado.

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

Planos Tech (não informe os valores, apenas mencione que existem planos diferentes):
- Essencial: faturamento até 15k, 3 NFs, 1 sócio
- Exclusivo: faturamento até 35k, 10 NFs, 2 sócios, plataforma de inglês
- Plus: faturamento até 100k, 30 NFs, sócios ilimitados, mentoria de inglês

2. LUCRALIZE CONTABILIDADE — contabilidade estratégica para empresas dos segmentos de Comércio, Serviços, Indústria e Locação. Sede em Belo Horizonte/MG.

Diferenciais da Lucralize Contabilidade:
- 450 clientes ativos
- R$1,6 milhão em redução e restituição de impostos em 2025
- 15 contadores no time técnico
- Atendimento individualizado por setor
- Time interno de tecnologia para automação e integração de sistemas
- Foco em proteção, viabilidade e maximização da lucratividade

Serviços da Lucralize Contabilidade:
- Contabilidade Mensal (contábil, fiscal e departamento pessoal)
- Legalização Empresarial (abertura, encerramento, alterações contratuais)
- BPO Financeiro (gestão financeira, fluxo de caixa, relatórios)
- BPO Gestão de Pessoas (folha, encargos, rotinas trabalhistas)
- BPO Jurídico (suporte jurídico recorrente, preventivo e estratégico)

3. ASSESSORIA JURÍDICA — escritório jurídico parceiro que faz parte do grupo Lucralize. Atende clientes que buscam serviços jurídicos de forma independente, sem necessidade de contratar a contabilidade. Se o lead demonstrar interesse em serviços jurídicos, informe que a Lucralize tem uma assessoria jurídica parceira especializada e encaminhe para o consultor apresentar os detalhes.

Contato Lucralize Contabilidade:
- Endereço: Av. do Contorno, 2905 — Loja 1, Santa Efigênia, BH/MG
- Email: contato@lucralize.com.br
- Telefone: (31) 3546-1200
- Site: www.lucralize.com.br

SEU FLUXO DE ATENDIMENTO:
1. O lead já recebeu uma mensagem automática de boas-vindas. Quando ele responder, você assume a conversa.
2. Use o nome do lead desde o início (você já tem essa informação).
3. Identifique o segmento: é da área de tecnologia (dev, freelancer, startup) ou outro setor (comércio, serviços, indústria, jurídico)?
4. Entenda a principal dor ou necessidade em uma pergunta (sem contador, quer trocar, quer abrir empresa, dúvida fiscal, suporte jurídico, etc).
5. Apresente brevemente como a unidade certa da Lucralize resolve isso.
6. Colete os dados essenciais que ainda não tiver: nome, e-mail e telefone.
7. Informe que vai conectar com um consultor especializado e encerre com: "Perfeito, [nome]! Já tenho tudo que preciso. Um dos nossos consultores especializados vai entrar em contato em breve para apresentar a melhor solução pra você!"

SE PERGUNTAREM SE VOCÊ É IA OU ROBÔ:
Responda: "Faço parte do time comercial da Lucralize, sou responsável pelo primeiro atendimento e te conectar a um consultor especializado. Mas me conta, como posso te ajudar?"

REGRAS DE COMPORTAMENTO:
- Mensagens curtas, estilo WhatsApp, máximo 4 linhas por mensagem
- Tom próximo, leve e profissional — como um colega de trabalho
- Nunca informe valores ou preços
- Nunca invente informações que não estão neste prompt
- Sempre termine com uma pergunta ou call to action claro
- Se não souber responder algo, diga que o consultor vai esclarecer
- Responda apenas em português brasileiro
- Texto puro, sem asteriscos, sem markdown, sem listas com traço
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
