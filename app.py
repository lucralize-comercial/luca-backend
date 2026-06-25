from flask import Flask, jsonify, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import requests
import os
import time
import json

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type"]}})

# ── Agendor ──────────────────────────────────────────────────────────────────
AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN", "a89b0def-fd5e-45ed-981f-efe89f20159a")
AGENDOR_BASE  = "https://api.agendor.com.br/v3"
HEADERS       = {"Authorization": f"Token {AGENDOR_TOKEN}"}

# ── Anthropic ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Azure AD / Microsoft Graph (agendamento Teams) ────────────────────────────
AZURE_CLIENT_ID     = os.environ.get("AZURE_CLIENT_ID",     "c0868f3b-764c-4c5b-a9fc-4af4b6eb0baf")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "PBL8Q~pfG-XmBkvvmv5K~NgY-pLxpWlbayUE5aOb")
AZURE_TENANT_ID     = os.environ.get("AZURE_TENANT_ID",     "5173aa83-66e1-49f3-9128-f2251b43294d")
CALENDAR_USER       = os.environ.get("CALENDAR_USER",       "ronaldojunior@lucralize.com.br")

# ── System prompt do Luca ─────────────────────────────────────────────────────
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

2. SEGMENTO: Com o nome, pergunte: "Para te direcionar ao time certo — seu negócio é da área de tecnologia ou de outro setor?"

3. POSICIONAMENTO: Conecte ao segmento do lead e à necessidade que ele trouxe. Para devs: "A Lucralize Tech foi feita pra isso — é contabilidade exclusiva para desenvolvedores, a gente entende o seu mundo." Para outros: apresente a Lucralize Contabilidade com os diferenciais do setor.

4. QUALIFICAÇÃO RÁPIDA: Faça no máximo 1 pergunta para entender melhor a situação — empresa aberta ou não, faturamento aproximado, contador atual. Use isso para personalizar o gancho de agendamento.

5. GANCHO PARA AGENDAMENTO: Assim que tiver entendido a necessidade, proponha a reunião de forma natural:
"O melhor caminho é uma conversa rápida com nosso especialista — são só 20 minutinhos e ele já te mostra o que faz sentido pro seu perfil. Qual o melhor dia pra você?"
Não resolva o problema todo pelo chat. Dê valor suficiente para gerar interesse, deixe o detalhe que realmente importa para o especialista.

6. DÚVIDAS TÉCNICAS: Valorize e use como gancho: "Essa é exatamente a conversa que nosso especialista adora ter — ele vai te mostrar o caminho certo pra isso. Quer marcar?"
Se o lead perguntar sobre tributação ou quanto pagaria de imposto, sugira a calculadora: lucralize.com.br/calculadora-dev — e já emende o convite para reunião.

7. COLETA DE DADOS: Quando o lead aceitar agendar, colete em ordem:
- E-mail: "Me passa seu e-mail para o consultor confirmar?"
- WhatsApp: "Posso usar esse número aqui para o contato?" (NUNCA peça telefone — ele já está disponível)

8. HORÁRIO: "Qual o melhor dia e horário? Atendemos seg a qui das 9h às 17h e sex das 9h às 16h30. São só 20 minutinhos!"
Se sugerir horário fora desses, redirecione gentilmente.

9. ENCERRAMENTO: "Perfeito! Anotei sua preferência para [dia] às [horário]. Nosso consultor confirma pelo WhatsApp em breve. Qualquer dúvida, estou por aqui!"

RESISTÊNCIAS COMUNS:
- "Quero falar com um atendente": "Claro! O consultor especializado é exatamente quem vai te atender — vamos marcar essa conversa?"
- "Quanto custa?": "O valor depende do seu perfil — o especialista te mostra isso na conversa, junto com o que faz mais sentido pra você. Qual o melhor dia?"
- "Me manda mais informações": "Posso te contar o básico aqui, mas o que vai realmente fazer diferença é a conversa com o especialista — ele adapta tudo pro seu caso. Tem 20 minutos essa semana?"
- "Vou pensar": "Claro, sem pressa! Só deixa eu já reservar um horário pra você — se não der, é só avisar. Qual dia costuma ser melhor?"

SE PERGUNTAREM SE VOCÊ É IA OU ROBÔ:
"Faço parte do time comercial da Lucralize. Mas me conta, como posso te ajudar?"

REGRAS INEGOCIÁVEIS:
- NUNCA escreva "[nome]" ou texto entre colchetes — use o nome real ou não use
- NUNCA informe preços ou valores
- NUNCA invente informações
- NUNCA deixe a conversa morrer — sempre termine com pergunta ou próximo passo
- Máximo 4 linhas por mensagem
- Texto puro, sem asteriscos, sem markdown
- Responda apenas em português brasileiro"""

# ── Cache de negócios Agendor ─────────────────────────────────────────────────
cache = {"deals": [], "total": 0, "updated_at": None}

# ── Histórico de conversas por conversa_id (em memória) ──────────────────────
# Estrutura: { conversation_id: [{"role": "user"|"assistant", "content": "..."}] }
conversation_histories = {}


# ═════════════════════════════════════════════════════════════════════════════
# AGENDOR — busca de negócios
# ═════════════════════════════════════════════════════════════════════════════

def fetch_page(page):
    for attempt in range(3):
        try:
            r = requests.get(
                f"{AGENDOR_BASE}/deals",
                headers=HEADERS,
                params={"per_page": 100, "page": page, "withCustomFields": "true",
                        "order_by": "updatedAt", "order_dir": "desc"},
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
    all_deals, page, total_count = [], 1, None
    while True:
        data = fetch_page(page)
        if data is None:
            break
        page_deals = data.get("data", [])
        if total_count is None:
            total_count = data.get("meta", {}).get("totalCount", 0)
        all_deals.extend(page_deals)
        if page % 10 == 0:
            cache.update({"deals": list(all_deals), "total": total_count or len(all_deals),
                          "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        next_link = data.get("links", {}).get("next")
        if not next_link or not page_deals:
            break
        page += 1
        time.sleep(0.2)

    cache.update({"deals": all_deals, "total": total_count or len(all_deals),
                  "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    cutoff = datetime.utcnow() - timedelta(days=180)
    for deal in all_deals:
        if (deal.get("dealStatus", {}).get("id") == 2
                and deal.get("wonAt")
                and datetime.strptime(deal["wonAt"][:10], "%Y-%m-%d") > cutoff):
            try:
                r = requests.get(f"{AGENDOR_BASE}/deals/{deal['id']}/products",
                                 headers=HEADERS, timeout=15)
                if r.status_code == 200:
                    products = r.json().get("data", [])
                    if products:
                        deal["products_entities"] = products
            except Exception as e:
                print(f"Erro produtos {deal['id']}: {e}", flush=True)
            time.sleep(0.1)

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


# ═════════════════════════════════════════════════════════════════════════════
# MICROSOFT GRAPH — agendamento de reunião Teams
# ═════════════════════════════════════════════════════════════════════════════

def get_graph_token():
    """Obtém access token via client credentials (app-only)."""
    url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
    resp = requests.post(url, data={
        "grant_type":    "client_credentials",
        "client_id":     AZURE_CLIENT_ID,
        "client_secret": AZURE_CLIENT_SECRET,
        "scope":         "https://graph.microsoft.com/.default",
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


def create_teams_meeting(lead_name: str, lead_email: str, start_iso: str, duration_minutes: int = 30):
    """
    Cria uma reunião Teams no calendário do consultor e retorna o joinUrl.
    start_iso: "2025-07-10T14:00:00" (horário de Brasília — será tratado como -03:00)
    """
    token = get_graph_token()
    start_dt = datetime.fromisoformat(start_iso)
    end_dt   = start_dt + timedelta(minutes=duration_minutes)

    body = {
        "subject": f"Consultoria Lucralize — {lead_name}",
        "body": {
            "contentType": "HTML",
            "content": (
                f"<p>Reunião agendada via Luca (agente comercial Lucralize).</p>"
                f"<p>Lead: <b>{lead_name}</b> | {lead_email}</p>"
            )
        },
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Sao_Paulo"},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "America/Sao_Paulo"},
        "attendees": [
            {
                "emailAddress": {"address": lead_email, "name": lead_name},
                "type": "required"
            }
        ],
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
        "allowNewTimeProposals": False,
    }

    url = f"https://graph.microsoft.com/v1.0/users/{CALENDAR_USER}/events"
    resp = requests.post(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }, json=body, timeout=20)
    resp.raise_for_status()
    event = resp.json()
    return {
        "event_id":  event["id"],
        "join_url":  event.get("onlineMeeting", {}).get("joinUrl", ""),
        "start":     event["start"]["dateTime"],
        "end":       event["end"]["dateTime"],
    }


# ═════════════════════════════════════════════════════════════════════════════
# ANTHROPIC — chamada ao Claude
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# ROTAS EXISTENTES
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return jsonify({
        "status": "ok",
        "cached_deals": len(cache["deals"]),
        "updated_at": cache["updated_at"],
        "fetch_running": fetch_running,
    })


@app.route("/refresh", methods=["POST"])
def refresh():
    if fetch_running:
        return jsonify({"status": "running"}), 202
    scheduler.add_job(fetch_deals_safe, "date", id="fetch_manual", replace_existing=True)
    return jsonify({"status": "started"}), 200


@app.route("/deals")
def deals():
    return jsonify({
        "data": cache["deals"],
        "meta": {"totalCount": cache["total"], "updated_at": cache["updated_at"]},
    })


@app.route("/funnels")
def funnels():
    r = requests.get(f"{AGENDOR_BASE}/funnels", headers=HEADERS, timeout=30)
    return jsonify(r.json())


# ── /chat — simulador web (mantido igual) ────────────────────────────────────
@app.route("/chat", methods=["POST", "OPTIONS"])
def chat():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 200

    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY não configurada"}), 500

    try:
        body     = request.get_json()
        messages = body.get("messages", [])
        max_tok  = body.get("max_tokens", 300)
        system   = body.get("system", SYSTEM_PROMPT)
        is_init  = body.get("is_init", False)

        if is_init:
            return jsonify({"content": [{"type": "text", "text": (
                "Olá! Tudo bem? Eu sou o Luca, da Lucralize. "
                "É um prazer falar com você! Como posso te ajudar hoje?"
            )}]}), 200

        text = call_claude(messages, max_tokens=max_tok, system=system)
        return jsonify({"content": [{"type": "text", "text": text}]}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═════════════════════════════════════════════════════════════════════════════
# NOVA ROTA — /agendorchat/webhook  (AgendorChat → Luca → AgendorChat)
# ═════════════════════════════════════════════════════════════════════════════
#
# Payload real do AgendorChat (event: message_created):
# {
#   "event": "message_created",
#   "message_type": "incoming",        ← só processar incoming (do lead)
#   "content": "Olá, quero saber...",
#   "conversation": {
#     "id": 77,
#     "meta": {
#       "sender": {
#         "name": "Lead Teste",
#         "phone_number": "+5548999999999"
#       }
#     }
#   },
#   "sender": { "type": "contact" }    ← "contact"=lead | "user"=agente
# }
#
# Resposta: envia mensagem de volta via API do AgendorChat
# POST https://chat.agendor.com.br/api/v1/accounts/825/conversations/{id}/messages

AGENDORCHAT_TOKEN      = os.environ.get("AGENDORCHAT_TOKEN", "3t9nxq9fmZLyd9SfH7JEsqK8")
AGENDORCHAT_ACCOUNT_ID = os.environ.get("AGENDORCHAT_ACCOUNT_ID", "1035")
AGENDORCHAT_BASE       = "https://chat.agendor.com.br/api/v1"


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

        # ── Extrai campos do payload ──────────────────────────────────────────
        message_text    = (body.get("content") or "").strip()
        conversation    = body.get("conversation") or {}
        conversation_id = conversation.get("id")
        meta_sender     = (conversation.get("meta") or {}).get("sender") or {}
        contact_name    = meta_sender.get("name", "")
        contact_phone   = meta_sender.get("phone_number", "")

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
                "system":   SYSTEM_PROMPT + extra,
                "messages": [],
            }

        conv = conversation_histories[conv_key]

        # ── Se é primeira mensagem, injeta saudação do Luca no histórico ─────
        # (para o Claude saber que já se apresentou)
        if not conv["messages"]:
            conv["messages"].append({
                "role":    "assistant",
                "content": (
                    "Olá! Tudo bem? Eu sou o Luca, da Lucralize. "
                    "É um prazer falar com você! Como posso te ajudar hoje?"
                ),
            })

        # ── Adiciona mensagem do lead e chama o Claude ────────────────────────
        conv["messages"].append({"role": "user", "content": message_text})
        reply = call_claude(conv["messages"], max_tokens=300, system=conv["system"])
        conv["messages"].append({"role": "assistant", "content": reply})

        # Limita histórico a 40 turnos para não explodir tokens
        if len(conv["messages"]) > 40:
            conv["messages"] = conv["messages"][-40:]

        # ── Envia resposta de volta ao AgendorChat ────────────────────────────
        send_agendorchat_message(conversation_id, reply)

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
scheduler.add_job(fetch_deals_safe, "date",
                  run_date=datetime.now() + timedelta(seconds=5), id="fetch_inicial")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
