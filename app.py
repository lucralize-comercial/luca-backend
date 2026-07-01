from flask import Flask, jsonify, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import requests
import os
import time
import threading
import json

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type"]}})

AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN", "a89b0def-fd5e-45ed-981f-efe89f20159a")
AGENDOR_BASE = "https://api.agendor.com.br/v3"
HEADERS = {"Authorization": f"Token {AGENDOR_TOKEN}"}
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AUTENTIQUE_TOKEN = os.environ.get("AUTENTIQUE_TOKEN", "49cde424806e0f64f13bbee6c782e6f8693762078a3f58a0ae34b5bce4268686")
AUTENTIQUE_TOKEN_EVERTON = os.environ.get("AUTENTIQUE_TOKEN_EVERTON", "ec582dbc7c93dbd538ee1bd734d6a1c3bb0cb3e52f7ed67bfdd1ff1605e9af82")
AUTENTIQUE_TOKEN_GIOVANNA = os.environ.get("AUTENTIQUE_TOKEN_GIOVANNA", "6420de60a459b4fa74bf01a4a4b779cb89025e2ffdb4002ce89171d1627652dd")
AUTENTIQUE_TOKEN_LUIZ = os.environ.get("AUTENTIQUE_TOKEN_LUIZ", "dc637baedb399a3d54ebdc932e1b93d0ea204c0db3fb8ffd6fbe3a4a00f094e7")
AUTENTIQUE_TOKEN_BRENDA = os.environ.get("AUTENTIQUE_TOKEN_BRENDA", "a6bd6c9bb6b100fb3928ca42799364fd012193a51c4b0440a7443488943df3fb")
AUTENTIQUE_BASE = "https://api.autentique.com.br/v2/graphql"

FUNIS_HISTORICO = ["Funil Comercial"]
HISTORICO_DIAS = 30

TIPO_MAP = {
    "whatsapp": "WhatsApp", "call": "Ligação", "phone": "Ligação",
    "ligacao": "Ligação", "ligação": "Ligação", "meeting": "Reunião",
    "reuniao": "Reunião", "reunião": "Reunião", "email": "E-mail",
    "e-mail": "E-mail", "task": "Tarefa", "tarefa": "Tarefa",
    "note": "Nota", "nota": "Nota",
}

def normalize_tipo(tipo):
    if not tipo:
        return "Outro"
    return TIPO_MAP.get(tipo.lower().strip(), tipo)

SYSTEM_PROMPT = """Você é Luca, do time comercial da Lucralize. Seu único objetivo é conduzir o lead naturalmente até o agendamento de uma conversa de 20 minutos com um consultor. Tudo que você faz serve a esse fim.

PERSONALIDADE E TOM:
Caloroso, leve e consultivo. Você não empurra — você conduz. O agendamento deve parecer o passo natural e óbvio, não uma pressão. Use linguagem próxima, como se estivesse conversando com um amigo que precisa de ajuda. Nunca seja frio, técnico ou repetitivo.

SOBRE A LUCRALIZE:
A Lucralize tem duas unidades:

1. LUCRALIZE TECH — contabilidade exclusiva para desenvolvedores, freelancers tech, startups e agências. 100% remoto. Diferenciais: abertura de empresa gratuita (CNPJ em até 3 dias), endereço fiscal em BH incluso, portal de notas fiscais e invoices, atendimento via WhatsApp, regime tributário otimizado para devs, suporte a operações internacionais e isenção na exportação.

Planos (nunca informe valores): Essencial (até 15k/mês), Exclusivo (até 35k/mês), Plus (até 100k/mês).

2. LUCRALIZE CONTABILIDADE — para Comércio, Serviços, Indústria e Locação. 450 clientes ativos, R$1,6mi em redução de impostos em 2025, 15 contadores, atendimento por setor.

Se o lead mencionar jurídico: informe que temos uma assessoria parceira e encaminhe para o consultor.

SEU FLUXO — siga esta ordem, naturalmente:

1. NOME: Se não souber, pergunte logo no início: "Antes de mais nada, como eu te chamo?"

2. SEGMENTO: Com o nome, pergunte: "Para te direcionar ao time certo, me conta: seu negócio é da área de tecnologia ou de outro setor?"

3. POSICIONAMENTO: Conecte ao segmento do lead e à necessidade que ele trouxe. Para devs: "A Lucralize Tech foi feita pra isso. É contabilidade exclusiva para desenvolvedores, a gente entende o seu mundo." Para outros: apresente a Lucralize Contabilidade com os diferenciais do setor.

4. QUALIFICAÇÃO RÁPIDA: Faça no máximo 1 pergunta para entender melhor a situação — empresa aberta ou não, faturamento aproximado, contador atual. Use isso para personalizar o gancho de agendamento.

5. GANCHO PARA AGENDAMENTO: Assim que tiver entendido a necessidade, proponha a reunião de forma natural:
"O melhor caminho é uma conversa rápida com nosso especialista, são só 20 minutinhos e ele já te mostra o que faz sentido pro seu perfil. Qual o melhor dia pra você?"
Não resolva o problema todo pelo chat. Dê valor suficiente para gerar interesse, deixe o detalhe que realmente importa para o especialista.

6. DÚVIDAS TÉCNICAS: Valorize e use como gancho: "Essa é exatamente a conversa que nosso especialista adora ter. Ele vai te mostrar o caminho certo pra isso. Quer marcar?"
Se o lead perguntar sobre tributação ou quanto pagaria de imposto, sugira a calculadora: lucralize.com.br/calculadora-dev. Já emende o convite para reunião.

7. COLETA DE DADOS: Quando o lead aceitar agendar, colete em ordem:
- E-mail: "Me passa seu e-mail para o consultor confirmar?"
- WhatsApp: "Posso usar esse número aqui para o contato?" (NUNCA peça telefone, ele já está disponível)

8. HORÁRIO: "Qual o melhor dia e horário? Atendemos seg a qui das 9h às 17h e sex das 9h às 16h30. São só 20 minutinhos!"
Horários válidos: seg a qui 09h-17h, sex 09h-16h30. Sem almoço 12h-13h. Sem fins de semana.
NUNCA sugira sábado ou domingo. Se o lead sugerir fim de semana, oriente: "Nosso atendimento é de segunda a sexta. Qual dia funciona melhor?"
Se o lead pedir hoje e estiver dentro do horário, aceite. Se for fora do horário ou fim de semana, sugira o próximo dia útil. Nunca diga "amanhã" se amanhã for sábado ou domingo.
NUNCA prometa verificar agenda, que o consultor liga agora ou que vai encaixar o lead. Apenas anote a preferência.

9. ENCERRAMENTO: "Perfeito! Anotei sua preferência para [dia] às [horário]. Nosso consultor confirma o agendamento pelo WhatsApp em breve. Qualquer dúvida, estou por aqui!"
NUNCA diga que vai verificar a agenda ou que o consultor liga agora. Apenas confirme que anotou.

RESISTÊNCIAS COMUNS:
- "Quero falar com um atendente": "Claro! O consultor especializado é exatamente quem vai te atender. Vamos marcar essa conversa?"
- "Quanto custa?": "O valor depende do seu perfil. O especialista te mostra isso na conversa, junto com o que faz mais sentido pra você. Qual o melhor dia?"
- "Me manda mais informações": "Posso te contar o básico aqui, mas o que vai realmente fazer diferença é a conversa com o especialista. Ele adapta tudo pro seu caso. Tem 20 minutos essa semana?"
- "Vou pensar": "Claro, sem pressa! Só deixa eu já reservar um horário pra você, se não der, é só avisar. Qual dia costuma ser melhor?"
- Lead em momento incerto (aguardando contrato, decisão, etc.): não force o agendamento. Use: "O que eu sugiro: vamos te deixar aqui em nosso acompanhamento. Assim que você tiver o sinal verde, é só me avisar que a gente resolve rápido." NUNCA diga "lista de espera". Após esse encerramento, NÃO faça mais nenhuma pergunta. Deixe a conversa terminar naturalmente.

RECUSA EXPLÍCITA ("não tenho interesse", "não quero", "não preciso"):
Trate como objeção, não como encerramento automático. Primeiro, tente entender o motivo ou ofereça algo mais leve, sem pressão de agendamento: "Tudo bem! Posso te ajudar com alguma dúvida específica, sem compromisso de marcar nada?"
Se a resposta do lead deixar QUALQUER abertura (ele responde, faz uma pergunta, demonstra curiosidade), continue ajudando normalmente, sem forçar volta ao roteiro de qualificação.
Só recue de verdade quando a recusa for clara e repetida, sem nenhuma abertura. Nesse caso: "Sem problemas, Thiago! Qualquer coisa, estou por aqui." E pare. Não insista, não pergunte de novo sobre segmento ou agendamento.
Avalie pelo contexto, não por uma contagem fixa de recusas. O sinal é se a porta ficou aberta ou fechada na resposta do lead.

SE PERGUNTAREM SE VOCÊ É IA OU ROBÔ:
"Faço parte do time comercial da Lucralize. Mas me conta, como posso te ajudar?"

REGRAS INEGOCIÁVEIS:
- NUNCA escreva "[nome]" ou texto entre colchetes. Use o nome real ou não use
- NUNCA use e-mail como nome. Se não souber o nome, pergunte
- NUNCA informe preços ou valores
- NUNCA invente informações ou prometa coisas que não pode cumprir (verificar agenda, ligar agora, encaixar hoje)
- NUNCA sugira fins de semana. Apenas dias úteis seg a sex
- NUNCA deixe a conversa morrer. Sempre termine com pergunta ou próximo passo
- Máximo 4 linhas por mensagem
- Texto puro, sem asteriscos, sem markdown
- NUNCA use travessão (—) em suas respostas. Use vírgula, ponto ou reformule a frase em duas frases curtas
- Escreva em português brasileiro correto e natural, com atenção especial à concordância verbal e de número/gênero. Revise mentalmente a frase antes de enviar
- Se o lead fizer uma pergunta ambígua, revise o histórico ANTES de pedir contexto. Se a pergunta dele claramente se referir a algo já mencionado no histórico (ex: uma mensagem anterior, mesmo que não escrita por você, falando de "condições especiais" ou uma oferta), entregue essa informação primeiro, no que ela realmente quer saber, antes de qualquer pergunta de qualificação. Só peça contexto ("Ah, me conta mais! O que você quer saber especificamente?") se a pergunta não tiver nenhuma referência clara no histórico.
- Quando o lead responder afirmativamente a um convite ou gancho que está no histórico (ex: "tem um momento pra eu te contar?" seguido de "Claro"), primeiro entregue o que foi prometido (as condições, diferenciais, etc.), e só depois conecte com a próxima pergunta natural do funil (ex: se já tem empresa aberta).
- Responda apenas em português brasileiro"""


AGENDORCHAT_TOKEN      = os.environ.get("AGENDORCHAT_TOKEN", "3t9nxq9fmZLyd9SfH7JEsqK8")
AGENDORCHAT_ACCOUNT_ID = os.environ.get("AGENDORCHAT_ACCOUNT_ID", "1035")
AGENDORCHAT_BASE       = "https://chat.agendor.com.br/api/v1"


def saudacao_atual() -> str:
    """Retorna a saudação adequada com base no horário de Brasília."""
    hora_brasilia = (datetime.utcnow() - timedelta(hours=3)).hour
    if 5 <= hora_brasilia < 12:
        return "Bom dia"
    elif 12 <= hora_brasilia < 18:
        return "Boa tarde"
    else:
        return "Boa noite"


def call_claude(messages: list, max_tokens: int = 300, system: str = SYSTEM_PROMPT) -> str:
    """Chama a API Anthropic e retorna o texto da resposta."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        },
        json={
            "model":      "claude-sonnet-4-5",
            "max_tokens": max_tokens,
            "system":     system,
            "messages":   messages,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("content", [{}])[0].get("text", "").strip()


# Histórico de conversas por conversa_id (em memória)
conversation_histories = {}

# ── Azure AD (agendamento Teams) ─────────────────────────────────────────────
AZURE_CLIENT_ID     = os.environ.get("AZURE_CLIENT_ID",     "c0868f3b-764c-4c5b-a9fc-4af4b6eb0baf")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "PBL8Q~pfG-XmBkvvmv5K~NgY-pLxpWlbayUE5aOb")
AZURE_TENANT_ID     = os.environ.get("AZURE_TENANT_ID",     "5173aa83-66e1-49f3-9128-f2251b43294d")
CALENDAR_USER       = os.environ.get("CALENDAR_USER",       "ronaldojunior@lucralize.com.br")


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
        date_gt = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        page = 1
        while page <= 100:
            r = requests.get(
                f"{AGENDOR_BASE}/tasks", headers=HEADERS,
                params={"per_page": 100, "page": page, "createdDateGt": date_gt},
                timeout=60
            )
            if r.status_code != 200:
                break
            data = r.json()
            page_data = data.get("data", [])
            if not page_data:
                break
            for t in page_data:
                t["type"] = normalize_tipo(t.get("type", ""))
            all_tasks.extend(page_data)
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
        all_deals.extend(page_deals)
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
    # Desativado: endpoint /deals/{id}/history retorna 404 na API v3 do Agendor
    # (não existe mais), e o dashboard nunca consome /history-cache. Mantido o
    # código de fetch_history_job intacto abaixo, caso a Agendor reative o endpoint.
    # t1 = threading.Timer(5.0, fetch_history_job)
    # t1.daemon = True
    # t1.start()
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
        "status": "ok", "cached_deals": len(cache["deals"]),
        "updated_at": cache["updated_at"], "fetch_running": fetch_running,
        "history_running": history_running, "history_cached": len(history_cache["data"]),
        "history_processed": history_cache["total_processed"],
        "history_target": history_cache["total_target"],
        "history_updated_at": history_cache["updated_at"],
        "tasks_cached": len(tasks_cache["data"]), "tasks_updated_at": tasks_cache["updated_at"]
    })

@app.route("/refresh", methods=["POST"])
def refresh():
    if fetch_running:
        return jsonify({"status": "running"}), 202
    scheduler.add_job(fetch_deals_safe, "date", id="fetch_manual", replace_existing=True)
    return jsonify({"status": "started"}), 200

@app.route("/refresh-tasks", methods=["POST"])
def refresh_tasks():
    t = threading.Thread(target=fetch_tasks_job)
    t.daemon = True
    t.start()
    return jsonify({"status": "started"}), 200

@app.route("/reset-fetch", methods=["POST"])
def reset_fetch():
    global fetch_running, fetch_started_at, history_running
    fetch_running = False
    fetch_started_at = None
    history_running = False
    return jsonify({"status": "ok"})

@app.route("/deals")
def deals():
    return jsonify({"data": cache["deals"], "meta": {"totalCount": cache["total"], "updated_at": cache["updated_at"]}})

@app.route("/tasks")
def tasks():
    return jsonify({"data": tasks_cache["data"], "total": len(tasks_cache["data"]), "updated_at": tasks_cache["updated_at"]})

@app.route("/funnels")
def funnels():
    r = requests.get(f"{AGENDOR_BASE}/funnels", headers=HEADERS, timeout=30)
    return jsonify(r.json())

autentique_cache = {"data": [], "updated_at": None}

def fetch_autentique_account(token):
    docs = []
    page = 1
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    while True:
        query = """
        query ($page: Int!) {
          documents(page: $page, limit: 60) {
            total
            data {
              id
              name
              created_at
              author { name email }
              signatures {
                name
                email
                type
                signed { created_at }
                rejected { created_at }
              }
            }
          }
        }
        """
        try:
            r = requests.post(AUTENTIQUE_BASE, json={"query": query, "variables": {"page": page}}, headers=headers, timeout=30)
            data = r.json()
            if data.get("errors"):
                print(f"Autentique erros token ...{token[-6:]}: {data['errors']}", flush=True)
                break
            page_docs = data.get("data", {}).get("documents", {}).get("data", [])
            total = data.get("data", {}).get("documents", {}).get("total", 0)
            docs.extend(page_docs)
            if len(docs) >= total or not page_docs:
                break
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"Erro Autentique token ...{token[-6:]} p{page}: {e}", flush=True)
            break
    print(f"Autentique token ...{token[-6:]}: {len(docs)} docs", flush=True)
    return docs

def fetch_autentique_all():
    print("Buscando documentos do Autentique (3 contas)...", flush=True)
    tokens = [AUTENTIQUE_TOKEN, AUTENTIQUE_TOKEN_EVERTON, AUTENTIQUE_TOKEN_GIOVANNA, AUTENTIQUE_TOKEN_LUIZ, AUTENTIQUE_TOKEN_BRENDA]
    seen_ids = set()
    all_docs = []
    for token in tokens:
        for doc in fetch_autentique_account(token):
            if doc["id"] not in seen_ids:
                seen_ids.add(doc["id"])
                all_docs.append(doc)
    autentique_cache["data"] = all_docs
    autentique_cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"Autentique total mesclado: {len(all_docs)} documentos.", flush=True)

@app.route("/autentique")
def autentique():
    if not autentique_cache["data"]:
        fetch_autentique_all()
    return jsonify({"data": autentique_cache["data"], "total": len(autentique_cache["data"]), "updated_at": autentique_cache["updated_at"]})

@app.route("/autentique/debug")
def autentique_debug():
    headers = {"Authorization": f"Bearer {AUTENTIQUE_TOKEN}", "Content-Type": "application/json"}
    query = """
    {
      documents(page: 1, limit: 60) {
        data {
          id
          name
          created_at
          deleted_at
          lifecycle_in
          signatures { email signed { created_at } rejected { created_at } }
        }
      }
    }
    """
    try:
        r = requests.post(AUTENTIQUE_BASE, json={"query": query}, headers=headers, timeout=30)
        data = r.json()
        docs = (data.get("data") or {}).get("documents", {}).get("data", [])
        especiais = [d for d in docs if d.get("deleted_at") or d.get("lifecycle_in")]
        # Também retorna o LITIO de maio para ver campos
        litio = next((d for d in docs if "LITIO" in d.get("name","") and "2026-05" in d.get("created_at","")), None)
        return jsonify({"total": len(docs), "especiais": especiais[:3], "litio_maio": litio, "errors": data.get("errors")})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/autentique/refresh", methods=["POST"])
def autentique_refresh():
    threading.Thread(target=fetch_autentique_all, daemon=True).start()
    return jsonify({"status": "ok"})

@app.route("/history-cache")
def history_cache_route():
    return jsonify({
        "data": history_cache["data"], "total": len(history_cache["data"]),
        "updated_at": history_cache["updated_at"], "processing": history_running,
        "processed": history_cache["total_processed"], "target": history_cache["total_target"]
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
def send_agendorchat_message(conversation_id: int, text: str):
    """Envia resposta do Luca de volta ao lead via API do AgendorChat."""
    url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
    resp = requests.post(
        url,
        headers={
            "api_access_token": AGENDORCHAT_TOKEN,
            "Content-Type":     "application/json",
        },
        json={"content": text, "message_type": "outgoing", "private": False},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def toggle_typing(inbox_identifier: str, contact_identifier: str, conversation_id: int, status: str = "on"):
    """Ativa ou desativa o indicador 'digitando...' no AgendorChat."""
    url = (
        f"https://chat.agendor.com.br/public/api/v1/inboxes/{inbox_identifier}"
        f"/contacts/{contact_identifier}/conversations/{conversation_id}/toggle_typing"
    )
    try:
        requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"typing_status": status},
            timeout=5,
        )
    except Exception as e:
        print(f"[typing] Erro: {e}", flush=True)


def send_private_note(conversation_id: int, text: str):
    """Cria ou atualiza nota interna visível apenas para agentes."""
    url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
    resp = requests.post(
        url,
        headers={
            "api_access_token": AGENDORCHAT_TOKEN,
            "Content-Type":     "application/json",
        },
        json={"content": text, "message_type": "outgoing", "private": True},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_conversation_details(conversation_id: int) -> dict:
    """Busca status e assignee atuais de uma conversa no AgendorChat."""
    url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}"
    try:
        resp = requests.get(
            url,
            headers={"api_access_token": AGENDORCHAT_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[conv_details] Erro ao buscar conv={conversation_id}: {e}", flush=True)
        return {}


def get_last_message_info(conversation_id: int) -> dict:
    """Retorna informações da última mensagem da conversa (quem enviou, se é do lead)."""
    url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
    try:
        resp = requests.get(
            url,
            headers={"api_access_token": AGENDORCHAT_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        messages = data.get("payload", [])
        if not messages:
            return {}
        last = messages[-1]
        return {
            "content": last.get("content", ""),
            "message_type": last.get("message_type"),  # 0=incoming(lead), 1=outgoing(agente)
            "private": last.get("private", False),
        }
    except Exception as e:
        print(f"[last_msg] Erro ao buscar conv={conversation_id}: {e}", flush=True)
        return {}


def fetch_conversation_history(conversation_id: int) -> list:
    """Busca histórico de mensagens da conversa no AgendorChat e retorna no formato Claude."""
    url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
    try:
        resp = requests.get(
            url,
            headers={"api_access_token": AGENDORCHAT_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        messages = data.get("payload", [])

        history = []
        for msg in messages:
            # Ignora mensagens privadas (notas internas) e vazias
            if msg.get("private"):
                continue
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            msg_type = msg.get("message_type")
            # 0 = incoming (lead), 1 = outgoing (agente/Luca)
            if msg_type == 0:
                history.append({"role": "user", "content": content})
            elif msg_type == 1:
                history.append({"role": "assistant", "content": content})

        return history
    except Exception as e:
        print(f"[history] Erro ao buscar histórico conv={conversation_id}: {e}", flush=True)
        return []


def build_lead_note(conv_data: dict) -> str:
    """Monta o texto da nota interna com o resumo do lead."""
    nome       = conv_data.get("nome", "Não informado")
    segmento   = conv_data.get("segmento", "Não identificado")
    necessidade = conv_data.get("necessidade", "Não informada")
    email      = conv_data.get("email", "Não informado")
    preferencia = conv_data.get("preferencia", "")
    status     = conv_data.get("status", "Em atendimento")

    lines = [
        "📋 Resumo do Lead",
        f"Nome: {nome}",
        f"Segmento: {segmento}",
        f"Necessidade: {necessidade}",
        f"E-mail: {email}",
    ]
    if preferencia:
        lines.append(f"Preferência: {preferencia}")
    note = "\n".join(lines)
    note += f"Status: {status}"
    return note


def extract_lead_data(messages: list, contact_name: str) -> dict:
    """Usa o Claude para extrair dados do lead a partir do histórico."""
    if not messages:
        return {}
    
    history_text = "\n".join([
        ("Lead: " if m["role"] == "user" else "Luca: ") + m["content"]
        for m in messages[-20:]
    ])
    
    prompt = f"""Com base nessa conversa, extraia as informações do lead em JSON.
Retorne APENAS o JSON, sem texto adicional.

Conversa:
{history_text}

Retorne este JSON (deixe em branco se não informado):
{{
  "nome": "",
  "segmento": "",
  "necessidade": "",
  "email": "",
  "preferencia": "",
  "status": ""
}}

Para status use: "Em qualificação" | "Interesse confirmado" | "Aguardando e-mail" | "Preferência informada: [dia] às [horário]" | "Agendamento confirmado"
"""
    try:
        reply = call_claude(
            [{"role": "user", "content": prompt}],
            max_tokens=300,
            system="Você extrai dados estruturados de conversas. Retorne apenas JSON válido."
        )
        # Remove possíveis backticks
        reply = reply.replace("```json", "").replace("```", "").strip()
        data = json.loads(reply)
        if contact_name and not data.get("nome"):
            data["nome"] = contact_name
        return data
    except Exception as e:
        print(f"[note] Erro ao extrair dados: {e}", flush=True)
        return {"nome": contact_name}


@app.route("/agendorchat/webhook", methods=["POST", "OPTIONS"])
def agendorchat_webhook():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 200

    try:
        body = request.get_json(force=True) or {}

        # ── Log completo para debug ───────────────────────────────────────────
        event        = body.get("event", "")
        message_type = body.get("message_type", "")
        sender_type  = (body.get("sender") or {}).get("type", "")
        print(f"[webhook] RAW event={event} | message_type={message_type} | sender_type={sender_type}", flush=True)
        print(f"[webhook] RAW payload={json.dumps(body)[:600]}", flush=True)

        # Ignora tudo que não seja mensagem nova do lead
        if event != "message_created":
            print(f"[webhook] IGNORADO event={event}", flush=True)
            return jsonify({}), 200
        if message_type != "incoming":
            print(f"[webhook] IGNORADO message_type={message_type}", flush=True)
            return jsonify({}), 200

        # ── Ignora se há agente humano atribuído à conversa ───────────────────
        conversation_meta = (body.get("conversation") or {}).get("meta") or {}
        assignee = conversation_meta.get("assignee")
        if assignee and assignee.get("type") == "user":
            print(f"[webhook] IGNORADO agente humano atribuído: {assignee.get('name')}", flush=True)
            return jsonify({}), 200

        # ── Extrai campos do payload ──────────────────────────────────────────
        message_text    = (body.get("content") or "").strip()
        message_id      = body.get("id")
        conversation    = body.get("conversation") or {}
        conversation_id = conversation.get("id")
        meta_sender     = (conversation.get("meta") or {}).get("sender") or {}
        contact_name    = meta_sender.get("name", "")
        contact_phone   = meta_sender.get("phone_number", "")

        # Identificadores para Toggle Typing (API pública)
        contact_inbox      = conversation.get("contact_inbox") or {}
        inbox_identifier   = contact_inbox.get("source_id", "")
        contact_identifier = contact_inbox.get("pubsub_token", "")
        print(f"[typing] inbox_identifier={inbox_identifier} | contact_identifier={contact_identifier}", flush=True)

        if not message_text or not conversation_id:
            return jsonify({}), 200

        print(f"[webhook] conv={conversation_id} | {contact_phone} | msg={message_text[:60]}", flush=True)

        # ── Recupera ou inicializa histórico ──────────────────────────────────
        conv_key = str(conversation_id)
        if conv_key not in conversation_histories:
            extra = ""
            if contact_name:
                extra += f"\n\nINFORMAÇÃO DO CONTATO: o lead se chama {contact_name}."
            if contact_phone:
                extra += f" Telefone/WhatsApp já disponível: {contact_phone}. NUNCA peça o telefone."
            conversation_histories[conv_key] = {
                "system":    SYSTEM_PROMPT + extra,
                "messages":  [],
                "note_id":   None,
                "lead_data": {"nome": contact_name},
                "last_msg_at": time.time(),
            }

        conv = conversation_histories[conv_key]

        # ── Detecta reabertura após encerramento — reseta para novo atendimento ─
        if conv.get("was_resolved"):
            print(f"[webhook] Conversa reaberta após encerramento — resetando histórico conv={conversation_id}", flush=True)
            extra = ""
            if contact_name:
                extra += f"\n\nINFORMAÇÃO DO CONTATO: o lead se chama {contact_name}."
            if contact_phone:
                extra += f" Telefone/WhatsApp já disponível: {contact_phone}. NUNCA peça o telefone."
            conversation_histories[conv_key] = {
                "system":    SYSTEM_PROMPT + extra,
                "messages":  [],
                "note_id":   None,
                "lead_data": {"nome": contact_name},
                "last_msg_at": time.time(),
                "was_resolved": False,
            }
            conv = conversation_histories[conv_key]
            # Injeta contexto de reabertura para o Luca tratar como retorno
            conv["messages"].append({
                "role": "assistant",
                "content": f"Oi, {contact_name.split()[0] if contact_name else 'tudo bem'}! Que bom ter você de volta. Como posso te ajudar hoje?"
            })

        # ── Se memória está vazia, busca histórico real do AgendorChat ────────
        if not conv["messages"]:
            remote_history = fetch_conversation_history(conversation_id)
            if remote_history:
                print(f"[history] Recuperados {len(remote_history)} msgs da conv={conversation_id}", flush=True)
                conv["messages"] = remote_history
            else:
                # Sem histórico remoto: injeta saudação inicial
                conv["messages"].append({
                    "role":    "assistant",
                    "content": (
                        "Olá! Tudo bem? Eu sou o Luca, da Lucralize. "
                        "É um prazer falar com você! Como posso te ajudar hoje?"
                    ),
                })

        # ── Detecta retomada após longa ausência (>2h) ───────────────────────
        now = time.time()
        last_msg_at = conv.get("last_msg_at", now)
        elapsed_minutes = (now - last_msg_at) / 60
        conv["last_msg_at"] = now

        # ── Monta mensagem do lead com contexto de retomada se necessário ────
        user_content = message_text
        if elapsed_minutes > 120 and len(conv["messages"]) > 1:
            saudacao = saudacao_atual()
            retomada = (
                "[O lead ficou ausente por " + str(int(elapsed_minutes // 60)) + "h e voltou, mandando apenas uma saudação curta. "
                "Comece respondendo a saudação dele normalmente, usando \"" + saudacao + "\" (horário atual de Brasília), de forma calorosa. "
                "Depois disso, NÃO trate o resto como uma conversa nova e NÃO pergunte genericamente 'o que você precisa' ou similar. "
                "Volte exatamente ao ponto em que a conversa parou: revise as últimas mensagens acima e continue "
                "a partir da última pergunta ou pendência que ficou em aberto (ex: se você tinha perguntado o faturamento "
                "ou sugerido um dia para a reunião, repita ou retome esse mesmo ponto).]\n\n" + message_text
            )
            user_content = retomada

        # ── Adiciona mensagem do lead e chama o Claude ────────────────────────
        conv["messages"].append({"role": "user", "content": user_content})

        # ── Agrupamento de mensagens em sequência rápida ──────────────────────
        # Marca esta como a versão mais recente da conversa e espera um pouco
        # para ver se o lead manda mais mensagens antes de responder.
        msg_token = time.time()
        conv["latest_msg_token"] = msg_token
        time.sleep(2.5)

        # Se durante a espera chegou mensagem mais nova, esta requisição desiste
        # silenciosamente — a requisição da mensagem mais nova vai responder por todas.
        if conv.get("latest_msg_token") != msg_token:
            print(f"[webhook] Mensagem agrupada — outra mais recente chegou, conv={conversation_id}", flush=True)
            return jsonify({"status": "grouped"}), 200

        # Ativa "digitando..." enquanto o Claude processa
        toggle_typing(inbox_identifier, contact_identifier, conversation_id, "on")

        reply = call_claude(conv["messages"], max_tokens=300, system=conv["system"])

        # Desativa "digitando..."
        toggle_typing(inbox_identifier, contact_identifier, conversation_id, "off")

        # Salva no histórico sem o contexto de retomada (para não poluir)
        if elapsed_minutes > 120 and len(conv["messages"]) > 1:
            conv["messages"][-1] = {"role": "user", "content": message_text}

        conv["messages"].append({"role": "assistant", "content": reply})

        # Limita histórico a 40 turnos para não explodir tokens
        if len(conv["messages"]) > 40:
            conv["messages"] = conv["messages"][-40:]

        # Marca o message_id respondido — impede o conv_updated de responder de novo
        if message_id:
            conv["last_responded_msg_id"] = message_id

        # ── Envia resposta de volta ao AgendorChat ────────────────────────────
        send_agendorchat_message(conversation_id, reply)

        # ── Nota interna — dados completos ou conversa encerrada ─────────────
        try:
            lead_data = extract_lead_data(conv["messages"], contact_name)
            if lead_data:
                conv["lead_data"].update({k: v for k, v in lead_data.items() if v})
                d = conv["lead_data"]

                dados_completos = (
                    d.get("nome") and d.get("nome") != "Não informado"
                    and d.get("segmento") and d.get("segmento") != "Não identificado"
                    and d.get("necessidade") and d.get("necessidade") != "Não informada"
                    and d.get("email") and d.get("email") != "Não informado"
                )

                # Detecta encerramento por acompanhamento
                termos_encerramento = ["acompanhamento", "sinal verde", "é só me avisar", "estou por aqui"]
                conversa_encerrada = any(t in reply.lower() for t in termos_encerramento)

                if (dados_completos or conversa_encerrada) and not conv.get("note_sent"):
                    note_text = build_lead_note(d)
                    send_private_note(conversation_id, note_text)
                    conv["note_sent"] = True
                    print(f"[note] Nota enviada conv={conversation_id} | completo={dados_completos} | encerrado={conversa_encerrada}", flush=True)
        except Exception as e:
            print(f"[note] Erro ao processar nota: {e}", flush=True)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[webhook] Erro: {e}", flush=True)
        return jsonify({"status": "error", "detail": str(e)}), 200


# ═════════════════════════════════════════════════════════════════════════════
# NOVA ROTA — /agendar  (criar reunião Teams via Graph API)
# ═════════════════════════════════════════════════════════════════════════════
#
# Payload:
# {
#   "lead_name":  "João Silva",
#   "lead_email": "joao@email.com",
#   "start":      "2025-07-10T14:00:00"   ← horário de Brasília
# }
#
# ATENÇÃO: requer permissão Calendars.ReadWrite no Azure AD (app-only).
# Enquanto a permissão não for concedida pelo administrador, esta rota
# retornará 503. Não há impacto nas demais rotas.

# ═════════════════════════════════════════════════════════════════════════════
# ROTA — /agendorchat/conversation-updated
# Detecta quando uma conversa é desatribuída SEM ser resolvida, e verifica
# se há mensagem do lead pendente de resposta. Se sim, o Luca assume e responde.
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/agendorchat/conversation-updated", methods=["POST", "OPTIONS"])
def agendorchat_conversation_updated():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 200

    try:
        body = request.get_json(force=True) or {}
        event = body.get("event", "")

        if event != "conversation_updated":
            return jsonify({}), 200

        conversation = body.get("conversation") or body  # alguns payloads vêm no nível raiz
        conversation_id = conversation.get("id")
        status = conversation.get("status", "")
        assignee = (conversation.get("meta") or {}).get("assignee")

        if not conversation_id:
            return jsonify({}), 200

        print(f"[conv_updated] conv={conversation_id} | status={status} | assignee={assignee}", flush=True)

        # Se foi resolvida, marca no histórico para detectar reabertura depois
        if status == "resolved":
            conv_key = str(conversation_id)
            if conv_key in conversation_histories:
                conversation_histories[conv_key]["was_resolved"] = True
            print(f"[conv_updated] IGNORADO — conversa resolvida", flush=True)
            return jsonify({}), 200

        # Se ainda está atribuída a alguém, não faz nada
        if assignee:
            print(f"[conv_updated] IGNORADO — ainda atribuída a {assignee.get('name')}", flush=True)
            return jsonify({}), 200

        # Conversa ficou sem atribuição e não foi resolvida — verifica se há mensagem pendente
        last_msg = get_last_message_info(conversation_id)
        if not last_msg:
            return jsonify({}), 200

        # message_type 0 = incoming (lead) — se a última mensagem é do lead e não é privada,
        # significa que ela ficou sem resposta
        if last_msg.get("message_type") == 0 and not last_msg.get("private"):
            conv_key = str(conversation_id)
            last_msg_id = last_msg.get("id")
            last_msg_content = last_msg.get("content", "")

            # Evita responder múltiplas vezes à mesma mensagem — usa message_id como chave
            # mais confiável que o conteúdo (que pode se repetir ou ser zerado por restart)
            existing_conv = conversation_histories.get(conv_key, {})
            if existing_conv.get("last_responded_msg_id") == last_msg_id:
                print(f"[conv_updated] IGNORADO — já respondeu a esta mensagem pendente, conv={conversation_id}", flush=True)
                return jsonify({}), 200

            print(f"[conv_updated] Mensagem pendente detectada — aguardando message_created conv={conversation_id}", flush=True)

            # Aguarda 5s para dar tempo ao message_created de chegar e processar primeiro.
            # Se message_created responder nesse intervalo, last_responded_msg_id será setado
            # e o conv_updated vai ignorar corretamente.
            time.sleep(5)

            # Verifica novamente após o sleep — se message_created já respondeu, cancela
            existing_conv = conversation_histories.get(conv_key, {})
            if existing_conv.get("last_responded_msg_id") == last_msg_id:
                print(f"[conv_updated] IGNORADO — message_created respondeu durante o sleep, conv={conversation_id}", flush=True)
                return jsonify({}), 200

            print(f"[conv_updated] Luca vai responder via conv_updated conv={conversation_id}", flush=True)

            conv_details = get_conversation_details(conversation_id)
            meta_sender = ((conv_details.get("meta") or {}).get("sender")) or {}
            contact_name = meta_sender.get("name", "")
            contact_phone = meta_sender.get("phone_number", "")

            conv_key = str(conversation_id)
            if conv_key not in conversation_histories:
                extra = ""
                if contact_name:
                    extra += f"\n\nINFORMAÇÃO DO CONTATO: o lead se chama {contact_name}."
                if contact_phone:
                    extra += f" Telefone/WhatsApp já disponível: {contact_phone}. NUNCA peça o telefone."
                conversation_histories[conv_key] = {
                    "system":    SYSTEM_PROMPT + extra,
                    "messages":  [],
                    "note_id":   None,
                    "lead_data": {"nome": contact_name},
                    "last_msg_at": time.time(),
                }

            conv = conversation_histories[conv_key]

            # Busca histórico remoto para responder com contexto completo
            remote_history = fetch_conversation_history(conversation_id)
            if remote_history:
                conv["messages"] = remote_history

            if conv["messages"]:
                # Marca a última mensagem do lead como pendente de resposta direta,
                # evitando que o Claude apenas continue um padrão anterior (ex: despedida)
                messages_for_call = list(conv["messages"])
                if messages_for_call and messages_for_call[-1]["role"] == "user":
                    last_content = messages_for_call[-1]["content"]
                    saudacao = saudacao_atual()
                    messages_for_call[-1] = {
                        "role": "user",
                        "content": (
                            "[Esta é a mensagem mais recente do lead, ainda sem resposta. "
                            "Se for apenas uma saudação curta (oi, olá, etc.), responda com \"" + saudacao + "\" "
                            "de forma calorosa antes de continuar. "
                            "Responda diretamente a ela, considerando todo o histórico acima, "
                            "sem repetir despedidas ou frases de encerramento anteriores.]\n\n"
                            + last_content
                        ),
                    }

                reply = call_claude(messages_for_call, max_tokens=300, system=conv["system"])
                conv["messages"].append({"role": "assistant", "content": reply})
                send_agendorchat_message(conversation_id, reply)
                conv["last_responded_msg_id"] = last_msg_id
                print(f"[conv_updated] Luca respondeu conv={conversation_id}", flush=True)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[conv_updated] Erro: {e}", flush=True)
        return jsonify({"status": "error", "detail": str(e)}), 200


@app.route("/agendar", methods=["POST", "OPTIONS"])
def agendar():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 200

    try:
        body       = request.get_json(force=True) or {}
        lead_name  = body.get("lead_name", "Lead")
        lead_email = body.get("lead_email", "")
        start      = body.get("start", "")

        if not lead_email or not start:
            return jsonify({"error": "lead_email e start são obrigatórios"}), 400

        result = create_teams_meeting(lead_name, lead_email, start)
        return jsonify(result), 200

    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 0
        detail = ""
        try:
            detail = e.response.json().get("error", {}).get("message", "")
        except Exception:
            pass
        if status == 403:
            return jsonify({
                "error": "Permissão Calendars.ReadWrite ainda não concedida no Azure AD.",
                "detail": detail,
                "action": "Solicite ao administrador do tenant que conceda a permissão e faça grant de admin consent."
            }), 503
        return jsonify({"error": str(e), "detail": detail}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULER + MAIN
# ═════════════════════════════════════════════════════════════════════════════

scheduler = BackgroundScheduler()
scheduler.add_job(fetch_deals_safe, "interval", hours=1, id="fetch_recorrente")
scheduler.add_job(fetch_tasks_job, "interval", hours=2, id="tasks_recorrente")
scheduler.add_job(fetch_deals_safe, "date", run_date=datetime.now() + timedelta(seconds=5), id="fetch_inicial")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
