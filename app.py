from flask import Flask, jsonify, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta, timezone
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
Próximo não significa desleixado: NUNCA use gírias informais demais como "trampo", "mano", "tipo assim", "top", "rolê", "firmeza", "massa", "show de bola". Palavras coloquiais leves como "certinho", "minutinhos", "tranquilo" estão ok. Em vez de "trampo", diga "trabalho"; em vez de "mano", use o nome da pessoa. O tom é de um consultor jovem e acessível, não de conversa entre amigos íntimos.

SOBRE A LUCRALIZE:
A Lucralize tem duas unidades:

1. LUCRALIZE TECH — contabilidade exclusiva para desenvolvedores, freelancers tech, startups e agências. 100% remoto. Diferenciais: abertura/migração de empresa com honorários gratuitos — a Lucralize não cobra pelo serviço (CNPJ em até 3 dias), endereço fiscal em BH incluso, portal de notas fiscais e invoices, atendimento via WhatsApp, regime tributário otimizado para devs, suporte a operações internacionais e isenção na exportação.

Planos (nunca informe valores): Essencial (até 15k/mês), Exclusivo (até 35k/mês), Plus (até 100k/mês).

2. LUCRALIZE CONTABILIDADE — para Comércio, Serviços, Indústria e Locação. 450 clientes ativos, R$1,6mi em redução de impostos em 2025, 15 contadores, atendimento por setor.

CUSTOS DE ABERTURA E MIGRAÇÃO — regra importante:
A gratuidade é dos HONORÁRIOS da Lucralize: a gente não cobra pelo serviço de abertura de empresa nem pela transformação do MEI. Porém existem custos de terceiros, que são do processo e não da Lucralize: taxas da Junta Comercial, Inscrição Municipal e o Certificado Digital de Pessoa Jurídica. Essas taxas variam de município para município — ninguém consegue precisar o valor exato de antemão.
- NUNCA diga que a abertura/migração "não tem custo", "não tem nenhuma taxa" ou "custo zero". Diga que a Lucralize não cobra pelo serviço.
- Se o lead perguntar sobre custos de abertura ou migração, responda no espírito de: "O serviço de abertura/migração a Lucralize não cobra nada. O que existe são as taxas dos órgãos públicos (Junta Comercial e Inscrição Municipal) e o certificado digital da empresa. Elas variam conforme o município, então o especialista te passa uma estimativa pro seu caso na conversa."
- NUNCA informe valores dessas taxas e NUNCA prometa valores exatos — o especialista passa uma ESTIMATIVA, não o valor preciso.

Se o lead mencionar jurídico: informe que temos uma assessoria parceira e encaminhe para o consultor.

SEU FLUXO — siga esta ordem, naturalmente:

1. NOME: Se não souber, pergunte logo no início: "Antes de mais nada, como eu te chamo?"

2. SEGMENTO: Com o nome, pergunte: "Para te direcionar ao time certo, me conta: seu negócio é da área de tecnologia ou de outro setor?"

3. POSICIONAMENTO: Conecte ao segmento do lead e à necessidade que ele trouxe. Para devs: "A Lucralize Tech foi feita pra isso. É contabilidade exclusiva para desenvolvedores, a gente entende o seu mundo." Para outros: apresente a Lucralize Contabilidade com os diferenciais do setor.

4. QUALIFICAÇÃO RÁPIDA: Faça no máximo 1 pergunta para entender melhor a situação — empresa aberta ou não, faturamento aproximado, contador atual. Use isso para personalizar o gancho de agendamento.

5. GANCHO PARA AGENDAMENTO: Assim que tiver entendido a necessidade, proponha a reunião de forma natural:
"O melhor caminho é uma conversa rápida com nosso especialista, são só 20 minutinhos e ele já te mostra o que faz sentido pro seu perfil. Qual o melhor dia pra você?"
Não resolva o problema todo pelo chat. Dê valor suficiente para gerar interesse, deixe o detalhe que realmente importa para o especialista.

FORMATO DA REUNIÃO: é uma videochamada pelo Microsoft Teams — o convite com o link vai por e-mail (por isso coletamos o e-mail). Não é preciso instalar nada, dá pra entrar pelo navegador ou pelo celular. NUNCA mencione Google Meet, Zoom ou ligação de WhatsApp como formato da reunião.

6. DÚVIDAS TÉCNICAS: Valorize e use como gancho: "Essa é exatamente a conversa que nosso especialista adora ter. Ele vai te mostrar o caminho certo pra isso. Quer marcar?"
Se o lead perguntar sobre tributação ou quanto pagaria de imposto, sugira a calculadora: lucralize.com.br/calculadora-dev. Já emende o convite para reunião.

7. COLETA DE DADOS: Quando o lead aceitar agendar, colete em ordem:
- E-mail: "Me passa seu e-mail para o consultor confirmar?"
- WhatsApp: "Posso usar esse número aqui para o contato?" (NUNCA peça telefone, ele já está disponível)

8. HORÁRIO: "Qual o melhor dia e horário? Atendemos seg a qui das 9h às 17h e sex das 9h às 16h30. São só 20 minutinhos!"
Horários válidos: seg a qui 09h-17h, sex 09h-16h30. Sem fins de semana.
HORÁRIO DE ALMOÇO (12h-13h): evite agendar nesse intervalo. Ao sugerir horários, NUNCA ofereça espontaneamente opções entre 12h e 13h — sugira manhã (antes das 12h) ou tarde (a partir das 13h). Se o lead disser que só consegue no almoço, primeiro tente alternativas: "E bem cedinho, tipo 9h? Ou no fim da tarde?". Somente se o lead realmente não tiver NENHUMA outra possibilidade, aceite anotar a preferência no almoço com a ressalva: "Esse horário depende de confirmação do especialista, tá? Ele te retorna confirmando ou sugerindo o mais próximo possível."
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
# Token usado para AÇÕES VISÍVEIS ao lead (enviar mensagem, "digitando...").
# Se configurado com o token do usuário "Bot", as mensagens do Luca saem em
# nome do Bot em vez do Ronaldo. Leituras e notas continuam no token principal.
LUCA_SEND_TOKEN        = os.environ.get("LUCA_SEND_TOKEN", "") or AGENDORCHAT_TOKEN
AGENDORCHAT_ACCOUNT_ID = os.environ.get("AGENDORCHAT_ACCOUNT_ID", "1035")
AGENDORCHAT_BASE       = "https://chat.agendor.com.br/api/v1"


LUCA_BOT_ASSIGNEE = os.environ.get("LUCA_BOT_ASSIGNEE", "Bot")


def eh_assignee_bot(assignee: dict) -> bool:
    """Retorna True se o agente atribuído é o usuário do bot da automação —
    conversas atribuídas a ele são território do Luca (ele responde normalmente).
    Compara pelo nome exato para não confundir com humanos (o usuário do
    Ronaldo tem available_name 'Luca', por exemplo). Configurável via env
    LUCA_BOT_ASSIGNEE."""
    if not assignee:
        return False
    return (assignee.get("name") or "").strip().lower() == LUCA_BOT_ASSIGNEE.strip().lower()


def saudacao_atual() -> str:
    """Retorna a saudação adequada com base no horário de Brasília."""
    hora_brasilia = (datetime.utcnow() - timedelta(hours=3)).hour
    if 5 <= hora_brasilia < 12:
        return "Bom dia"
    elif 12 <= hora_brasilia < 18:
        return "Boa tarde"
    else:
        return "Boa noite"


def contexto_data_atual() -> str:
    """Retorna a data/hora atual de Brasília por extenso, para o Luca não
    errar dia da semana ao propor reuniões."""
    agora = datetime.utcnow() - timedelta(hours=3)
    dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
            "sexta-feira", "sábado", "domingo"]
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
    return (f"\n\nDATA E HORA ATUAIS (horário de Brasília): {dias[agora.weekday()]}, "
            f"{agora.day} de {meses[agora.month - 1]} de {agora.year}, {agora.strftime('%H:%M')}. "
            f"Use esta informação ao falar de dias da semana, 'amanhã', prazos e horários de reunião. "
            f"Lembre-se: o atendimento é de segunda a quinta das 9h às 17h e sexta das 9h às 16h30, sem fins de semana.")


def call_claude(messages: list, max_tokens: int = 300, system: str = SYSTEM_PROMPT) -> str:
    """Chama a API Anthropic e retorna o texto da resposta."""
    system = system + contexto_data_atual()
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
    if resp.status_code != 200:
        print(f"[claude] Erro API status={resp.status_code} body={resp.text[:300]}", flush=True)
    resp.raise_for_status()
    data = resp.json()
    content = data.get("content") or []
    if not content or not content[0].get("text"):
        print(f"[claude] Resposta sem conteúdo: {json.dumps(data)[:300]}", flush=True)
        raise ValueError("Resposta da Anthropic sem conteúdo de texto")
    return content[0]["text"].strip()


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

@app.route("/agendor/deal-created", methods=["POST"])
def agendor_deal_created():
    try:
        body = request.get_json(force=True) or {}
        deal = body.get("deal") or body.get("data") or {}
        deal_id = deal.get("id")
        description = (deal.get("description") or "").strip()

        print(f"[deal-created] Negócio id={deal_id} | descrição: {description[:80]}", flush=True)

        if not deal_id:
            return jsonify({"status": "ignored", "reason": "no deal_id"}), 200

        # Se veio do RD Station, não preenche origem
        if "Criado automaticamente pela integração com RD Station" in description:
            print(f"[deal-created] IGNORADO — origem RD Station, deal={deal_id}", flush=True)
            return jsonify({"status": "ignored", "reason": "rd_station"}), 200

        # Se já tem origem preenchida, não sobrescreve
        custom = deal.get("customFields") or {}
        if custom.get("origem_do_negocio"):
            print(f"[deal-created] IGNORADO — origem já preenchida, deal={deal_id}", flush=True)
            return jsonify({"status": "ignored", "reason": "already_filled"}), 200

        # Preenche origem como whatsapp_pagina
        payload = {"customFields": {"origem_do_negocio": 59538}}
        r = requests.put(
            f"{AGENDOR_BASE}/deals/{deal_id}",
            headers={**HEADERS, "Content-Type": "application/json"},
            json=payload,
            timeout=15
        )
        print(f"[deal-created] Origem preenchida deal={deal_id} | status={r.status_code}", flush=True)
        return jsonify({"status": "ok", "deal_id": deal_id}), 200

    except Exception as e:
        print(f"[deal-created] Erro: {e}", flush=True)
        return jsonify({"status": "error"}), 200

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
          signatures {
            email
            archived_at
            signed { created_at }
            rejected { created_at }
          }
        }
      }
    }
    """
    try:
        r = requests.post(AUTENTIQUE_BASE, json={"query": query}, headers=headers, timeout=30)
        data = r.json()
        if data.get("errors"):
            return jsonify({"errors": data["errors"]})
        docs = (data.get("data") or {}).get("documents", {}).get("data", [])
        litio = next((d for d in docs if "LITIO" in d.get("name","") and "2026-05" in d.get("created_at","")), None)
        com_archived = [d for d in docs if any(s.get("archived_at") for s in d.get("signatures",[]))]
        return jsonify({"total": len(docs), "com_archived": len(com_archived), "litio_maio": litio, "exemplos_archived": com_archived[:2]})
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
            "api_access_token": LUCA_SEND_TOKEN,
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


def buscar_pessoa_e_negocio(phone):
    """Localiza a pessoa pelo telefone e o negócio mais recente dela no Agendor.
    Retorna (person, deal) ou (None, None)."""
    phone_clean = phone.replace("+", "").replace(" ", "").strip()
    r = requests.get(f"{AGENDOR_BASE}/people", headers=HEADERS,
                     params={"phone": phone_clean}, timeout=15)
    pessoas = r.json().get("data", [])
    if not pessoas:
        return None, None
    person = pessoas[0]
    r2 = requests.get(f"{AGENDOR_BASE}/deals", headers=HEADERS,
                      params={"personId": person.get("id"), "per_page": 5}, timeout=15)
    deals = r2.json().get("data", [])
    if not deals:
        return person, None
    deal = sorted(deals, key=lambda d: d.get("createdAt", ""), reverse=True)[0]
    return person, deal


_campo_agendada_por_cache = None

def resolver_campo_agendada_por():
    """Descobre a chave do campo personalizado 'Reunião agendada por' e o ID
    da opção 'Luca', consultando /custom_fields/deals. Cacheia em memória."""
    global _campo_agendada_por_cache
    if _campo_agendada_por_cache is not None:
        return _campo_agendada_por_cache
    try:
        r = requests.get(f"{AGENDOR_BASE}/custom_fields/deals", headers=HEADERS, timeout=15)
        campos = r.json().get("data", [])
        for campo in campos:
            nome = (campo.get("name") or "").lower()
            if "agendada por" in nome:
                chave = campo.get("key") or campo.get("slug")
                opcao_luca = None
                for opt in (campo.get("options") or campo.get("values") or []):
                    if (opt.get("name") or opt.get("value") or "").strip().lower() == "luca":
                        opcao_luca = opt.get("id")
                        break
                _campo_agendada_por_cache = {"key": chave, "luca_id": opcao_luca}
                print(f"[crm] Campo 'agendada por' resolvido: key={chave} luca_id={opcao_luca}", flush=True)
                return _campo_agendada_por_cache
        print("[crm] Campo 'Reunião agendada por' não encontrado em /custom_fields/deals", flush=True)
    except Exception as e:
        print(f"[crm] Erro ao resolver campo agendada_por: {e}", flush=True)
    _campo_agendada_por_cache = {}
    return _campo_agendada_por_cache


def parse_preferencia_datetime(preferencia: str):
    """Converte a preferência do lead ('terça às 12h10') em ISO usando o Claude,
    que já recebe a data atual de Brasília no system. Retorna ISO ou None."""
    if not preferencia or not preferencia.strip():
        return None
    try:
        prompt = (
            "Converta a preferência de reunião abaixo para data e hora futuras no formato "
            "ISO exato AAAA-MM-DDTHH:MM (ex: 2026-07-15T10:00), usando a data atual "
            "informada no sistema como referência. Se a preferência não tiver informação "
            "suficiente para determinar data e hora, responda apenas INDEFINIDA.\n"
            "Responda APENAS o ISO ou INDEFINIDA, nada mais.\n\n"
            f"Preferência: {preferencia}"
        )
        resp = call_claude([{"role": "user", "content": prompt}], max_tokens=30).strip()
        if "INDEFINIDA" in resp.upper():
            return None
        datetime.strptime(resp[:16], "%Y-%m-%dT%H:%M")
        return resp[:16]
    except Exception as e:
        print(f"[crm] Preferência não convertida ('{preferencia}'): {e}", flush=True)
        return None


def registrar_no_crm(conv, conversation_id, contact_name):
    """Fecha o ciclo no Agendor quando a qualificação conclui:
    1. Nota com o resumo do lead
    2. Registro WhatsApp com a transcrição da conversa
    3. Reunião [Luca] atribuída ao dono do negócio (se houver preferência)
    4. Campo personalizado 'Reunião agendada por' = Luca"""
    try:
        if conv.get("crm_registrado"):
            return
        phone = conv.get("phone", "")
        if not phone:
            print(f"[crm] Sem telefone na conversa {conversation_id} — registro pulado", flush=True)
            return
        person, deal = buscar_pessoa_e_negocio(phone)
        if not deal:
            print(f"[crm] Negócio não encontrado para {phone} conv={conversation_id}", flush=True)
            return
        deal_id = deal.get("id")
        d = conv.get("lead_data", {})

        # ── 1. Nota: resumo do lead ──────────────────────────────────────────
        nota = (
            "📋 Atendimento via Luca (WhatsApp)\n"
            f"Nome: {d.get('nome') or contact_name}\n"
            f"Segmento: {d.get('segmento', '')}\n"
            f"Necessidade: {d.get('necessidade', '')}\n"
            f"E-mail: {d.get('email', '')}\n"
            f"Preferência de reunião: {d.get('preferencia', '')}\n"
            f"Status: {d.get('status', '')}"
        )
        r1 = requests.post(f"{AGENDOR_BASE}/deals/{deal_id}/tasks",
                           headers={**HEADERS, "Content-Type": "application/json"},
                           json={"text": nota}, timeout=15)
        print(f"[crm] Nota resumo deal={deal_id} status={r1.status_code}", flush=True)

        # ── 2. Registro WhatsApp: transcrição compacta ───────────────────────
        linhas = []
        for m in conv.get("messages", []):
            papel = "Lead" if m["role"] == "user" else "Luca"
            texto = m["content"]
            # Remove instruções internas injetadas entre colchetes no início
            if texto.startswith("["):
                fim = texto.find("]\n\n")
                if fim != -1:
                    texto = texto[fim + 3:]
            linhas.append(f"{papel}: {texto}")
        transcricao = "💬 Conversa via Luca (WhatsApp):\n\n" + "\n\n".join(linhas)
        blocos = [transcricao[i:i + 9000] for i in range(0, len(transcricao), 9000)]
        for idx, bloco in enumerate(blocos):
            sufixo = f" (parte {idx+1}/{len(blocos)})" if len(blocos) > 1 else ""
            r2 = requests.post(f"{AGENDOR_BASE}/deals/{deal_id}/tasks",
                               headers={**HEADERS, "Content-Type": "application/json"},
                               json={"text": bloco + sufixo, "type": "whatsapp"}, timeout=15)
            print(f"[crm] Transcrição{sufixo} deal={deal_id} status={r2.status_code}", flush=True)

        # ── 3. Reunião [Luca] — somente se há preferência de horário ─────────
        preferencia = (d.get("preferencia") or "").strip()
        if preferencia:
            owner_id = (deal.get("owner") or {}).get("id")
            dt_iso = parse_preferencia_datetime(preferencia)
            if dt_iso:
                texto_reuniao = ("[Luca] Reunião com especialista — pré-agendada pelo Luca via WhatsApp, "
                                 f"aguardando confirmação do consultor. Preferência do lead: {preferencia}")
                due = dt_iso
            else:
                prox = datetime.utcnow() - timedelta(hours=3) + timedelta(days=1)
                while prox.weekday() >= 5:
                    prox += timedelta(days=1)
                due = prox.strftime("%Y-%m-%dT09:00")
                texto_reuniao = ("[Luca] Reunião com especialista — HORÁRIO A CONFIRMAR com o lead. "
                                 f"Preferência informada: {preferencia}")
            payload_reuniao = {"text": texto_reuniao, "type": "meeting", "dueDate": due}
            if owner_id:
                payload_reuniao["assignedUsers"] = [owner_id]
            r3 = requests.post(f"{AGENDOR_BASE}/deals/{deal_id}/tasks",
                               headers={**HEADERS, "Content-Type": "application/json"},
                               json=payload_reuniao, timeout=15)
            print(f"[crm] Reunião [Luca] deal={deal_id} due={due} status={r3.status_code} body={r3.text[:200]}", flush=True)

            # ── 4. Campo personalizado 'Reunião agendada por' = Luca ─────────
            campo = resolver_campo_agendada_por()
            if campo.get("key") and campo.get("luca_id"):
                r4 = requests.put(f"{AGENDOR_BASE}/deals/{deal_id}",
                                  headers={**HEADERS, "Content-Type": "application/json"},
                                  json={"customFields": {campo["key"]: campo["luca_id"]}}, timeout=15)
                print(f"[crm] Campo agendada_por=Luca deal={deal_id} status={r4.status_code}", flush=True)

        conv["crm_registrado"] = True
        print(f"[crm] ✅ Ciclo registrado no CRM deal={deal_id} conv={conversation_id}", flush=True)
    except Exception as e:
        print(f"[crm] Erro ao registrar conv={conversation_id}: {e}", flush=True)


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
        # A API não garante ordem cronológica — ordena por id (crescente)
        messages = sorted(messages, key=lambda m: m.get("id") or 0)
        last = messages[-1]
        return {
            "id":      last.get("id"),
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
        # A API não garante ordem cronológica — ordena por id (crescente)
        messages = sorted(messages, key=lambda m: m.get("id") or 0)

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
                role = "user"
            elif msg_type == 1:
                role = "assistant"
            else:
                continue
            # Mescla turnos consecutivos do mesmo papel — a API da Anthropic
            # exige alternância user/assistant (leads costumam mandar várias
            # mensagens seguidas)
            if history and history[-1]["role"] == role:
                history[-1]["content"] += "\n\n" + content
            else:
                history.append({"role": role, "content": content})

        # A API da Anthropic exige que a primeira mensagem seja do user —
        # descarta turnos iniciais do assistant (ex: template disparado antes
        # da primeira mensagem do lead)
        while history and history[0]["role"] == "assistant":
            history.pop(0)

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


def preencher_origem_whatsapp_pagina(phone):
    """Busca o negócio mais recente pelo telefone e preenche origem=whatsapp_pagina se descrição vazia."""
    try:
        # Aguarda 10s para garantir que o negócio já foi criado no Agendor
        time.sleep(10)
        # Normaliza telefone — remove +, espaços
        phone_clean = phone.replace("+", "").replace(" ", "").strip()
        # Busca pessoa pelo telefone
        r = requests.get(f"{AGENDOR_BASE}/people", headers=HEADERS,
                         params={"phone": phone_clean}, timeout=15)
        pessoas = r.json().get("data", [])
        if not pessoas:
            print(f"[origem] Pessoa não encontrada para telefone {phone_clean}", flush=True)
            return
        person_id = pessoas[0].get("id")
        # Busca negócios da pessoa, ordenados por criação desc
        r2 = requests.get(f"{AGENDOR_BASE}/deals", headers=HEADERS,
                          params={"personId": person_id, "per_page": 5}, timeout=15)
        deals = r2.json().get("data", [])
        if not deals:
            print(f"[origem] Nenhum negócio encontrado para person_id={person_id}", flush=True)
            return
        # Pega o negócio mais recente
        deal = sorted(deals, key=lambda d: d.get("createdAt",""), reverse=True)[0]
        deal_id = deal.get("id")
        description = (deal.get("description") or "").strip()
        # Só preenche se descrição vazia
        if description:
            print(f"[origem] IGNORADO — descrição não vazia deal={deal_id}: {description[:60]}", flush=True)
            return
        # Verifica se origem já preenchida
        custom = deal.get("customFields") or {}
        if custom.get("origem_do_negocio"):
            print(f"[origem] IGNORADO — origem já preenchida deal={deal_id}", flush=True)
            return
        # Preenche origem
        r3 = requests.put(
            f"{AGENDOR_BASE}/deals/{deal_id}",
            headers={**HEADERS, "Content-Type": "application/json"},
            json={"customFields": {"origem_do_negocio": 59538}},
            timeout=15
        )
        print(f"[origem] whatsapp_pagina preenchida deal={deal_id} | status={r3.status_code}", flush=True)
    except Exception as e:
        print(f"[origem] Erro: {e}", flush=True)

def conta_respostas_apos(conversation_id: int, incoming_msg_id) -> int:
    """Conta quantas respostas (outgoing não-privadas) existem depois da mensagem
    do lead, consultando a própria API do AgendorChat. Proteção cross-worker/
    cross-instância contra duplicatas — funciona mesmo com processos de memórias
    isoladas (ex: janela de deploy com dois containers vivos)."""
    try:
        url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
        resp = requests.get(url, headers={"api_access_token": AGENDORCHAT_TOKEN}, timeout=15)
        resp.raise_for_status()
        messages = resp.json().get("payload", [])
        # A API não garante ordem cronológica — ordena por id (crescente)
        messages = sorted(messages, key=lambda m: m.get("id") or 0)
        achou_incoming = False
        count = 0
        for m in messages:
            if m.get("id") == incoming_msg_id:
                achou_incoming = True
                continue
            if achou_incoming and m.get("message_type") == 1 and not m.get("private"):
                count += 1
        return count
    except Exception as e:
        print(f"[dedup-api] Erro ao verificar conv={conversation_id}: {e}", flush=True)
        return 0


def _processar_resposta_luca(conv_key, conversation_id, msg_token, message_id,
                             is_first_message, retomada_ctx, message_text, contact_name,
                             inbox_identifier, contact_identifier, delay):
    """Processa a resposta do Luca em background, fora do ciclo da request.

    O webhook responde 200 imediatamente e esta thread faz a espera (90s na
    primeira mensagem / 2.5s de agrupamento), a chamada ao Claude e o envio.
    Assim o worker único do Gunicorn nunca fica bloqueado nem estoura o
    timeout de 120s. As threads compartilham o mesmo conversation_histories,
    então o agrupamento por latest_msg_token continua funcionando."""
    try:
        time.sleep(delay)

        conv = conversation_histories.get(conv_key)
        if not conv:
            print(f"[luca-bg] Histórico não encontrado conv={conversation_id}", flush=True)
            return

        # Se durante a espera chegou mensagem mais nova, esta thread desiste
        # silenciosamente — a thread da mensagem mais nova responde por todas.
        if conv.get("latest_msg_token") != msg_token:
            print(f"[luca-bg] Mensagem agrupada — outra mais recente chegou, conv={conversation_id}", flush=True)
            return

        if is_first_message:
            # Após o delay, busca histórico atualizado para incluir o template
            remote_history = fetch_conversation_history(conversation_id)
            if remote_history:
                conv["messages"] = remote_history
                print(f"[history] Histórico atualizado após delay: {len(remote_history)} msgs conv={conversation_id}", flush=True)
            # Injeta instrução para não repetir o que o template já disse
            if conv["messages"] and conv["messages"][-1]["role"] == "user":
                conv["messages"][-1]["content"] = (
                    "[ATENÇÃO: Um template de boas-vindas já foi enviado automaticamente pelo sistema antes desta resposta. "
                    "NÃO repita a saudação nem se apresente novamente. "
                    "Responda diretamente à mensagem do lead, continuando de onde o template parou.]\n\n"
                    + conv["messages"][-1]["content"]
                )
            # Reconfere agrupamento após o fetch remoto
            if conv.get("latest_msg_token") != msg_token:
                print(f"[luca-bg] Mensagem agrupada após fetch conv={conversation_id}", flush=True)
                return

        # Ativa "digitando..." enquanto o Claude processa
        toggle_typing(inbox_identifier, contact_identifier, conversation_id, "on")

        reply = call_claude(conv["messages"], max_tokens=300, system=conv["system"])

        # Desativa "digitando..."
        toggle_typing(inbox_identifier, contact_identifier, conversation_id, "off")

        # Salva no histórico sem o contexto de retomada (para não poluir)
        if retomada_ctx and conv["messages"] and conv["messages"][-1]["role"] == "user":
            conv["messages"][-1] = {"role": "user", "content": message_text}

        conv["messages"].append({"role": "assistant", "content": reply})

        # Limita histórico a 40 turnos para não explodir tokens
        if len(conv["messages"]) > 40:
            conv["messages"] = conv["messages"][-40:]

        # Marca o message_id respondido — impede o conv_updated de responder de novo
        if message_id:
            conv["last_responded_msg_id"] = message_id

        # Última checagem antes do envio: se durante o processamento chegou
        # mensagem mais nova (ou outra thread assumiu), desiste sem enviar.
        if conv.get("latest_msg_token") != msg_token:
            print(f"[luca-bg] Abortado antes do envio — thread mais recente assumiu conv={conversation_id}", flush=True)
            return

        # Checagem cross-worker: consulta a API para ver se alguém (outro worker,
        # outra instância ou um humano) já respondeu esta mensagem do lead.
        # Em mensagens normais, 1 resposta existente já bloqueia o envio.
        # Na primeira mensagem, tolera-se 1 outgoing (o template de boas-vindas
        # é esperado antes do Luca); 2 ou mais indicam duplicata.
        if message_id:
            limite = 2 if is_first_message else 1
            respostas = conta_respostas_apos(conversation_id, message_id)
            if respostas >= limite:
                print(f"[luca-bg] Abortado — {respostas} resposta(s) já existem após msg={message_id} conv={conversation_id}", flush=True)
                return

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
                    # Fecha o ciclo no CRM: nota, transcrição, reunião [Luca] e campo
                    registrar_no_crm(conv, conversation_id, contact_name)
        except Exception as e:
            print(f"[note] Erro ao processar nota: {e}", flush=True)

    except Exception as e:
        print(f"[luca-bg] Erro conv={conversation_id}: {e}", flush=True)


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
        # Exceção: o usuário do bot da automação (LUCA_BOT_ASSIGNEE) é território
        # do Luca — conversas atribuídas a ele são respondidas normalmente.
        conversation_meta = (body.get("conversation") or {}).get("meta") or {}
        assignee = conversation_meta.get("assignee")
        if assignee and assignee.get("type") == "user" and not eh_assignee_bot(assignee):
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
        if contact_phone:
            conv["phone"] = contact_phone

        # ── Detecta origem whatsapp_pagina na primeira mensagem ───────────────
        TEXTO_BOTAO_WHATSAPP = "Olá! Gostaria de saber mais sobre os serviços da Lucralize Tech."
        is_primeira_msg = conv.get("message_count", 0) == 0
        if is_primeira_msg and message_text.strip() == TEXTO_BOTAO_WHATSAPP and contact_phone:
            threading.Thread(
                target=preencher_origem_whatsapp_pagina,
                args=(contact_phone,),
                daemon=True
            ).start()

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
                # Preserva a contagem para não tratar a reabertura como
                # "primeira mensagem" (evita o delay de 90s e o refetch de
                # histórico remoto, que desfariam o reset).
                "message_count": conv.get("message_count", 1),
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

        retomada_ctx = elapsed_minutes > 120 and len(conv["messages"]) > 1

        # ── Monta mensagem do lead com contexto de retomada se necessário ────
        user_content = message_text
        if retomada_ctx:
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

        # ── Adiciona mensagem do lead ao histórico ────────────────────────────
        conv["messages"].append({"role": "user", "content": user_content})

        # ── Agrupamento de mensagens em sequência rápida ──────────────────────
        # Marca esta como a versão mais recente da conversa; a thread em
        # background só responde se nenhuma mensagem mais nova chegar durante
        # a espera.
        msg_token = time.time()
        conv["latest_msg_token"] = msg_token

        # Na primeira mensagem de uma conversa nova, aguarda 90s (em background)
        # para que a automação do Agendor (boas_vindas_primeiro_contato) dispare
        # primeiro. Nas mensagens seguintes, responde após 2.5s (agrupamento).
        # IMPORTANTE: a espera acontece numa thread separada — o webhook responde
        # 200 imediatamente. Isso evita bloquear o worker único do Gunicorn e
        # estourar o timeout de 120s (que matava o worker e zerava a memória).
        is_first_message = conv.get("message_count", 0) == 0
        conv["message_count"] = conv.get("message_count", 0) + 1
        delay = 90.0 if is_first_message else 2.5
        if is_first_message:
            print(f"[webhook] Primeira mensagem — 90s em background conv={conversation_id}", flush=True)

        threading.Thread(
            target=_processar_resposta_luca,
            args=(conv_key, conversation_id, msg_token, message_id, is_first_message,
                  retomada_ctx, message_text, contact_name,
                  inbox_identifier, contact_identifier, delay),
            daemon=True,
        ).start()

        return jsonify({"status": "scheduled"}), 200

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

        # Se ainda está atribuída a alguém (que não seja o bot), não faz nada
        if assignee and not eh_assignee_bot(assignee):
            print(f"[conv_updated] IGNORADO — ainda atribuída a {assignee.get('name')}", flush=True)
            return jsonify({}), 200

        # ── Desatribuída e aberta: verifica se há mensagem do lead sem resposta ─
        # Cenário: humano se atribui, conclui/abandona, conversa é desatribuída
        # com o lead pendente. O Luca assume e responde.
        conv_key = str(conversation_id)
        last = get_last_message_info(conversation_id)
        if not last:
            print(f"[conv_updated] IGNORADO — sem mensagens na conversa", flush=True)
            return jsonify({}), 200
        if last.get("private") or last.get("message_type") != 0:
            print(f"[conv_updated] IGNORADO — última mensagem não é do lead", flush=True)
            return jsonify({}), 200

        conv = conversation_histories.get(conv_key)
        last_id = last.get("id")
        if conv and last_id and conv.get("last_responded_msg_id") == last_id:
            print(f"[conv_updated] IGNORADO — última mensagem já respondida pelo Luca", flush=True)
            return jsonify({}), 200
        # Guard contra eventos conversation_updated duplicados: se já existe uma
        # retomada agendada/em andamento para esta mesma mensagem, ignora.
        if conv and last_id and conv.get("retomada_msg_id") == last_id:
            print(f"[conv_updated] IGNORADO — retomada já em andamento para msg={last_id}", flush=True)
            return jsonify({}), 200

        # ── Lead pendente de resposta — Luca assume a conversa ────────────────
        meta_sender   = (conversation.get("meta") or {}).get("sender") or {}
        contact_name  = meta_sender.get("name", "")
        contact_phone = meta_sender.get("phone_number", "")
        contact_inbox = conversation.get("contact_inbox") or {}
        inbox_identifier   = contact_inbox.get("source_id", "")
        contact_identifier = contact_inbox.get("pubsub_token", "")

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
        if contact_phone:
            conv["phone"] = contact_phone

        # Sincroniza o histórico real (inclui a mensagem pendente do lead e o
        # trecho do atendimento humano, para o Luca ter o contexto completo)
        remote_history = fetch_conversation_history(conversation_id)
        if remote_history:
            conv["messages"] = remote_history
        if not conv["messages"] or conv["messages"][-1]["role"] != "user":
            print(f"[conv_updated] IGNORADO — histórico sem mensagem pendente do lead", flush=True)
            return jsonify({}), 200

        conv["last_msg_at"] = time.time()
        conv["message_count"] = max(conv.get("message_count", 0), 1)  # não é primeira mensagem
        conv["was_resolved"] = False  # histórico já sincronizado; evita reset indevido depois
        conv["retomada_msg_id"] = last_id  # marca antes de disparar — bloqueia eventos duplicados
        msg_token = time.time()
        conv["latest_msg_token"] = msg_token

        print(f"[conv_updated] RETOMADA — desatribuída com mensagem pendente, Luca assume conv={conversation_id}", flush=True)
        threading.Thread(
            target=_processar_resposta_luca,
            args=(conv_key, conversation_id, msg_token, last_id, False,
                  False, last.get("content", ""), contact_name,
                  inbox_identifier, contact_identifier, 2.5),
            daemon=True,
        ).start()
        return jsonify({"status": "retomada"}), 200

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
# LEMBRETES AUTOMÁTICOS DE REUNIÃO
# Varredura a cada 15 min das reuniões do Agendor; lembretes 24h e 1h antes.
# Cascata: janela de 24h aberta -> mensagem livre | fechada -> template Meta.
# Controles (variáveis no Railway):
#   LEMBRETES_ATIVOS             liga/desliga tudo (padrão: false)
#   LEMBRETES_MODO_OBSERVACAO    só simula com nota privada (padrão: true)
#   LEMBRETE_ENVIA_COM_ATRIBUICAO envia mesmo com humano atribuído (padrão: true)
#   MSG_LEMBRETE_24H / MSG_LEMBRETE_1H  textos da janela aberta ({nome},{hora},{hora_txt})
# ═════════════════════════════════════════════════════════════════════════════

AGENDORCHAT_INBOX_ID = os.environ.get("AGENDORCHAT_INBOX_ID", "2367")

MSG_LEMBRETE_24H_PADRAO = (
    "Olá, {nome}, tudo bem?\n\n"
    "Sua reunião com o especialista está confirmada para amanhã{hora_txt}.\n\n"
    "Ele já está se preparando para o seu caso. O convite com o link da videochamada está no seu e-mail.\n\n"
    "Até amanhã!"
)
MSG_LEMBRETE_1H_PADRAO = (
    "Olá, {nome}! Nossa conversa com o especialista é daqui a pouco, às {hora}.\n\n"
    "O link da videochamada está no seu e-mail, dá pra entrar pelo navegador ou pelo celular.\n\n"
    "Até já!"
)


def _flag(nome: str, padrao: str) -> bool:
    return os.environ.get(nome, padrao).strip().lower() in ("1", "true", "sim", "on")


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _parse_dt(iso):
    """Converte ISO do Agendor em datetime com timezone (assume BRT se vier sem)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=-3)))
        return dt
    except Exception:
        return None


_templates_cache = {"data": [], "ts": 0}

def templates_aprovados():
    """Lista os templates aprovados da inbox (cache de 30 min)."""
    if time.time() - _templates_cache["ts"] < 1800 and _templates_cache["data"]:
        return _templates_cache["data"]
    try:
        url = (f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/message_templates"
               f"?inbox_id={AGENDORCHAT_INBOX_ID}&status=approved")
        resp = requests.get(url, headers={"api_access_token": AGENDORCHAT_TOKEN}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        _templates_cache["data"] = data.get("payload", data if isinstance(data, list) else [])
        _templates_cache["ts"] = time.time()
    except Exception as e:
        print(f"[lembrete] Erro ao listar templates: {e}", flush=True)
    return _templates_cache["data"]


def template_por_nome(nome: str):
    for t in templates_aprovados():
        if t.get("name") == nome:
            return t
    return None


def enviar_template_conversa(conversation_id, tpl, variaveis, preview):
    """Dispara um template aprovado numa conversa (funciona fora da janela)."""
    payload = {
        "content": preview,
        "template_params": {
            "name": tpl.get("name"),
            "category": tpl.get("category"),
            "language": tpl.get("language") or "pt_BR",
            "processed_params": variaveis,
            "id": tpl.get("template_id") or tpl.get("id"),
        },
    }
    url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
    resp = requests.post(url, headers={"api_access_token": LUCA_SEND_TOKEN,
                                       "Content-Type": "application/json"},
                         json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()


_phone_cache = {}

def telefone_da_pessoa(person_id):
    """Busca o telefone/WhatsApp de uma pessoa no Agendor (com cache)."""
    if person_id in _phone_cache:
        return _phone_cache[person_id]
    try:
        r = requests.get(f"{AGENDOR_BASE}/people/{person_id}", headers=HEADERS, timeout=15)
        data = r.json().get("data", {}) or {}
        contato = data.get("contact") or {}
        for campo in ("whatsapp", "mobile", "phone", "workPhone"):
            valor = (contato.get(campo) or "").strip()
            if valor:
                _phone_cache[person_id] = valor
                return valor
    except Exception as e:
        print(f"[lembrete] Erro ao buscar telefone person={person_id}: {e}", flush=True)
    _phone_cache[person_id] = ""
    return ""


def conversa_do_telefone(phone):
    """Localiza a conversa mais recente do lead na inbox da API oficial."""
    try:
        digits = "".join(c for c in phone if c.isdigit())
        for q in (phone, "+" + digits, digits):
            r = requests.get(
                f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/contacts/search",
                headers={"api_access_token": AGENDORCHAT_TOKEN},
                params={"q": q}, timeout=15)
            contatos = r.json().get("payload", [])
            if contatos:
                break
        if not contatos:
            return None
        contact_id = contatos[0].get("id")
        r2 = requests.get(
            f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/contacts/{contact_id}/conversations",
            headers={"api_access_token": AGENDORCHAT_TOKEN}, timeout=15)
        convs = r2.json().get("payload", [])
        convs = [c for c in convs if str(c.get("inbox_id")) == str(AGENDORCHAT_INBOX_ID)]
        if not convs:
            return None
        return sorted(convs, key=lambda c: c.get("id") or 0)[-1]
    except Exception as e:
        print(f"[lembrete] Erro ao localizar conversa de {phone}: {e}", flush=True)
        return None


def mensagens_da_conversa(conversation_id):
    """Mensagens da conversa ordenadas por id (inclui notas privadas)."""
    try:
        url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
        resp = requests.get(url, headers={"api_access_token": AGENDORCHAT_TOKEN}, timeout=15)
        resp.raise_for_status()
        msgs = resp.json().get("payload", [])
        return sorted(msgs, key=lambda m: m.get("id") or 0)
    except Exception as e:
        print(f"[lembrete] Erro ao buscar mensagens conv={conversation_id}: {e}", flush=True)
        return []


def janela_aberta(msgs) -> bool:
    """True se a última mensagem do lead tem menos de 24h (com folga de 30 min)."""
    ultima_incoming = None
    for m in msgs:
        if m.get("message_type") == 0 and not m.get("private"):
            ultima_incoming = m
    if not ultima_incoming:
        return False
    criada = ultima_incoming.get("created_at") or 0
    return (time.time() - float(criada)) < (24 * 3600 - 1800)


def marcador_existe(msgs, marcador: str) -> bool:
    return any(marcador in (m.get("content") or "") for m in msgs)


def espelho_crm(deal_id, texto):
    """Registro-espelho no negócio (tipo WhatsApp) para auditoria no CRM."""
    if not deal_id:
        return
    try:
        r = requests.post(f"{AGENDOR_BASE}/deals/{deal_id}/tasks",
                          headers={**HEADERS, "Content-Type": "application/json"},
                          json={"text": texto, "type": "whatsapp"}, timeout=15)
        print(f"[lembrete] Espelho CRM deal={deal_id} status={r.status_code}", flush=True)
    except Exception as e:
        print(f"[lembrete] Erro no espelho CRM deal={deal_id}: {e}", flush=True)


def processar_lembrete(task, tipo, due):
    task_id = task.get("id")
    deal_id = (task.get("deal") or {}).get("id")
    pessoa = task.get("person") or {}
    person_id = pessoa.get("id")
    if not person_id:
        print(f"[lembrete] Reunião sem pessoa vinculada task={task_id} — pulada", flush=True)
        return

    phone = telefone_da_pessoa(person_id)
    if not phone:
        print(f"[lembrete] Pessoa {person_id} sem telefone task={task_id} — pulada", flush=True)
        return

    conv = conversa_do_telefone(phone)
    if not conv:
        print(f"[lembrete] Sem conversa no AgendorChat para {phone} task={task_id}", flush=True)
        return
    conv_id = conv.get("id")

    msgs = mensagens_da_conversa(conv_id)
    marcador = f"[lembrete:{task_id}:{tipo}]"
    if marcador_existe(msgs, marcador):
        return  # já tratado

    # Humano atribuído: envia mesmo assim por padrão (com nota), configurável
    detalhe = get_conversation_details(conv_id) or {}
    assignee = (detalhe.get("meta") or {}).get("assignee")
    if assignee and assignee.get("type") == "user" and not eh_assignee_bot(assignee):
        if not _flag("LEMBRETE_ENVIA_COM_ATRIBUICAO", "true"):
            print(f"[lembrete] Congelado — humano atribuído conv={conv_id} task={task_id}", flush=True)
            return

    nome = (pessoa.get("name") or "").strip().split(" ")[0] if pessoa.get("name") else ""
    due_brt = due.astimezone(timezone(timedelta(hours=-3)))
    hora = due_brt.strftime("%Hh%M").lstrip("0") if due_brt.strftime("%M") != "00" else due_brt.strftime("%Hh").lstrip("0")
    hora_confirmada = "HORÁRIO A CONFIRMAR" not in (task.get("text") or "").upper()

    # Modo observação: só registra o que faria, sem enviar ao lead
    if _flag("LEMBRETES_MODO_OBSERVACAO", "true"):
        send_private_note(conv_id, (
            f"👁️ [observação] Lembrete {tipo} SERIA enviado agora para {nome or phone} "
            f"(reunião {due_brt.strftime('%d/%m %H:%M')}, hora confirmada: {'sim' if hora_confirmada else 'não'}, "
            f"janela: {'aberta' if janela_aberta(msgs) else 'fechada'}). {marcador}"))
        print(f"[lembrete] OBSERVAÇÃO {tipo} conv={conv_id} task={task_id}", flush=True)
        return

    if janela_aberta(msgs):
        # ── Janela aberta: mensagem livre, texto editável no Railway ─────────
        modelo = os.environ.get("MSG_LEMBRETE_24H" if tipo == "24h" else "MSG_LEMBRETE_1H", "") \
                 or (MSG_LEMBRETE_24H_PADRAO if tipo == "24h" else MSG_LEMBRETE_1H_PADRAO)
        hora_txt = f", às {hora}" if hora_confirmada else ""
        texto = modelo.format_map(_SafeDict(nome=nome, hora=hora, hora_txt=hora_txt))
        texto = texto.replace("Olá, ,", "Olá,").replace("Olá, !", "Olá!")
        send_agendorchat_message(conv_id, texto)
        via = "mensagem livre"
    else:
        # ── Janela fechada: template aprovado da Meta ─────────────────────────
        if tipo == "1h":
            tpl, variaveis, preview = template_por_nome("lembrete_de_evento"), {}, \
                "Compromisso confirmado. Sua reunião está agendada para hoje."
        else:
            if hora_confirmada and template_por_nome("lembrete_reuniao_amanha_hora"):
                tpl = template_por_nome("lembrete_reuniao_amanha_hora")
                variaveis = {"1": nome or "tudo bem", "2": hora}
                preview = f"Sua reunião com o especialista está confirmada para amanhã, às {hora}."
            else:
                tpl = template_por_nome("lembrete_reuniao_amanha")
                variaveis = {"1": nome or "tudo bem"}
                preview = "Sua reunião com o especialista está confirmada para amanhã."
        if not tpl:
            # Sem template disponível: alerta para contato manual (plano interino)
            send_private_note(conv_id, (
                f"🔔 Lembrete {tipo} NÃO enviado (janela fechada e template indisponível). "
                f"Recomenda-se contato manual com o lead. {marcador}"))
            espelho_crm(deal_id, f"🤖 Lembrete de reunião ({tipo}) não enviado — janela fechada, "
                                 f"template pendente. Contato manual recomendado.")
            print(f"[lembrete] {tipo} SEM TEMPLATE conv={conv_id} task={task_id}", flush=True)
            return
        enviar_template_conversa(conv_id, tpl, variaveis, preview)
        via = f"template {tpl.get('name')}"

    send_private_note(conv_id, f"🔔 Lembrete de reunião ({tipo}) enviado ao lead via {via}. {marcador}")
    espelho_crm(deal_id, f"🤖 Lembrete de reunião ({tipo}) enviado ao lead via WhatsApp ({via}). "
                         f"Reunião: {due_brt.strftime('%d/%m/%Y %H:%M')}.")
    print(f"[lembrete] ✅ {tipo} enviado conv={conv_id} task={task_id} via {via}", flush=True)


def varredura_lembretes():
    if not _flag("LEMBRETES_ATIVOS", "false"):
        return
    agora_brt = datetime.utcnow() - timedelta(hours=3)
    if not (8 <= agora_brt.hour < 20):
        return  # fora da janela de envio

    tasks = tasks_cache.get("data") or []
    if not tasks:
        fetch_tasks_job()
        tasks = tasks_cache.get("data") or []

    agora = datetime.now(timezone.utc)

    # Mapa negócio -> status a partir do cache do dashboard (1=andamento, 2=ganho, 3=perdido)
    status_por_deal = {d.get("id"): (d.get("dealStatus") or {}).get("id")
                       for d in (cache.get("deals") or [])}

    def negocio_permite_lembrete(task):
        """Lembrete só para negócio em andamento. Sem negócio vinculado: permite.
        Status desconhecido: consulta a API; em erro, permite (fail-open)."""
        deal_id = (task.get("deal") or {}).get("id")
        if not deal_id:
            return True
        status = status_por_deal.get(deal_id)
        if status is None:
            try:
                r = requests.get(f"{AGENDOR_BASE}/deals/{deal_id}", headers=HEADERS, timeout=15)
                status = ((r.json().get("data") or {}).get("dealStatus") or {}).get("id")
                status_por_deal[deal_id] = status
            except Exception as e:
                print(f"[lembrete] Status do negócio {deal_id} indisponível ({e}) — permitindo", flush=True)
                return True
        if status == 1 or status is None:
            return True
        rotulo = "ganho" if status == 2 else "perdido" if status == 3 else f"status {status}"
        print(f"[lembrete] Pulado — negócio {deal_id} {rotulo} (task={task.get('id')})", flush=True)
        return False

    for t in tasks:
        try:
            if t.get("type") != "Reunião" or t.get("finishedAt"):
                continue
            due = _parse_dt(t.get("dueDate"))
            if not due:
                continue
            delta = (due - agora).total_seconds()
            if not (72000 <= delta <= 86400 or 900 <= delta <= 3600):
                continue
            if not negocio_permite_lembrete(t):
                continue
            if 72000 <= delta <= 86400:          # 20h a 24h antes
                processar_lembrete(t, "24h", due)
            elif 900 <= delta <= 3600:            # 15 a 60 min antes
                criada = _parse_dt(t.get("createdAt"))
                if criada and (agora - criada).total_seconds() < 7200:
                    continue  # reunião marcada há menos de 2h: lembrete redundante
                processar_lembrete(t, "1h", due)
        except Exception as e:
            print(f"[lembrete] Erro na task {t.get('id')}: {e}", flush=True)


def varredura_lembretes_safe():
    try:
        varredura_lembretes()
    except Exception as e:
        print(f"[lembrete] Erro geral na varredura: {e}", flush=True)


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULER + MAIN
# ═════════════════════════════════════════════════════════════════════════════

scheduler = BackgroundScheduler()
scheduler.add_job(fetch_deals_safe, "interval", hours=1, id="fetch_recorrente")
scheduler.add_job(fetch_tasks_job, "interval", hours=2, id="tasks_recorrente")
scheduler.add_job(varredura_lembretes_safe, "interval", minutes=15, id="lembretes_reuniao")
scheduler.add_job(fetch_deals_safe, "date", run_date=datetime.now() + timedelta(seconds=5), id="fetch_inicial")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
