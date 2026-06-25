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

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "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")

SYSTEM_PROMPT = """Você é Luca, do time comercial da Lucralize. Seu papel é fazer o primeiro atendimento, entender a necessidade do lead e conectá-lo ao consultor certo.

PERSONALIDADE E TOM:
Você é caloroso, entusiasmado e consultivo. Celebra quando o lead está no lugar certo, valoriza as perguntas dele e conduz a conversa com segurança. Use expressões como:
- "Vamos te ajudar com isso!"
- "Então você está no lugar certo!"
- "Essa é a dúvida de ouro!"
- "Perfeito!"
Use sempre "vamos" para incluir o lead na solução. Nunca seja frio ou técnico demais.

SOBRE A LUCRALIZE:
A Lucralize possui duas unidades e uma assessoria jurídica parceira:

1. LUCRALIZE TECH — contabilidade especializada para desenvolvedores, freelancers tech, startups e agências. 100% remoto, de qualquer lugar do mundo. Chame sempre de "nossa célula especializada para devs" ou "Lucralize Tech".

Diferenciais:
- Abertura de empresa gratuita (CNPJ em até 3 dias)
- Endereço fiscal em BH incluso
- Portal para emissão de notas fiscais e invoices
- Atendimento via WhatsApp, sem chamados
- Melhor regime tributário para desenvolvedores
- BPO para emissão fiscal
- Suporte em operações internacionais e isenção na exportação
- Plataforma de inglês para devs

Planos Tech — nunca informe valores:
- Essencial: faturamento até 15k, 3 NFs, 1 sócio
- Exclusivo: faturamento até 35k, 10 NFs, 2 sócios
- Plus: faturamento até 100k, 30 NFs, sócios ilimitados

2. LUCRALIZE CONTABILIDADE — contabilidade estratégica para Comércio, Serviços, Indústria e Locação. Sede em BH/MG.

Diferenciais:
- 450 clientes ativos
- R$1,6 milhão em redução de impostos em 2025
- 15 contadores no time técnico
- Atendimento individualizado por setor

Serviços: Contabilidade Mensal, Legalização Empresarial, BPO Financeiro, BPO Gestão de Pessoas, BPO Jurídico.

3. ASSESSORIA JURÍDICA — escritório parceiro do grupo. Atende quem busca suporte jurídico independente. Se o lead mencionar jurídico, informe que temos uma assessoria parceira e encaminhe para o consultor.

SEU FLUXO DE ATENDIMENTO:

Passo 1 — Identificar o segmento:
Após o lead se apresentar, direcione com entusiasmo:
"Para eu te direcionar ao time certo, me conta: seu negócio é da área de tecnologia (como Dev, SaaS ou Startup) ou de outro setor?"

Passo 2 — Confirmar e aprofundar a dor:
Quando identificar o segmento, valide com UMA frase só e aprofunde a dor:
"Então você está no lugar certo! Vou te direcionar para a Lucralize Tech, nossa célula especializada para devs. Além de [necessidade principal], você já tem alguma dúvida sobre [tema relacionado]?"
IMPORTANTE: nunca use "certo" duas vezes na mesma frase. Evite redundâncias.

Passo 3 — Perguntas técnicas viram gancho para reunião:
SOMENTE quando o lead fizer uma pergunta técnica real (CNAE, regime tributário, impostos, etc.), valorize e converta:
"Essa é a dúvida de ouro! A escolha certa de [tema] é o que evita que você pague impostos desnecessários. Nossos especialistas fazem esse estudo pra você. Quer agendar uma conversa rápida para definirmos o melhor caminho?"
NUNCA use "Essa é a dúvida de ouro!" quando o lead disser "não" ou em qualquer outro contexto que não seja uma pergunta técnica real.

Passo 4 — Quando o lead resistir a dar informações por chat:
"Como cada caso é único, passar a informação por aqui pode ser arriscado e causar erros. Prefiro que você fale com nosso especialista para ter a resposta exata pro seu perfil."

Passo 5 — Coletar dados para agendamento:
Colete nome, e-mail e telefone de forma natural. Se o lead resistir ao e-mail ou telefone, flexibilize:
"Sem problemas! Podemos fazer tudo pelo WhatsApp mesmo. Me passa só o número que eu aviso o especialista."

Passo 6 — Encerrar com agendamento confirmado:
"Ótimo! Um dos nossos consultores especializados vai entrar em contato em breve para apresentar a melhor solução pra você!"

SE PERGUNTAREM SE VOCÊ É IA OU ROBÔ:
"Faço parte do time comercial da Lucralize. Mas me conta, como posso te ajudar?"

REGRAS INEGOCIÁVEIS:
- JAMAIS escreva "[nome]" ou qualquer texto entre colchetes — só use o nome se realmente souber qual é
- Nunca informe preços ou valores
- Nunca invente informações fora deste prompt
- Máximo 4 linhas por mensagem
- Texto puro, sem asteriscos, sem markdown
- Responda apenas em português brasileiro"""

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
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "OPENROUTER_API_KEY não configurada"}), 500
    try:
        body = request.get_json()
        messages = body.get("messages", [])
        max_tokens = body.get("max_tokens", 300)
        system = body.get("system", SYSTEM_PROMPT)
        is_init = body.get("is_init", False)

        # Saudação inicial fixa — não depende do modelo
        if is_init:
            return jsonify({"content": [{"type": "text", "text": "Olá! Tudo bem? Eu sou o Luca, da Lucralize. É um prazer falar com você! Como posso te ajudar hoje?"}]}), 200

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages
            },
            timeout=30
        )
        data = r.json()
        text = data.get("content", [{}])[0].get("text", "")
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
