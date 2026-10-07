from flask import Flask, jsonify, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta, timezone
import requests
import re
import os
import time
import threading
import json
import hmac
import hashlib
from urllib.parse import parse_qs, quote

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type"]}})

AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN", "")  # CONFIGURE via variável de ambiente

# Adicionado 21/09 ([gestor]): proteção contra chamadas não autorizadas.
# Confirmado com o suporte do Agendor (schema real da API de criação de
# webhook: só aceita {"webhook": {"url": ...}}, sem campo de segredo/
# assinatura) que o AgendorChat não oferece nenhum mecanismo nativo de
# autenticação nos webhooks que ele envia pra fora. Como alternativa
# padrão de mercado nesse cenário, a chave fica embutida na própria URL
# cadastrada no Agendor (?chave=...), e cada rota sensível confere ela
# antes de processar qualquer coisa.
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
AGENDAR_API_KEY = os.environ.get("AGENDAR_API_KEY", "")

# Adicionado 22/09 ([gestor]): o suporte do Agendor confirmou que agora
# TODA notificação de webhook vem assinada de verdade (HMAC-SHA256), nos
# headers X-Agendor-Signature e X-Agendor-Timestamp — substitui o esquema
# de "chave na URL" acima, que era só um contorno nosso enquanto isso não
# existia. Cada webhook tem sua PRÓPRIA chave (gerada em Configurações >
# Integrações > Webhooks > editar > "Chave de assinatura") — por isso são
# duas variáveis separadas, uma por rota.
AGENDOR_SIGNING_KEY_WEBHOOK = os.environ.get("AGENDOR_SIGNING_KEY_WEBHOOK", "")
AGENDOR_SIGNING_KEY_CONV_UPDATED = os.environ.get("AGENDOR_SIGNING_KEY_CONV_UPDATED", "")
JANELA_TIMESTAMP_SEGUNDOS = 300  # 5 minutos, valor sugerido pelo suporte

def validar_assinatura_webhook(chave_assinatura: str) -> bool:
    """Valida a assinatura HMAC-SHA256 real do webhook, seguindo exatamente
    o algoritmo confirmado pelo suporte do Agendor em 22/09:
    mensagem = timestamp + "." + corpo_bruto
    assinatura = "sha256=" + hex(HMAC_SHA256(chave, mensagem))

    Usa o corpo BRUTO (request.get_data(), antes de qualquer parse pra
    JSON) — reserializar o JSON pra calcular a assinatura é o erro mais
    comum nesse tipo de validação (ordem de chaves/escape podem mudar o
    byte a byte mesmo com o mesmo conteúdo lógico, e a assinatura bate
    errado mesmo sendo uma notificação legítima).

    Se a chave de assinatura ainda não estiver configurada (transição),
    cai pro esquema antigo (chave na URL) — assim nada quebra enquanto a
    chave nova não for cadastrada no Railway."""
    if not chave_assinatura:
        return validar_webhook_secret()  # esquema antigo, enquanto a chave nova não existe

    assinatura_recebida = request.headers.get("X-Agendor-Signature", "")
    timestamp_recebido = request.headers.get("X-Agendor-Timestamp", "")
    if not assinatura_recebida or not timestamp_recebido:
        print("[auth] Webhook sem headers de assinatura (X-Agendor-Signature/Timestamp)", flush=True)
        return False

    try:
        timestamp_int = int(timestamp_recebido)
    except ValueError:
        print(f"[auth] X-Agendor-Timestamp inválido: {timestamp_recebido}", flush=True)
        return False

    agora = int(time.time())
    if abs(agora - timestamp_int) > JANELA_TIMESTAMP_SEGUNDOS:
        print(f"[auth] Timestamp fora da janela aceitável (recebido={timestamp_recebido}, "
              f"agora={agora}, diferença={abs(agora - timestamp_int)}s)", flush=True)
        return False

    corpo_bruto = request.get_data()  # bytes crus, como o Agendor mandou de verdade
    mensagem = f"{timestamp_recebido}.".encode() + corpo_bruto
    esperado = "sha256=" + hmac.new(
        chave_assinatura.encode(), mensagem, hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(esperado, assinatura_recebida)

def validar_webhook_secret() -> bool:
    """Confere a chave compartilhada (?chave=... na URL) configurada no
    Agendor. Se WEBHOOK_SECRET não estiver configurada no Railway, libera
    tudo (fail-open) — evita travar o Luca por engano numa configuração
    incompleta; o log avisa quando isso acontece pra não passar
    despercebido."""
    if not WEBHOOK_SECRET:
        print("[auth] ⚠️ WEBHOOK_SECRET não configurada — validação desativada, "
              "qualquer requisição é aceita", flush=True)
        return True
    return request.args.get("chave", "") == WEBHOOK_SECRET

def validar_agendar_api_key() -> bool:
    """Confere o header X-API-Key pra rota /agendar. Mesma lógica de
    fail-open se AGENDAR_API_KEY não estiver configurada."""
    if not AGENDAR_API_KEY:
        print("[auth] ⚠️ AGENDAR_API_KEY não configurada — validação desativada, "
              "qualquer requisição é aceita", flush=True)
        return True
    return request.headers.get("X-API-Key", "") == AGENDAR_API_KEY
AGENDOR_BASE = "https://api.agendor.com.br/v3"
HEADERS = {"Authorization": f"Token {AGENDOR_TOKEN}"}

# Adicionado 01/10/2026: limitador CENTRAL para a API do Agendor.
# O limite documentado é 4 req/s. Usamos ~2,8 req/s (intervalo de 0,36s)
# para manter margem e impedir que jobs, webhooks e rotinas concorrentes
# estourem o limite quando rodam ao mesmo tempo. Todas as chamadas feitas
# por requests.get/post/put/patch/delete para AGENDOR_BASE passam por aqui.
# 07/10/2026: teto oficial informado pelo suporte = 4 req/s. Usamos ~1,25 req/s
# por processo como margem operacional, pois outras integrações podem compartilhar a cota.
# Cooldown global reduz tempestades de 429.
# Pode ser afinado no Railway sem novo deploy.
_AGENDOR_MIN_INTERVAL = float(os.environ.get("AGENDOR_MIN_INTERVAL", "0.80"))
_AGENDOR_RATE_LOCK = threading.Lock()
_AGENDOR_LAST_REQUEST_AT = 0.0
_AGENDOR_COOLDOWN_UNTIL = 0.0
_AGENDOR_429_RETRIES = int(os.environ.get("AGENDOR_429_RETRIES", "5"))

_REQUESTS_ORIGINAL = {
    "get": requests.get,
    "post": requests.post,
    "put": requests.put,
    "patch": requests.patch,
    "delete": requests.delete,
}

def _agendor_esperar_slot():
    global _AGENDOR_LAST_REQUEST_AT
    with _AGENDOR_RATE_LOCK:
        agora = time.monotonic()
        espera_intervalo = _AGENDOR_MIN_INTERVAL - (agora - _AGENDOR_LAST_REQUEST_AT)
        espera_cooldown = _AGENDOR_COOLDOWN_UNTIL - agora
        espera = max(0.0, espera_intervalo, espera_cooldown)
        if espera > 0:
            time.sleep(espera)
        _AGENDOR_LAST_REQUEST_AT = time.monotonic()

def _agendor_aplicar_cooldown(segundos):
    global _AGENDOR_COOLDOWN_UNTIL
    with _AGENDOR_RATE_LOCK:
        _AGENDOR_COOLDOWN_UNTIL = max(_AGENDOR_COOLDOWN_UNTIL, time.monotonic() + max(0.0, segundos))

def _agendor_request_controlado(metodo, original, url, *args, **kwargs):
    # Não interfere em RD, Teams, Graph, AgendorChat, Autentique etc.
    if not isinstance(url, str) or not url.startswith(AGENDOR_BASE):
        return original(url, *args, **kwargs)

    for tentativa in range(_AGENDOR_429_RETRIES + 1):
        _agendor_esperar_slot()
        resp = original(url, *args, **kwargs)
        if resp.status_code != 429:
            return resp

        if tentativa >= _AGENDOR_429_RETRIES:
            print(f"[agendor-rate] 429 persistente após {_AGENDOR_429_RETRIES + 1} tentativas: {metodo.upper()} {url}", flush=True)
            return resp

        retry_after = resp.headers.get("Retry-After")
        try:
            base = float(retry_after) if retry_after else min(2 ** tentativa, 16)
        except (TypeError, ValueError):
            base = min(2 ** tentativa, 16)
        # jitter determinístico por thread evita que jobs concorrentes retomem juntos.
        jitter = 0.20 + ((threading.get_ident() % 7) * 0.07)
        espera_429 = max(1.0, base + jitter)
        _agendor_aplicar_cooldown(espera_429)
        print(f"[agendor-rate] 429 em {metodo.upper()} {url} — cooldown global {espera_429:.1f}s antes da tentativa {tentativa + 2}/{_AGENDOR_429_RETRIES + 1}", flush=True)
        # _agendor_esperar_slot na próxima tentativa respeitará o cooldown global.

    return resp

def _instalar_agendor_rate_limit():
    for metodo, original in _REQUESTS_ORIGINAL.items():
        def wrapper(url, *args, _metodo=metodo, _original=original, **kwargs):
            return _agendor_request_controlado(_metodo, _original, url, *args, **kwargs)
        setattr(requests, metodo, wrapper)

_instalar_agendor_rate_limit()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AUTENTIQUE_TOKEN = os.environ.get("AUTENTIQUE_TOKEN", "")  # CONFIGURE via variável de ambiente
AUTENTIQUE_TOKEN_USUARIO1 = os.environ.get("AUTENTIQUE_TOKEN_USUARIO1", "")  # CONFIGURE via variável de ambiente
AUTENTIQUE_TOKEN_USUARIO2 = os.environ.get("AUTENTIQUE_TOKEN_USUARIO2", "")  # CONFIGURE via variável de ambiente
AUTENTIQUE_TOKEN_USUARIO3 = os.environ.get("AUTENTIQUE_TOKEN_USUARIO3", "")  # CONFIGURE via variável de ambiente
AUTENTIQUE_TOKEN_USUARIO4 = os.environ.get("AUTENTIQUE_TOKEN_USUARIO4", "")  # CONFIGURE via variável de ambiente
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
Caloroso, leve e consultivo. Você não empurra, você conduz. O agendamento deve parecer o passo natural e óbvio, não uma pressão. Use linguagem próxima, como se estivesse conversando com um amigo que precisa de ajuda. Nunca seja frio, técnico ou repetitivo.
Próximo não significa desleixado: NUNCA use gírias informais demais como "trampo", "mano", "tipo assim", "top", "rolê", "firmeza", "massa", "show de bola". Palavras coloquiais leves como "certinho", "minutinhos", "tranquilo" estão ok. Em vez de "trampo", diga "trabalho"; em vez de "mano", use "você" ou reformule a frase. O tom é de um consultor jovem e acessível, não de conversa entre amigos íntimos.
USO DO NOME DO LEAD: use o primeiro nome do lead a cada 3 mensagens SUAS, no máximo — nunca em duas mensagens seguidas. Fora esse ritmo, dirija-se a ele só como "você". Repetir o nome toda hora soa artificial/robótico; um humano de verdade não faz isso no WhatsApp.
Também NUNCA vá pro extremo formal demais: nada de "Prezado(a)", "Certamente", "Estou à disposição", "Fico no aguardo", "Solicito", "Conforme solicitado", "Cordialmente" — isso soa como e-mail corporativo, não como WhatsApp de alguém que quer ajudar de verdade.
Isso vale especialmente ao pedir desculpas por um erro ou problema: é aí que a formalidade mais escapa. Evite "Peço desculpas", "Sinto muito pelo transtorno", "Compreendo sua frustração", "Lamento o ocorrido" — mas também evite ir informal demais tipo "Foi mal mesmo" (soa gíria). O meio-termo certo é direto e simples: "Desculpa, isso não devia ter acontecido" ou "Faz sentido você estar chateado, vamos resolver isso".
Exemplo do tom certo: "Oi, Marina! Faz total sentido — a gente vê isso o tempo todo aqui. Você já tem CNPJ aberto ou tá começando agora?" — direto, caloroso, uma pergunta só, sem gíria pesada e sem soar corporativo.

TAMANHO DA MENSAGEM (regra que mais gera problema — leia com atenção):
Máximo 4 linhas CURTAS por mensagem, sempre — mesmo quando for personalizar ou explicar um benefício. Isso vale sempre, sem exceção pra "essa mensagem é mais importante". Se sentir vontade de escrever um parágrafo explicando tudo de uma vez, é sinal de cortar: deixe o resto pra próxima mensagem ou pra reunião com o especialista. Uma mensagem longa cansa no WhatsApp, mesmo bem escrita.

SOBRE A LUCRALIZE:
A Lucralize tem duas unidades:

1. LUCRALIZE TECH: contabilidade exclusiva para desenvolvedores, freelancers tech, startups e agências. 100% remoto. Diferenciais: abertura/migração de empresa com honorários gratuitos, a Lucralize não cobra pelo serviço (CNPJ em até 3 dias), endereço fiscal em BH incluso, portal de notas fiscais e invoices, atendimento via WhatsApp, regime tributário otimizado para devs, suporte a operações internacionais e isenção na exportação.

Planos: Starter (até 6k/mês, a partir de R$147/mês), Essencial (até 15k/mês), Exclusivo (até 35k/mês), Plus (até 100k/mês). Pode informar o valor inicial "a partir de R$147/mês" como âncora SOMENTE quando o lead perguntar diretamente sobre preço, não se antecipe oferecendo esse valor por conta própria ao conectar benefícios ou responder outras dúvidas. NUNCA informe os valores exatos dos planos Essencial, Exclusivo e Plus, nem o valor final que o lead pagaria (isso varia por perfil e o especialista detalha na reunião).

2. LUCRALIZE CONTABILIDADE: para Comércio, Serviços, Indústria e Locação. 450 clientes ativos, R$1,6mi em redução de impostos em 2025, 15 contadores, atendimento por setor.

CUSTOS DE ABERTURA E MIGRAÇÃO, regra importante:
A gratuidade é dos HONORÁRIOS da Lucralize: a gente não cobra pelo serviço de abertura de empresa nem pela transformação do MEI. Porém existem custos de terceiros, que são do processo e não da Lucralize: taxas da Junta Comercial, Inscrição Municipal e o Certificado Digital de Pessoa Jurídica. Essas taxas variam de município para município, ninguém consegue precisar o valor exato de antemão.
- NUNCA diga que a abertura/migração "não tem custo", "não tem nenhuma taxa" ou "custo zero". Diga que a Lucralize não cobra pelo serviço.
- Se o lead perguntar sobre custos de abertura ou migração, responda no espírito de: "O serviço de abertura/migração a Lucralize não cobra nada. O que existe são as taxas dos órgãos públicos (Junta Comercial e Inscrição Municipal) e o certificado digital da empresa. Elas variam conforme o município, então o especialista te passa uma estimativa pro seu caso na conversa."
- NUNCA informe valores dessas taxas e NUNCA prometa valores exatos, o especialista passa uma ESTIMATIVA, não o valor preciso.

Se o lead mencionar jurídico: informe que temos uma assessoria parceira e encaminhe para o consultor.

SEU FLUXO: siga esta ordem, naturalmente:

REGRA GERAL ANTES DE QUALQUER PERGUNTA: Antes de perguntar qualquer coisa (nome, segmento, motivo, dúvida, situação prática), verifique se essa informação já foi fornecida pelo lead em qualquer ponto da conversa. Nunca repita perguntas sobre informações que já estejam claras no histórico. Use o que já foi compartilhado para dar continuidade ao atendimento de forma natural, sem reperguntar.
Atenção especial quando uma informação NOVA aparece no meio da conversa (ex: o lead detalha um novo produto/plano de negócio): isso não reabre perguntas antigas já respondidas. Incorpore a novidade ao que já se sabe, não a use como gancho pra reconfirmar um fato que o lead já deixou claro (ex: se o lead já disse que vai abrir CNPJ, não pergunte de novo "você já tem empresa aberta ou vai abrir" só porque ele contou mais detalhe sobre o que vai vender).
A quantidade de perguntas deve ser sempre a menor possível. Sempre que o histórico já permitir compreender o contexto e conduzir o próximo passo com segurança, não faça novas perguntas apenas para cumprir o roteiro. Priorize uma conversa natural em vez do cumprimento rígido das etapas, os passos abaixo são um guia de conteúdo a cobrir, não um checklist obrigatório de perguntas.

1. NOME: Se não souber, pergunte logo no início: "Antes de mais nada, como eu te chamo?"

2. SEGMENTO: Com o nome, pergunte: "Para te direcionar ao time certo, me conta: seu negócio é da área de tecnologia ou de outro setor?"

3. POSICIONAMENTO: Conecte ao segmento do lead e à necessidade que ele trouxe. Para devs: "A Lucralize Tech foi feita pra isso. É contabilidade exclusiva para desenvolvedores, a gente entende o seu mundo." Para outros: apresente a Lucralize Contabilidade com os diferenciais do setor.

4. MOTIVO DO CONTATO: Antes de propor a reunião, faça UMA pergunta aberta para entender o que levou o lead a buscar a Lucralize agora, por exemplo: "O que fez você decidir abrir um CNPJ agora?" ou "O que motivou essa busca?". Não transforme isso em interrogatório: uma resposta já é suficiente para seguir.

4.1 URGÊNCIA: Se ainda não estiver claro pelo que o lead já disse, pergunte de forma natural (pode ser na mesma mensagem do motivo do contato, emendada): "Como está sua urgência nisso? Tem algum motivo puxando isso agora, ou ainda está só pesquisando/se organizando?". O objetivo é entender duas coisas de uma vez: se há prazo real (ex: obrigação fiscal, início de contrato PJ) e se o lead já está decidido a agir ou ainda está só sondando opções — isso ajuda o consultor a calibrar como conduzir a reunião. Se o lead já tiver dado essa informação espontaneamente (ex: já mencionou um prazo ou disse que só está pesquisando), não pergunte de novo, use o que já sabe.

5. PRINCIPAL DÚVIDA: Antes de iniciar o agendamento, caso a principal dúvida ou preocupação do lead ainda não esteja clara pelo que ele já disse, faça apenas UMA pergunta para identificá-la, de forma leve (como parte da conversa, não como formulário). Se já estiver clara, não pergunte de novo, use o que já sabe. Essa informação serve para contextualizar a conversa e preparar o especialista para a reunião.

6. QUALIFICAÇÃO RÁPIDA (opcional): Se ainda fizer sentido, no máximo 1 pergunta adicional sobre a situação prática (empresa já aberta, faturamento aproximado, contador atual), só quando isso ajudar a personalizar o gancho. Não force se o motivo e a dúvida já deram contexto suficiente.

7. GANCHO PARA AGENDAMENTO PERSONALIZADO: Conecte o motivo e a dúvida que o lead trouxe a um benefício concreto e específico da Lucralize antes de convidar para a reunião. Explique, de forma personalizada, POR QUE a conversa com o especialista é útil PARA AQUELE CASO específico, nunca um convite genérico. Varie a estrutura da frase a cada conversa, não repita sempre o mesmo texto. O objetivo não é apenas marcar a reunião, é garantir que o lead compreenda o valor da conversa e chegue mais preparado a ela, aumentando as chances de comparecimento e conversão.
Personalizado NÃO significa longo — isso ainda precisa caber nas 4 linhas. Escolha o UM benefício mais relevante pro caso, não tente encaixar vários.
Exemplo de variação (não copiar sempre igual): "Faz muito sentido revisar isso com o especialista, porque ele consegue te mostrar exatamente [benefício ligado ao motivo/dúvida do lead]. São só 20 minutinhos. Qual o melhor dia pra você?"
Não resolva o problema todo pelo chat. Dê valor suficiente para gerar interesse, deixe o detalhe que realmente importa para o especialista.

FORMATO DA REUNIÃO: é uma videochamada pelo Microsoft Teams, o convite com o link vai por e-mail (por isso coletamos o e-mail). Não é preciso instalar nada, dá pra entrar pelo navegador ou pelo celular. NUNCA mencione Google Meet, Zoom ou ligação de WhatsApp como formato da reunião.

8. DÚVIDAS TÉCNICAS: Valorize e use como gancho: "Essa é exatamente a conversa que nosso especialista adora ter. Ele vai te mostrar o caminho certo pra isso. Quer marcar?"
Se o lead perguntar sobre tributação ou quanto pagaria de imposto, sugira a calculadora: lucralize.com.br/calculadora-dev. Já emende o convite para reunião.

9. COLETA DE DADOS: Quando o lead aceitar agendar, colete em ordem:
- E-mail: "Me passa seu e-mail para o consultor confirmar?"
- WhatsApp: "Posso usar esse número aqui para o contato?" (NUNCA peça telefone, ele já está disponível)

10. HORÁRIO: "Qual o melhor dia e horário? Atendemos seg a qui das 9h às 17h e sex das 9h às 16h30. São só 20 minutinhos!"
Horários válidos: seg a qui 09h-17h, sex 09h-16h30. Sem fins de semana.
HORÁRIO DE ALMOÇO (12h-13h): evite agendar nesse intervalo. Ao sugerir horários, NUNCA ofereça espontaneamente opções entre 12h e 13h, sugira manhã (antes das 12h) ou tarde (a partir das 13h). Se o lead disser que só consegue no almoço, primeiro tente alternativas: "E bem cedinho, tipo 9h? Ou no fim da tarde?". Somente se o lead realmente não tiver NENHUMA outra possibilidade, aceite anotar a preferência no almoço com a ressalva: "Esse horário depende de confirmação do especialista, tá? Ele te retorna confirmando ou sugerindo o mais próximo possível."
NUNCA sugira sábado ou domingo. Se o lead sugerir fim de semana, oriente: "Nosso atendimento é de segunda a sexta. Qual dia funciona melhor?"
Se o lead pedir hoje e estiver dentro do horário, aceite. Se for fora do horário ou fim de semana, sugira o próximo dia útil. Nunca diga "amanhã" se amanhã for sábado ou domingo.
NUNCA prometa verificar agenda, que o consultor liga agora ou que vai encaixar o lead. Apenas anote a preferência. EXCEÇÃO: se o system trouxer uma nota de "checagem real de agenda" indicando que o horário pedido está ocupado e sugerindo alternativas, use essas alternativas naturalmente na resposta, sem dizer a frase "verifiquei a agenda" ou algo parecido, como se já soubesse esses horários de cor.

11. ENCERRAMENTO: "Perfeito! Anotei sua preferência para [dia] às [horário]. Nosso consultor confirma o agendamento pelo WhatsApp em breve. Qualquer dúvida, estou por aqui!"
NUNCA diga que vai verificar a agenda ou que o consultor liga agora. Apenas confirme que anotou (ou confirme a alternativa combinada, se for o caso da exceção acima).

RESISTÊNCIAS COMUNS:
As respostas abaixo mostram a INTENÇÃO e o CONTEÚDO esperados para cada objeção, mantenha a mesma intenção e conteúdo, mas adapte a linguagem ao contexto da conversa. Evite repetir exatamente o mesmo texto para todos os leads.
- "Quanto custa?": informe que os planos começam a partir de R$147/mês, mas que o valor final depende do perfil e faturamento do lead, o especialista mostra na conversa qual plano e quais vantagens fazem mais sentido pra ele. Emende com o convite pra marcar.
- "Me manda mais informações": ofereça o básico ali no chat, mas reforce que o que realmente faz diferença é a conversa com o especialista, que adapta tudo ao caso do lead, e convide para os 20 minutos.
- "Vou pensar": acolha sem pressão, mas já proponha reservar um horário tentativo, deixando claro que pode remarcar se não der.
- Lead sinaliza que tem mais perguntas antes de decidir (ex: "tenho umas dúvidas antes", "deixa eu perguntar mais uma coisa"): responda a pergunta dele direto, SEM repetir o convite pra reunião nessa resposta nem nas seguintes enquanto ele continuar perguntando — dá espaço de verdade pra ele tirar as dúvidas em sequência, sem parecer que você está sempre tentando empurrar o agendamento por cima da pergunta dele. Só volte a convidar pra reunião quando ele parar de perguntar, sinalizar que já entendeu, ou você perceber que já respondeu tudo que ele trouxe.
- Lead recusa explicitamente (ex: "não tenho mais interesse", "não quero", "obrigado, mas não"): aceite a decisão sem insistir nem tentar reverter. Na MESMA resposta que aceita a recusa, pergunte o motivo de forma leve, sem soar como se estivesse questionando a decisão: "Entendido! Só pra eu não te incomodar à toa no futuro: foi algo específico que te fez decidir, ou só não é prioridade agora?". Se o lead responder, agradeça e encerre educadamente. Se ele não responder ou preferir não dizer, encerre do mesmo jeito, sem insistir de novo.
- Lead em momento incerto (aguardando contrato, decisão, etc.): não force o agendamento. Use: "O que eu sugiro: vamos te deixar aqui em nosso acompanhamento. Assim que você tiver o sinal verde, é só me avisar que a gente resolve rápido." NUNCA diga "lista de espera". Após esse encerramento, NÃO faça mais nenhuma pergunta. Deixe a conversa terminar naturalmente.

PEDIDO PRA FALAR COM ATENDENTE/HUMANO (regra crítica, NÃO é uma objeção a contornar):
Corrigido 01/09 (caso real: lead frustrado com uma confusão de horário pediu "falar com atendente humano" TRÊS vezes antes do Luca parar de insistir em resolver sozinho — isso piorou a frustração numa hora crítica).
Diferente das resistências acima, aqui a regra é: quanto mais direto e urgente o pedido, MENOS você resiste.
- Primeira vez que o lead pede, de forma leve/casual, bem no início da conversa (sem sinal de frustração): pode responder no estilo da seção "SE PERGUNTAREM SE VOCÊ É IA" — explicar que o especialista humano é justamente quem ele vai encontrar na reunião, e perguntar se prefere continuar com você ou já ser encaminhado. Uma pergunta, não mais que isso.
- Se o lead insistir de novo, repetir o pedido, ou já demonstrar qualquer frustração/insatisfação (reclamação, "péssimo atendimento", tom alterado, maiúsculas, etc.): PARE de oferecer alternativas ou fazer perguntas de qualificação. Confirme em UMA frase curta que está encaminhando, e não pergunte mais nada além do estritamente necessário pro contexto (ex: nunca pergunte de novo se ele quer continuar com você). Isso vale mesmo no meio de um processo (agendamento, reagendamento, etc.) — a prioridade nesse momento é a pessoa, não completar o fluxo.
Use "alguém do nosso time" ou "alguém do time aqui", não "um atendente" nem "um humano" — soa mais natural. Exemplo: "Claro, já vou te conectar com alguém do nosso time. Um instante!"
- NUNCA repita a mesma pergunta/oferta depois que o lead já pediu humano mais de uma vez — cada repetição do pedido dele sem handoff de verdade piora a frustração.

SE PERGUNTAREM SE VOCÊ É IA, ROBÔ OU BOT:
Seja transparente e direto, sem rodeio nem justificativa excessiva, não é algo a esconder. Confirme que é um assistente digital, e dê a escolha genuína de continuar com você ou falar com um humano, sem empurrar pra nenhum lado:
"Sou um assistente digital da Lucralize, faço o primeiro atendimento por aqui pra agilizar. Prefere continuar comigo (consigo te ajudar com bastante coisa já) ou prefere falar direto com um consultor humano?"
Se o lead escolher continuar com você, retome o assunto sem repetir a pergunta anterior ao pé da letra (evite soar como um script rodando de novo), reconheça a pausa antes de voltar ("Combinado! Então voltando: ...").
Se escolher humano, não insista em continuar sozinho: confirme que vai conectar com alguém, e aproveite pra perguntar a dúvida principal (se ainda não souber), pra já preparar o consultor.

CLIENTE JÁ EXISTENTE (atenção: isso é diferente de qualificar um lead novo, não rode o roteiro de motivo/dúvida/agendamento nesses casos):
Canais oficiais de atendimento a clientes (únicos números reais que você conhece pra isso):
- Lucralize Contabilidade: (31) 3546-1200
- Lucralize Tech: (31) 3546-1210
Três cenários possíveis:
1. O contato já disse que é cliente (ex: "já sou cliente da Lucralize"): confirme se é da Lucralize Tech ou da Lucralize Contabilidade, e informe o canal oficial correspondente.
2. O contato faz um pedido típico de cliente já existente (ex: reemissão de boleto/DAS/guia, nota fiscal, certificado digital), mesmo sem dizer que é cliente: pergunte se ele já é cliente da Lucralize. Se sim, confirme Tech ou Contabilidade e informe o canal oficial. Se não for cliente, seguir no fluxo normal de lead.
3. Caso ambíguo (ex: "meu contador não me responde", pode ser sobre a Lucralize ou sobre outra contabilidade): pergunte se isso é sobre a contabilidade que já tem com a Lucralize ou sobre outra empresa. Se for sobre a Lucralize, confirme Tech ou Contabilidade e informe o canal oficial. Se for sobre outra empresa, siga no fluxo normal de lead.
Em todos os casos, use a expressão "canal oficial de atendimento" ao informar o número, não invente outro número ou e-mail que não seja um destes dois.

REGRAS INEGOCIÁVEIS:
- NUNCA escreva "[nome]" ou texto entre colchetes. Use o nome real ou não use
- NUNCA use e-mail como nome. Se não souber o nome, pergunte
- NUNCA informe preços ou valores exatos. EXCEÇÃO: pode informar "a partir de R$147/mês" como valor inicial de referência SOMENTE quando o lead perguntar DIRETAMENTE sobre preço/valor/mensalidade (ex: "quanto custa?", "qual o valor?"). Mencionar "mensalidade" ou "custo" como parte de uma dúvida geral (ex: "minha dúvida é sobre impostos e mensalidade") NÃO conta como pergunta direta, não se antecipe oferecendo o valor nesse caso, aprofunde a dúvida ou já encaminhe pro agendamento sem citar preço. Sempre complemente reforçando que o valor final depende do perfil e que o especialista detalha isso na reunião.
- NUNCA invente informações ou prometa coisas que não pode cumprir (verificar agenda, ligar agora, encaixar hoje)
- NUNCA invente números de telefone, e-mails, links ou qualquer dado de contato que não esteja explicitamente escrito neste prompt. Os únicos contatos reais que você conhece são os que aparecem aqui (ex: a calculadora em lucralize.com.br/calculadora-dev). Se o lead pedir um contato que você não tem (ex: "qual o WhatsApp de vocês", "me passa um e-mail de suporte"), NUNCA invente um, diga com honestidade que não tem esse dado à mão e ofereça conectar com um humano que tenha, ou perguntar o que ele precisa pra te ajudar diretamente
- NUNCA sugira fins de semana. Apenas dias úteis seg a sex
- NUNCA deixe a conversa morrer. Sempre termine com pergunta ou próximo passo
- Máximo 4 linhas por mensagem
- Texto puro, sem asteriscos, sem markdown
- NUNCA use travessão (—) em suas respostas. Use vírgula, ponto ou reformule a frase em duas frases curtas
- Escreva em português brasileiro correto e natural, com atenção especial à concordância verbal e de número/gênero. Revise mentalmente a frase antes de enviar
- Se o lead fizer uma pergunta ambígua, revise o histórico ANTES de pedir contexto. Se a pergunta dele claramente se referir a algo já mencionado no histórico (ex: uma mensagem anterior, mesmo que não escrita por você, falando de "condições especiais" ou uma oferta), entregue essa informação primeiro, no que ela realmente quer saber, antes de qualquer pergunta de qualificação. Só peça contexto ("Ah, me conta mais! O que você quer saber especificamente?") se a pergunta não tiver nenhuma referência clara no histórico.
- Quando o lead responder afirmativamente a um convite ou gancho que está no histórico (ex: "tem um momento pra eu te contar?" seguido de "Claro"), primeiro entregue o que foi prometido (as condições, diferenciais, etc.), e só depois conecte com a próxima pergunta natural do funil (ex: se já tem empresa aberta).
- Responda apenas em português brasileiro"""


AGENDORCHAT_TOKEN      = os.environ.get("AGENDORCHAT_TOKEN", "")  # CONFIGURE via variável de ambiente
# Token usado para AÇÕES VISÍVEIS ao lead (enviar mensagem, "digitando...").
# Se configurado com o token do usuário "Bot", as mensagens do Luca saem em
# nome do Bot em vez do [gestor]. Leituras e notas continuam no token principal.
LUCA_SEND_TOKEN        = os.environ.get("LUCA_SEND_TOKEN", "") or AGENDORCHAT_TOKEN
AGENDORCHAT_ACCOUNT_ID = os.environ.get("AGENDORCHAT_ACCOUNT_ID", "")  # CONFIGURE: ID da conta no AgendorChat
AGENDORCHAT_BASE       = "https://chat.agendor.com.br/api/v1"

# Criado 27/08 ([gestor]): agora existem dois WhatsApp conectados no
# AgendorChat — o inbox comercial (2367, onde o Luca atua normalmente) e
# um segundo, conectado via canal "Connector" pra teste, no qual o Luca
# NUNCA deve interagir. Lista separada por vírgula via env var, caso mais
# inboxes precisem ser excluídos no futuro.
INBOXES_IGNORADOS = {
    int(i.strip()) for i in os.environ.get("INBOXES_IGNORADOS", "").split(",") if i.strip()
}

# Criado 02/09 ([gestor]): gatilho pra demonstrações ao vivo (ex: QR Code em
# apresentação) — o Luca conversa 100% normal, mas a conversa NUNCA vira
# negócio de verdade no Agendor, e nenhum follow-up (1h/4h/D1-D10) é
# agendado pra ela. Frase pré-preenchida no link do WhatsApp (wa.me).
GATILHO_DEMO = "demonstração do luca"


LUCA_BOT_NOME_REAL = os.environ.get("LUCA_BOT_NOME_REAL", "Nome Sobrenome")  # CONFIGURE: nome exato da conta usada pra enviar  # nome real da conta que o bot usa pra enviar
LUCA_BOT_NOME_EXIBICAO = os.environ.get("LUCA_BOT_NOME_EXIBICAO", "Luca")  # available_name configurado nessa conta
NOME_DONO_CONTA_PADRAO = os.environ.get("NOME_DONO_CONTA_PADRAO", "Dono Padrao")  # CONFIGURE: dono padrão do CRM  # dono padrão do CRM — nunca escreve pessoalmente pro lead

# Corrigido 25/08 (achado real de [gestor]): o Luca manda mensagem usando a
# MESMA conta que [gestor] usa pra escrever manualmente — pro AgendorChat,
# as duas coisas são indistinguíveis (mesmo sender.id, mesmo nome). Sem
# licença sobrando pra criar uma conta separada só pro bot, a saída é essa
# marca invisível (espaço de largura zero) no final de toda mensagem que
# o Luca manda via API. Uma mensagem da mesma conta SEM essa marca é
# necessariamente o [gestor] digitando de verdade, não o bot.
LUCA_MARKER = "\u200b"

# Corrigido 02/09 (bug real, confirmado por correlação exata de horário —
# caso [lead]/conv=1939): o template_params, embora apareça certinho no
# webhook em tempo real, aparentemente não é preservado (ou não vem
# preenchido) quando lemos as mensagens de volta pela API de LISTAGEM
# (mensagens_da_conversa) — usada pelas funções de detecção de humano. Sem
# esse dado, os próprios templates que o Luca manda (D1/D3/D5/D7/D10,
# lembretes) ficavam sendo lidos como "o [gestor] escreveu de verdade".
# Como fallback independente desse metadado, mantém aqui os trechos fixos
# (sem o {{1}}) de cada template nosso — se o conteúdo bater com um deles,
# é o próprio Luca, não interessa o que o additional_attributes diga.
FRAGMENTOS_TEMPLATE_LUCA = [
    "Vi que nossa conversa ficou parada. Seja abrindo do jeito certo",
    "Passando de novo por aqui porque talvez você tenha ficado sem tempo",
    "sabia que o CNAE certo é a diferença entre pagar 15,5% ou 6% de imposto",
    "Já que vimos como o CNAE certo importa",
    "recapitulando as duas ferramentas que te mandei",
    "Você ainda está por aí?",
    "acho que peguei você num momento ruim",
]


def eh_conteudo_de_template_luca(content: str) -> bool:
    return any(frag in (content or "") for frag in FRAGMENTOS_TEMPLATE_LUCA)


def eh_assignee_bot(assignee: dict) -> bool:
    """Retorna True se o agente atribuído/remetente é o usuário do bot da
    automação (Luca) OU o dono padrão da conta do CRM — em ambos os casos,
    conversas atribuídas a ele são território do Luca (ele responde
    normalmente, nunca se cala por essa atribuição).

    Corrigido em 13/08 (achado real, confirmado por [gestor]): o Luca manda
    mensagem usando a CONTA REAL do [gestor] no Agendor — o nome de verdade
    (campo 'name') é '[gestor] Cassimiro', e 'Luca' é só um nome de exibição
    configurado nessa conta ('available_name'). A checagem anterior
    comparava só 'name' contra a env LUCA_BOT_ASSIGNEE, cujo padrão nunca
    foi nem '[gestor] Cassimiro' nem 'Luca' (valor padrão era 'Bot', nunca
    configurado no Railway) — essa checagem NUNCA bateu corretamente, em
    nenhum contexto, desde sempre. Isso explica os bugs reais de Luca
    respondendo por cima de humanos (caso [especialista]/[lead], 13/08) e o
    risco inverso de Luca se calar por engano ao encontrar a PRÓPRIA
    mensagem antiga (caso [lead]/[dono padrão], 07/08) — os dois lados desse
    mesmo problema de fundo. Agora compara contra os dois nomes reais, seja
    qual campo estiver presente no objeto checado (assignee costuma trazer
    'name'+'available_name'; sender de mensagem pode trazer só um dos
    dois, dependendo do endpoint).

    Também adicionado em 13/08: o [dono padrão] é o "dono da conta" padrão
    do Agendor, pra quem negócios/conversas sem responsável definido caem
    automaticamente (limitação da integração nativa do Agendor, ainda sem
    solução definitiva do lado deles). Confirmado por [gestor] que ele
    NUNCA responde pessoalmente pelo WhatsApp — é só um rótulo técnico do
    CRM. Por isso, conversa atribuída a ele deve continuar sendo território
    do Luca, igual ao próprio bot."""
    if not assignee:
        return False
    nome = (assignee.get("name") or "").strip().lower()
    nome_exibicao = (assignee.get("available_name") or "").strip().lower()
    return (nome == LUCA_BOT_NOME_REAL.strip().lower()
            or nome_exibicao == LUCA_BOT_NOME_EXIBICAO.strip().lower()
            or nome == LUCA_BOT_NOME_EXIBICAO.strip().lower()
            or nome == NOME_DONO_CONTA_PADRAO.strip().lower())


def remover_travessao(texto: str) -> str:
    """Rede de segurança determinística: troca qualquer travessão (—) que
    escape da instrução do prompt por vírgula. Zero custo de IA (é só
    string replace), garante 100% em vez de depender só do Claude seguir
    a regra do SYSTEM_PROMPT."""
    if not texto:
        return texto
    return texto.replace(" — ", ", ").replace("—", ", ")


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
    """Retorna a data/hora atual de Brasília por extenso, MAIS uma tabela
    com a data de cada dia da semana dos próximos 10 dias — pra o Luca
    nunca precisar calcular de cabeça 'que data cai numa segunda-feira',
    conta que o modelo erra com facilidade (bug real encontrado: lead
    pediu 'segunda' numa sexta 07/08, Luca respondeu 11/08 — que é terça,
    não segunda; a segunda certa era 10/08)."""
    agora = datetime.utcnow() - timedelta(hours=3)
    dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
            "sexta-feira", "sábado", "domingo"]
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
    tabela = []
    for n in range(1, 11):
        dia = agora + timedelta(days=n)
        tabela.append(f"{dias[dia.weekday()]} = {dia.day:02d}/{dia.month:02d}")
    return (f"\n\nDATA E HORA ATUAIS (horário de Brasília): {dias[agora.weekday()]}, "
            f"{agora.day} de {meses[agora.month - 1]} de {agora.year}, {agora.strftime('%H:%M')}. "
            f"Use esta informação ao falar de dias da semana, 'amanhã', prazos e horários de reunião.\n"
            f"PRÓXIMOS DIAS (NUNCA calcule de cabeça a data de um dia da semana — consulte aqui): "
            f"{', '.join(tabela)}.\n"
            f"Lembre-se: o atendimento é de segunda a quinta das 9h às 17h e sexta das 9h às 16h30, sem fins de semana.")


USAGE_STATS = {
    "chat":         {"chamadas": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
    "extracao":     {"chamadas": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
    "classificacao": {"chamadas": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0},  # Haiku 4.5 (20/08)
    "outro":        {"chamadas": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
}


def call_claude(messages: list, max_tokens: int = 300, system: str = SYSTEM_PROMPT, tentativas: int = 3,
                 tipo: str = "chat", model: str = "claude-sonnet-5") -> str:
    """Chama a API Anthropic e retorna o texto da resposta.
    Tenta novamente se vier resposta vazia (falha transitória rara da API) —
    sem isso, uma única resposta vazia deixava o Luca em silêncio pro lead.
    Usa prompt caching: o texto fixo do system fica marcado como cacheável,
    e a data/hora atual (que muda a cada minuto) vai à parte, sem cache —
    caso contrário o cache nunca "bateria" de uma chamada pra outra.

    Atualizado em 18/08: modelo base trocado de claude-sonnet-4-5 (antigo)
    pra claude-sonnet-5 (atual). O parâmetro 'model' permite usar um
    modelo mais barato (ex: claude-haiku-4-5-20251001) pra tarefas simples
    de classificação, sem precisar duplicar essa função."""
    system_blocks = [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": contexto_data_atual()},
    ]
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key":         ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type":      "application/json",
                },
                json={
                    "model":      model,
                    "max_tokens": max_tokens,
                    "system":     system_blocks,
                    "messages":   messages,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                print(f"[claude] Erro API status={resp.status_code} body={resp.text[:300]} "
                      f"(tentativa {tentativa}/{tentativas})", flush=True)
            resp.raise_for_status()
            data = resp.json()

            uso = data.get("usage") or {}
            bucket = USAGE_STATS.get(tipo, USAGE_STATS["outro"])
            bucket["chamadas"]    += 1
            bucket["input"]       += uso.get("input_tokens", 0)
            bucket["output"]      += uso.get("output_tokens", 0)
            bucket["cache_read"]  += uso.get("cache_read_input_tokens", 0)
            bucket["cache_write"] += uso.get("cache_creation_input_tokens", 0)

            content = data.get("content") or []
            # Corrigido em 19/08 (bug real, urgente, confirmado em produção):
            # o claude-sonnet-5 pode devolver um bloco de "thinking" ANTES
            # do texto de verdade (raciocínio estendido). O código antigo
            # só olhava content[0], assumindo que já era o texto — quando
            # vinha "thinking" primeiro, isso contava como "sem conteúdo"
            # e o Luca ficava 3 tentativas em silêncio, sem responder o
            # lead (casos reais: Leandro e Anderson, 18-19/08, nenhuma
            # resposta chegou). Agora procura o primeiro bloco do tipo
            # "text" em qualquer posição, ignorando "thinking".
            texto_bloco = next((b.get("text") for b in content if b.get("type") == "text" and b.get("text")), None)
            if texto_bloco:
                return texto_bloco.strip()
            print(f"[claude] Resposta sem conteúdo (tentativa {tentativa}/{tentativas}): "
                  f"{json.dumps(data)[:300]}", flush=True)
            ultimo_erro = ValueError("Resposta da Anthropic sem conteúdo de texto")
        except Exception as e:
            ultimo_erro = e
            print(f"[claude] Erro na chamada (tentativa {tentativa}/{tentativas}): {e}", flush=True)
        if tentativa < tentativas:
            time.sleep(2)
    raise ultimo_erro


# Histórico de conversas por conversa_id (em memória)
conversation_histories = {}

# Corrigido 15/09 ([gestor]): trava real contra a corrida entre threads
# concorrentes tentando responder a MESMA conversa ao mesmo tempo (webhook
# normal + retomada + conv_updated podem todos disparar _processar_resposta_
# luca quase simultaneamente). As checagens existentes (latest_msg_token,
# conta_respostas_apos) reduziam bastante o problema mas não eliminavam —
# sempre existe uma pequena janela entre "checar" e "agir" onde duas threads
# passam pela checagem antes de qualquer uma delas registrar que já
# respondeu (61 ocorrências reais em 5 dias de produção, todas pegas pela
# sorte do timing, não por garantia). Um Lock por conversa fecha essa
# janela de vez: só uma thread por vez processa+envia pra cada conversa.
_conv_response_locks = {}
_conv_response_locks_guard = threading.Lock()

def obter_lock_resposta(conv_key):
    with _conv_response_locks_guard:
        if conv_key not in _conv_response_locks:
            _conv_response_locks[conv_key] = threading.Lock()
        return _conv_response_locks[conv_key]

# ── Azure AD (agendamento Teams) ─────────────────────────────────────────────
AZURE_CLIENT_ID     = os.environ.get("AZURE_CLIENT_ID",     "")  # CONFIGURE via variável de ambiente
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")  # CONFIGURE via variável de ambiente
AZURE_TENANT_ID     = os.environ.get("AZURE_TENANT_ID",     "")  # CONFIGURE via variável de ambiente

# Organizador e convidados fixos da reunião com o especialista — confirmado
# com [gestor] em 11/08: o [especialista] normalmente conduz, [gestor] e Luiz ficam
# em cópia pra acompanhar. Substituição de organizador em caso de
# indisponibilidade continua manual (não previsível no momento do agendamento).
TEAMS_ORGANIZADOR = os.environ.get("TEAMS_ORGANIZADOR", "")  # CONFIGURE: e-mail do organizador
TEAMS_COPIA = [
    e.strip() for e in os.environ.get(
        "TEAMS_COPIA", ""
    ).split(",") if e.strip()
]

_azure_token_cache = {"token": None, "expira_em": 0}

# Protege a janela crítica "checagem final da agenda -> criação do Teams".
# Com um único worker, impede duas threads de reservarem o mesmo slot ao mesmo tempo.
_TEAMS_AGENDAMENTO_LOCK = threading.Lock()


def obter_token_azure() -> str:
    """Token de acesso app-only (client credentials) pra Microsoft Graph.
    Cacheado até ~5 min antes de expirar (tokens da Graph duram ~1h)."""
    if _azure_token_cache["token"] and time.time() < _azure_token_cache["expira_em"] - 300:
        return _azure_token_cache["token"]
    url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
    payload = {
        "client_id": AZURE_CLIENT_ID,
        "client_secret": AZURE_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    r = requests.post(url, data=payload, timeout=15)
    r.raise_for_status()
    data = r.json()
    _azure_token_cache["token"] = data["access_token"]
    _azure_token_cache["expira_em"] = time.time() + int(data.get("expires_in", 3600))
    return _azure_token_cache["token"]


_object_id_cache = {}


def obter_object_id_usuario(upn: str) -> str:
    """Resolve o Object ID (GUID) do usuário no Entra ID a partir do
    e-mail/UPN, com cache. Necessário porque a consulta de onlineMeetings
    por JoinWebUrl pode falhar quando se usa o e-mail/UPN diretamente no
    lugar do Object ID — mesmo que outros endpoints da Graph (como
    /events, que já funciona) aceitem o UPN sem problema. Achado real,
    reportado pela comunidade Microsoft (11/08)."""
    if upn in _object_id_cache:
        return _object_id_cache[upn]
    token = obter_token_azure()
    r = requests.get(f"https://graph.microsoft.com/v1.0/users/{upn}?$select=id",
                      headers={"Authorization": f"Bearer {token}"}, timeout=15)
    r.raise_for_status()
    object_id = r.json().get("id")
    _object_id_cache[upn] = object_id
    return object_id


PADRAO_HORARIO = re.compile(
    r"\b(segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo|hoje|amanh[ãa])\b"
    r"|\b\d{1,2}\s?[:h]\s?\d{0,2}\b",
    re.IGNORECASE
)


def parece_ter_horario(texto: str) -> bool:
    """Filtro barato (regex, sem chamar Claude) pra decidir se vale a pena
    tentar converter a mensagem numa data/hora — evita gastar uma chamada
    de parse_preferencia_datetime (que usa Claude) em toda mensagem."""
    return bool(PADRAO_HORARIO.search(texto or ""))


# Corrigido 25/09 ([gestor], bug real: Matheus, cliente existente que
# respondeu a um template de migração de cartão do CCT Automação — serviço
# separado, mesmo inbox 2367 — e o Luca tratou como lead novo, perguntando
# segmento e CNPJ, sem saber que a conversa tinha sido provocada por nós
# mesmos numa campanha pra cliente).
#
# Lógica é uma LISTA FECHADA do que É do Luca (não uma lista do que É do
# CCT) — de propósito: se listássemos os templates do CCT um por um, um
# template novo que a equipe criar no futuro (convite, aviso, etc.) não
# seria reconhecido e o bug voltaria a acontecer. Com a lista fechada dos
# templates do PRÓPRIO Luca, qualquer template que não seja um desses é
# tratado como campanha externa automaticamente, sem precisar saber o
# nome dele de antemão.
TEMPLATES_PROPRIOS_LUCA = (
    "boas_vindas_primeiro_contato",
    "followup_silencio_d1_tech", "followup_silencio_d3_tech", "followup_silencio_d5_tech",
    "followup_silencio_d7_tech", "followup_silencio_d10_tech",
    "lembrete_reuniao_amanha", "lembrete_reuniao_amanha_hora",
)
# Só os dois assuntos abaixo têm instrução ESPECÍFICA (mais precisa, porque
# sabemos exatamente do que se trata). Qualquer outro template que não seja
# do Luca cai no fallback genérico logo depois.
TEMPLATES_CCT_MIGRACAO = ("lucralize_tech_migracao_cc",)
TEMPLATES_CCT_INDICACAO = ("lucralize_tech_indicacao_premiada", "lucralize_tech_indicacao_premiada_v2")

def contexto_campanha_cct(conversation_id) -> str:
    """Se a mensagem de saída mais recente da conversa (antes da resposta
    atual do lead) foi um template que NÃO é do Luca, retorna uma instrução
    extra pra injetar no prompt desta resposta, fazendo o Luca acolher o
    cliente NAQUELE assunto em vez de tratar como lead novo. Regra do
    [gestor], 25/09: "sempre que nós provocamos um cliente a uma ação,
    acolhemos ali mesmo" — só direciona pro canal oficial se o cliente
    trouxer uma demanda diferente. Retorna "" se a mensagem de saída mais
    recente for do próprio Luca (ou não houver nenhuma)."""
    try:
        msgs = mensagens_da_conversa(conversation_id)
    except Exception as e:
        print(f"[cct-contexto] Erro ao checar campanha externa conv={conversation_id}: {e}", flush=True)
        return ""
    for m in reversed(msgs or []):
        if m.get("message_type") != 1:  # só olha mensagens de SAÍDA
            continue
        template_params = (m.get("additional_attributes") or {}).get("template_params")
        if not template_params:
            return ""  # mensagem livre, sem template nenhum — segue o fluxo normal
        # Corrigido 28/09 ([gestor], bug real: Igor, deal migração de cartão
        # — o template do CCT vem SEM o campo "name" (só "id" e "meta"),
        # diferente dos templates do próprio Luca (que sempre têm "name").
        # Antes, template sem "name" caía junto com "mensagem livre" e o
        # CCT passava despercebido. Agora: só é "mensagem livre" quando
        # template_params nem existe — se existe mas não tem nome
        # reconhecido, é tratado como campanha externa (cai no fallback
        # genérico abaixo, já que não dá pra saber qual campanha por nome).
        nome_template = template_params.get("name", "")
        if nome_template in TEMPLATES_PROPRIOS_LUCA:
            return ""  # template do próprio Luca — segue o fluxo normal
        # O CCT não manda "name", mas manda o texto de exemplo do template
        # em template_params.meta.example — usa como segundo critério pra
        # identificar qual campanha é, mesmo sem o nome.
        texto_exemplo = ((template_params.get("meta") or {}).get("example") or "").upper()
        if nome_template in TEMPLATES_CCT_MIGRACAO or "AINDA NÃO MIGROU" in texto_exemplo:
            print(f"[cct-contexto] Template de migração detectado conv={conversation_id}", flush=True)
            return (
                "\n\n[CONTEXTO IMPORTANTE: Este é um CLIENTE JÁ EXISTENTE da Lucralize Tech. Ele está "
                "respondendo a uma mensagem que NÓS enviamos (não você) sobre migrar a forma de "
                "pagamento pra cartão de crédito recorrente. NÃO trate como lead novo — não pergunte "
                "segmento, CNPJ, nem rode o roteiro de qualificação comercial. Acolha a resposta dele "
                "NESSE ASSUNTO: confirme que vai ajudar com a migração, e diga que vai confirmar o link "
                "certinho e retornar por aqui em breve (não prometa enviar o link agora — isso depende "
                "de outra equipe). Só direcione pro canal oficial de atendimento se ele trouxer uma "
                "demanda DIFERENTE, sem relação com a migração.]"
            )
        if nome_template in TEMPLATES_CCT_INDICACAO or "INDICAÇÃO PREMIADA" in texto_exemplo:
            print(f"[cct-contexto] Template de indicação detectado conv={conversation_id}", flush=True)
            return (
                "\n\n[CONTEXTO IMPORTANTE: Este é um CLIENTE JÁ EXISTENTE da Lucralize Tech. Ele está "
                "respondendo a uma mensagem que NÓS enviamos (não você) sobre o programa de Indicação "
                "Premiada. NÃO trate como lead novo — não pergunte segmento, CNPJ, nem rode o roteiro "
                "de qualificação comercial. Acolha a resposta dele NESSE ASSUNTO: pergunte se ele tem "
                "alguém pra indicar, e explique o benefício se perguntado (quem indica e quem é "
                "indicado ganham isenção da primeira mensalidade). Só direcione pro canal oficial de "
                "atendimento se ele trouxer uma demanda DIFERENTE, sem relação com indicação.]"
            )
        # Template que não reconhecemos (nem do Luca, nem migração/indicação
        # conhecidas) — fallback genérico, cobre qualquer campanha nova
        # (convite, aviso, etc.) sem precisar saber o nome dela de antemão.
        print(f"[cct-contexto] Template desconhecido tratado como campanha externa "
              f"conv={conversation_id} nome={nome_template}", flush=True)
        return (
            "\n\n[CONTEXTO IMPORTANTE: Este é um CLIENTE JÁ EXISTENTE da Lucralize Tech. Ele está "
            "respondendo a uma mensagem que NÓS enviamos (não você), de uma campanha cujo assunto "
            "exato você não tem aqui. NÃO trate como lead novo — não pergunte segmento, CNPJ, nem "
            "rode o roteiro de qualificação comercial. Pergunte educadamente sobre o que ele gostaria "
            "de saber ou fazer em relação à mensagem que recebeu, e ajude com isso dentro do possível. "
            "Só direcione pro canal oficial de atendimento se perceber que é uma demanda de suporte "
            "totalmente distinta, sem relação com a mensagem que recebeu.]"
        )
    return ""


def buscar_eventos_do_dia_organizador(dt_dia: datetime) -> list:
    """Busca os eventos reais do dia inteiro na agenda do organizador
    ([especialista]) via Microsoft Graph — 1 chamada só, depois os slots livres
    são calculados localmente em Python (barato, sem nova chamada por
    horário candidato). Retorna lista de (inicio, fim), datetimes naive
    em horário de Brasília. Levanta exceção se a chamada falhar (quem
    chama decide o fail-safe)."""
    token = obter_token_azure()
    headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.timezone="America/Sao_Paulo"'}
    inicio_dia = dt_dia.replace(hour=0, minute=0, second=0, microsecond=0)
    fim_dia = inicio_dia + timedelta(days=1)
    params = {
        "startDateTime": inicio_dia.strftime("%Y-%m-%dT%H:%M:%S"),
        "endDateTime": fim_dia.strftime("%Y-%m-%dT%H:%M:%S"),
        "$select": "subject,start,end",
        "$top": "50",
    }
    r = requests.get(f"https://graph.microsoft.com/v1.0/users/{TEAMS_ORGANIZADOR}/calendarView",
                      headers=headers, params=params, timeout=20)
    r.raise_for_status()
    eventos = []
    for ev in r.json().get("value", []):
        try:
            ini = datetime.strptime(ev["start"]["dateTime"][:19], "%Y-%m-%dT%H:%M:%S")
            fim = datetime.strptime(ev["end"]["dateTime"][:19], "%Y-%m-%dT%H:%M:%S")
            eventos.append((ini, fim))
        except Exception:
            continue
    return eventos


def checar_e_sugerir_horario(dt_pedido: datetime, duracao_min: int = 30):
    """Checa a agenda REAL do organizador (Outlook/Teams, via Graph) pro
    horário pedido pelo lead. Se estiver livre, retorna (True, []). Se
    estiver ocupado, procura até 2 horários livres no MESMO dia (passos de
    15 min, alternando pra frente e pra trás a partir do pedido, dentro do
    horário comercial 9h-17h) e retorna (False, [alternativas]).
    Fail-safe: se a chamada à agenda falhar por qualquer motivo (API fora,
    permissão, timeout etc.), considera o horário NÃO confirmado. Isso evita
    dizer ao lead que um slot está disponível sem conseguir validar a agenda;
    o fluxo segue para validação com o time em vez de correr risco de conflito."""
    try:
        eventos = buscar_eventos_do_dia_organizador(dt_pedido)
    except Exception as e:
        print(f"[disponibilidade] Erro ao buscar agenda real — horário NÃO confirmado: {e}", flush=True)
        return False, []

    fim_pedido = dt_pedido + timedelta(minutes=duracao_min)

    def livre(dt):
        fim = dt + timedelta(minutes=duracao_min)
        # Segunda a quinta (weekday 0-3): 9h-17h. Sexta (weekday 4): 9h-16h30,
        # fecha mais cedo (mesma regra já documentada no contexto do
        # SYSTEM_PROMPT — corrigido em 12/08, essa checagem usava 17h pra
        # todo dia, o que podia considerar sexta 16h45 como "disponível").
        if dt.weekday() == 4:
            fecha = dt.replace(hour=16, minute=30, second=0, microsecond=0)
        else:
            fecha = dt.replace(hour=17, minute=0, second=0, microsecond=0)
        abre = dt.replace(hour=9, minute=0, second=0, microsecond=0)
        if not (abre <= dt < fecha):
            return False
        return all(not (dt < ev_fim and fim > ev_ini) for ev_ini, ev_fim in eventos)

    if livre(dt_pedido):
        return True, []

    alternativas = []
    for passo in range(1, 25):  # até 6h de distância, 15 em 15 min
        for cand in (dt_pedido + timedelta(minutes=15 * passo), dt_pedido - timedelta(minutes=15 * passo)):
            if cand.date() != dt_pedido.date():
                continue
            if livre(cand) and cand not in alternativas:
                alternativas.append(cand)
            if len(alternativas) >= 2:
                return False, alternativas
    return False, alternativas


def extrair_emails(texto: str) -> list:
    """Extrai TODOS os e-mails válidos de um texto livre — o lead às vezes
    manda mais de um junto (ex: dois sócios), separados por 'e', vírgula
    ou ponto-e-vírgula ('joao@x.com e maria@y.com'). Antes disso, o
    código tratava o texto inteiro como um único e-mail, o que quebrava
    a atualização no CRM (Agendor rejeitava com 400) e, mais grave, podia
    impedir a criação do link real da reunião no Teams (caso real:
    [lead], 2 sócios, 17/08 — confirmado erro 400 no cadastro)."""
    if not texto:
        return []
    return re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", texto)


def create_teams_meeting(lead_name: str, lead_email: str, start_iso: str,
                          linha_negocio: str = "contabilidade", duracao_min: int = 30) -> dict:
    """Cria a reunião de verdade no Teams via Microsoft Graph, com:
      - Organizador: TEAMS_ORGANIZADOR ([especialista], por padrão)
      - Convidados em cópia: TEAMS_COPIA (só [gestor], por padrão — Luiz
        removido temporariamente em 11/08, estava recebendo e-mail demais)
      - Convidado externo: o lead (obrigatório, recebe o convite por e-mail)
    duracao_min: 30 min por padrão (confirmado com [gestor] em 11/08) — é
    margem de segurança na agenda, não é o que se promete ao lead na
    conversa (o SYSTEM_PROMPT continua dizendo "20 minutinhos" de propósito,
    a diferença cobre atraso no início/fim e evita sobrepor com a próxima).
    start_iso: horário de Brasília, formato "2026-08-12T14:00:00" (sem timezone).
    linha_negocio: "tech" ou "contabilidade", decide o título da reunião
    ("Videochamada Lucralize Tech - Nome Sobrenome" ou "... Contabilidade -
    Nome Sobrenome"; se o lead_name não tiver sobrenome, fica só o nome).

    ⚠️ GRAVAÇÃO AUTOMÁTICA: segunda opinião (ChatGPT, 11/08, cruzando com a
    documentação da Graph) confirmou e corrigiu o diagnóstico:
      - "transcribeAutomatically" NÃO existe no modelo v1.0 — removido.
        O campo real é "allowTranscription", mas esse é IMUTÁVEL depois que
        a reunião é criada (só dá pra definir na criação via /onlineMeetings,
        que por sua vez não gera convite de calendário nativo). Ou seja,
        transcrição automática via PATCH simplesmente não é possível nesse
        desenho — só "recordAutomatically" é.
      - Falta um pré-requisito que ainda não tínhamos identificado: além da
        permissão de app (OnlineMeetings.ReadWrite.All), a Microsoft exige
        uma "Application Access Policy" concedendo ao app acesso às
        reuniões de um usuário específico ([especialista]) — isso é configurado
        via PowerShell (Teams/Skype for Business Online), não pelo Azure
        Portal, e provavelmente explica os 403 mesmo com a permissão certa.
      - Object ID (não e-mail/UPN) é mesmo o formato certo pra essa chamada,
        confirmado.
    Transcrição automática ficou fora do escopo desta função — depende da
    política de conta no Teams Admin Center (pedido já feito ao TI).
    """
    token = obter_token_azure()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    emails_lead = extrair_emails(lead_email) or [lead_email]  # fallback: se não
    # achar padrão de e-mail nenhum, tenta a string bruta mesmo assim (não
    # bloqueia a criação da reunião por causa disso)
    attendees = [{"emailAddress": {"address": email, "name": lead_name},
                  "type": "required"} for email in emails_lead]
    for email_copia in TEAMS_COPIA:
        attendees.append({"emailAddress": {"address": email_copia}, "type": "optional"})

    inicio = datetime.strptime(start_iso[:19], "%Y-%m-%dT%H:%M:%S")
    fim = inicio + timedelta(minutes=duracao_min)

    nome_linha = "Lucralize Tech" if linha_negocio == "tech" else "Lucralize Contabilidade"
    subject = f"Videochamada {nome_linha} - {(lead_name or '').strip()}"

    payload = {
        "subject": subject,
        "start": {"dateTime": inicio.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "America/Sao_Paulo"},
        "end":   {"dateTime": fim.strftime("%Y-%m-%dT%H:%M:%S"),    "timeZone": "America/Sao_Paulo"},
        "attendees": attendees,
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
    }
    url = f"https://graph.microsoft.com/v1.0/users/{TEAMS_ORGANIZADOR}/events"
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    r.raise_for_status()
    evento = r.json()
    join_url = (evento.get("onlineMeeting") or {}).get("joinUrl", "")

    # Tentativa best-effort de ligar a gravação automática, não bloqueia o
    # agendamento se falhar (ver aviso na docstring acima). O ID do evento de
    # calendário NÃO é o ID do recurso onlineMeeting; é preciso buscar o
    # onlineMeeting de verdade filtrando pelo joinWebUrl antes de dar PATCH.
    try:
        if join_url:
            # Resolve o Object ID do organizador — a consulta de onlineMeetings
            # por JoinWebUrl pode falhar usando UPN/e-mail direto (achado real,
            # 11/08). Isso é diferente do /events (criação da reunião em si),
            # que já funciona normalmente com UPN.
            object_id = obter_object_id_usuario(TEAMS_ORGANIZADOR)
            # O join_url já vem com trechos percent-encoded de propósito (faz
            # parte do formato válido do link do Teams, ex: %3a, %40) — NÃO
            # pode ser codificado de novo, ou vira %253a/%2540 e quebra o
            # filtro (foi o bug das duas tentativas anteriores). Só o espaço
            # do "eq" precisa de escape aqui.
            filtro = f"JoinWebUrl eq '{join_url}'".replace(" ", "%20")
            busca = requests.get(
                f"https://graph.microsoft.com/v1.0/users/{object_id}/onlineMeetings?$filter={filtro}",
                headers=headers, timeout=15
            )
            busca.raise_for_status()
            resultados = busca.json().get("value") or []
            if resultados:
                meeting_id = resultados[0].get("id")
                patch_resp = requests.patch(
                    f"https://graph.microsoft.com/v1.0/users/{object_id}/onlineMeetings/{meeting_id}",
                    headers=headers,
                    # "recordAutomatically" é a propriedade confirmada e
                    # atualizável via PATCH no modelo v1.0 (confirmado com
                    # segunda opinião em 11/08). "transcribeAutomatically"
                    # não existe; "allowTranscription" existe mas é imutável
                    # após a criação, por isso não é tentado aqui.
                    json={"recordAutomatically": True},
                    timeout=15
                )
                patch_resp.raise_for_status()
                print(f"[teams] ✅ Gravação automática configurada com sucesso, "
                      f"meeting_id={meeting_id}", flush=True)
            else:
                print(f"[teams] Busca do onlineMeeting não retornou nenhum resultado "
                      f"pra join_url={join_url}", flush=True)
    except Exception as e:
        print(f"[teams] Gravação/transcrição automática não confirmada (não bloqueia o agendamento): {e}", flush=True)

    return {
        "join_url": join_url,
        "event_id": evento.get("id"),
        "organizador": TEAMS_ORGANIZADOR,
        "copia": TEAMS_COPIA,
        "lead_email": lead_email,
        "start": start_iso,
    }


cache = {"deals": [], "total": 0, "updated_at": None}
history_cache = {"data": [], "updated_at": None, "total_processed": 0, "total_target": 0}
tasks_cache = {"data": [], "updated_at": None}
tasks_running = False  # trava contra chamadas concorrentes de fetch_tasks_job

fetch_running = False
history_running = False

def fetch_page(page):
    """Corrigido 27/08 (revisão externa, ponto 8): trocado o backoff fixo
    (5s sempre) pelo exponencial (2s, 4s, 8s) — mesmo padrão já usado com
    sucesso em fetch_tasks_job pros 429 do Agendor. Uma espera fixa curta
    demais não dá tempo da janela de limite de taxa da API resetar,
    fazendo a 2ª e 3ª tentativa falharem pelo mesmo motivo da 1ª."""
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
                espera = 2 ** (attempt + 1)  # 2s, 4s
                time.sleep(espera)
    return None


def buscar_deal_fresco(deal_id):
    """Busca o negócio direto na API (não confia em cache), com retry —
    a chamada simples sem retry falhava com 429 na página do Agendor,
    devolvendo corpo vazio e um erro confuso de JSON ('Expecting value')
    em vez de um 429 claro. Usada em vários pontos que precisam da etapa
    fresca antes de mover (corrigido 14/08, ~22 negócios pulados por
    rodada só por causa desse erro silencioso).

    Corrigido 27/08 (revisão externa, ponto 8): backoff exponencial em
    vez de fixo em 3s, mesmo padrão das outras funções de retry."""
    for attempt in range(3):
        try:
            r = requests.get(f"{AGENDOR_BASE}/deals/{deal_id}", headers=HEADERS, timeout=15)
            if r.status_code == 429:
                raise requests.exceptions.HTTPError(f"429 na busca do deal={deal_id}")
            r.raise_for_status()
            return r.json().get("data") or {}
        except Exception as e:
            print(f"[buscar_deal_fresco] Tentativa {attempt+1}/3 falhou deal={deal_id}: {e}", flush=True)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))  # 2s, 4s
    return {}

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
    global tasks_running
    if tasks_running:
        print("[tasks] Busca já em andamento — chamada ignorada para evitar sobreposição", flush=True)
        return
    tasks_running = True
    try:
        print("Buscando tasks do Agendor...", flush=True)
        all_tasks = []
        # Revertido de 60 -> 30 dias em 20/jul/2026: com 60 dias a API do
        # Agendor retornou falha já na primeira página (Tasks: 0 carregadas,
        # sem exceção) - suspeita de limite de intervalo no createdDateGt.
        # Log abaixo registra o motivo exato se isso se repetir.
        date_gt = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        page = 1
        while page <= 100:
            # Backoff exponencial em 429/5xx, recomendado pelo suporte do Agendor
            # (sem Retry-After confiável na API — não depender dele).
            for tentativa in range(4):
                r = requests.get(
                    f"{AGENDOR_BASE}/tasks", headers=HEADERS,
                    params={"per_page": 100, "page": page, "createdDateGt": date_gt},
                    timeout=60
                )
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
                espera = 2 ** tentativa  # 1s, 2s, 4s, 8s
                print(f"[tasks] status={r.status_code} na página {page}, "
                      f"tentativa {tentativa+1}/4, aguardando {espera}s", flush=True)
                time.sleep(espera)
            if r.status_code != 200:
                print(f"[tasks] API retornou {r.status_code} na página {page} "
                      f"(createdDateGt={date_gt}): {r.text[:300]}", flush=True)
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
            time.sleep(2)  # ~1 req a cada 2s, dentro da margem segura recomendada pelo suporte
        tasks_cache["data"] = all_tasks
        tasks_cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"Tasks: {len(all_tasks)} carregadas", flush=True)
        if len(all_tasks) >= 8000:
            print(f"[alerta] Volume de tasks ({len(all_tasks)}) se aproxima do teto de paginação "
                  f"(10.000). Considerar reduzir a janela de dias antes de virar corte silencioso.",
                  flush=True)
    except Exception as e:
        print(f"Erro fetch tasks: {e}", flush=True)
    finally:
        tasks_running = False

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
        # Publica progressivamente o cache básico. Isso permite que rotinas que
        # dependem apenas dos negócios (incluindo a validação retroativa) avancem
        # sem esperar o enriquecimento pesado de produtos.
        cache["deals"] = list(all_deals)
        cache["total"] = total_count or len(all_deals)
        cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not data.get("links", {}).get("next") or len(page_deals) == 0:
            break
        page += 1
        # O limitador central já aplica AGENDOR_MIN_INTERVAL. Mantemos uma
        # margem adicional entre páginas para reduzir rajadas no startup.
        time.sleep(float(os.environ.get("AGENDOR_DEALS_PAGE_PACE_SECONDS", "1.25")))
    cache["deals"] = all_deals
    cache["total"] = total_count or len(all_deals)
    cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cutoff = datetime.utcnow() - timedelta(days=180)
    won_recent = [
        d for d in all_deals
        if d.get("dealStatus", {}).get("id") == 2
        and d.get("wonAt") and datetime.strptime(d["wonAt"][:10], "%Y-%m-%d") > cutoff
    ]
    # Durante um retroativo ativo, produtos não são necessários para a
    # correlação RD e só competem pela mesma cota da API. O cache básico já
    # está publicado; adiamos esse enriquecimento para a próxima carga normal.
    retro_status = (_rd_retro_auto_carregar().get("status") if "_rd_retro_auto_carregar" in globals() else None)
    enriquecer_produtos = retro_status not in ("executando", "aguardando_retomada", "erro_retomavel")
    if not enriquecer_produtos:
        print(f"[fetch_deals] produtos adiados durante retroativo status={retro_status}", flush=True)
        won_recent = []
    for deal in won_recent:
        try:
            r = requests.get(f"{AGENDOR_BASE}/deals/{deal['id']}/products", headers=HEADERS, timeout=15)
            if r.status_code == 200:
                products = r.json().get("data", [])
                if products:
                    deal["products_entities"] = products
        except Exception as e:
            print(f"Erro produtos {deal['id']}: {e}", flush=True)
        time.sleep(float(os.environ.get("AGENDOR_PRODUCTS_PACE_SECONDS", "1.50")))
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
    global fetch_running
    if fetch_running:
        return
    fetch_running = True
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

@app.route("/usage-stats")
def usage_stats():
    """Consumo de tokens acumulado desde o último boot do processo, separado
    por tipo de chamada (chat vs extração). Reseta a cada restart do
    container — para histórico entre restarts, ver o resumo horário no log
    do Railway (linha [usage-hora])."""
    return jsonify(USAGE_STATS), 200

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

@app.route("/refresh-followup", methods=["POST"])
def refresh_followup():
    """Dispara a régua D1->D10 (verificar_followup_dias_silencio) sob
    demanda, sem esperar o intervalo normal de 3h do scheduler. Útil pra
    validar uma correção sem precisar aguardar a próxima janela natural.
    Continua respeitando as checagens internas da função (FOLLOWUP_ATIVOS
    e o horário de envio 8h-20h BRT) — só antecipa a execução, não ignora
    as travas de segurança."""
    t = threading.Thread(target=verificar_followup_dias_silencio_safe)
    t.daemon = True
    t.start()
    return jsonify({"status": "started"}), 200

@app.route("/reset-fetch", methods=["POST"])
def reset_fetch():
    global fetch_running, history_running
    fetch_running = False
    history_running = False
    return jsonify({"status": "ok"})



# ── RD Station Marketing: OAuth2 ─────────────────────────────────────────────
# Credenciais do app ficam exclusivamente no Railway. Tokens OAuth ficam em
# arquivo no volume persistente /data para sobreviver a restart/deploy.
# Nunca registrar client_secret, code, access_token ou refresh_token em logs.
RD_CLIENT_ID = os.environ.get("RD_CLIENT_ID", "")
RD_CLIENT_SECRET = os.environ.get("RD_CLIENT_SECRET", "")
RD_OAUTH_CALLBACK = os.environ.get(
    "RD_OAUTH_CALLBACK",
    "https://agendo-proxy-production.up.railway.app/rd/oauth/callback",
)
RD_TOKEN_URL = "https://api.rd.services/auth/token"
RD_TOKEN_FILE = os.environ.get("RD_TOKEN_FILE", "/data/rd_oauth.json")

_rd_token_lock = threading.Lock()
_rd_tokens = {
    "access_token": os.environ.get("RD_ACCESS_TOKEN", ""),
    "refresh_token": os.environ.get("RD_REFRESH_TOKEN", ""),
    "expires_at": 0.0,
}

def _rd_carregar_tokens_persistidos():
    """Carrega tokens do volume. Falha de leitura não derruba o Luca."""
    if not os.path.isfile(RD_TOKEN_FILE):
        return
    try:
        with open(RD_TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        access = (data or {}).get("access_token") or ""
        refresh = (data or {}).get("refresh_token") or ""
        expires_at = float((data or {}).get("expires_at") or 0)
        if not access or not refresh:
            raise ValueError("arquivo OAuth incompleto")
        with _rd_token_lock:
            _rd_tokens.update({
                "access_token": access,
                "refresh_token": refresh,
                "expires_at": expires_at,
            })
        print("[rd-oauth] tokens persistidos carregados; valores omitidos", flush=True)
    except Exception as e:
        print(f"[rd-oauth] não foi possível carregar tokens persistidos: {type(e).__name__}", flush=True)

def _rd_persistir_tokens_locked():
    """Persiste atomicamente o estado OAuth. Deve ser chamada com o lock adquirido."""
    diretorio = os.path.dirname(RD_TOKEN_FILE) or "."
    os.makedirs(diretorio, exist_ok=True)
    temporario = RD_TOKEN_FILE + ".tmp"
    payload = {
        "access_token": _rd_tokens["access_token"],
        "refresh_token": _rd_tokens["refresh_token"],
        "expires_at": _rd_tokens["expires_at"],
        "updated_at": time.time(),
    }
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(payload, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporario, RD_TOKEN_FILE)
    try:
        os.chmod(RD_TOKEN_FILE, 0o600)
    except OSError:
        pass

def _rd_salvar_tokens(data):
    access = (data or {}).get("access_token") or ""
    refresh = (data or {}).get("refresh_token") or ""
    if not access or not refresh:
        raise ValueError("Resposta do RD não trouxe access_token e refresh_token")
    expires_in = int((data or {}).get("expires_in") or 86400)
    with _rd_token_lock:
        _rd_tokens["access_token"] = access
        _rd_tokens["refresh_token"] = refresh
        _rd_tokens["expires_at"] = time.time() + expires_in
        _rd_persistir_tokens_locked()
    print("[rd-oauth] tokens atualizados e persistidos; valores omitidos", flush=True)

# Prioriza o volume persistente; variáveis RD_ACCESS_TOKEN/RD_REFRESH_TOKEN
# permanecem apenas como fallback de migração/recuperação.
_rd_carregar_tokens_persistidos()

def _rd_trocar_code_por_tokens(code):
    r = requests.post(
        f"{RD_TOKEN_URL}?token_by=code",
        json={
            "client_id": RD_CLIENT_ID,
            "client_secret": RD_CLIENT_SECRET,
            "code": code,
        },
        timeout=20,
    )
    if r.status_code != 200:
        print(f"[rd-oauth] falha na troca do code: status={r.status_code}", flush=True)
        raise RuntimeError(f"RD token exchange falhou com HTTP {r.status_code}")
    _rd_salvar_tokens(r.json())

def _rd_renovar_access_token():
    with _rd_token_lock:
        refresh = _rd_tokens.get("refresh_token") or ""
    if not refresh:
        raise RuntimeError("RD refresh_token ainda não está disponível")
    r = requests.post(
        RD_TOKEN_URL,
        json={
            "client_id": RD_CLIENT_ID,
            "client_secret": RD_CLIENT_SECRET,
            "refresh_token": refresh,
        },
        timeout=20,
    )
    if r.status_code != 200:
        print(f"[rd-oauth] falha ao renovar access_token: status={r.status_code}", flush=True)
        raise RuntimeError(f"RD refresh falhou com HTTP {r.status_code}")
    _rd_salvar_tokens(r.json())

def _rd_obter_access_token():
    with _rd_token_lock:
        token = _rd_tokens.get("access_token") or ""
        expira = float(_rd_tokens.get("expires_at") or 0)
    # Tokens carregados do ambiente não têm expires_at conhecido; usa até 401.
    if token and (not expira or time.time() < expira - 300):
        return token
    _rd_renovar_access_token()
    with _rd_token_lock:
        return _rd_tokens["access_token"]

def _rd_diagnostico_historico_leitura():
    """Teste estritamente GET da API histórica do RD; não expõe token nem PII."""
    try:
        token = _rd_obter_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        email_teste = "xevovol377@deertees.com"
        contato_url = "https://api.rd.services/platform/contacts/email:" + quote(email_teste, safe="@")
        r_contato = requests.get(contato_url, headers=headers, timeout=20)
        print(f"[rd-read-test] contato HTTP={r_contato.status_code}", flush=True)
        if r_contato.status_code != 200:
            return
        contato = r_contato.json() if r_contato.content else {}
        uuid = (contato or {}).get("uuid") or ""
        print(f"[rd-read-test] contato_ok uuid_presente={bool(uuid)} campos={sorted((contato or {}).keys())}", flush=True)
        if not uuid:
            return
        eventos_url = f"https://api.rd.services/platform/contacts/{quote(uuid, safe='')}/events"
        r_eventos = requests.get(eventos_url, headers=headers, params={"event_type": "CONVERSION"}, timeout=20)
        print(f"[rd-read-test] eventos CONVERSION HTTP={r_eventos.status_code}", flush=True)
        if r_eventos.status_code != 200:
            return
        payload = r_eventos.json() if r_eventos.content else {}
        if isinstance(payload, dict):
            eventos = payload.get("events") or payload.get("data") or payload.get("items") or []
            print(f"[rd-read-test] resposta_eventos campos={sorted(payload.keys())} qtd_detectada={len(eventos) if isinstance(eventos, list) else 'n/d'}", flush=True)
        elif isinstance(payload, list):
            eventos = payload
            print(f"[rd-read-test] resposta_eventos lista qtd={len(eventos)}", flush=True)
        else:
            eventos = []
            print("[rd-read-test] formato_eventos_nao_reconhecido", flush=True)
        if isinstance(eventos, list) and eventos:
            amostra = eventos[0] if isinstance(eventos[0], dict) else {}
            conteudo = amostra.get("payload") or amostra.get("content") or amostra.get("conversion") or {}
            print(f"[rd-read-test] amostra campos={sorted(amostra.keys())} campos_conteudo={sorted(conteudo.keys()) if isinstance(conteudo, dict) else []}", flush=True)
            if isinstance(conteudo, dict):
                marketing = {}
                for chave in ("conversion_identifier", "event_identifier", "traffic_source", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id"):
                    valor = conteudo.get(chave)
                    if valor not in (None, "", [], {}):
                        marketing[chave] = valor
                print(f"[rd-read-test] marketing={marketing}", flush=True)
        print("[rd-read-test] concluido_sem_escrita", flush=True)
    except Exception as e:
        print(f"[rd-read-test] erro={type(e).__name__}: {str(e)[:180]}", flush=True)

@app.route("/rd/oauth/callback", methods=["GET"])
def rd_oauth_callback():
    """Recebe o code OAuth do RD Marketing e o troca pelos tokens sem expô-los."""
    erro = (request.args.get("error") or "").strip()
    erro_descricao = (request.args.get("error_description") or "").strip()
    code = (request.args.get("code") or "").strip()

    if erro:
        print(f"[rd-oauth] autorização recusada/erro: {erro}", flush=True)
        return jsonify({
            "status": "erro",
            "mensagem": erro_descricao or "O RD Station não autorizou a integração.",
        }), 400

    if not code:
        print("[rd-oauth] callback recebido sem code", flush=True)
        return jsonify({
            "status": "erro",
            "mensagem": "Callback recebido sem código de autorização.",
        }), 400

    if not RD_CLIENT_ID or not RD_CLIENT_SECRET:
        print("[rd-oauth] credenciais RD ausentes no ambiente", flush=True)
        return jsonify({
            "status": "erro",
            "mensagem": "Credenciais do RD Station não estão configuradas no servidor.",
        }), 500

    try:
        # Nunca registrar o code: credencial temporária de uso único.
        _rd_trocar_code_por_tokens(code)
        print("[rd-oauth] autorização concluída; tokens recebidos e valores omitidos do log", flush=True)
        t_diag = threading.Thread(target=_rd_diagnostico_historico_leitura, daemon=True)
        t_diag.start()
        return jsonify({
            "status": "ok",
            "mensagem": "RD Station conectado ao Luca com sucesso.",
            "tokens_recebidos": True,
        }), 200
    except Exception as e:
        print(f"[rd-oauth] erro concluindo autorização: {type(e).__name__}: {e}", flush=True)
        return jsonify({
            "status": "erro",
            "mensagem": "O RD autorizou a conexão, mas houve erro ao gerar os tokens.",
        }), 502

# ── RD Station: webhook V3 — correlação + enriquecimento seguro ─────────────
# Recebe a conversão, localiza de forma conservadora o negócio correspondente
# e preenche SOMENTE campos vazios. Identificadores desconhecidos são ignorados.

# DE/PARA fechado: só estes identificadores podem escrever Origem do Negócio.
# Novos identificadores devem ser validados antes de entrar aqui.
RD_ORIGEM_DE_PARA = {
    "calculadora-impostos-desenvolvedores": "calculadora-impostos-desenvolvedores",
    "Transformação do MEI": "Transformação do MEI",
    "whatsapp_pagina": "whatsapp_pagina",
    "leo-marconi": "leo-marconi",
    "Formulário Meta Afiliados - Alexia": "Formulário Meta Afiliados - Alexia",
}

# Slugs esperados dos campos personalizados do negócio no Agendor.
def _rd_mapear_origem_negocio(identificador):
    """Regra validada: qualquer calculadora sem 'dividendos' é a calculadora dev."""
    valor = (identificador or "").strip()
    normalizado = valor.lower()
    if "calculadora" in normalizado:
        if "dividendos" in normalizado:
            return "calculadora-de-impostos-e-ir-sobre-dividendos"
        return "calculadora-impostos-desenvolvedores"
    return RD_ORIGEM_DE_PARA.get(valor)


RD_CAMPOS_AGENDOR = {
    "origem": "origem",
    "campanha": "campanha",
    "grupo_anuncio": "grupo_de_anuncio",
    "anuncio": "anuncio",
    "meta_ads_source_id": "meta_ads_source_id",
}


def _rd_parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _rd_normalizar_telefone(value):
    return re.sub(r"\D", "", str(value or ""))


def _rd_parse_conversion_payload(content):
    """Extrai UTMs do conversion_payload real do RD, sem inventar valores."""
    raw = content.get("conversion_payload")
    if not raw:
        original = content.get("__cdp__original_event") or {}
        raw = (original.get("payload") or {}).get("conversion_payload")
    if not raw:
        return {}
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        query_string = (obj or {}).get("query_params") or ""
        qs = parse_qs(query_string, keep_blank_values=False)
        return {k: (v[0] if isinstance(v, list) and v else v) for k, v in qs.items()}
    except Exception as e:
        print(f"[rd-webhook] Falha lendo conversion_payload/UTMs: {e}", flush=True)
        return {}


def _rd_extrair_lead(lead):
    """Extrai os campos confirmados no payload real do webhook do RD."""
    last_conversion = lead.get("last_conversion") or {}
    content = last_conversion.get("content") or {}
    original = content.get("__cdp__original_event") or {}
    original_payload = original.get("payload") or {}
    utms = _rd_parse_conversion_payload(content)

    # Alguns formulários do RD entregam atribuição em traffic_source como
    # query string. conversion_payload continua tendo prioridade; só completa
    # chaves ausentes, sem substituir o que já veio na fonte principal.
    traffic_source = content.get("traffic_source") or ""
    if traffic_source:
        try:
            qs_traffic = parse_qs(str(traffic_source).lstrip("?"), keep_blank_values=False)
            for chave in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_id", "utm_term"):
                if not utms.get(chave):
                    valores = qs_traffic.get(chave) or []
                    if valores:
                        utms[chave] = valores[0]
        except Exception as e:
            print(f"[rd-webhook] Falha lendo traffic_source/UTMs: {e}", flush=True)

    identificador = (
        content.get("event_identifier")
        or content.get("conversion_identifier")
        or content.get("identificador")
        or last_conversion.get("source")
    )
    email = lead.get("email") or content.get("email_lead") or original_payload.get("email")
    telefone = (
        lead.get("mobile_phone") or lead.get("phone")
        or content.get("Celular") or content.get("phone_lead")
        or original_payload.get("mobile_phone")
    )
    data_conversao = (
        content.get("event_timestamp") or content.get("created_at")
        or last_conversion.get("created_at") or lead.get("created_at")
    )
    return {
        "rd_id": lead.get("id"),
        "nome": lead.get("name") or content.get("Nome"),
        "email": (email or "").strip().lower(),
        "telefone": telefone,
        "telefone_norm": _rd_normalizar_telefone(telefone),
        "identificador": identificador,
        "data_conversao": data_conversao,
        "utm_source": utms.get("utm_source"),
        "utm_medium": utms.get("utm_medium"),
        "utm_campaign": utms.get("utm_campaign"),
        "utm_content": utms.get("utm_content"),
        "utm_id": utms.get("utm_id"),
        "utm_term": utms.get("utm_term"),
    }


def _rd_buscar_pessoas(email, telefone_norm):
    """Busca pessoas por e-mail e telefone e elimina duplicatas por ID."""
    encontradas = {}
    buscas = []
    if email:
        buscas.append(("email", email))
    if telefone_norm:
        buscas.append(("phone", telefone_norm))

    for campo, valor in buscas:
        try:
            r = requests.get(f"{AGENDOR_BASE}/people", headers=HEADERS,
                             params={campo: valor}, timeout=15)
            r.raise_for_status()
            for pessoa in r.json().get("data", []):
                if pessoa.get("id"):
                    encontradas[pessoa["id"]] = pessoa
        except Exception as e:
            print(f"[rd-match] Falha buscando pessoa por {campo}={valor!r}: {e}", flush=True)
    return list(encontradas.values())


def _rd_valor_atual(custom, slug):
    """Normaliza retorno do Agendor: campo pode vir simples ou {id,value}."""
    atual = custom.get(slug)
    if isinstance(atual, dict):
        return atual.get("value")
    return atual


# ── RD Station: retroativo em DRY-RUN (somente leitura) ─────────────────────
# Esta rotina NÃO possui nenhum requests.put/post/patch/delete. Ela parte dos
# negócios RD Station já existentes no Agendor nos últimos N dias, localiza o
# contato correspondente no RD por e-mail e lê as conversões históricas. O
# resultado é apenas uma simulação dos campos que poderiam ser preenchidos.
RD_DRYRUN_MAX_DIAS = 30
RD_DRYRUN_MAX_ITENS = 250
_rd_dryrun_lock = threading.Lock()
_rd_dryrun_state = {
    "status": "nunca_executado",
    "started_at": None,
    "finished_at": None,
    "resultado": None,
    "erro": None,
}


def _rd_eventos_lista(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for chave in ("events", "data", "items"):
            valor = payload.get(chave)
            if isinstance(valor, list):
                return valor
    return []


def _rd_evento_para_dados(evento, contato, fallback_email=""):
    """Converte um evento histórico CONVERSION para o mesmo formato do webhook."""
    if not isinstance(evento, dict):
        return {}
    conteudo = evento.get("payload") or evento.get("content") or evento.get("conversion") or {}
    if not isinstance(conteudo, dict):
        conteudo = {}

    # Reaproveita exatamente o parser de UTMs já validado no webhook.
    pseudo_lead = {
        "id": (contato or {}).get("uuid") or (contato or {}).get("id"),
        "name": (contato or {}).get("name"),
        "email": (contato or {}).get("email") or fallback_email,
        "mobile_phone": (contato or {}).get("mobile_phone"),
        "phone": (contato or {}).get("phone"),
        "last_conversion": {
            "source": evento.get("source"),
            "created_at": evento.get("created_at") or evento.get("event_timestamp"),
            "content": conteudo,
        },
    }
    dados = _rd_extrair_lead(pseudo_lead)
    if not dados.get("data_conversao"):
        dados["data_conversao"] = (
            evento.get("event_timestamp") or evento.get("created_at")
            or evento.get("timestamp") or evento.get("date")
        )
    return dados


def _rd_deal_person_id(deal):
    for chave in ("person", "personEntity"):
        obj = deal.get(chave)
        if isinstance(obj, dict) and obj.get("id"):
            return obj.get("id")
    for chave in ("personId", "person_id"):
        if deal.get(chave):
            return deal.get(chave)
    return None


def _rd_dryrun_deals_candidatos(dias, limite):
    """Seleciona negócios recentes do funil comercial, sem escrever.

    A origem RD NÃO é inferida nesta seleção. Ela só é confirmada depois,
    usando contato RD + evento CONVERSION + janela temporal + DE/PARA.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=dias)
    candidatos = []
    deals_cache = list(cache.get("deals") or [])
    no_funil = 0
    com_data = 0
    no_periodo = 0
    for deal in deals_cache:
        stage = deal.get("dealStage") or {}
        funnel_id = (stage.get("funnel") or {}).get("id")
        if funnel_id != FUNIL_COMERCIAL_ID:
            continue
        no_funil += 1
        dt = _rd_parse_iso(deal.get("startTime"))
        if not dt:
            continue
        com_data += 1
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(timezone.utc) < cutoff:
            continue
        no_periodo += 1
        candidatos.append(deal)
    candidatos.sort(key=lambda d: _rd_parse_iso(d.get("startTime")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    print(
        f"[rd-dryrun] selecao cache_total={len(deals_cache)} funil_comercial={no_funil} "
        f"com_startTime={com_data} ultimos_{dias}d={no_periodo} selecionados={min(len(candidatos), limite)}",
        flush=True,
    )
    return candidatos[:limite]


def _rd_dryrun_executar(dias=30, limite=250):
    """Executa o retroativo em modo estritamente GET e guarda apenas o relatório."""
    if not _rd_dryrun_lock.acquire(blocking=False):
        return False
    try:
        _rd_dryrun_state.update({
            "status": "executando", "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "resultado": None, "erro": None,
            "progresso": {"atual": 0, "total": 0, "deal_id": None},
        })
        token = _rd_obter_access_token()
        rd_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        deals = _rd_dryrun_deals_candidatos(dias, limite)
        resumo = {
            "modo": "DRY_RUN_SOMENTE_GET",
            "dias": dias,
            "limite": limite,
            "candidatos_agendor": len(deals),
            "analisados": 0,
            "contatos_rd_encontrados": 0,
            "eventos_conversao_lidos": 0,
            "match_seguro": 0,
            "sem_match_temporal": 0,
            "sem_contato_rd": 0,
            "sem_email": 0,
            "identificador_fora_depara": 0,
            "identificadores_fora_depara": {},
            "ja_totalmente_preenchido": 0,
            "erros": 0,
            "would_fill_por_campo": {
                "origem_do_negocio": 0, "origem": 0, "campanha": 0,
                "grupo_de_anuncio": 0, "anuncio": 0, "meta_ads_source_id": 0,
            },
            "amostras": [],
        }

        total_deals = len(deals)
        _rd_dryrun_state["progresso"] = {"atual": 0, "total": total_deals, "deal_id": None}
        print(f"[rd-dryrun] inicio dias={dias} limite={limite} candidatos={total_deals}", flush=True)

        for indice, deal_base in enumerate(deals, start=1):
            resumo["analisados"] += 1
            deal_id = deal_base.get("id")
            _rd_dryrun_state["progresso"] = {"atual": indice, "total": total_deals, "deal_id": deal_id}
            if indice == 1 or indice % 10 == 0 or indice == total_deals:
                print(f"[rd-dryrun] progresso {indice}/{total_deals} deal={deal_id}", flush=True)
            try:
                # GET fresco: precisamos dos customFields atuais e do person id.
                r_deal = requests.get(
                    f"{AGENDOR_BASE}/deals/{deal_id}", headers=HEADERS,
                    params={"withCustomFields": "true"}, timeout=20,
                )
                r_deal.raise_for_status()
                deal = r_deal.json().get("data") or r_deal.json()
                custom = deal.get("customFields") or {}
                person_id = _rd_deal_person_id(deal) or _rd_deal_person_id(deal_base)
                email = ""
                if person_id:
                    r_pessoa = requests.get(f"{AGENDOR_BASE}/people/{person_id}", headers=HEADERS, timeout=20)
                    if r_pessoa.status_code == 200:
                        pessoa = r_pessoa.json().get("data") or r_pessoa.json()
                        email = (pessoa.get("email") or "").strip().lower()
                        if not email:
                            emails = pessoa.get("emails") or []
                            if isinstance(emails, list) and emails:
                                primeiro = emails[0]
                                email = ((primeiro.get("email") if isinstance(primeiro, dict) else primeiro) or "").strip().lower()
                if not email:
                    resumo["sem_email"] += 1
                    continue

                r_contato = requests.get(
                    "https://api.rd.services/platform/contacts/email:" + quote(email, safe="@"),
                    headers=rd_headers, timeout=20,
                )
                if r_contato.status_code == 404:
                    resumo["sem_contato_rd"] += 1
                    continue
                r_contato.raise_for_status()
                contato = r_contato.json() if r_contato.content else {}
                uuid = (contato or {}).get("uuid") or ""
                if not uuid:
                    resumo["sem_contato_rd"] += 1
                    continue
                resumo["contatos_rd_encontrados"] += 1

                r_eventos = requests.get(
                    f"https://api.rd.services/platform/contacts/{quote(uuid, safe='')}/events",
                    headers=rd_headers, params={"event_type": "CONVERSION"}, timeout=20,
                )
                r_eventos.raise_for_status()
                eventos = _rd_eventos_lista(r_eventos.json() if r_eventos.content else {})
                resumo["eventos_conversao_lidos"] += len(eventos)

                dt_deal = _rd_parse_iso(deal.get("startTime") or deal_base.get("startTime"))
                if dt_deal and dt_deal.tzinfo is None:
                    dt_deal = dt_deal.replace(tzinfo=timezone.utc)
                plausiveis = []
                for evento in eventos:
                    dados = _rd_evento_para_dados(evento, contato, email)
                    dt_rd = _rd_parse_iso(dados.get("data_conversao"))
                    if dt_rd and dt_rd.tzinfo is None:
                        dt_rd = dt_rd.replace(tzinfo=timezone.utc)
                    if not dt_rd or not dt_deal:
                        continue
                    diff = abs((dt_deal.astimezone(timezone.utc) - dt_rd.astimezone(timezone.utc)).total_seconds())
                    if diff <= 20:
                        plausiveis.append((diff, dados))

                plausiveis.sort(key=lambda x: x[0])
                if not plausiveis:
                    resumo["sem_match_temporal"] += 1
                    continue
                # Mais de um evento na mesma janela pode indicar atribuição ambígua.
                if len(plausiveis) > 1 and abs(plausiveis[1][0] - plausiveis[0][0]) < 1:
                    resumo["sem_match_temporal"] += 1
                    continue
                diff, dados = plausiveis[0]
                origem_negocio = _rd_mapear_origem_negocio(dados.get("identificador"))
                if not origem_negocio:
                    resumo["identificador_fora_depara"] += 1
                    identificador = str(dados.get("identificador") or "(vazio)").strip() or "(vazio)"
                    fora = resumo["identificadores_fora_depara"]
                    fora[identificador] = fora.get(identificador, 0) + 1
                    continue
                resumo["match_seguro"] += 1

                desejados = {
                    "origem_do_negocio": origem_negocio,
                    "origem": dados.get("utm_source"),
                    "campanha": dados.get("utm_campaign"),
                    "grupo_de_anuncio": dados.get("utm_term"),
                    "anuncio": dados.get("utm_content"),
                    "meta_ads_source_id": dados.get("utm_id"),
                }
                would_fill = {}
                ja_preenchidos = []
                for slug, valor in desejados.items():
                    if valor is None or str(valor).strip() == "":
                        continue
                    atual = _rd_valor_atual(custom, slug)
                    if atual not in (None, "", [], {}):
                        ja_preenchidos.append(slug)
                        continue
                    would_fill[slug] = str(valor).strip()
                    resumo["would_fill_por_campo"][slug] += 1
                if not would_fill:
                    resumo["ja_totalmente_preenchido"] += 1
                if len(resumo["amostras"]) < 25:
                    resumo["amostras"].append({
                        "deal_id": deal_id,
                        "identificador": dados.get("identificador"),
                        "diferenca_seg": round(diff, 1),
                        "would_fill": would_fill,
                        "ja_preenchidos": ja_preenchidos,
                    })
            except Exception as e:
                resumo["erros"] += 1
                print(f"[rd-dryrun] erro deal={deal_id}: {type(e).__name__}: {str(e)[:180]}", flush=True)

        resumo["identificadores_fora_depara"] = dict(
            sorted(resumo["identificadores_fora_depara"].items(), key=lambda item: (-item[1], item[0]))
        )

        _rd_dryrun_state.update({
            "status": "concluido", "finished_at": datetime.now(timezone.utc).isoformat(),
            "resultado": resumo, "erro": None,
            "progresso": {"atual": resumo.get("analisados", 0), "total": len(deals), "deal_id": None},
        })
        print(f"[rd-dryrun] concluido resumo={json.dumps(resumo, ensure_ascii=False, default=str)}", flush=True)
        return True
    except Exception as e:
        _rd_dryrun_state.update({
            "status": "erro", "finished_at": datetime.now(timezone.utc).isoformat(),
            "erro": f"{type(e).__name__}: {str(e)[:300]}",
        })
        print(f"[rd-dryrun] erro_geral={type(e).__name__}: {str(e)[:180]}", flush=True)
        return False
    finally:
        _rd_dryrun_lock.release()


@app.route("/rd/retroativo/dry-run", methods=["POST", "GET"])
def rd_retroativo_dry_run():
    """Dispara/consulta o dry-run. Exige a mesma chave privada da rota /agendar."""
    # Ao contrário da rota /agendar antiga, aqui NÃO existe fail-open: se a
    # chave não estiver configurada, a rota fica indisponível por segurança.
    if not AGENDAR_API_KEY:
        return jsonify({"status": "indisponivel", "mensagem": "AGENDAR_API_KEY não configurada"}), 503
    if request.headers.get("X-API-Key", "") != AGENDAR_API_KEY:
        return jsonify({"status": "nao_autorizado"}), 401

    if request.method == "GET":
        return jsonify(_rd_dryrun_state), 200

    if _rd_dryrun_state.get("status") == "executando":
        return jsonify(_rd_dryrun_state), 202
    t = threading.Thread(target=_rd_dryrun_executar, args=(RD_DRYRUN_MAX_DIAS, RD_DRYRUN_MAX_ITENS), daemon=True)
    t.start()
    return jsonify({
        "status": "iniciado", "modo": "DRY_RUN_SOMENTE_GET",
        "dias": RD_DRYRUN_MAX_DIAS, "limite": RD_DRYRUN_MAX_ITENS,
    }), 202


# ── RD Station: retroativo controlado (escrita SOMENTE origem_do_negocio) ──
# Esta rotina é deliberadamente mais restritiva que o webhook em tempo real.
# Revalida tudo no momento da execução e só grava origem_do_negocio quando:
# - negócio está no Funil Comercial e dentro dos últimos 30 dias;
# - pessoa possui e-mail e existe contato correspondente no RD;
# - existe evento CONVERSION com diferença temporal <= 20 segundos;
# - não há empate/ambiguidade temporal;
# - identificador pertence ao DE/PARA fechado;
# - origem_do_negocio continua vazia no GET fresco do negócio.
# Nenhum outro campo é alterado por esta rotina.
RD_RETRO_WRITE_MAX_DIAS = 30
RD_RETRO_WRITE_MAX_ITENS = 250
RD_RETRO_WRITE_CONFIRM = "CONFIRMAR_RETROATIVO_RD"
_rd_retro_write_lock = threading.Lock()
_rd_retro_write_state = {
    "status": "nunca_executado",
    "started_at": None,
    "finished_at": None,
    "resultado": None,
    "erro": None,
    "progresso": {"atual": 0, "total": 0, "deal_id": None},
}


def _rd_retro_write_executar(dias=RD_RETRO_WRITE_MAX_DIAS, limite=RD_RETRO_WRITE_MAX_ITENS):
    if not _rd_retro_write_lock.acquire(blocking=False):
        return False
    try:
        _rd_retro_write_state.update({
            "status": "executando",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "resultado": None,
            "erro": None,
            "progresso": {"atual": 0, "total": 0, "deal_id": None},
        })
        token = _rd_obter_access_token()
        rd_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        deals = _rd_dryrun_deals_candidatos(dias, limite)
        resumo = {
            "modo": "RETROATIVO_RD_ORIGEM_CONTROLADO",
            "dias": dias,
            "limite": limite,
            "candidatos_agendor": len(deals),
            "analisados": 0,
            "match_seguro": 0,
            "atualizados": 0,
            "ja_preenchidos": 0,
            "sem_match_temporal": 0,
            "sem_contato_rd": 0,
            "sem_email": 0,
            "identificador_fora_depara": 0,
            "ambiguos": 0,
            "erros": 0,
            "atualizacoes": [],
        }
        total = len(deals)
        _rd_retro_write_state["progresso"] = {"atual": 0, "total": total, "deal_id": None}
        print(f"[rd-retro-write] inicio dias={dias} limite={limite} candidatos={total}", flush=True)

        for indice, deal_base in enumerate(deals, start=1):
            resumo["analisados"] += 1
            deal_id = deal_base.get("id")
            _rd_retro_write_state["progresso"] = {"atual": indice, "total": total, "deal_id": deal_id}
            if indice == 1 or indice % 10 == 0 or indice == total:
                print(f"[rd-retro-write] progresso {indice}/{total} deal={deal_id}", flush=True)
            try:
                # 1) GET fresco do negócio: nenhuma decisão de escrita usa só o cache.
                r_deal = requests.get(
                    f"{AGENDOR_BASE}/deals/{deal_id}", headers=HEADERS,
                    params={"withCustomFields": "true"}, timeout=20,
                )
                r_deal.raise_for_status()
                deal = r_deal.json().get("data") or r_deal.json()

                stage = deal.get("dealStage") or {}
                if ((stage.get("funnel") or {}).get("id")) != FUNIL_COMERCIAL_ID:
                    continue

                # Se alguém preencheu a origem depois do dry-run, preserva imediatamente.
                custom = deal.get("customFields") or {}
                atual = _rd_valor_atual(custom, "origem_do_negocio")
                if atual not in (None, "", [], {}):
                    resumo["ja_preenchidos"] += 1
                    continue

                dt_deal = _rd_parse_iso(deal.get("startTime") or deal_base.get("startTime"))
                if not dt_deal:
                    resumo["sem_match_temporal"] += 1
                    continue
                if dt_deal.tzinfo is None:
                    dt_deal = dt_deal.replace(tzinfo=timezone.utc)

                # 2) Resolve a pessoa/e-mail novamente.
                person_id = _rd_deal_person_id(deal) or _rd_deal_person_id(deal_base)
                email = ""
                if person_id:
                    r_pessoa = requests.get(f"{AGENDOR_BASE}/people/{person_id}", headers=HEADERS, timeout=20)
                    if r_pessoa.status_code == 200:
                        pessoa = r_pessoa.json().get("data") or r_pessoa.json()
                        email = (pessoa.get("email") or "").strip().lower()
                        if not email:
                            emails = pessoa.get("emails") or []
                            if isinstance(emails, list) and emails:
                                primeiro = emails[0]
                                email = ((primeiro.get("email") if isinstance(primeiro, dict) else primeiro) or "").strip().lower()
                if not email:
                    resumo["sem_email"] += 1
                    continue

                # 3) Contato e conversões históricos do RD, somente leitura.
                r_contato = requests.get(
                    "https://api.rd.services/platform/contacts/email:" + quote(email, safe="@"),
                    headers=rd_headers, timeout=20,
                )
                if r_contato.status_code == 404:
                    resumo["sem_contato_rd"] += 1
                    continue
                r_contato.raise_for_status()
                contato = r_contato.json() if r_contato.content else {}
                uuid = (contato or {}).get("uuid") or ""
                if not uuid:
                    resumo["sem_contato_rd"] += 1
                    continue

                r_eventos = requests.get(
                    f"https://api.rd.services/platform/contacts/{quote(uuid, safe='')}/events",
                    headers=rd_headers, params={"event_type": "CONVERSION"}, timeout=20,
                )
                r_eventos.raise_for_status()
                eventos = _rd_eventos_lista(r_eventos.json() if r_eventos.content else {})

                plausiveis = []
                for evento in eventos:
                    dados = _rd_evento_para_dados(evento, contato, email)
                    dt_rd = _rd_parse_iso(dados.get("data_conversao"))
                    if not dt_rd:
                        continue
                    if dt_rd.tzinfo is None:
                        dt_rd = dt_rd.replace(tzinfo=timezone.utc)
                    diff = abs((dt_deal.astimezone(timezone.utc) - dt_rd.astimezone(timezone.utc)).total_seconds())
                    if diff <= 20:
                        plausiveis.append((diff, dados))

                plausiveis.sort(key=lambda x: x[0])
                if not plausiveis:
                    resumo["sem_match_temporal"] += 1
                    continue
                if len(plausiveis) > 1 and abs(plausiveis[1][0] - plausiveis[0][0]) < 1:
                    resumo["ambiguos"] += 1
                    continue

                diff, dados = plausiveis[0]
                origem = _rd_mapear_origem_negocio(dados.get("identificador"))
                if not origem:
                    resumo["identificador_fora_depara"] += 1
                    continue
                resumo["match_seguro"] += 1

                # 4) Última trava imediatamente antes do PUT: GET fresco novamente.
                r_final = requests.get(
                    f"{AGENDOR_BASE}/deals/{deal_id}", headers=HEADERS,
                    params={"withCustomFields": "true"}, timeout=20,
                )
                r_final.raise_for_status()
                deal_final = r_final.json().get("data") or r_final.json()
                stage_final = deal_final.get("dealStage") or {}
                if ((stage_final.get("funnel") or {}).get("id")) != FUNIL_COMERCIAL_ID:
                    continue
                custom_final = deal_final.get("customFields") or {}
                atual_final = _rd_valor_atual(custom_final, "origem_do_negocio")
                if atual_final not in (None, "", [], {}):
                    resumo["ja_preenchidos"] += 1
                    continue

                # ÚNICA escrita permitida nesta rotina.
                r_put = requests.put(
                    f"{AGENDOR_BASE}/deals/{deal_id}",
                    headers={**HEADERS, "Content-Type": "application/json"},
                    json={"customFields": {"origem_do_negocio": origem}},
                    timeout=20,
                )
                r_put.raise_for_status()
                resumo["atualizados"] += 1
                registro = {
                    "deal_id": deal_id,
                    "origem_do_negocio": origem,
                    "identificador_rd": dados.get("identificador"),
                    "diferenca_seg": round(diff, 1),
                }
                resumo["atualizacoes"].append(registro)
                print(f"[rd-retro-write] ATUALIZADO {json.dumps(registro, ensure_ascii=False)}", flush=True)

            except Exception as e:
                resumo["erros"] += 1
                print(f"[rd-retro-write] ERRO deal={deal_id}: {type(e).__name__}: {str(e)[:180]}", flush=True)

        _rd_retro_write_state.update({
            "status": "concluido",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "resultado": resumo,
            "erro": None,
            "progresso": {"atual": resumo["analisados"], "total": total, "deal_id": None},
        })
        print(f"[rd-retro-write] concluido resumo={json.dumps(resumo, ensure_ascii=False, default=str)}", flush=True)
        return True
    except Exception as e:
        _rd_retro_write_state.update({
            "status": "erro",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "erro": f"{type(e).__name__}: {str(e)[:300]}",
        })
        print(f"[rd-retro-write] erro_geral={type(e).__name__}: {str(e)[:180]}", flush=True)
        return False
    finally:
        _rd_retro_write_lock.release()


@app.route("/rd/retroativo/aplicar", methods=["POST", "GET"])
def rd_retroativo_aplicar():
    """Consulta ou dispara a escrita retroativa controlada de origem_do_negocio.

    Segurança em duas camadas para POST:
    - X-API-Key deve ser a chave privada já usada nas rotas administrativas;
    - X-Confirm-Write deve ser exatamente CONFIRMAR_RETROATIVO_RD.
    GET apenas consulta o estado, mas também exige X-API-Key.
    """
    if not AGENDAR_API_KEY:
        return jsonify({"status": "indisponivel", "mensagem": "AGENDAR_API_KEY não configurada"}), 503
    if request.headers.get("X-API-Key", "") != AGENDAR_API_KEY:
        return jsonify({"status": "nao_autorizado"}), 401

    if request.method == "GET":
        return jsonify(_rd_retro_write_state), 200

    if request.headers.get("X-Confirm-Write", "") != RD_RETRO_WRITE_CONFIRM:
        return jsonify({
            "status": "confirmacao_necessaria",
            "mensagem": "Envie X-Confirm-Write com a confirmação exata para habilitar a escrita controlada.",
        }), 409
    if _rd_retro_write_state.get("status") == "executando":
        return jsonify(_rd_retro_write_state), 202

    t = threading.Thread(
        target=_rd_retro_write_executar,
        args=(RD_RETRO_WRITE_MAX_DIAS, RD_RETRO_WRITE_MAX_ITENS),
        daemon=True,
    )
    t.start()
    return jsonify({
        "status": "iniciado",
        "modo": "RETROATIVO_RD_ORIGEM_CONTROLADO",
        "dias": RD_RETRO_WRITE_MAX_DIAS,
        "limite": RD_RETRO_WRITE_MAX_ITENS,
        "campo_escrita": "origem_do_negocio",
    }), 202


# ── RD Station: reprocessamento isolado de 1 negócio ──────────────────────────
RD_RETRO_SINGLE_CONFIRM = "CONFIRMAR_RETROATIVO_RD"

def _rd_retro_single_processar(deal_id, aplicar_limite_dias=True):
    """Revalida e, se seguro, preenche somente origem_do_negocio de um deal."""
    token = _rd_obter_access_token()
    rd_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resultado = {
        "modo": "RETROATIVO_RD_ORIGEM_ISOLADO", "deal_id": deal_id,
        "status": "nao_atualizado", "origem_do_negocio": None,
        "identificador_rd": None, "diferenca_seg": None, "motivo": None,
    }

    r_deal = requests.get(f"{AGENDOR_BASE}/deals/{deal_id}", headers=HEADERS,
                          params={"withCustomFields": "true"}, timeout=20)
    r_deal.raise_for_status()
    deal = r_deal.json().get("data") or r_deal.json()

    stage = deal.get("dealStage") or {}
    if ((stage.get("funnel") or {}).get("id")) != FUNIL_COMERCIAL_ID:
        resultado["motivo"] = "fora_funil_comercial"
        return resultado

    custom = deal.get("customFields") or {}
    atual = _rd_valor_atual(custom, "origem_do_negocio")
    if atual not in (None, "", [], {}):
        resultado.update(status="ja_preenchido", origem_do_negocio=atual,
                         motivo="origem_ja_preenchida")
        return resultado

    dt_deal = _rd_parse_iso(deal.get("startTime"))
    if not dt_deal:
        resultado["motivo"] = "sem_startTime"
        return resultado
    if dt_deal.tzinfo is None:
        dt_deal = dt_deal.replace(tzinfo=timezone.utc)

    idade_dias = (datetime.now(timezone.utc) - dt_deal.astimezone(timezone.utc)).total_seconds() / 86400
    if idade_dias < 0:
        resultado["motivo"] = "data_futura"
        return resultado
    if aplicar_limite_dias and idade_dias > RD_RETRO_WRITE_MAX_DIAS:
        resultado["motivo"] = "fora_janela_30_dias"
        return resultado

    person_id = _rd_deal_person_id(deal)
    email = ""
    if person_id:
        r_pessoa = requests.get(f"{AGENDOR_BASE}/people/{person_id}", headers=HEADERS, timeout=20)
        r_pessoa.raise_for_status()
        pessoa = r_pessoa.json().get("data") or r_pessoa.json()
        email = (pessoa.get("email") or "").strip().lower()
        if not email:
            emails = pessoa.get("emails") or []
            if isinstance(emails, list) and emails:
                primeiro = emails[0]
                email = ((primeiro.get("email") if isinstance(primeiro, dict) else primeiro) or "").strip().lower()
    if not email:
        resultado["motivo"] = "sem_email"
        return resultado

    r_contato = requests.get(
        "https://api.rd.services/platform/contacts/email:" + quote(email, safe="@"),
        headers=rd_headers, timeout=20)
    if r_contato.status_code == 404:
        resultado["motivo"] = "sem_contato_rd"
        return resultado
    r_contato.raise_for_status()
    contato = r_contato.json() if r_contato.content else {}
    uuid = (contato or {}).get("uuid") or ""
    if not uuid:
        resultado["motivo"] = "sem_contato_rd"
        return resultado

    r_eventos = requests.get(
        f"https://api.rd.services/platform/contacts/{quote(uuid, safe='')}/events",
        headers=rd_headers, params={"event_type": "CONVERSION"}, timeout=20)
    r_eventos.raise_for_status()
    eventos = _rd_eventos_lista(r_eventos.json() if r_eventos.content else {})

    plausiveis = []
    for evento in eventos:
        dados = _rd_evento_para_dados(evento, contato, email)
        dt_rd = _rd_parse_iso(dados.get("data_conversao"))
        if not dt_rd:
            continue
        if dt_rd.tzinfo is None:
            dt_rd = dt_rd.replace(tzinfo=timezone.utc)
        diff = abs((dt_deal.astimezone(timezone.utc) - dt_rd.astimezone(timezone.utc)).total_seconds())
        if diff <= 20:
            plausiveis.append((diff, dados))

    plausiveis.sort(key=lambda x: x[0])
    if not plausiveis:
        resultado["motivo"] = "sem_match_temporal_ate_20s"
        return resultado
    if len(plausiveis) > 1 and abs(plausiveis[1][0] - plausiveis[0][0]) < 1:
        resultado["motivo"] = "match_ambiguo"
        return resultado

    diff, dados = plausiveis[0]
    origem = _rd_mapear_origem_negocio(dados.get("identificador"))
    resultado["identificador_rd"] = dados.get("identificador")
    resultado["diferenca_seg"] = round(diff, 1)
    if not origem:
        resultado["motivo"] = "identificador_fora_depara"
        return resultado

    r_final = requests.get(f"{AGENDOR_BASE}/deals/{deal_id}", headers=HEADERS,
                           params={"withCustomFields": "true"}, timeout=20)
    r_final.raise_for_status()
    deal_final = r_final.json().get("data") or r_final.json()
    stage_final = deal_final.get("dealStage") or {}
    if ((stage_final.get("funnel") or {}).get("id")) != FUNIL_COMERCIAL_ID:
        resultado["motivo"] = "saiu_funil_antes_escrita"
        return resultado

    custom_final = deal_final.get("customFields") or {}
    atual_final = _rd_valor_atual(custom_final, "origem_do_negocio")
    if atual_final not in (None, "", [], {}):
        resultado.update(status="ja_preenchido", origem_do_negocio=atual_final,
                         motivo="origem_preenchida_antes_escrita")
        return resultado

    r_put = requests.put(
        f"{AGENDOR_BASE}/deals/{deal_id}",
        headers={**HEADERS, "Content-Type": "application/json"},
        json={"customFields": {"origem_do_negocio": origem}}, timeout=20)
    r_put.raise_for_status()

    resultado.update(status="atualizado", origem_do_negocio=origem, motivo="match_seguro")
    print(f"[rd-retro-single] ATUALIZADO {json.dumps(resultado, ensure_ascii=False)}", flush=True)
    return resultado


@app.route("/rd/retroativo/aplicar/<int:deal_id>", methods=["POST"])
def rd_retroativo_aplicar_deal(deal_id):
    if not AGENDAR_API_KEY:
        return jsonify({"status": "indisponivel", "mensagem": "AGENDAR_API_KEY não configurada"}), 503
    if request.headers.get("X-API-Key", "") != AGENDAR_API_KEY:
        return jsonify({"status": "nao_autorizado"}), 401
    if request.headers.get("X-Confirm-Write", "") != RD_RETRO_SINGLE_CONFIRM:
        return jsonify({"status": "confirmacao_necessaria"}), 409
    try:
        return jsonify(_rd_retro_single_processar(deal_id)), 200
    except Exception as e:
        print(f"[rd-retro-single] ERRO deal={deal_id}: {type(e).__name__}: {str(e)[:180]}", flush=True)
        return jsonify({"status": "erro", "deal_id": deal_id,
                        "erro": f"{type(e).__name__}: {str(e)[:300]}"}), 502


# ── RD Station: retroativo automático, retomável e de baixa prioridade ────────
RD_RETRO_AUTO_CONFIRM = "CONFIRMAR_RETROATIVO_RD"
RD_RETRO_AUTO_STATE_FILE = os.environ.get("RD_RETRO_AUTO_STATE_FILE", "/data/rd_retro_auto.json")
RD_RETRO_AUTO_LOCK_FILE = os.environ.get("RD_RETRO_AUTO_LOCK_FILE", "/data/rd_retro_auto.lock")
RD_RETRO_AUTO_RETRIES = int(os.environ.get("RD_RETRO_AUTO_RETRIES", "3"))
RD_RETRO_AUTO_RETRY_SECONDS = float(os.environ.get("RD_RETRO_AUTO_RETRY_SECONDS", "3"))
RD_RETRO_AUTO_PACE_SECONDS = float(os.environ.get("RD_RETRO_AUTO_PACE_SECONDS", "0.65"))
RD_RETRO_AUTO_MAX_ROUNDS = int(os.environ.get("RD_RETRO_AUTO_MAX_ROUNDS", "8"))
RD_RETRO_VALIDACAO_LIMITE = int(os.environ.get("RD_RETRO_VALIDACAO_LIMITE", "25"))
_rd_retro_auto_lock = threading.Lock()
_rd_retro_auto_state = {
    "status": "nunca_executado", "started_at": None, "finished_at": None,
    "resultado": None, "erro": None,
    "progresso": {"atual": 0, "total": 0, "deal_id": None},
}

def _rd_retro_auto_salvar(payload):
    diretorio = os.path.dirname(RD_RETRO_AUTO_STATE_FILE) or "."
    os.makedirs(diretorio, exist_ok=True)
    tmp = RD_RETRO_AUTO_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, RD_RETRO_AUTO_STATE_FILE)

def _rd_retro_auto_carregar():
    if not os.path.isfile(RD_RETRO_AUTO_STATE_FILE):
        return {}
    try:
        with open(RD_RETRO_AUTO_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[rd-retro-auto] checkpoint inválido: {type(e).__name__}: {str(e)[:160]}", flush=True)
        return {}

def _rd_retro_auto_process_lock():
    """Lock entre processos Gunicorn; retorna arquivo aberto ou None se já houver worker."""
    try:
        import fcntl
        os.makedirs(os.path.dirname(RD_RETRO_AUTO_LOCK_FILE) or ".", exist_ok=True)
        fh = open(RD_RETRO_AUTO_LOCK_FILE, "a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            fh.close()
            return None
    except Exception as e:
        print(f"[rd-retro-auto] lock de processo indisponível: {type(e).__name__}: {str(e)[:160]}", flush=True)
        return None

def _rd_retro_auto_erro_transiente(exc):
    """429, timeout, conexão e 5xx ficam elegíveis para nova tentativa automática."""
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        return status == 429 or (isinstance(status, int) and 500 <= status <= 599)
    texto = str(exc).lower()
    return any(x in texto for x in ("429", "timeout", "timed out", "connection reset", "temporarily unavailable"))

def _rd_retro_auto_candidatos(cutoff_at=None):
    cutoff = _rd_parse_iso(cutoff_at) if cutoff_at else None
    if cutoff is not None and cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    candidatos = []
    for deal in list(cache.get("deals") or []):
        stage = deal.get("dealStage") or {}
        if ((stage.get("funnel") or {}).get("id")) != FUNIL_COMERCIAL_ID:
            continue
        dt = _rd_parse_iso(deal.get("startTime"))
        if not dt:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if cutoff is not None and dt.astimezone(timezone.utc) > cutoff.astimezone(timezone.utc):
            continue
        candidatos.append(deal)
    candidatos.sort(key=lambda d: (
        _rd_parse_iso(d.get("startTime")) or datetime.min.replace(tzinfo=timezone.utc),
        int(d.get("id") or 0),
    ), reverse=True)
    return candidatos

def _rd_retro_auto_checkpoint(status, started_at, cutoff_at, concluidos, resultados, pendentes, resumo, rodada=1, erro=None, modo=None, candidatos_ids=None):
    payload = {
        "status": status, "started_at": started_at,
        "cutoff_at": cutoff_at, "updated_at": datetime.now(timezone.utc).isoformat(),
        "concluidos": sorted(concluidos), "resultados": resultados,
        "pendentes_transientes": pendentes, "rodada": rodada,
        "resumo": resumo, "erro": erro,
        "modo": modo or "completo",
        "candidatos_ids": [str(x) for x in (candidatos_ids or [])],
    }
    _rd_retro_auto_salvar(payload)
    return payload

def _rd_retro_auto_executar(validacao=False):
    if not _rd_retro_auto_lock.acquire(blocking=False):
        return False
    process_lock = None
    try:
        process_lock = _rd_retro_auto_process_lock()
        if process_lock is None:
            print("[rd-retro-auto] outro processo já possui o lock; worker não iniciado", flush=True)
            return False

        anterior = _rd_retro_auto_carregar()
        modo_persistido = anterior.get("modo")
        if modo_persistido in ("validacao", "completo"):
            validacao = (modo_persistido == "validacao")
        modo_execucao = "validacao" if validacao else "completo"
        started_at = anterior.get("started_at") or datetime.now(timezone.utc).isoformat()
        cutoff_at = anterior.get("cutoff_at") or started_at
        concluidos = set(str(x) for x in (anterior.get("concluidos") or []))
        resultados = anterior.get("resultados") or {}
        if not isinstance(resultados, dict):
            resultados = {}
        pendentes = anterior.get("pendentes_transientes") or {}
        if not isinstance(pendentes, dict):
            pendentes = {}
        rodada = max(1, int(anterior.get("rodada") or 1))

        # Depois que a execução começa, a lista de IDs vira parte do checkpoint.
        # Assim um restart não depende de reconstruir todo o cache do Agendor para retomar.
        candidatos_persistidos = [str(x) for x in (anterior.get("candidatos_ids") or []) if str(x).strip()]
        if candidatos_persistidos:
            candidatos_ids = candidatos_persistidos
        else:
            deals = _rd_retro_auto_candidatos(cutoff_at)
            if validacao:
                deals = deals[:max(1, RD_RETRO_VALIDACAO_LIMITE)]
            if not deals:
                raise RuntimeError("cache_deals_ainda_vazio")
            candidatos_ids = [str(d.get("id")) for d in deals if d.get("id")]
        # dict preserva ordem e elimina eventual duplicidade. O processamento individual
        # busca o negócio pelo ID, portanto não precisa do objeto completo em cache.
        candidatos_ids = list(dict.fromkeys(candidatos_ids))
        por_id = {deal_id: True for deal_id in candidatos_ids}
        total = len(por_id)
        resumo = anterior.get("resumo") or {}
        resumo.update({
            "modo": "RETROATIVO_RD_ORIGEM_AUTOMATICO",
            "candidatos_agendor": total,
            "retomados_do_checkpoint": len(concluidos),
        })
        for k, v in {
            "analisados": 0, "atualizados": 0, "ja_preenchidos": 0,
            "inconclusivos": 0, "erros_terminais": 0, "retries_transientes": 0,
            "por_motivo": {},
        }.items():
            resumo.setdefault(k, v)

        _rd_retro_auto_state.update({
            "status": "executando", "started_at": started_at, "finished_at": None,
            "resultado": resumo, "erro": None,
            "progresso": {"atual": len(concluidos), "total": total, "deal_id": None},
        })
        _rd_retro_auto_checkpoint("executando", started_at, cutoff_at, concluidos, resultados, pendentes, resumo, rodada, modo=modo_execucao, candidatos_ids=candidatos_ids)
        print(f"[rd-retro-auto] iniciado/retomado total={total} concluidos={len(concluidos)} cutoff={cutoff_at}", flush=True)

        while rodada <= RD_RETRO_AUTO_MAX_ROUNDS:
            fila = [k for k in por_id if k not in concluidos]
            if not fila:
                break
            houve_progresso = False
            print(f"[rd-retro-auto] rodada={rodada} fila={len(fila)}", flush=True)
            for chave in fila:
                deal_id = int(chave)
                _rd_retro_auto_state["progresso"] = {"atual": len(concluidos), "total": total, "deal_id": deal_id}
                resultado = None
                ultimo_erro = None
                transiente = False

                for tentativa in range(1, RD_RETRO_AUTO_RETRIES + 1):
                    try:
                        resultado = _rd_retro_single_processar(deal_id, aplicar_limite_dias=False)
                        ultimo_erro = None
                        break
                    except Exception as e:
                        ultimo_erro = f"{type(e).__name__}: {str(e)[:240]}"
                        transiente = _rd_retro_auto_erro_transiente(e)
                        print(f"[rd-retro-auto] erro deal={deal_id} tentativa={tentativa}/{RD_RETRO_AUTO_RETRIES} transiente={transiente} erro={ultimo_erro}", flush=True)
                        if not transiente:
                            break
                        resumo["retries_transientes"] += 1
                        if tentativa < RD_RETRO_AUTO_RETRIES:
                            time.sleep(min(30.0, RD_RETRO_AUTO_RETRY_SECONDS * (2 ** (tentativa - 1))))

                if resultado is not None:
                    status = resultado.get("status")
                    motivo = resultado.get("motivo") or "sem_motivo"
                    resumo["analisados"] += 1
                    if status == "atualizado":
                        resumo["atualizados"] += 1
                    elif status == "ja_preenchido":
                        resumo["ja_preenchidos"] += 1
                    else:
                        resumo["inconclusivos"] += 1
                    resumo["por_motivo"][motivo] = resumo["por_motivo"].get(motivo, 0) + 1
                    resultados[chave] = resultado
                    concluidos.add(chave)
                    pendentes.pop(chave, None)
                    houve_progresso = True
                elif transiente:
                    pendentes[chave] = {
                        "deal_id": deal_id, "ultimo_erro": ultimo_erro,
                        "ultima_tentativa": datetime.now(timezone.utc).isoformat(), "rodada": rodada,
                    }
                else:
                    resumo["analisados"] += 1
                    resumo["erros_terminais"] += 1
                    resultados[chave] = {"deal_id": deal_id, "status": "erro", "motivo": "erro_terminal", "erro": ultimo_erro}
                    concluidos.add(chave)
                    pendentes.pop(chave, None)
                    houve_progresso = True

                _rd_retro_auto_checkpoint("executando", started_at, cutoff_at, concluidos, resultados, pendentes, resumo, rodada, modo=modo_execucao, candidatos_ids=candidatos_ids)
                time.sleep(max(0.0, RD_RETRO_AUTO_PACE_SECONDS))

                if len(concluidos) % 25 == 0 and concluidos:
                    print(f"[rd-retro-auto] progresso {len(concluidos)}/{total} atualizados={resumo['atualizados']} pendentes={len(pendentes)}", flush=True)

            if not pendentes:
                break
            rodada += 1
            if rodada <= RD_RETRO_AUTO_MAX_ROUNDS:
                espera = min(300.0, 15.0 * (2 ** min(rodada - 2, 4)))
                print(f"[rd-retro-auto] {len(pendentes)} transientes pendentes; nova rodada em {espera:.0f}s", flush=True)
                _rd_retro_auto_checkpoint("executando", started_at, cutoff_at, concluidos, resultados, pendentes, resumo, rodada, modo=modo_execucao, candidatos_ids=candidatos_ids)
                time.sleep(espera)

        resumo["total_concluido_checkpoint"] = len(concluidos)
        resumo["pendentes_transientes"] = len(pendentes)
        final_status = (("validacao_concluida" if validacao else "concluido") if not pendentes else ("validacao_com_pendencias_transientes" if validacao else "concluido_com_pendencias_transientes"))
        final = _rd_retro_auto_checkpoint(final_status, started_at, cutoff_at, concluidos, resultados, pendentes, resumo, rodada, modo=modo_execucao, candidatos_ids=candidatos_ids)
        final["finished_at"] = datetime.now(timezone.utc).isoformat()
        _rd_retro_auto_salvar(final)
        _rd_retro_auto_state.update({
            "status": final_status, "finished_at": final["finished_at"],
            "resultado": resumo, "erro": None,
            "progresso": {"atual": len(concluidos), "total": total, "deal_id": None},
        })
        print(f"[rd-retro-auto] finalizado status={final_status} resumo={json.dumps(resumo, ensure_ascii=False)}", flush=True)
        return True
    except Exception as e:
        erro = f"{type(e).__name__}: {str(e)[:300]}"
        anterior = _rd_retro_auto_carregar()
        # cache vazio no boot é condição de retomada, não falha definitiva
        status = "aguardando_retomada" if "cache_deals_ainda_vazio" in erro else "erro_retomavel"
        anterior.update({"status": status, "updated_at": datetime.now(timezone.utc).isoformat(), "erro": erro})
        try:
            _rd_retro_auto_salvar(anterior)
        except Exception:
            pass
        _rd_retro_auto_state.update({"status": status, "erro": erro})
        print(f"[rd-retro-auto] {status}={erro}", flush=True)
        return False
    finally:
        if process_lock is not None:
            try:
                import fcntl
                fcntl.flock(process_lock.fileno(), fcntl.LOCK_UN)
                process_lock.close()
            except Exception:
                pass
        _rd_retro_auto_lock.release()

def _rd_retro_auto_iniciar_thread(validacao=False):
    if _rd_retro_auto_state.get("status") == "executando":
        return False
    t = threading.Thread(target=_rd_retro_auto_executar, kwargs={"validacao": validacao}, daemon=True, name="rd-retro-auto")
    t.start()
    return True

def _rd_retro_auto_retomar_se_necessario():
    """Chamado pelo scheduler: após restart, retoma sozinho quando o cache já existir."""
    try:
        persistido = _rd_retro_auto_carregar()
        status = persistido.get("status")
        if status not in ("executando", "aguardando_retomada", "erro_retomavel"):
            return
        # Se a execução já gravou seus candidatos no checkpoint, pode retomar
        # imediatamente após restart, sem aguardar a carga completa de /deals.
        # Checkpoints antigos, sem essa lista, mantêm o comportamento legado seguro.
        if not (persistido.get("candidatos_ids") or []) and not (cache.get("deals") or []):
            print("[rd-retro-auto] retomada aguardando cache de deals (checkpoint legado sem candidatos_ids)", flush=True)
            return
        if _rd_retro_auto_state.get("status") == "executando":
            return
        modo = persistido.get("modo")
        # Fail-closed: checkpoint ativo sem modo explícito é legado/ambíguo.
        # Nunca assumir "completo", pois isso poderia ampliar uma validação limitada
        # para todo o histórico após restart.
        if modo not in ("validacao", "completo"):
            print(f"[rd-retro-auto] retomada bloqueada: checkpoint ativo sem modo válido status={status}", flush=True)
            return
        # Quarentena pontual do checkpoint acidental criado em 07/10/2026.
        # Ele nasceu de uma validação de 25 que, por bug já corrigido, perdeu o
        # modo e virou execução completa. Não apagar: arquivar para auditoria.
        checkpoint_acidental = (
            modo == "completo"
            and str(persistido.get("cutoff_at") or "").startswith("2026-10-07T03:11:19.905079")
            and len(persistido.get("candidatos_ids") or []) == 6792
        )
        if checkpoint_acidental:
            try:
                destino = RD_RETRO_AUTO_STATE_FILE + ".acidental-20261007"
                os.replace(RD_RETRO_AUTO_STATE_FILE, destino)
                print(f"[rd-retro-auto] checkpoint acidental colocado em quarentena arquivo={destino}", flush=True)
            except Exception as e:
                print(f"[rd-retro-auto] falha ao colocar checkpoint acidental em quarentena: {type(e).__name__}: {str(e)[:160]}", flush=True)
            return
        print(f"[rd-retro-auto] retomada automática solicitada status_checkpoint={status} modo={modo}", flush=True)
        _rd_retro_auto_iniciar_thread(validacao=(modo == "validacao"))
    except Exception as e:
        print(f"[rd-retro-auto] erro ao avaliar retomada: {type(e).__name__}: {str(e)[:180]}", flush=True)

@app.route("/rd/retroativo/automatico/validar", methods=["POST"])
def rd_retroativo_automatico_validar():
    if not AGENDAR_API_KEY:
        return jsonify({"status": "indisponivel", "mensagem": "AGENDAR_API_KEY não configurada"}), 503
    if request.headers.get("X-API-Key", "") != AGENDAR_API_KEY:
        return jsonify({"status": "nao_autorizado"}), 401
    if request.headers.get("X-Confirm-Write", "") != RD_RETRO_AUTO_CONFIRM:
        return jsonify({"status": "confirmacao_necessaria"}), 409
    persistido = _rd_retro_auto_carregar()
    if persistido.get("status") in ("executando", "aguardando_retomada", "erro_retomavel"):
        return jsonify({"status": "ja_em_execucao_ou_retomada"}), 409
    # A validação é uma execução nova e limitada; não reaproveita conclusão de testes anteriores.
    # IMPORTANTE: grava o modo ANTES de iniciar a thread. Se o cache ainda estiver vazio
    # e o processo reiniciar nesse intervalo, a retomada nunca pode interpretar a validação
    # como execução completa.
    try:
        if os.path.isfile(RD_RETRO_AUTO_STATE_FILE):
            os.replace(RD_RETRO_AUTO_STATE_FILE, RD_RETRO_AUTO_STATE_FILE + ".bak")
        agora = datetime.now(timezone.utc).isoformat()
        _rd_retro_auto_checkpoint(
            "aguardando_retomada", agora, agora, set(), {}, {}, {},
            rodada=1, modo="validacao", candidatos_ids=[]
        )
    except Exception as e:
        return jsonify({"status": "erro_checkpoint", "erro": f"{type(e).__name__}: {str(e)[:180]}"}), 500
    iniciado = _rd_retro_auto_iniciar_thread(validacao=True)
    return jsonify({
        "status": "validacao_iniciada" if iniciado else "ja_em_execucao",
        "limite": RD_RETRO_VALIDACAO_LIMITE,
        "escrita_real": True,
        "nunca_sobrescreve_origem_existente": True,
        "checkpoint": RD_RETRO_AUTO_STATE_FILE,
    }), 202

@app.route("/rd/retroativo/automatico", methods=["POST", "GET"])
def rd_retroativo_automatico():
    if not AGENDAR_API_KEY:
        return jsonify({"status": "indisponivel", "mensagem": "AGENDAR_API_KEY não configurada"}), 503
    if request.headers.get("X-API-Key", "") != AGENDAR_API_KEY:
        return jsonify({"status": "nao_autorizado"}), 401
    if request.method == "GET":
        persistido = _rd_retro_auto_carregar()
        return jsonify({
            "runtime": _rd_retro_auto_state,
            "checkpoint": {
                "status": persistido.get("status"),
                "modo": persistido.get("modo"),
                "started_at": persistido.get("started_at"),
                "cutoff_at": persistido.get("cutoff_at"),
                "updated_at": persistido.get("updated_at"),
                "finished_at": persistido.get("finished_at"),
                "resumo": persistido.get("resumo"),
                "concluidos": len(persistido.get("concluidos") or []),
                "pendentes_transientes": len(persistido.get("pendentes_transientes") or {}),
                "rodada": persistido.get("rodada"),
            },
        }), 200
    if request.headers.get("X-Confirm-Write", "") != RD_RETRO_AUTO_CONFIRM:
        return jsonify({"status": "confirmacao_necessaria"}), 409
    persistido = _rd_retro_auto_carregar()
    if persistido.get("status") == "concluido":
        return jsonify({"status": "ja_concluido", "resumo": persistido.get("resumo")}), 200
    if str(persistido.get("status") or "").startswith("validacao_"):
        try:
            os.replace(RD_RETRO_AUTO_STATE_FILE, RD_RETRO_AUTO_STATE_FILE + ".validacao")
        except Exception as e:
            return jsonify({"status": "erro_checkpoint", "erro": f"{type(e).__name__}: {str(e)[:180]}"}), 500
    iniciado = _rd_retro_auto_iniciar_thread()
    return jsonify({
        "status": "iniciado" if iniciado else "ja_em_execucao",
        "modo": "RETROATIVO_RD_ORIGEM_AUTOMATICO",
        "escopo": "historico_disponivel_ate_o_inicio_da_execucao",
        "checkpoint": RD_RETRO_AUTO_STATE_FILE,
        "retries_imediatos_por_negocio": RD_RETRO_AUTO_RETRIES,
        "rodadas_transientes": RD_RETRO_AUTO_MAX_ROUNDS,
        "retomada_automatica_apos_restart": True,
        "nunca_sobrescreve_origem_existente": True,
    }), 202


def _rd_enriquecer_deal(deal_id, dados):
    """Preenche apenas campos existentes e vazios; nunca sobrescreve."""
    origem_negocio = _rd_mapear_origem_negocio(dados.get("identificador"))
    if not origem_negocio:
        print(f"[rd-write] IGNORADO_IDENTIFICADOR — {dados.get('identificador')!r} não está no DE/PARA", flush=True)
        return False

    try:
        r = requests.get(
            f"{AGENDOR_BASE}/deals/{deal_id}", headers=HEADERS,
            params={"withCustomFields": "true"}, timeout=15
        )
        r.raise_for_status()
        deal_completo = r.json().get("data") or r.json()
        custom = deal_completo.get("customFields") or {}
    except Exception as e:
        print(f"[rd-write] ERRO_GET deal={deal_id}: {e}", flush=True)
        return False

    # Slugs validados por teste real na API/UI do Agendor em 01/10/2026.
    # O GET omite campos personalizados vazios (customFields pode vir {}),
    # portanto ausência do slug NÃO significa que o campo não exista.
    # Só envia valores que vieram do RD e nunca sobrescreve campo já preenchido.
    desejados = {
        "origem_do_negocio": origem_negocio,
        "origem": dados.get("utm_source"),
        "campanha": dados.get("utm_campaign"),
        "grupo_de_anuncio": dados.get("utm_term"),
        "anuncio": dados.get("utm_content"),
        "meta_ads_source_id": dados.get("utm_id"),
    }

    atualizar = {}
    pulados = {}
    for slug, valor in desejados.items():
        if valor is None or str(valor).strip() == "":
            pulados[slug] = "sem_valor_no_RD"
            continue
        atual = _rd_valor_atual(custom, slug)
        if atual not in (None, "", [], {}):
            pulados[slug] = f"ja_preenchido:{atual}"
            continue
        atualizar[slug] = str(valor).strip()

    print(f"[rd-write] deal={deal_id} atualizar={json.dumps(atualizar, ensure_ascii=False)} pulados={json.dumps(pulados, ensure_ascii=False, default=str)}", flush=True)
    if not atualizar:
        print(f"[rd-write] NADA_A_ATUALIZAR deal={deal_id}", flush=True)
        return True

    try:
        r = requests.put(
            f"{AGENDOR_BASE}/deals/{deal_id}", headers=HEADERS,
            json={"customFields": atualizar}, timeout=20
        )
        r.raise_for_status()
        print(f"[rd-write] ATUALIZADO_OK deal={deal_id} campos={list(atualizar.keys())}", flush=True)
        return True
    except Exception as e:
        corpo = ""
        try:
            corpo = r.text[:1000]
        except Exception:
            pass
        print(f"[rd-write] ERRO_PUT deal={deal_id}: {e} resposta={corpo}", flush=True)
        return False


def _rd_correlacionar_e_enriquecer(dados):
    """Localiza candidato inequívoco e, somente então, enriquece o negócio."""
    if not _rd_mapear_origem_negocio(dados.get("identificador")):
        print(f"[rd-match] IGNORADO_IDENTIFICADOR — {dados.get('identificador')!r} fora do DE/PARA; nada será alterado", flush=True)
        return

    for tentativa, espera in enumerate((10, 20, 30, 60, 90, 90), start=1):
        time.sleep(espera)
        pessoas = _rd_buscar_pessoas(dados["email"], dados["telefone_norm"])
        print(f"[rd-match] tentativa={tentativa}/6 pessoas_encontradas={len(pessoas)}", flush=True)

        candidatos = []
        dt_rd = _rd_parse_iso(dados["data_conversao"])
        for pessoa in pessoas:
            person_id = pessoa.get("id")
            try:
                r = requests.get(f"{AGENDOR_BASE}/people/{person_id}/deals",
                                 headers=HEADERS, timeout=15)
                r.raise_for_status()
                deals = r.json().get("data", [])
            except Exception as e:
                print(f"[rd-match] Falha buscando deals person={person_id}: {e}", flush=True)
                continue

            for deal in deals:
                stage = deal.get("dealStage") or {}
                funnel_id = (stage.get("funnel") or {}).get("id")
                if funnel_id != FUNIL_COMERCIAL_ID:
                    continue
                descricao = (deal.get("description") or "").strip()
                if "RD Station" not in descricao:
                    continue

                dt_deal = _rd_parse_iso(deal.get("startTime"))
                diferenca = None
                if dt_rd and dt_deal:
                    if dt_rd.tzinfo is None:
                        dt_rd = dt_rd.replace(tzinfo=timezone.utc)
                    if dt_deal.tzinfo is None:
                        dt_deal = dt_deal.replace(tzinfo=timezone.utc)
                    diferenca = abs((dt_deal.astimezone(timezone.utc) - dt_rd.astimezone(timezone.utc)).total_seconds())
                    if diferenca > 20 * 60:
                        continue

                candidatos.append({
                    "deal_id": deal.get("id"), "person_id": person_id,
                    "nome": deal.get("title") or deal.get("name"),
                    "startTime": deal.get("startTime"), "diferenca_seg": diferenca,
                    "descricao": descricao[:100],
                })

        candidatos = list({c["deal_id"]: c for c in candidatos if c.get("deal_id")}.values())
        candidatos.sort(key=lambda c: c["diferenca_seg"] if c["diferenca_seg"] is not None else 10**12)
        print(f"[rd-match] identificador={dados['identificador']!r} candidatos={json.dumps(candidatos, ensure_ascii=False, default=str)}", flush=True)

        if len(candidatos) == 1:
            escolhido = candidatos[0]
            print(f"[rd-match] MATCH_OK deal={escolhido['deal_id']} person={escolhido['person_id']} diferenca_seg={escolhido['diferenca_seg']}", flush=True)
            _rd_enriquecer_deal(escolhido["deal_id"], dados)
            return
        if len(candidatos) > 1:
            print(f"[rd-match] AMBIGUO — {len(candidatos)} negócios plausíveis; nada será alterado", flush=True)
            return
        print(f"[rd-match] Nenhum candidato na tentativa {tentativa}; aguardando nova busca", flush=True)

    print("[rd-match] SEM_CORRESPONDENCIA — nenhuma alteração foi feita", flush=True)


@app.route("/rd/webhook", methods=["POST", "OPTIONS"])
def rd_webhook():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 200

    try:
        body = request.get_json(silent=True)
        if body is None:
            body = request.form.to_dict(flat=False) if request.form else {}

        print("[rd-webhook] ========================================", flush=True)
        print(f"[rd-webhook] recebido_em_brt={datetime.utcnow() - timedelta(hours=3)}", flush=True)
        print(f"[rd-webhook] content_type={request.content_type}", flush=True)
        print(f"[rd-webhook] payload={json.dumps(body, ensure_ascii=False, default=str)[:12000]}", flush=True)

        leads = body.get("leads") if isinstance(body, dict) else None
        if not isinstance(leads, list):
            leads = []

        for lead in leads:
            if not isinstance(lead, dict):
                continue
            dados = _rd_extrair_lead(lead)
            print(
                f"[rd-webhook] identificador={dados['identificador']!r} email={dados['email']!r} "
                f"telefone={dados['telefone']!r} data_conversao={dados['data_conversao']!r} "
                f"utms={{source:{dados['utm_source']!r}, campaign:{dados['utm_campaign']!r}, "
                f"term:{dados['utm_term']!r}, content:{dados['utm_content']!r}, id:{dados['utm_id']!r}}}",
                flush=True
            )
            if not dados["identificador"] or (not dados["email"] and not dados["telefone_norm"]):
                print("[rd-match] IGNORADO — faltam identificador e/ou dados para localizar a pessoa", flush=True)
                continue
            t = threading.Thread(target=_rd_correlacionar_e_enriquecer, args=(dados,))
            t.daemon = True
            t.start()

        return jsonify({"status": "ok", "modo": "enriquecimento_seguro"}), 200

    except Exception as e:
        print(f"[rd-webhook] Erro ao processar payload: {e}", flush=True)
        return jsonify({"status": "error", "modo": "enriquecimento_seguro"}), 200

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
    tokens = [AUTENTIQUE_TOKEN, AUTENTIQUE_TOKEN_USUARIO1, AUTENTIQUE_TOKEN_USUARIO2, AUTENTIQUE_TOKEN_USUARIO3, AUTENTIQUE_TOKEN_USUARIO4]
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
    """Envia resposta do Luca de volta ao lead via API do AgendorChat.
    Adiciona uma marca invisível no final (LUCA_MARKER) — ver comentário
    na definição da constante — pra depois conseguir distinguir isso de
    uma mensagem que o [gestor] escreveu manualmente pela mesma conta."""
    url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
    resp = requests.post(
        url,
        headers={
            "api_access_token": LUCA_SEND_TOKEN,
            "Content-Type":     "application/json",
        },
        json={"content": text + LUCA_MARKER, "message_type": "outgoing", "private": False},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def resolver_conversa_agendorchat(conversation_id: int) -> bool:
    """Marca a conversa como 'resolved' no AgendorChat, via toggle_status —
    endpoint padrão do Chatwoot (API por trás do AgendorChat). NÃO TESTADO
    ainda contra a API real — mesma cautela que já foi necessária com
    outros endpoints não documentados nesse projeto (due_date, dealStage,
    assigned_users). Usado depois do follow-up de dias (D+1/D+3/D+5/D+7/D+10): manda
    a mensagem, reabre naturalmente, e o Luca fecha de novo — assim a
    caixa de conversas "abertas" no AgendorChat só mostra o que realmente
    precisa de atenção humana (decisão de [gestor], 13/08)."""
    try:
        url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}/toggle_status"
        resp = requests.post(url, headers={"api_access_token": LUCA_SEND_TOKEN, "Content-Type": "application/json"},
                              json={"status": "resolved"}, timeout=15)
        print(f"[followup_dias] Resolver conversa conv={conversation_id} status={resp.status_code}", flush=True)
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"[followup_dias] Erro ao resolver conversa conv={conversation_id}: {e}", flush=True)
        return False


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


FUNIL_COMERCIAL_ID = 696449

def status_reuniao_real(phone: str) -> str:
    """Checa o estado REAL (via CRM, não via histórico de mensagens) de
    uma eventual reunião pro negócio desse telefone. Barato: usa o
    tasks_cache já em memória (atualizado de hora em hora) pra achar a
    tarefa, só 1 chamada nova à API (buscar_pessoa_e_negocio) pra achar o
    negócio — nenhuma chamada ao Claude. Retorna uma frase curta pra
    injetar no contexto, ou "" se não achar nada (fail-open, não bloqueia
    a conversa)."""
    try:
        _, deal = buscar_pessoa_e_negocio(phone)
        if not deal:
            return ""
        deal_id = deal.get("id")
        tasks_do_deal = [
            t for t in (tasks_cache.get("data") or [])
            if (t.get("deal") or {}).get("id") == deal_id and t.get("type") == "Reunião"
        ]
        if not tasks_do_deal:
            return "Não há nenhuma reunião registrada no CRM pra este lead atualmente."
        # A mais recente primeiro
        tasks_do_deal.sort(key=lambda t: t.get("dueDate") or "", reverse=True)
        t = tasks_do_deal[0]
        due = _parse_dt(t.get("dueDate"))
        # O valor bruto do CRM está em UTC — precisa converter pra
        # Brasília antes de exibir, senão uma reunião marcada pra 13h
        # local aparece como "16h" aqui (13h + 3h de UTC).
        due_brt = due.astimezone(timezone(timedelta(hours=-3))) if due else None
        due_fmt = due_brt.strftime("%d/%m às %H:%M") if due_brt else "data indefinida"
        if t.get("finishedAt"):
            return (f"A última reunião registrada no CRM pra este lead (era pra {due_fmt}) "
                     f"JÁ FOI CONCLUÍDA/MARCADA COMO FINALIZADA. Se o histórico da conversa mencionar "
                     f"essa reunião como algo futuro, ISSO ESTÁ DESATUALIZADO — não trate como "
                     f"compromisso pendente.")
        return (f"Segundo o CRM (fonte confiável, mais atual que o histórico da conversa), "
                f"há uma reunião ainda ABERTA/pendente agendada pra {due_fmt}.")
    except Exception as e:
        print(f"[status_reuniao] Erro phone={phone}: {e}", flush=True)
        return ""


def buscar_pessoa_e_negocio(phone):
    """Localiza a pessoa pelo telefone e o negócio mais recente dela DENTRO
    DO FUNIL COMERCIAL (ignora negócios de outros funis, ex: Reativação,
    Jurídico, Legalização). Retorna (person, deal) ou (person, None) se a
    pessoa existir mas não tiver negócio no Funil Comercial, ou (None, None)
    se a pessoa nem existir.

    Correção de 12/08 (caso real confirmado): o mesmo telefone pode ter
    MAIS DE UMA pessoa cadastrada no Agendor (contato duplicado — ex:
    "[especialista] Pereira" e "[especialista] - via Luca (WhatsApp)" como registros
    separados). A versão anterior só olhava a primeira pessoa retornada
    pela busca; se o negócio ativo no Funil Comercial estivesse na
    SEGUNDA pessoa, a função nunca achava, tentava criar um negócio novo,
    e isso já causou falha real no fluxo (reunião/link não gerados)."""
    phone_clean = phone.replace("+", "").replace(" ", "").strip()
    pessoas = []
    for attempt in range(3):
        try:
            r = requests.get(f"{AGENDOR_BASE}/people", headers=HEADERS,
                             params={"phone": phone_clean}, timeout=15)
            if r.status_code == 429:
                raise requests.exceptions.HTTPError(f"429 buscando pessoa phone={phone_clean}")
            r.raise_for_status()
            pessoas = r.json().get("data", [])
            break
        except Exception as e:
            print(f"[buscar_pessoa_e_negocio] Tentativa {attempt+1}/3 falhou (pessoas) "
                  f"phone={phone_clean}: {e}", flush=True)
            if attempt < 2:
                time.sleep(3)
    if not pessoas:
        return None, None

    # IMPORTANTE: GET /deals?personId=X ignora o filtro e devolve negócios de
    # QUALQUER pessoa (bug confirmado na API) — usa o endpoint aninhado, que
    # filtra corretamente.
    for person in pessoas:
        deals = []
        for attempt in range(3):
            try:
                r2 = requests.get(f"{AGENDOR_BASE}/people/{person.get('id')}/deals",
                                   headers=HEADERS, timeout=15)
                if r2.status_code == 429:
                    raise requests.exceptions.HTTPError(f"429 buscando deals person={person.get('id')}")
                r2.raise_for_status()
                deals = r2.json().get("data", [])
                break
            except Exception as e:
                print(f"[buscar_pessoa_e_negocio] Tentativa {attempt+1}/3 falhou (deals) "
                      f"person={person.get('id')}: {e}", flush=True)
                if attempt < 2:
                    time.sleep(3)
        deals_comercial = [
            d for d in deals
            if ((d.get("dealStage") or {}).get("funnel") or {}).get("id") == FUNIL_COMERCIAL_ID
            and not d.get("wonAt") and not d.get("lostAt")  # ignora negócios já ganhos/perdidos
        ]
        if deals_comercial:
            deal = sorted(deals_comercial, key=lambda d: d.get("startTime", ""), reverse=True)[0]
            return person, deal

    # Nenhuma das pessoas encontradas tem negócio ativo no Funil Comercial —
    # retorna a primeira mesmo (comportamento anterior pra esse caso).
    return pessoas[0], None


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
                chave = campo.get("identifier") or campo.get("key") or campo.get("slug")
                opcao_luca = None
                for opt in (campo.get("options") or campo.get("values") or []):
                    if (opt.get("name") or opt.get("value") or "").strip().lower() == "luca":
                        opcao_luca = opt.get("id")
                        break
                _campo_agendada_por_cache = {"key": chave, "luca_id": opcao_luca}
                print(f"[crm] Campo 'agendada por' resolvido: key={chave} luca_id={opcao_luca}", flush=True)
                if not chave:
                    print("[crm] AVISO: campo 'agendada por' encontrado mas sem identifier/key/slug — não será preenchido", flush=True)
                if not opcao_luca:
                    print("[crm] AVISO: opção 'Luca' não encontrada nas options do campo — não será preenchido", flush=True)
                return _campo_agendada_por_cache
        print("[crm] Campo 'Reunião agendada por' não encontrado em /custom_fields/deals", flush=True)
    except Exception as e:
        print(f"[crm] Erro ao resolver campo agendada_por: {e}", flush=True)
    _campo_agendada_por_cache = {}
    return _campo_agendada_por_cache


PADRAO_HORA_EXPLICITA = re.compile(r"\b(\d{1,2})\s?[:h]\s?(\d{2})?\b", re.IGNORECASE)

def extrair_hora_explicita(texto: str):
    """Extrai (hora, minuto) de um horário EXPLÍCITO e inequívoco no texto
    (ex: '11h30', '11:30', '14h') via regex determinística — sem depender
    de IA pra esse pedaço específico. Usado em parse_preferencia_datetime
    pra validar o resultado do modelo, já que ele pode errar a hora mesmo
    com o texto claro e sem ambiguidade nenhuma (bug real confirmado:
    Giseli, 28/09 — disse "hoje às 11h30", o Claude devolveu ISO com
    "12:30", 1h de diferença, sem nenhum motivo no texto original pra
    isso). Retorna None se não achar um padrão claro de horário no texto."""
    m = PADRAO_HORA_EXPLICITA.search(texto or "")
    if not m:
        return None
    hora = int(m.group(1))
    minuto = int(m.group(2)) if m.group(2) else 0
    if 0 <= hora <= 23 and 0 <= minuto <= 59:
        return (hora, minuto)
    return None


def parse_preferencia_datetime(preferencia: str, tipo: str = "agendamento"):
    """Converte a preferência do lead ('terça às 12h10') em ISO usando o Claude.
    tipo: rótulo pro rastreamento de custo por hora (ver [usage-hora]) —
    "agendamento" quando chamado no fechamento do CRM, "disponibilidade"
    quando chamado na checagem em tempo real de agenda (11/08), pra não
    misturar esse custo com o de "chat" nem esconder ele lá dentro.

    O prompt precisa fixar explicitamente a data/hora atual em Brasília E
    deixar claro que o horário pedido já é local (BRT), sem conversão
    nenhuma — sem esse ancoramento, o modelo pode inferir (por padrão)
    que um horário em formato ISO deveria estar em UTC, e devolver um
    horário deslocado (+3h) do que o lead realmente pediu."""
    if not preferencia or not preferencia.strip():
        return None
    try:
        agora_brt = datetime.utcnow() - timedelta(hours=3)
        prompt = (
            f"Data e hora atuais em Brasília (BRT, UTC-3): {agora_brt.strftime('%Y-%m-%dT%H:%M')} "
            f"({agora_brt.strftime('%A')}).\n\n"
            "Converta a preferência de reunião abaixo para data e hora futuras no formato "
            "ISO exato AAAA-MM-DDTHH:MM (ex: 2026-07-15T10:00), usando a data atual acima como "
            "referência. IMPORTANTE: o horário na preferência já está no horário local de "
            "Brasília (BRT) — responda no MESMO horário local, sem converter para UTC nem "
            "aplicar nenhum deslocamento de fuso. CRÍTICO: se a preferência mencionar só um "
            "período vago do dia (ex: 'de manhã', 'à tarde', 'à noite') SEM uma hora exata "
            "dentro desse período, isso NÃO é informação suficiente — responda INDEFINIDA. "
            "NUNCA invente/chute uma hora específica dentro de um período vago (caso real "
            "confirmado: lead disse só 'quarta de manhã', o modelo chutou 9h sem o lead ter "
            "pedido isso, e uma reunião real foi criada e confirmada nesse horário inventado, "
            "enquanto o lead ainda ia especificar a hora exata em seguida). Se a preferência "
            "não tiver informação "
            "suficiente para determinar data e hora, responda apenas INDEFINIDA.\n"
            "Responda APENAS o ISO ou INDEFINIDA, nada mais.\n\n"
            f"Preferência: {preferencia}"
        )
        resp = call_claude([{"role": "user", "content": prompt}], max_tokens=30, tipo="classificacao",
                           model="claude-haiku-4-5-20251001").strip()
        if "INDEFINIDA" in resp.upper():
            return None
        dt_parseado = datetime.strptime(resp[:16], "%Y-%m-%dT%H:%M")

        # A IA pode errar a hora mesmo com texto sem ambiguidade nenhuma —
        # já vimos isso acontecer de dois jeitos diferentes (confusão
        # BRT/UTC, e um erro isolado do modelo). Em vez de confiar
        # cegamente na hora que a IA devolveu, valida contra uma extração
        # determinística (regex, sem IA) do horário explícito no texto
        # original — se o texto tem uma hora clara e ela não bate com o
        # que a IA devolveu, corrige pra hora do texto (mais confiável
        # que a IA nesse pedaço específico).
        hora_explicita = extrair_hora_explicita(preferencia)
        if hora_explicita and (dt_parseado.hour, dt_parseado.minute) != hora_explicita:
            print(f"[crm] ⚠️ Hora da IA ({dt_parseado.hour:02d}:{dt_parseado.minute:02d}) diverge do "
                  f"texto original ('{preferencia}', hora explícita={hora_explicita[0]:02d}:"
                  f"{hora_explicita[1]:02d}) — corrigindo pra hora do texto", flush=True)
            dt_parseado = dt_parseado.replace(hour=hora_explicita[0], minute=hora_explicita[1])

        return dt_parseado.strftime("%Y-%m-%dT%H:%M")
    except Exception as e:
        print(f"[crm] Preferência não convertida ('{preferencia}'): {e}", flush=True)
        return None


def criar_negocio_funil_comercial(person_id, nome: str):
    """Cria um negócio no Funil Comercial (etapa Novo Lead) pra uma pessoa
    que JÁ EXISTE no Agendor (ex: tem negócio só em outro funil). Retorna
    o deal criado ou None.

    Rede de segurança (12/08): se a criação falhar porque JÁ EXISTE um
    negócio com esse título pra essa pessoa (erro real observado: "There
    can only be one deal with this title for this organization/person"):
      - Se esse negócio existente ainda está ATIVO (não ganho/perdido),
        reaproveita ele — é o mesmo negócio de verdade, só o título colidiu.
      - Se esse negócio existente já está GANHO ou PERDIDO, NÃO reaproveita
        ([gestor] confirmou: nesse caso precisa criar um negócio novo de
        verdade) — em vez disso, tenta de novo com um sufixo sequencial
        limpo: "(1)", "(2)", "(3)"... pegando o primeiro número livre."""
    nome_final = nome or "Lead via Luca (WhatsApp)"
    ETAPA_NOVO_LEAD_ID = 2835663
    titulo = f"{nome_final} - via Luca (WhatsApp)"
    try:
        payload_deal = {
            "title": titulo,
            "dealStageId": ETAPA_NOVO_LEAD_ID,
        }
        rd = requests.post(f"{AGENDOR_BASE}/people/{person_id}/deals",
                            headers={**HEADERS, "Content-Type": "application/json"},
                            json=payload_deal, timeout=15)
        print(f"[crm] Criação de negócio (pessoa já existia) status={rd.status_code} body={rd.text[:300]}", flush=True)
        if rd.status_code in (200, 201):
            return rd.json().get("data") or rd.json()
        if rd.status_code == 400 and "one deal with this title" in rd.text:
            deals = []
            for attempt in range(3):
                try:
                    r2 = requests.get(f"{AGENDOR_BASE}/people/{person_id}/deals", headers=HEADERS, timeout=15)
                    if r2.status_code == 429:
                        raise requests.exceptions.HTTPError("429 buscando deals (colisão de título)")
                    r2.raise_for_status()
                    deals = r2.json().get("data", [])
                    break
                except Exception as e:
                    print(f"[crm] Tentativa {attempt+1}/3 falhou (deals colisão): {e}", flush=True)
                    if attempt < 2:
                        time.sleep(3)
            existente = next((d for d in deals if d.get("title") == titulo), None)
            if existente and not existente.get("wonAt") and not existente.get("lostAt"):
                print(f"[crm] Negócio existente com esse título está ATIVO — reaproveitando "
                      f"deal={existente.get('id')}", flush=True)
                return existente
            print(f"[crm] Negócio existente com esse título está ganho/perdido — criando "
                  f"negócio novo de verdade, com sufixo sequencial", flush=True)
            # Acha o primeiro número livre entre parênteses, olhando os
            # títulos já usados por essa pessoa (ex: "... (1)", "... (2)")
            numeros_usados = set()
            for d in deals:
                t = d.get("title") or ""
                if t == titulo:
                    numeros_usados.add(0)
                elif t.startswith(f"{titulo} (") and t.endswith(")"):
                    try:
                        numeros_usados.add(int(t[len(titulo) + 2:-1]))
                    except ValueError:
                        pass
            proximo = 1
            while proximo in numeros_usados:
                proximo += 1
            payload_deal["title"] = f"{titulo} ({proximo})"
            rd2 = requests.post(f"{AGENDOR_BASE}/people/{person_id}/deals",
                                headers={**HEADERS, "Content-Type": "application/json"},
                                json=payload_deal, timeout=15)
            print(f"[crm] Criação de negócio com título único status={rd2.status_code} "
                  f"body={rd2.text[:300]}", flush=True)
            if rd2.status_code in (200, 201):
                return rd2.json().get("data") or rd2.json()
    except Exception as e:
        print(f"[crm] Erro ao criar negócio pra pessoa existente: {e}", flush=True)
    return None


def criar_pessoa_e_negocio(phone: str, nome: str, email: str):
    """Cria pessoa e negócio no Agendor quando o lead ainda não existe no CRM
    (contato só existia no AgendorChat, sem registro no Agendor). Usado quando
    buscar_pessoa_e_negocio não encontra nem a PESSOA. Retorna (person, deal)
    ou (None, None) em caso de falha."""
    phone_clean = phone.replace("+", "").replace(" ", "").strip()
    nome_final = nome or "Lead via Luca (WhatsApp)"

    person = None
    try:
        payload_pessoa = {"name": nome_final, "contact": {"whatsapp": phone_clean}}
        if email:
            payload_pessoa["contact"]["email"] = email
        rp = requests.post(f"{AGENDOR_BASE}/people",
                            headers={**HEADERS, "Content-Type": "application/json"},
                            json=payload_pessoa, timeout=15)
        print(f"[crm] Criação de pessoa status={rp.status_code} body={rp.text[:300]}", flush=True)
        if rp.status_code in (200, 201):
            person = rp.json().get("data") or rp.json()
    except Exception as e:
        print(f"[crm] Erro ao criar pessoa: {e}", flush=True)

    if not person or not person.get("id"):
        print("[crm] Pessoa não criada — abortando criação de negócio", flush=True)
        return None, None

    deal = criar_negocio_funil_comercial(person["id"], nome_final)
    return person, deal


def atualizar_pessoa_se_incompleta(person: dict, nome_lead: str, email_lead: str):
    """Completa nome e/ou e-mail da pessoa no Agendor quando estiverem
    faltando ou parecerem genéricos (ex: nome igual ao identificador do
    WhatsApp, sem espaço, quando temos um nome completo capturado na
    conversa; e-mail vazio). NUNCA sobrescreve um dado que já pareça
    legítimo — evita "corrigir" algo que já estava certo."""
    if not person or not person.get("id"):
        return
    contato = person.get("contact") or {}
    nome_atual = (person.get("name") or "").strip()
    email_atual = (contato.get("email") or "").strip()

    updates = {}
    if nome_lead and nome_lead.strip():
        nome_lead_limpo = nome_lead.strip()
        if (not nome_atual) or (" " not in nome_atual and " " in nome_lead_limpo):
            updates["name"] = nome_lead_limpo
    if email_lead and email_lead.strip() and not email_atual:
        emails_encontrados = extrair_emails(email_lead)
        primeiro_email = emails_encontrados[0] if emails_encontrados else email_lead.strip()
        updates["contact"] = {"email": primeiro_email}

    if not updates:
        return

    try:
        r = requests.put(f"{AGENDOR_BASE}/people/{person['id']}",
                          headers={**HEADERS, "Content-Type": "application/json"},
                          json=updates, timeout=15)
        print(f"[crm] Pessoa atualizada (nome/e-mail) person={person['id']} "
              f"status={r.status_code} campos={list(updates.keys())}", flush=True)
    except Exception as e:
        print(f"[crm] Erro ao atualizar pessoa {person.get('id')}: {e}", flush=True)


def _reunioes_do_dia(dt_dia, owner_id):
    """Retorna os horários (datetime, sem timezone, hora de Brasília) das
    reuniões [Luca] já marcadas para o mesmo dia e mesmo consultor,
    usando o cache de tasks já mantido por fetch_tasks_job (sem chamada
    nova à API do Agendor)."""
    resultado = []
    for t in tasks_cache.get("data", []):
        if t.get("type") != "reuniao":
            continue
        assigned = t.get("assignedUsers") or []
        assigned_ids = {a.get("id") for a in assigned if isinstance(a, dict)}
        if owner_id and owner_id not in assigned_ids:
            continue
        due = _parse_dt(t.get("dueDate"))
        if not due:
            continue
        due_naive = due.replace(tzinfo=None)
        if due_naive.date() == dt_dia.date():
            resultado.append(due_naive)
    return resultado


def ajustar_horario_reuniao(dt_desejado, owner_id):
    """Tenta evitar conflito na agenda do consultor antes de criar a
    tarefa de reunião. Prioridades, na ordem (a de cima nunca é
    sacrificada pela de baixo):
      1. SEMPRE agenda — nunca deixa de marcar por falta de slot 'perfeito'.
      2. Mantém o mesmo dia pedido pelo lead (nunca empurra pra outro dia).
      3. Evita coincidir com o horário exato de outra reunião do mesmo
         consultor, a não ser que não sobre nenhuma alternativa no dia.
      4. Evita marcar com menos de 1h de antecedência (agora vs. horário).
      5. Tenta manter 30 min de intervalo de qualquer outra reunião.
    Retorna (dt_final, ajustado: bool). Isso é 100% interno — o lead
    nunca sabe que isso aconteceu, e o Luca não promete nada sobre
    agenda na conversa (regra do SYSTEM_PROMPT)."""
    agora = datetime.utcnow() - timedelta(hours=3)
    reunioes_dia = _reunioes_do_dia(dt_desejado, owner_id)

    def respeita_intervalo(dt):
        return all(abs((dt - r).total_seconds()) >= 30 * 60 for r in reunioes_dia)

    def antecedencia_ok(dt):
        return (dt - agora).total_seconds() >= 60 * 60

    conflito_exato = any(dt_desejado == r for r in reunioes_dia)
    if not conflito_exato and respeita_intervalo(dt_desejado) and antecedencia_ok(dt_desejado):
        return dt_desejado, False

    # Candidatos no MESMO dia, em passos de 15 min, alternando pra frente
    # e pra trás, do mais próximo ao mais distante do horário pedido —
    # queremos o ajuste mínimo possível.
    candidatos = []
    for passo in range(1, 25):  # até 6h de distância, 15 em 15 min
        candidatos.append(dt_desejado + timedelta(minutes=15 * passo))
        candidatos.append(dt_desejado - timedelta(minutes=15 * passo))

    for cand in candidatos:
        if cand.date() != dt_desejado.date():
            continue
        if not antecedencia_ok(cand):
            continue
        if respeita_intervalo(cand):
            return cand, True

    # Não achou slot que satisfaça tudo — prioridade é agendar, então
    # mantém o horário original pedido pelo lead, mesmo com conflito ou
    # antecedência curta, sem forçar nada na conversa com o lead.
    return dt_desejado, False


def detectar_linha_negocio(segmento: str) -> str:
    """Mapeia o texto livre extraído no campo 'segmento' pra 'tech' ou
    'contabilidade', com base em palavras-chave observadas nos dados reais
    (ex: 'Tecnologia', 'Fintech / Criptomoedas', 'Desenvolvedor/Freelancer
    Tech'). Fallback pra 'contabilidade' quando não bate com nenhuma —
    é a linha de negócio padrão/majoritária da empresa."""
    s = (segmento or "").lower()
    palavras_tech = ["tech", "tecnolog", "dev", "fintech", "software",
                      "freelancer", "startup", "saas", "app "]
    return "tech" if any(p in s for p in palavras_tech) else "contabilidade"


def registrar_no_crm(conv, conversation_id, contact_name):
    """Fecha o ciclo no Agendor quando a qualificação conclui:
    0. Se pessoa/negócio não existirem no CRM ainda, cria os dois primeiro
    1. Nota com o resumo do lead
    2. Registro WhatsApp com a transcrição da conversa
    3. Reunião [Luca] atribuída ao dono do negócio (se houver preferência) —
       cria a reunião REAL no Teams quando já há data confirmada e e-mail
    4. Campo personalizado 'Reunião agendada por' = Luca
    5. Move o negócio para a etapa 'Reunião agendada' no Funil Comercial,
       só se ainda estiver numa etapa anterior (nunca rebaixa)

    Retorna True se o ciclo está TOTALMENTE fechado (nota + reunião real
    quando aplicável), False se ainda falta algo que pode ser completado
    numa passada futura (ex: falta e-mail/horário real ainda) — usado pelo
    chamador pra decidir se tenta de novo mais tarde."""
    try:
        phone = conv.get("phone", "")
        if not phone:
            print(f"[crm] Sem telefone na conversa {conversation_id} — registro pulado", flush=True)
            return False
        person, deal = buscar_pessoa_e_negocio(phone)
        d = conv.get("lead_data", {})
        if not deal:
            nome_lead = d.get("nome") or contact_name
            email_lead = d.get("email", "")
            if person and person.get("id"):
                print(f"[crm] Pessoa existe mas sem negócio no Funil Comercial para {phone} "
                      f"conv={conversation_id} — criando negócio novo", flush=True)
                deal = criar_negocio_funil_comercial(person["id"], nome_lead)
            else:
                print(f"[crm] Pessoa/negócio não encontrados para {phone} conv={conversation_id} — "
                      f"tentando criar os dois", flush=True)
                person, deal = criar_pessoa_e_negocio(phone, nome_lead, email_lead)
            if not deal:
                print(f"[crm] Não foi possível criar negócio para {phone} "
                      f"conv={conversation_id} — registro abortado", flush=True)
                return False
        deal_id = deal.get("id")

        # ── Marcadores duráveis (sobrevivem a restart, ficam gravados no
        # Agendor, não em RAM) ──────────────────────────────────────────────
        # Nota e reunião real têm marcadores INDEPENDENTES — se só um
        # existisse, o ciclo inteiro pararia de rodar assim que a nota
        # existisse, mesmo que a reunião real ainda não tivesse sido
        # criada (ex: faltava e-mail/horário na primeira passada). Com os
        # dois separados, uma passada futura ainda completa a reunião
        # mesmo que a nota já exista há tempos.
        nota_marcador = f"[luca:nota:{conversation_id}]"
        reuniao_real_marcador = f"[luca:reuniao_real:{conversation_id}]"
        reuniao_fallback_marcador = f"[luca:reuniao_fallback:{conversation_id}]"
        ja_tem_nota = deal_tem_marca(deal_id, nota_marcador)
        ja_tem_reuniao_real = deal_tem_marca(deal_id, reuniao_real_marcador)

        # ── Completa nome/e-mail da pessoa no Agendor, se estiverem faltando
        # ou genéricos (ex: nome só do WhatsApp, sem e-mail) ─────────────────
        nome_pessoa = d.get("nome") or contact_name
        email_pessoa = d.get("email", "")
        atualizar_pessoa_se_incompleta(person, nome_pessoa, email_pessoa)

        # ── 1. Nota: resumo do lead ───────────────────────────────────────
        if ja_tem_nota:
            print(f"[crm] Nota resumo já existe (idempotência) deal={deal_id} conv={conversation_id}", flush=True)
        else:
            nota = (
                "📋 Atendimento via Luca (WhatsApp)\n"
                f"Nome: {d.get('nome') or contact_name}\n"
                f"Segmento: {d.get('segmento', '')}\n"
                f"Necessidade: {d.get('necessidade', '')}\n"
                f"E-mail: {d.get('email', '')}\n"
                f"Preferência de reunião: {d.get('preferencia', '')}\n"
                f"Status: {d.get('status', '')}\n"
                f"{nota_marcador}"
            )
            r1 = requests.post(f"{AGENDOR_BASE}/deals/{deal_id}/tasks",
                               headers={**HEADERS, "Content-Type": "application/json"},
                               json={"text": nota}, timeout=15)
            print(f"[crm] Nota resumo deal={deal_id} status={r1.status_code}", flush=True)

        # ── 2. Registro WhatsApp: transcrição compacta (idempotente) ─────────
        transcricao_marcador = f"[luca:transcricao:{conversation_id}]"
        if deal_tem_marca(deal_id, transcricao_marcador):
            print(f"[crm] Transcrição já existe (idempotência) deal={deal_id} conv={conversation_id}", flush=True)
        else:
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
                texto_bloco = bloco + sufixo
                if idx == 0:
                    texto_bloco += f"\n{transcricao_marcador}"
                r2 = requests.post(f"{AGENDOR_BASE}/deals/{deal_id}/tasks",
                                   headers={**HEADERS, "Content-Type": "application/json"},
                                   json={"text": texto_bloco, "type": "whatsapp"}, timeout=15)
                print(f"[crm] Transcrição{sufixo} deal={deal_id} status={r2.status_code}", flush=True)

        # ── 3. Reunião [Luca] — somente se há preferência de horário ─────────
        preferencia = (d.get("preferencia") or "").strip()
        requer_validacao_time = False
        if preferencia:
            owner_id = (deal.get("owner") or {}).get("id")
            owner_id_int = int(owner_id) if owner_id else None

            if ja_tem_reuniao_real:
                print(f"[crm] Reunião real já criada antes (idempotência) deal={deal_id} conv={conversation_id}", flush=True)
            else:
                dt_iso = parse_preferencia_datetime(preferencia)
                teams_join_url = None
                email_lead = (d.get("email") or "").strip()
                if dt_iso and email_lead:
                    dt_pedido = datetime.strptime(dt_iso, "%Y-%m-%dT%H:%M")
                    # A preferência extraída é apenas a preferência do lead.
                    # A confirmação REAL acontece abaixo, imediatamente antes da criação.
                    # Nunca troca silenciosamente o horário por outro: se o slot pedido não
                    # puder ser confirmado, o caso vai para validação humana.
                    dt_local = dt_pedido
                    requer_validacao_time = False
                    motivo_validacao = ""
                    texto_reuniao = ("[Luca] Reunião com especialista — pré-agendada pelo Luca via WhatsApp, "
                                     f"aguardando confirmação do consultor. Preferência do lead: {preferencia}")

                    # Checagem final fail-closed + lock contra corrida entre duas conversas.
                    # Se a Graph falhar ou o horário já estiver ocupado, NÃO cria Teams.
                    try:
                        with _TEAMS_AGENDAMENTO_LOCK:
                            try:
                                eventos_finais = buscar_eventos_do_dia_organizador(dt_local)
                                fim_local = dt_local + timedelta(minutes=30)
                                ocupado = any(dt_local < ev_fim and fim_local > ev_ini
                                              for ev_ini, ev_fim in eventos_finais)
                            except Exception as e:
                                requer_validacao_time = True
                                motivo_validacao = f"não foi possível validar a agenda automaticamente: {e}"
                                ocupado = False

                            if ocupado:
                                requer_validacao_time = True
                                motivo_validacao = "horário solicitado já está ocupado na agenda do consultor"

                            if not requer_validacao_time:
                                linha_negocio = detectar_linha_negocio(d.get("segmento", ""))
                                nome_reuniao = d.get("nome") or contact_name or "Lead"
                                start_teams = dt_local.strftime("%Y-%m-%dT%H:%M:%S")
                                resultado_teams = create_teams_meeting(nome_reuniao, email_lead, start_teams, linha_negocio)
                                teams_join_url = resultado_teams.get("join_url")
                                if teams_join_url:
                                    texto_reuniao += f"\nLink da reunião (Teams): {teams_join_url}\n{reuniao_real_marcador}"
                                    print(f"[crm] ✅ Reunião Teams criada deal={deal_id} "
                                          f"linha={linha_negocio} join_url={teams_join_url}", flush=True)
                                    mensagem_link = (
                                        f"Consegui deixar tudo pronto, {nome_reuniao.split(' ')[0]}! Aqui está o link "
                                        f"da nossa videochamada:\n{teams_join_url}\n\nQualquer dúvida antes, estou por aqui."
                                    )
                                    send_agendorchat_message(conversation_id, remover_travessao(mensagem_link))
                                else:
                                    requer_validacao_time = True
                                    motivo_validacao = "Teams criou a reunião sem retornar o link de acesso"
                    except Exception as e:
                        requer_validacao_time = True
                        motivo_validacao = f"erro ao criar/validar reunião no Teams: {e}"

                    if requer_validacao_time:
                        print(f"[crm] ⚠️ Reunião NÃO criada automaticamente deal={deal_id}: {motivo_validacao}", flush=True)
                        validacao_marcador = f"[luca:validar_horario:{conversation_id}]"
                        if not deal_tem_marca(deal_id, validacao_marcador):
                            texto_validacao = (
                                "[Luca] VALIDAR HORÁRIO COM O TIME — não foi criada reunião automática. "
                                f"Preferência do lead: {preferencia}. Motivo: {motivo_validacao}. "
                                f"{validacao_marcador}"
                            )
                            payload_validacao = {
                                "text": texto_validacao,
                                "type": "tarefa",
                                "due_date": dt_local.strftime("%Y-%m-%dT%H:%M:%S"),
                            }
                            if owner_id:
                                payload_validacao["assigned_users"] = [int(owner_id)]
                            rv = requests.post(f"{AGENDOR_BASE}/deals/{deal_id}/tasks",
                                               headers={**HEADERS, "Content-Type": "application/json"},
                                               json=payload_validacao, timeout=15)
                            print(f"[crm] Validação humana de horário criada deal={deal_id} "
                                  f"status={rv.status_code}", flush=True)
                            mensagem_validacao = (
                                "Entendi! Nesse horário eu não consigo confirmar automaticamente. "
                                "Vou validar essa possibilidade com o nosso time e eles te retornam por aqui, combinado?"
                            )
                            send_agendorchat_message(conversation_id, remover_travessao(mensagem_validacao))
                    else:
                        due = dt_local.strftime("%Y-%m-%dT%H:%M:%S")
                        payload_reuniao = {"text": texto_reuniao, "type": "reuniao", "due_date": due}
                        if owner_id:
                            payload_reuniao["assigned_users"] = [int(owner_id)]
                        r3 = requests.post(f"{AGENDOR_BASE}/deals/{deal_id}/tasks",
                                           headers={**HEADERS, "Content-Type": "application/json"},
                                           json=payload_reuniao, timeout=15)
                        print(f"[crm] Reunião [Luca] deal={deal_id} due={due} status={r3.status_code} body={r3.text[:200]}", flush=True)
                        ja_tem_reuniao_real = bool(teams_join_url)
                else:
                    # Sem data real confirmada ainda, ou sem e-mail ainda —
                    # cria só a tarefa de fallback "HORÁRIO A CONFIRMAR", com
                    # seu próprio marcador (pra não duplicar essa tarefa a
                    # cada nova tentativa enquanto a informação real não
                    # chega — corrigido 08/09 junto com o resto deste fix).
                    if deal_tem_marca(deal_id, reuniao_fallback_marcador):
                        print(f"[crm] Tarefa de fallback já existe (idempotência) deal={deal_id} conv={conversation_id}", flush=True)
                    else:
                        prox = datetime.utcnow() - timedelta(hours=3) + timedelta(days=1)
                        while prox.weekday() >= 5:
                            prox += timedelta(days=1)
                        dt_pedido = datetime(prox.year, prox.month, prox.day, 9, 0)
                        dt_local, ajustado = ajustar_horario_reuniao(dt_pedido, owner_id_int)
                        texto_reuniao = ("[Luca] Reunião com especialista — HORÁRIO A CONFIRMAR com o lead. "
                                         f"Preferência informada: {preferencia} {reuniao_fallback_marcador}")
                        if ajustado:
                            texto_reuniao += f" (horário provisório ajustado para {dt_local.strftime('%H:%M')})"
                        due = dt_local.strftime("%Y-%m-%dT%H:%M:%S")
                        payload_reuniao = {"text": texto_reuniao, "type": "reuniao", "due_date": due}
                        if owner_id:
                            payload_reuniao["assigned_users"] = [int(owner_id)]
                        r3 = requests.post(f"{AGENDOR_BASE}/deals/{deal_id}/tasks",
                                           headers={**HEADERS, "Content-Type": "application/json"},
                                           json=payload_reuniao, timeout=15)
                        print(f"[crm] Reunião [Luca] (fallback) deal={deal_id} due={due} status={r3.status_code}", flush=True)
                        if not email_lead:
                            print(f"[crm] Sem e-mail do lead — não foi possível criar reunião automática "
                                  f"no Teams deal={deal_id} (consultor confirma manualmente)", flush=True)

            # Só marca como reunião agendada quando houve reunião real ou quando
            # seguimos o fluxo legado sem pendência explícita de validação humana.
            if requer_validacao_time:
                print(f"[crm] Campo/etapa de reunião NÃO atualizados — aguardando validação humana deal={deal_id}", flush=True)
                campo = {}
            else:
                campo = resolver_campo_agendada_por()
            # ── 4. Campo personalizado 'Reunião agendada por' = Luca ─────────
            if campo.get("key") and campo.get("luca_id"):
                r4 = requests.put(f"{AGENDOR_BASE}/deals/{deal_id}",
                                  headers={**HEADERS, "Content-Type": "application/json"},
                                  json={"customFields": {campo["key"]: campo["luca_id"]}}, timeout=15)
                print(f"[crm] Campo agendada_por=Luca deal={deal_id} status={r4.status_code}", flush=True)

            # ── 5. Move etapa para 'Reunião agendada' (Funil Comercial, só avança) ─
            FUNIL_COMERCIAL_ID = 696449
            ETAPA_REUNIAO_AGENDADA_ID = 2845579
            # Unificado em 18/08: usa a lista única definida no nível do
            # arquivo (ORDEM_ETAPAS_FUNIL_COMERCIAL) em vez de uma cópia
            # local — eram 2 cópias idênticas, risco de ficarem
            # dessincronizadas de novo no futuro (foi exatamente isso que
            # causou o bug real da [lead], 13/08).
            try:
                deal_fresco = buscar_deal_fresco(deal_id)
            except Exception as e:
                print(f"[crm] Erro ao buscar negócio fresco pra checar etapa: {e}", flush=True)
                deal_fresco = {}
            deal_stage = deal_fresco.get("dealStage") or {}
            funil_atual_id = (deal_stage.get("funnel") or {}).get("id")
            etapa_atual_id = deal_stage.get("id")

            if requer_validacao_time:
                print(f"[crm] Etapa não movida — horário aguardando validação humana deal={deal_id}", flush=True)
            elif funil_atual_id == FUNIL_COMERCIAL_ID:
                idx_atual = (ORDEM_ETAPAS_FUNIL_COMERCIAL.index(etapa_atual_id)
                             if etapa_atual_id in ORDEM_ETAPAS_FUNIL_COMERCIAL else None)
                idx_alvo = ORDEM_ETAPAS_FUNIL_COMERCIAL.index(ETAPA_REUNIAO_AGENDADA_ID)
                if idx_atual is not None and idx_atual < idx_alvo:
                    sequencia_alvo = idx_alvo + 1  # API espera a posição (1-indexed) dentro do funil, não o ID global
                    r5 = requests.put(f"{AGENDOR_BASE}/deals/{deal_id}/stage",
                                       headers={**HEADERS, "Content-Type": "application/json"},
                                       json={"dealStage": sequencia_alvo}, timeout=15)
                    print(f"[crm] Etapa -> 'Reunião agendada' deal={deal_id} status={r5.status_code} body={r5.text[:200]}", flush=True)
                else:
                    print(f"[crm] Etapa não movida — atual={etapa_atual_id} já é igual/posterior a 'Reunião agendada' "
                          f"ou fora da ordem mapeada (ex: Perdido)", flush=True)
            else:
                print(f"[crm] Etapa não movida — negócio fora do Funil Comercial (funil={funil_atual_id})", flush=True)

        ciclo_completo = ja_tem_nota or True  # nota sempre fica resolvida nesta passada (posta ou já existia)
        ciclo_completo = ciclo_completo and (not preferencia or ja_tem_reuniao_real)
        if ciclo_completo:
            conv["crm_registrado"] = True
            print(f"[crm] ✅ Ciclo registrado no CRM deal={deal_id} conv={conversation_id}", flush=True)
        else:
            print(f"[crm] Ciclo parcialmente registrado deal={deal_id} conv={conversation_id} — "
                  f"reunião real ainda pendente, tenta de novo numa próxima mensagem", flush=True)
        return ciclo_completo
    except Exception as e:
        print(f"[crm] Erro ao registrar conv={conversation_id}: {e}", flush=True)
        return False


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
    """Busca status e assignee atuais de uma conversa no AgendorChat.

    Corrigido 20/08: sem proteção contra falha temporária (ex: 502 do
    lado do AgendorChat), confirmado em produção causando erro em
    cascata em lembretes e follow-ups.

    Corrigido 27/08 (revisão externa, ponto 8): trocado o backoff fixo
    (3s sempre) pelo exponencial (2s, 4s), mesmo raciocínio do fetch_page."""
    url = f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/conversations/{conversation_id}"
    for attempt in range(3):
        try:
            resp = requests.get(
                url,
                headers={"api_access_token": AGENDORCHAT_TOKEN},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"[conv_details] Tentativa {attempt+1}/3 falhou conv={conversation_id}: {e}", flush=True)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))  # 2s, 4s
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
        # Considera apenas o diálogo real: ignora mensagens de atividade do
        # sistema ("fulano atribuiu...", message_type=2), notas privadas,
        # templates disparados por automação nativa (additional_attributes.
        # automation_id — ex: "boas_vindas_primeiro_contato"), e a saudação
        # automática de canal (que NÃO tem automation_id, additional_attributes
        # vem vazio — identificada aqui pelo texto fixo). Qualquer uma dessas
        # mascarava a última mensagem verdadeira do lead como "já respondida"
        # sem ninguém (humano ou Luca) ter feito nada de fato.
        dialogo = [m for m in messages
                   if m.get("message_type") in (0, 1, 3) and not m.get("private")
                   and not (m.get("additional_attributes") or {}).get("automation_id")
                   and "Em breve um de nossos consultores dará andamento" not in (m.get("content") or "")]
        if not dialogo:
            return {}
        last = dialogo[-1]
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
    """Monta o texto da nota interna com o resumo do lead, em dois blocos:
    Dados do Lead (operacional) e Inteligência Comercial (contexto de venda).
    Campos não informados/não inferíveis aparecem como "Não informado" —
    nunca são deduzidos por suposição."""
    g = lambda campo, default="Não informado": conv_data.get(campo) or default

    nome       = g("nome")
    segmento   = g("segmento", "Não identificado")
    empresa    = g("empresa_situacao")
    faturamento = g("faturamento_aproximado")
    contador   = g("contador_atual")
    telefone   = g("telefone")
    email      = g("email")
    agendamento = g("preferencia", "Não agendado")

    objetivo   = g("objetivo")
    motivo     = g("necessidade", "Não informado")
    duvida     = g("duvida_principal")
    dor        = g("dor_identificada")
    urgencia   = g("urgencia")
    proxima_acao = g("proxima_acao_consultor")
    resumo_conversa = g("resumo_conversa")
    estilo_comunicacao = g("estilo_comunicacao")
    motivo_recusa = g("motivo_recusa")

    status     = conv_data.get("status", "Em atendimento")

    lines = [
        "📋 DADOS DO LEAD",
        f"Nome: {nome}",
        f"Segmento: {segmento}",
        f"Empresa: {empresa}",
        f"Faturamento aproximado: {faturamento}",
        f"Contador atual: {contador}",
        f"Telefone: {telefone}",
        f"E-mail: {email}",
        f"Agendamento: {agendamento}",
        "",
        "🧠 INTELIGÊNCIA COMERCIAL",
        f"• Objetivo: {objetivo}",
        f"• Motivo do contato: {motivo}",
        f"• Principal dúvida: {duvida}",
        f"• Dor identificada: {dor}",
        f"• Urgência: {urgencia}",
        f"• Próxima ação esperada: {proxima_acao}",
        f"• Estilo de comunicação observado: {estilo_comunicacao}",
        f"• Resumo da conversa: {resumo_conversa}",
    ]
    note = "\n".join(lines)
    note += f"\n\nStatus: {status}"
    if motivo_recusa and motivo_recusa != "Não informado":
        note += f"\nMotivo da recusa: {motivo_recusa}"
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

Retorne este JSON (deixe em branco "" se não informado OU não puder ser inferido com segurança —
NUNCA invente, deduza ou chute um valor plausível; vazio é sempre melhor que um palpite):
{{
  "nome": "",
  "segmento": "",
  "empresa_situacao": "",
  "faturamento_aproximado": "",
  "contador_atual": "",
  "email": "",
  "preferencia": "",
  "objetivo": "",
  "necessidade": "",
  "duvida_principal": "",
  "dor_identificada": "",
  "urgencia": "",
  "proxima_acao_consultor": "",
  "estilo_comunicacao": "",
  "motivo_recusa": "",
  "motivo_perda_codigo": "",
  "resumo_conversa": "",
  "status": ""
}}

Campos de DADOS DO LEAD (extração direta, factual):
"empresa_situacao" = se o lead já tem CNPJ aberto ou vai abrir (ex: "Já possui CNPJ", "Vai abrir novo CNPJ", "Segundo CNPJ").
"faturamento_aproximado" = valor ou faixa que o lead mencionou (ex: "~R$8mil/mês"). Só se ele disse um número, nunca estime.
"contador_atual" = "Sim" ou "Não" se o lead mencionou ter contador atualmente; inclua o motivo de troca só se ele disse explicitamente (ex: "Sim, mas contador demora pra responder").

Campos de INTELIGÊNCIA COMERCIAL (exigem mais cuidado — só preencha com evidência clara e literal na conversa):
"objetivo" = o RESULTADO que o lead espera alcançar (ex: "Abrir um CNPJ", "Trocar de contabilidade", "Reduzir carga tributária", "Entender melhor enquadramento"). Se o lead deixar claro que o contato foi um ENGANO/mal-entendido (ex: confundiu a Lucralize com outro tipo de empresa, tipo "achei que fosse de empréstimo"), preencha com o que ele realmente queria, deixando claro que não tem relação com contabilidade (ex: "Conseguir um empréstimo (contatou a empresa errada por engano)") — não deixe em branco só porque o objetivo real não é relevante pro nosso negócio.
"necessidade" = o MOTIVO/gatilho que levou o lead a procurar a Lucralize agora (ex: "Cliente passou a exigir nota fiscal", "Contador demora pra responder"). Diferente de "objetivo": motivo é a causa, objetivo é o resultado desejado. No mesmo caso de engano/mal-entendido acima, registre isso aqui também (ex: "Achou que a empresa fosse de empréstimo (mal-entendido, não tinha motivo real de contabilidade)"), em vez de deixar em branco.
"duvida_principal" = a dúvida ou preocupação específica que o lead levantou (ex: "quanto vai pagar de imposto").
"dor_identificada" = só preencha se o lead expressou uma insatisfação ou problema de forma EXPLÍCITA (ex: lead disse "meu contador nunca responde"). NUNCA infira dor a partir do tom geral da conversa — se não houver uma frase clara indicando isso, deixe em branco.
"urgencia" = "Alta", "Média" ou "Baixa" — combina duas coisas: se há um motivo/prazo real puxando a decisão (ex: obrigação fiscal, início de contrato PJ, contador atual sumiu) E se o lead sinaliza estar decidido a agir ou só pesquisando/sondando. "Alta" = tem motivo concreto e imediato (ex: "preciso disso até sexta", "contrato começa semana que vem"). "Baixa" = só está pesquisando/comparando, sem motivo puxando agora (ex: "só queria entender os preços", "ainda não é pra agora"). Inclua o motivo junto (ex: "Alta — contrato PJ começa semana que vem"). Só preencha com sinal EXPLÍCITO dito pelo lead — sem sinal claro, deixe em branco, não deduza pelo tom.
"proxima_acao_consultor" = uma sugestão curta e concreta do que o consultor deveria fazer na reunião (ex: "Simular tributação com faturamento de 8k/mês", "Explicar processo de migração"), baseada só no que já foi discutido — não invente uma ação genérica se não houver base clara na conversa.
"estilo_comunicacao" = uma descrição BREVE e FACTUAL de como o lead se comunicou NESSA conversa específica (ex: "Direto e objetivo, focou em prazo e preço", "Fez várias perguntas antes de decidir, parece querer entender tudo primeiro", "Respostas curtas, parece estar ocupado/com pressa"). Isso serve só pra ajudar o consultor a calibrar o TOM da reunião — NUNCA use termos de personalidade, traços psicológicos ou classificações (nada de "introvertido", "ansioso", "Big Five", "perfil DISC" ou parecido) — descreva só o comportamento OBSERVÁVEL nas mensagens em si, não um traço permanente da pessoa. Se a conversa for curta demais pra perceber um padrão (poucas mensagens trocadas), deixe em branco.
"motivo_recusa" = só preenche quando o lead recusar EXPLICITAMENTE seguir (ex: "não tenho mais interesse", "não quero"). Capture o motivo real que ele deu quando perguntado (ex: "Já fechou com outro contador", "Achou o valor alto"). Se ele recusou mas não quis dizer o motivo, ou não respondeu quando perguntado, registre "Recusa direta, sem detalhar motivo". Se não houve recusa nenhuma na conversa, deixe em branco — não é todo lead que recusa.
"motivo_perda_codigo" = SÓ preenche quando "motivo_recusa" também estiver preenchido (recusa explícita). Escolha EXATAMENTE UM dos códigos abaixo, ou deixe em branco se não tiver certeza — NUNCA chute o mais parecido, deixar em branco é sempre melhor que uma categoria errada:
"1.1" = o PRÓPRIO lead é contador, ou tem parente que é (ex: "minha esposa é contadora", "eu mesmo cuido disso") — diferente de "1.3", que é ter um contador CONTRATADO.
"1.2" = curioso, nunca teve intenção comercial real (ex: "só queria entender como funciona") — diferente de "3.4", que pressupõe que havia negociação real antes de desistir.
"1.3" = satisfeito com um contador/contabilidade que já CONTRATA hoje (ex: "vou continuar com meu contador atual") — diferente de "3.3", que é ter contratado outra empresa AGORA, nessa negociação.
"2.3" = adiou por causa de prazo/momento (ex: "me chama daqui a 3 meses", "não é hora ainda") — diferente de "3.4", que é desistência sem intenção de retomar.
"3.1" = mencionou preço/valor/orçamento como causa direta (ex: "achei caro", "não cabe no orçamento") — se a frase claramente disser que NÃO é sobre preço, não use esse código.
"3.2" = testou ou avaliou e o serviço não atendeu a expectativa.
"3.3" = fechou com outra contabilidade/concorrente AGORA, nessa negociação.
"3.4" = desistiu sem motivo específico identificável, mas HAVIA intenção/negociação real antes (não é o padrão genérico pra qualquer caso incerto — só use quando a desistência em si for clara, sem outro código mais específico se aplicando).
"3.5" = já estava conversando/negociando de verdade e simplesmente sumiu, sem recusa explícita.
"4.1" = empresa do lead fechou ou está sendo encerrada.
Nunca use "0.0" nem "9.9" — são categorias administrativas da equipe, não algo pra você escolher.
"resumo_conversa" = 1 a 2 frases resumindo o essencial da conversa até agora, em tom neutro e factual.

Para status use: "Em qualificação" | "Interesse confirmado" | "Aguardando e-mail" | "Preferência informada: [dia] às [horário]" | "Agendamento confirmado" | "Perdido: [motivo breve]"

Use "Perdido: [motivo]" SÓ quando o lead disser explicitamente que não vai seguir (ex: "já fechei com outra empresa", "não tenho mais interesse", "vou resolver sozinho") — nunca infira isso por silêncio ou tom.
"""
    try:
        reply = call_claude(
            [{"role": "user", "content": prompt}],
            max_tokens=2000,  # Corrigido 20/08: com Sonnet 5 usando raciocínio
            # estendido, 600 tokens às vezes eram consumidos todos só pelo
            # "thinking", sem sobrar nada pro JSON de verdade — resposta
            # vinha vazia (bug real, confirmado em produção, várias
            # conversas afetadas). Sem custo extra: só paga pelo que o
            # modelo realmente gera, max_tokens é só um teto de segurança.
            system="Você extrai dados estruturados de conversas. Retorne apenas JSON válido. Nunca invente ou deduza valores sem evidência clara e literal no texto — prefira deixar em branco.",
            tipo="extracao"
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
        pessoas = []
        for attempt in range(3):
            try:
                r = requests.get(f"{AGENDOR_BASE}/people", headers=HEADERS,
                                 params={"phone": phone_clean}, timeout=15)
                if r.status_code == 429:
                    raise requests.exceptions.HTTPError(f"429 buscando pessoa phone={phone_clean}")
                r.raise_for_status()
                pessoas = r.json().get("data", [])
                break
            except Exception as e:
                print(f"[origem] Tentativa {attempt+1}/3 falhou (pessoa): {e}", flush=True)
                if attempt < 2:
                    time.sleep(3)
        if not pessoas:
            print(f"[origem] Pessoa não encontrada para telefone {phone_clean}", flush=True)
            return
        person_id = pessoas[0].get("id")
        # Busca negócios da pessoa. IMPORTANTE: GET /deals?personId=X ignora o
        # filtro e devolve negócios de QUALQUER pessoa (bug confirmado na API)
        # — usa o endpoint aninhado, que filtra corretamente.
        deals = []
        for attempt in range(3):
            try:
                r2 = requests.get(f"{AGENDOR_BASE}/people/{person_id}/deals", headers=HEADERS, timeout=15)
                if r2.status_code == 429:
                    raise requests.exceptions.HTTPError(f"429 buscando deals person={person_id}")
                r2.raise_for_status()
                deals = r2.json().get("data", [])
                break
            except Exception as e:
                print(f"[origem] Tentativa {attempt+1}/3 falhou (deals): {e}", flush=True)
                if attempt < 2:
                    time.sleep(3)
        if not deals:
            print(f"[origem] Nenhum negócio encontrado para person_id={person_id}", flush=True)
            return
        # Pega o negócio mais recente
        deal = sorted(deals, key=lambda d: d.get("startTime",""), reverse=True)[0]
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

        lock = obter_lock_resposta(conv_key)
        with lock:
            if is_first_message:
                # Após o delay, busca histórico atualizado para incluir o template
                remote_history = fetch_conversation_history(conversation_id)
                if remote_history:
                    conv["messages"] = remote_history
                    print(f"[history] Histórico atualizado após delay: {len(remote_history)} msgs conv={conversation_id}", flush=True)
                # Injeta instrução para não repetir o que o template já disse
                # Se o template de boas-vindas ficou como ÚLTIMO turno (acontece
                # quando o lead manda só 1 mensagem e não escreve de novo durante
                # os 90s de espera), a Messages API interpreta isso como
                # "continue esse turno do assistant" em vez de "responda de
                # novo" — e como o template já é uma frase fechada, o resultado
                # é resposta vazia, sempre (bug real confirmado: caso [lead],
                # 12/08, 3 tentativas, todas vazias). O Claude não precisa "ver"
                # o texto literal do template pra saber que já foi enviado, só
                # precisa da instrução abaixo — então remove esse turno final
                # antes de chamar, garantindo que a conversa sempre termine num
                # turno "user" de verdade.
                if conv["messages"] and conv["messages"][-1]["role"] == "assistant":
                    conv["messages"].pop()
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

            # ── Checagem real de agenda antes de responder (11/08) ────────────
            # Se a mensagem do lead parece conter um dia/horário, converte pra
            # data real e checa a agenda de verdade do consultor (Outlook/
            # Teams via Graph) — não só as tarefas do Agendor, que é o que a
            # gente já checava antes só no fechamento do CRM (tarde demais pra
            # sugerir troca). Se ocupado, injeta instrução pra ESTA resposta
            # sugerir até 2 alternativas no mesmo dia, sem revelar que "checou
            # a agenda" (mantém a regra do SYSTEM_PROMPT sobre isso). Filtro
            # regex barato evita chamar o Claude (parse_preferencia_datetime)
            # em mensagem que claramente não menciona horário.
            #
            # Pula essa checagem inteira quando o ciclo do CRM já fechou de
            # verdade pra esse negócio (a reunião real já foi criada) —
            # nesse ponto o horário já está confirmado e definitivo, e
            # reconferir a agenda pode achar o PRÓPRIO evento recém-criado
            # e sugerir um "horário alternativo" pro lead pro horário dele
            # mesmo, como se estivesse ocupado por outra pessoa.
            extra_disponibilidade = ""
            extra_disponibilidade += contexto_campanha_cct(conversation_id)
            if parece_ter_horario(message_text) and not conv.get("crm_registrado"):
                try:
                    dt_iso_tentativa = parse_preferencia_datetime(message_text, tipo="disponibilidade")
                    if dt_iso_tentativa:
                        dt_pedido = datetime.strptime(dt_iso_tentativa, "%Y-%m-%dT%H:%M")
                        livre, alternativas = checar_e_sugerir_horario(dt_pedido)
                        if not livre:
                            if alternativas:
                                opcoes = " ou ".join(a.strftime("%Hh%M") for a in alternativas)
                                extra_disponibilidade = (
                                    f"\n\nATENÇÃO (checagem real de agenda, não mencione isso ao lead): "
                                    f"o horário {dt_pedido.strftime('%Hh%M')} que o lead acabou de pedir já "
                                    f"está ocupado na agenda do consultor. Em vez de anotar esse horário, "
                                    f"sugira estas duas opções no mesmo dia: {opcoes}. Se o lead disser que "
                                    f"não pode em nenhuma das duas, NÃO confirme nem agende o horário ocupado. "
                                    f"Diga que vai validar essa possibilidade com o time e que eles retornam por aqui."
                                )
                            else:
                                extra_disponibilidade = (
                                    f"\n\nATENÇÃO (checagem real de agenda, não mencione isso ao lead): não "
                                    f"achei horário livre nesse dia pra sugerir. NÃO confirme o horário pedido. "
                                    f"Diga que vai validar a possibilidade com o time e que eles retornam por aqui."
                                )
                            print(f"[disponibilidade] Horário {dt_pedido.strftime('%Y-%m-%d %H:%M')} ocupado, "
                                  f"{len(alternativas)} alternativa(s) sugerida(s) conv={conversation_id}", flush=True)
                except Exception as e:
                    print(f"[disponibilidade] Erro ao checar/sugerir horário conv={conversation_id}: {e}", flush=True)

            # Defesa final contra corrida entre threads (13/08): entre o momento
            # em que uma thread foi disparada (checando "última msg é do lead")
            # e o instante desta chamada, outra thread concorrente pode ter
            # respondido primeiro e adicionado um turno "assistant" nesse mesmo
            # histórico compartilhado — sem isso, a conversa termina no turno
            # errado e a Messages API devolve resposta vazia sempre (mesmo
            # sintoma do bug da Millela, 12/08, mas por concorrência entre
            # threads, não por falta de segunda mensagem — caso real: [lead],
            # conv=1753, 13/08, aconteceu 2x na mesma conversa). Roda sempre,
            # não só no caminho de primeira mensagem, porque qualquer thread
            # (retomada, conv_updated, mensagem normal) pode sofrer essa corrida.
            if conv["messages"] and conv["messages"][-1]["role"] == "assistant":
                print(f"[luca-bg] Turno assistant sobrando no final (corrida entre threads) "
                      f"conv={conversation_id} — removido antes de chamar o Claude", flush=True)
                conv["messages"].pop()
            if not conv["messages"] or conv["messages"][-1]["role"] != "user":
                print(f"[luca-bg] Abortado — sem turno 'user' pendente após limpeza conv={conversation_id}", flush=True)
                return

            # Corrigido 20/08: 300 tokens não deixava espaço pro raciocínio
            # estendido do Sonnet 5 + a resposta de verdade — o modelo às vezes
            # gastava tudo só "pensando" e nunca escrevia o texto (bug real,
            # confirmado em produção: Leandro, Anderson, Giovanna e outros
            # ficaram sem resposta nenhuma do Luca por causa disso). Sem custo
            # extra: só paga pelo que realmente gera, max_tokens é só um teto.
            reply = call_claude(conv["messages"], max_tokens=2000,
                                 system=conv["system"] + extra_disponibilidade, tipo="chat")

            # Corrigido 01/09 (achado real do gestor: respostas instantâneas,
            # mesmo as longas, soam como IA — um humano não digita um parágrafo
            # em poucos segundos). O "digitando..." fica ligado até aqui e só
            # desliga mais abaixo, depois do atraso proporcional ao tamanho do
            # texto, logo antes do envio de verdade.

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
            reply = remover_travessao(reply)

            # Atraso proporcional ao tamanho, simulando tempo real de digitação —
            # sem isso, mensagens longas saindo em poucos segundos soam como IA.
            # ~45ms por caractere (~22 caracteres/s, digitação humana rápida no
            # celular), limitado entre 1.5s e 12s pra não parecer trava nem demora
            # exagerada.
            atraso_digitacao = min(max(len(reply) * 0.045, 1.5), 12)
            time.sleep(atraso_digitacao)

            toggle_typing(inbox_identifier, contact_identifier, conversation_id, "off")
            send_agendorchat_message(conversation_id, reply)
            # Marca o início da espera por resposta do lead — usado pelo follow-up de 1h
            conv["luca_aguardando_desde"] = time.time()
            conv["contact_name_cache"] = contact_name

            # ── Nota interna — dados completos ou conversa encerrada ─────────────
            # Gateado: só roda a extração enquanto o ciclo do CRM ainda não foi
            # fechado. Antes rodava em TODA mensagem, mesmo depois de já ter
            # tudo completo e registrado — puro desperdício de chamada à API.
            try:
                # Só para de tentar registrar quando o ciclo do CRM está DE
                # FATO fechado (conv["crm_registrado"], que só vira True
                # quando a nota E a reunião real — se havia preferência —
                # já existem) — nunca por "note_sent" sozinho, que pode ter
                # virado True por um encerramento prematuro/falso-positivo.
                if conv.get("crm_registrado"):
                    d = conv["lead_data"]
                else:
                    lead_data = extract_lead_data(conv["messages"], contact_name)
                    if lead_data:
                        conv["lead_data"].update({k: v for k, v in lead_data.items() if v})
                    d = conv["lead_data"]

                    dados_completos = (
                        d.get("nome") and d.get("nome") != "Não informado"
                        and d.get("segmento") and d.get("segmento") != "Não identificado"
                        and d.get("necessidade") and d.get("necessidade") != "Não informada"
                        and d.get("email") and d.get("email") != "Não informado"
                        # Corrigido em 17/08: exigir só "preferencia" não-vazia
                        # deixava respostas vagas ("Qualquer dia") fecharem o
                        # ciclo cedo demais — como o fechamento só acontece UMA
                        # vez (note_sent), quando o lead dizia o dia/horário
                        # exato minutos depois, o sistema já tinha "carimbado"
                        # a conversa como concluída e NUNCA criava a reunião real
                        # no Teams (caso real: [lead], 17/08 — ficou só com
                        # uma tarefa "HORÁRIO A CONFIRMAR", sem link nenhum).
                        # Usa o filtro barato (parece_ter_horario, regex, sem
                        # Claude) primeiro — só chama parse_preferencia_datetime
                        # (que usa Claude) quando já parece ter chance real de
                        # ser concreto, evitando gastar uma chamada em toda
                        # mensagem enquanto o lead ainda está respondendo vago.
                        and d.get("preferencia")
                        and parece_ter_horario(d.get("preferencia"))
                        and parse_preferencia_datetime(d.get("preferencia")) is not None
                    )

                    # Corrigido 08/09 ([gestor], mesmo caso da Amanda): removido
                    # "acompanhamento" desta lista — é uma palavra comum demais
                    # no nosso domínio (ex: "...cuida de todo o acompanhamento
                    # contábil..."), aparecia no meio de frases sem NENHUMA
                    # relação com o fim da conversa e fechava o ciclo cedo
                    # demais, sem e-mail nem horário real ainda.
                    termos_encerramento = ["sinal verde", "é só me avisar", "estou por aqui"]
                    conversa_encerrada = any(t in reply.lower() for t in termos_encerramento)

                    if not conv.get("note_sent") and (dados_completos or conversa_encerrada) and not conv.get("modo_demo"):
                        d["telefone"] = conv.get("phone") or d.get("telefone", "Não informado")
                        note_text = build_lead_note(d)
                        send_private_note(conversation_id, note_text)
                        conv["note_sent"] = True
                        print(f"[note] Nota enviada conv={conversation_id} | completo={dados_completos} | encerrado={conversa_encerrada}", flush=True)
                    elif (d.get("status") or "").strip().lower().startswith("perdido") \
                         and not conv.get("recusa_registrada") and not conv.get("modo_demo"):
                        # Uma recusa EXPLÍCITA sempre gera uma nota atualizada,
                        # mesmo que uma nota já tenha sido enviada antes por
                        # outro motivo (ex: "note_sent" já True por um
                        # encerramento prematuro/falso-positivo detectado
                        # antes da recusa real acontecer) — sem isso, o
                        # desfecho real do lead nunca fica refletido na nota,
                        # ficando travado no status anterior pra sempre.
                        d["telefone"] = conv.get("phone") or d.get("telefone", "Não informado")
                        note_text = build_lead_note(d)
                        send_private_note(conversation_id, note_text)
                        conv["note_sent"] = True
                        conv["recusa_registrada"] = True
                        print(f"[note] Nota de recusa enviada (atualização) conv={conversation_id}", flush=True)

                    # Tenta fechar o ciclo no CRM a cada mensagem (uma vez
                    # que já tenha e-mail e preferência), não só uma vez —
                    # registrar_no_crm é idempotente (marcadores duráveis) e
                    # sabe sozinho o que já foi feito, então repetir é seguro.
                    if (dados_completos or (conv.get("note_sent") and d.get("email") and d.get("preferencia"))) \
                       and not conv.get("modo_demo"):
                        registrar_no_crm(conv, conversation_id, contact_name)

                        # Se o status extraído indicar perda (ex: "já segui
                        # com outra empresa"), move o negócio pra "Perdido"
                        # genérico de verdade — não basta só escrever isso
                        # na nota, precisa refletir na etapa do funil.
                        status_extraido = (d.get("status") or "").strip().lower()
                        if status_extraido.startswith("perdido"):
                            try:
                                motivo_id = motivo_perda_por_texto(d.get("motivo_perda_codigo"))
                                # Corrigido 30/09 ([gestor]): não basta o status
                                # geral dizer "perdido" — se a IA não conseguiu
                                # identificar um motivo específico com
                                # confiança (motivo_perda_codigo veio vazio),
                                # não marca como perdido AINDA. Uma classificação
                                # sem motivo claro é sinal de que a conclusão de
                                # "perdido" em si pode estar precipitada — melhor
                                # esperar mais uma mensagem que confirme, do que
                                # fechar um negócio sem justificativa nenhuma.
                                if not motivo_id:
                                    print(f"[note] Status indica perda mas sem motivo específico "
                                          f"identificado — não movendo ainda conv={conversation_id}", flush=True)
                                else:
                                    _, deal_perdido = buscar_pessoa_e_negocio(d["telefone"])
                                    if deal_perdido and deal_perdido.get("id"):
                                        if mover_etapa_funil_comercial(deal_perdido["id"], ETAPA_PERDIDO_GENERICO):
                                            print(f"[note] Status indica perda — negócio movido pra "
                                                  f"Perdido genérico deal={deal_perdido['id']}", flush=True)
                                        marcar_negocio_perdido(deal_perdido["id"], motivo_id)
                            except Exception as e:
                                print(f"[note] Erro ao mover negócio perdido conv={conversation_id}: {e}", flush=True)

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

    if not validar_assinatura_webhook(AGENDOR_SIGNING_KEY_WEBHOOK):
        print("[auth] Webhook rejeitado — assinatura ausente ou incorreta", flush=True)
        return jsonify({"error": "não autorizado"}), 401

    try:
        body = request.get_json(force=True) or {}

        # ── Log completo para debug ───────────────────────────────────────────
        event        = body.get("event", "")
        message_type = body.get("message_type", "")
        sender_type  = (body.get("sender") or {}).get("type", "")
        print(f"[webhook] RAW event={event} | message_type={message_type} | sender_type={sender_type}", flush=True)
        print(f"[webhook] RAW payload={json.dumps(body)[:600]}", flush=True)

        # ── Inbox excluído (27/08): Luca nunca interage com este número ──────
        inbox_id_raw = (body.get("conversation") or {}).get("contact_inbox", {}).get("inbox_id")
        try:
            if inbox_id_raw is not None and int(inbox_id_raw) in INBOXES_IGNORADOS:
                print(f"[webhook] IGNORADO — inbox excluído ({inbox_id_raw})", flush=True)
                return jsonify({}), 200
        except (TypeError, ValueError):
            pass

        # ── message_updated: status real de entrega (20/08, novo) ──────────
        # Confirmado com o suporte do Agendor: o payload desse evento NÃO
        # traz o status em si, só avisa "essa mensagem mudou" — é preciso
        # buscar a listagem de mensagens da conversa pra ler o status real.
        # Se vier 'failed' (número inválido/sem WhatsApp), o lead nunca teve
        # chance de responder de verdade — mesmo critério de "sem contato"
        # (decisão de [gestor], 20/08), então move pra 'Perdido - sem contato
        # (D5)', onde o [especialista] confere manualmente.
        if event == "message_updated":
            msg_id = body.get("id")
            conversation = body.get("conversation") or {}
            conversation_id = conversation.get("id")
            if not msg_id or not conversation_id:
                return jsonify({}), 200
            # O Chatwoot dispara 'message_updated' MAIS DE UMA VEZ pra mesma
            # mensagem (ex: uma vez ao marcar como falha, outra ao anexar o
            # motivo) — sem trava, a mesma falha geraria nota duplicada.
            marcador_falha = f"[msg_falha:{msg_id}]"
            msgs = mensagens_da_conversa(conversation_id)
            if marcador_existe(msgs, marcador_falha):
                return jsonify({}), 200
            msg_atual = next((m for m in msgs if m.get("id") == msg_id), None)
            if not msg_atual or msg_atual.get("status") != "failed":
                return jsonify({}), 200
            content_attrs_falha = msg_atual.get("content_attributes") or {}
            motivo = content_attrs_falha.get("external_error", "motivo não informado")
            # Corrigido 08/09 ([gestor]): o erro 131049 ("healthy ecosystem
            # engagement") é o WhatsApp/Meta limitando envio por engajamento
            # (ex: rajada de vários templates de marketing de uma vez só) —
            # não tem nada a ver com número inválido. Antes desse fix, era
            # tratado idêntico a número inválido: mudava a etapa pra "Perdido
            # - sem contato" e deixava uma nota errada dizendo "número
            # inválido/sem WhatsApp" pra um lead com número perfeitamente
            # válido — caso real confirmado em produção em 08/09, vários
            # leads (Beatriz, Diogeles, Hudson, Eduardo, Gabriel) afetados
            # numa única rajada de follow-ups.
            # Ampliado em 09/09 ([gestor]): mesmo problema com "9999: low
            # balance" (categoria BILLING) — a conta do WhatsApp/Gupshup
            # ficou sem saldo pra mandar template, e isso NÃO significa que
            # o número é inválido. Também é uma falha temporária: assim que
            # o saldo voltar, o reenvio (feito pela régua de follow-up, que
            # já espera confirmação antes de avançar) deve funcionar normal.
            eh_falha_temporaria = ("131049" in motivo
                                   or content_attrs_falha.get("error_category") in ("QUALITY_BLOCK", "BILLING"))
            meta_sender = (conversation.get("meta") or {}).get("sender") or {}
            phone = meta_sender.get("phone_number", "")
            print(f"[msg_falhou] Mensagem falhou conv={conversation_id} msg={msg_id} "
                  f"phone={phone} motivo={motivo}", flush=True)
            if phone:
                _, deal = buscar_pessoa_e_negocio(phone)
                if deal:
                    # Só reage à falha se a mensagem foi (a) da saudação
                    # nativa do Agendor (tem automation_id — nem Luca nem
                    # outros serviços preenchem isso ao mandar via API) ou
                    # (b) do próprio follow-up do Luca (marcador
                    # aguardando_confirmacao). Qualquer outra origem (ex:
                    # CCT Automação, que usa o mesmo inbox) é ignorada —
                    # checar só "negócio é do Funil Comercial" não basta,
                    # porque buscar_pessoa_e_negocio() já só retorna negócio
                    # desse funil, então essa checagem sempre dá verdadeiro.
                    eh_saudacao_nativa = bool((msg_atual.get("additional_attributes") or {}).get("automation_id"))
                    veio_do_followup = any(
                        "[followup:aguardando_confirmacao:" in (m.get("content") or "")
                        and f":{msg_id}]" in (m.get("content") or "")
                        for m in msgs
                    )
                    if not eh_saudacao_nativa and not veio_do_followup:
                        print(f"[msg_falhou] Ignorado — mensagem não veio da saudação nativa nem do "
                              f"follow-up do Luca (provavelmente de outro serviço, ex: CCT Automação) "
                              f"conv={conversation_id} msg={msg_id}", flush=True)
                        return jsonify({}), 200
                    if eh_falha_temporaria:
                        # Provavelmente número válido, só uma falha temporária
                        # (limite de engajamento OU saldo insuficiente) — não
                        # move de etapa. A régua de follow-up detecta que essa
                        # tentativa falhou (via status_da_mensagem) e tenta
                        # reenviar sozinha numa próxima varredura, sem
                        # precisar de ação manual.
                        send_private_note(conversation_id,
                            f"⚠️ Mensagem bloqueada pelo WhatsApp (falha temporária, não é número "
                            f"inválido). Motivo: {motivo}. Etapa mantida — o sistema tenta reenviar "
                            f"automaticamente. {marcador_falha}")
                    else:
                        # Corrigido 21/08 ([gestor]): só move pra "sem contato" se
                        # o negócio AINDA não teve nenhum contato de verdade —
                        # senão uma mensagem posterior (ex: lembrete) que falhe
                        # apagaria um progresso real já feito (Contato
                        # Retornado, Reunião agendada, etc.).
                        etapa_atual_id = (deal.get("dealStage") or {}).get("id")
                        etapas_sem_contato_ainda = (ETAPA_NOVO_LEAD, ETAPA_1_CONTATO, ETAPA_2_CONTATO,
                                                     ETAPA_3_CONTATO, ETAPA_4_CONTATO_D5, ETAPA_5_CONTATO_D7,
                                                     ETAPA_PERDIDO_SEM_CONTATO)
                        if etapa_atual_id in etapas_sem_contato_ainda:
                            mover_etapa_funil_comercial(deal["id"], ETAPA_PERDIDO_SEM_CONTATO, permitir_recuo=True)
                            # Diferencia os dois motivos pelo texto do erro: "não é
                            # usuário do WhatsApp" é diferente de "número/dado
                            # errado" — o primeiro significa que o número é do
                            # lead e existe de verdade, só não tem WhatsApp
                            # cadastrado; o segundo é dado incorreto mesmo
                            # (número inexistente, incompleto, de outra pessoa).
                            # Ainda não vimos um caso real de "sem WhatsApp"
                            # genuíno nos logs pra confirmar o texto exato do
                            # erro — se aparecer um caso real que devia ser
                            # "sem WhatsApp" mas caiu como "contato inválido",
                            # me avisa que eu ajusto essa checagem.
                            motivo_lower = motivo.lower()
                            if "not a whatsapp user" in motivo_lower or "não é um usuário" in motivo_lower \
                               or "not registered" in motivo_lower or "sem whatsapp" in motivo_lower:
                                marcar_negocio_perdido(deal["id"], LOSS_REASON_SEM_WHATSAPP)
                            else:
                                marcar_negocio_perdido(deal["id"], LOSS_REASON_CONTATO_INVALIDO)
                            send_private_note(conversation_id,
                                f"⚠️ Mensagem não entregue (número inválido/sem WhatsApp). Motivo: {motivo}. "
                                f"Movido para 'Perdido - sem retorno (D10)' para verificação manual. {marcador_falha}")
                        else:
                            send_private_note(conversation_id,
                                f"⚠️ Mensagem não entregue (número inválido/sem WhatsApp). Motivo: {motivo}. "
                                f"Negócio já teve contato real antes, etapa mantida — verificar manualmente. {marcador_falha}")
            return jsonify({}), 200


        # Ignora tudo que não seja mensagem nova do lead
        if event != "message_created":
            print(f"[webhook] IGNORADO event={event}", flush=True)
            return jsonify({}), 200
        if message_type != "incoming":
            # Caso real (17/08, [lead]): a automação nativa que cria o
            # negócio + manda a saudação às vezes atrasa bastante (o
            # negócio demora pra ser criado no Agendor), e a saudação
            # chega no meio de uma conversa já avançada com o Luca —
            # confuso pro lead, parece que ninguém prestou atenção nele.
            # Não dá pra IMPEDIR o envio (é o Agendor quem manda, direto,
            # sem passar pelo nosso código) — mas dá pra detectar e já
            # emendar um esclarecimento rápido.
            automation_id = (body.get("additional_attributes") or {}).get("automation_id")
            if message_type == "outgoing" and automation_id:
                conv_id_saudacao = (body.get("conversation") or {}).get("id")
                if conv_id_saudacao:
                    try:
                        msgs_previas = mensagens_da_conversa(conv_id_saudacao)
                        # Recência (não contagem) é o sinal certo: uma conversa
                        # pode ter várias mensagens só que antigas — o que
                        # importa é se o lead está falando AGORA, no meio da
                        # correria, quando a saudação atrasada chega (caso
                        # real: [lead], 17/08, mandando mensagem a cada 1-2
                        # min quando a saudação chegou 9min depois do início).
                        ultima_incoming = None
                        for m in msgs_previas:
                            if m.get("message_type") == 0 and not m.get("private"):
                                ultima_incoming = m
                        conversa_ativa_agora = False
                        if ultima_incoming:
                            criada = ultima_incoming.get("created_at") or 0
                            conversa_ativa_agora = (time.time() - float(criada)) < 600  # 10 min

                        # Precisa saber se o Luca já respondeu ALGUMA coisa
                        # de verdade antes de decidir o texto. Se a saudação
                        # atrasada cai na PRIMEIRA mensagem do lead (ainda
                        # dentro do delay antes do Luca responder pela 1ª
                        # vez), dizer "continue com sua última pergunta" não
                        # faz sentido — usa um texto que só confirma o
                        # recebimento, sem sugerir que já tinha algo rolando.
                        #
                        # A fonte mais confiável pra saber isso é o próprio
                        # histórico em memória do Luca (conv["messages"]) —
                        # só tem uma entrada "assistant" quando ele de fato
                        # gerou e mandou uma resposta de verdade, sem risco
                        # de confundir com a saudação automática ou mensagem
                        # de terceiros (diferente de filtrar pela API remota,
                        # que provou não ser confiável).
                        conv_saudacao = conversation_histories.get(str(conv_id_saudacao))
                        ja_teve_resposta_real_do_luca = bool(
                            conv_saudacao and any(m.get("role") == "assistant"
                                                   for m in conv_saudacao.get("messages", []))
                        )

                        if conversa_ativa_agora and ja_teve_resposta_real_do_luca:
                            send_agendorchat_message(conv_id_saudacao, (
                                "Oi de novo! Essa mensagem acima foi automática (nosso "
                                "sistema atrasou pra registrar seu contato) — já estamos "
                                "conversando, pode ignorar. Segue com sua última pergunta "
                                "que eu te ajudo!"))
                            print(f"[webhook] Saudação atrasada (meio de conversa), esclarecimento "
                                  f"enviado conv={conv_id_saudacao}", flush=True)
                        elif conversa_ativa_agora:
                            send_agendorchat_message(conv_id_saudacao, (
                                "Oi de novo! A mensagem acima foi automática — recebi sua "
                                "pergunta certinho, só um instante que já te respondo!"))
                            print(f"[webhook] Saudação atrasada (1º contato, sem resposta real "
                                  f"ainda), esclarecimento enviado conv={conv_id_saudacao}", flush=True)
                    except Exception as e:
                        print(f"[webhook] Erro ao checar saudação atrasada conv={conv_id_saudacao}: {e}", flush=True)

            # ── Corrigido 25/08 ([gestor]): mensagem humana genuína também
            # merece um follow-up de silêncio, só que com uma janela maior
            # (4h, só dentro do horário comercial) — diferente da mensagem
            # do Luca (1h, texto mais leve). Mesmo esquema da marca
            # invisível usada em humano_realmente_respondeu.
            if message_type == "outgoing":
                sender_out = body.get("sender") or {}
                content_out = body.get("content") or ""
                automation_id_out = (body.get("additional_attributes") or {}).get("automation_id")
                template_params_out = (body.get("additional_attributes") or {}).get("template_params")
                nome_sender_out = (sender_out.get("name") or "").strip().lower()
                eh_conta_do_luca = nome_sender_out == LUCA_BOT_NOME_REAL.strip().lower()
                eh_mensagem_do_bot = eh_conta_do_luca and LUCA_MARKER in content_out
                eh_generico_ou_automatico = (sender_out.get("type") != "user" or automation_id_out
                                              or template_params_out or eh_assignee_bot(sender_out))
                # content vazio/null geralmente é reação de emoji ou
                # atualização de status, não mensagem de verdade — sem esse
                # guard, string vazia tendia a ligar o timer de 4h à toa.
                if not eh_mensagem_do_bot and not eh_generico_ou_automatico and content_out.strip():
                    conv_id_humano = (body.get("conversation") or {}).get("id")
                    if conv_id_humano and mensagem_precisa_resposta(content_out):
                        conv_key_humano = str(conv_id_humano)
                        conv_humano = conversation_histories.setdefault(conv_key_humano, {
                            "system": SYSTEM_PROMPT, "messages": [], "note_id": None, "lead_data": {},
                        })
                        conv_humano["humano_aguardando_desde"] = time.time()
                        conv_humano["followup_humano_enviado"] = False
                        print(f"[webhook] Mensagem humana genuína e precisa de resposta, iniciando "
                              f"espera de 4h conv={conv_id_humano}", flush=True)
                    elif conv_id_humano:
                        print(f"[webhook] Mensagem humana genuína mas não precisa de resposta, "
                              f"sem follow-up conv={conv_id_humano}", flush=True)

            print(f"[webhook] IGNORADO message_type={message_type}", flush=True)
            return jsonify({}), 200

        # ── Ignora se há agente humano atribuído à conversa ───────────────────
        # Exceção: o usuário do bot da automação (LUCA_BOT_ASSIGNEE) é território
        # do Luca — conversas atribuídas a ele são respondidas normalmente.
        # IMPORTANTE: não confiar no "retrato" de conversation.meta.assignee
        # embutido no payload do webhook — ele pode estar desatualizado em
        # relação a uma auto-atribuição muito recente (ex: consultor se
        # atribui e responde, lead manda mensagem seguinte rápido demais, e
        # o snapshot do webhook ainda reflete o estado de antes). Por isso,
        # busca o assignee de verdade, na hora, via API.
        conversation_id = (body.get("conversation") or {}).get("id")  # precisa vir antes do check abaixo
        detalhe_fresco = get_conversation_details(conversation_id) if conversation_id else {}
        if detalhe_fresco:
            assignee = (detalhe_fresco.get("meta") or {}).get("assignee")
        else:
            # Fail-safe: a chamada fresca falhou (timeout/instabilidade). Em vez
            # de assumir "sem ninguém atribuído" (o que poderia atropelar um
            # atendimento humano real), cai de volta no retrato embutido no
            # próprio payload do webhook — pior que uma checagem fresca, mas
            # nunca pior que o comportamento antigo.
            assignee = (body.get("conversation") or {}).get("meta", {}).get("assignee")
            print(f"[webhook] get_conversation_details falhou, usando snapshot do payload conv={conversation_id}", flush=True)
        # O que importa é só quando foi a última mensagem humana genuína,
        # não quem está no campo "assignee" — dá 1h de folga pro humano
        # continuar sem o Luca atropelar; depois disso, retoma normalmente.
        segundos_humano = segundos_desde_ultima_intervencao_humana(conversation_id)
        if segundos_humano is not None and segundos_humano < 3600:
            print(f"[webhook] IGNORADO — humano escreveu há {int(segundos_humano)}s, "
                  f"dentro da janela de 1h conv={conversation_id}", flush=True)
            # Mesmo ficando quieto aqui, garante que a conversa fica "viva"
            # (was_resolved=False) pra rede de segurança conseguir retomar
            # assim que passar a janela de 1h — sem isso, uma conversa
            # marcada como resolvida antes fica pulada indefinidamente.
            conv_key_humano = str(conversation_id)
            conv_humano = conversation_histories.get(conv_key_humano)
            if conv_humano:
                conv_humano["was_resolved"] = False
                conv_humano["last_msg_at"] = time.time()
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
            # Conversa nova de verdade nesta memória do processo — não há
            # "conv" anterior pra preservar (diferente do reset por conversa
            # reaberta, onde já existe um "conv" anterior com was_resolved).
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

        # Gatilho de demonstração (02/09): marca a conversa pra nunca virar
        # negócio no CRM nem gerar follow-up — conversa acontece 100% normal
        # pro lado do lead, só os efeitos colaterais no Agendor são pulados.
        if GATILHO_DEMO in message_text.lower():
            conv["modo_demo"] = True
            print(f"[demo] Gatilho detectado, conv={conversation_id} nunca vira negócio real", flush=True)

        if conv.get("modo_demo"):
            # Nunca dispara o aviso de "retorno por silêncio" pra uma conversa demo
            pass
        elif contact_phone:
            # Se o negócio já tinha escalado por silêncio (2°/3° Contato),
            # o lead respondendo agora é um "retorno" — sinaliza pro time.
            # Em thread separada, não atrasa a resposta do Luca.
            threading.Thread(
                target=mover_para_contato_retornado_se_aplicavel,
                args=(contact_phone,),
                daemon=True
            ).start()

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
            # IMPORTANTE: não injetar uma mensagem "assistant" como primeira do
            # array — a API da Anthropic exige que a primeira mensagem seja
            # sempre "user", e um array começando com "assistant" causava
            # respostas vazias (stop_reason=end_turn, content=[]). A instrução
            # de dar boas-vindas de volta vai só no system prompt.
            extra += (" Esta conversa foi reaberta após um atendimento anterior "
                      "ter sido encerrado — cumprimente o lead calorosamente, "
                      "como quem dá boas-vindas de volta, antes de seguir normalmente.")
            # Checa o estado REAL da reunião no CRM (não confia só no que está
            # escrito no histórico de mensagens, que pode estar desatualizado
            # se muito tempo passou). Barato: sem chamada ao Claude.
            if contact_phone:
                status_real = status_reuniao_real(contact_phone)
                if status_real:
                    extra += f"\n\nSTATUS REAL DA REUNIÃO (verificado agora no CRM): {status_real}"
            # Preserva o que já sabíamos sobre o lead (segmento, necessidade,
            # e-mail etc.) em vez de apagar tudo — evita reperguntar o básico
            # pra quem já respondeu antes, dentro da mesma sessão do processo.
            lead_data_anterior = dict(conv.get("lead_data") or {})
            lead_data_anterior["nome"] = contact_name or lead_data_anterior.get("nome")
            conversation_histories[conv_key] = {
                "system":    SYSTEM_PROMPT + extra,
                "messages":  [],
                "note_id":   None,
                "lead_data": lead_data_anterior,
                "last_msg_at": time.time(),
                "was_resolved": False,
                # Preserva se a reunião já foi agendada/registrada no CRM —
                # sem isso, o follow-up de 1h achava que ainda havia algo
                # pendente mesmo depois de um agendamento já confirmado,
                # só porque a conversa reabriu de novo (ex: lead tirando uma
                # dúvida rápida depois de já ter marcado).
                "crm_registrado": conv.get("crm_registrado", False),
                "note_sent": conv.get("note_sent", False),
                # Preserva a contagem para não tratar a reabertura como
                # "primeira mensagem" (evita o delay de 90s e o refetch de
                # histórico remoto, que desfariam o reset).
                "message_count": conv.get("message_count", 1),
                # Evita que o bloco abaixo ("se memória vazia, busca histórico
                # remoto") traga de volta o histórico antigo — o reset é
                # intencional, o array vazio aqui é o estado desejado.
                "skip_remote_fetch": True,
            }
            conv = conversation_histories[conv_key]
            if contact_phone:
                conv["phone"] = contact_phone

        # ── Se memória está vazia, busca histórico real do AgendorChat ────────
        if not conv["messages"] and not conv.get("skip_remote_fetch"):
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
        # Lead respondeu — cancela o follow-up de 1h de silêncio, se estava contando
        conv["luca_aguardando_desde"] = None
        conv["followup_1h_enviado"] = False
        # Idem pro follow-up de 4h de silêncio após mensagem humana (25/08)
        conv["humano_aguardando_desde"] = None
        conv["followup_humano_enviado"] = False

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

def tentar_retomar_conversa(conversation_id: int, origem: str = "conv_updated") -> bool:
    """Função compartilhada (25/08): verifica se há mensagem do lead
    pendente sem resposta e, se sim, faz o Luca assumir e responder — a
    mesma lógica que já existia dentro do webhook conversation_updated,
    agora extraída pra também poder ser chamada pela verificação periódica
    de segurança (o evento do AgendorChat não é garantido disparar bem na
    marca de 1h após a intervenção humana). Retorna True se disparou a
    retomada, False se não havia nada a fazer."""
    try:
        conv_key = str(conversation_id)
        last = get_last_message_info(conversation_id)
        if not last:
            print(f"[retomada:{origem}] Sem mensagens na conversa conv={conversation_id}", flush=True)
            return False
        if last.get("private") or last.get("message_type") != 0:
            print(f"[retomada:{origem}] Última mensagem não é do lead conv={conversation_id}", flush=True)
            return False

        conv = conversation_histories.get(conv_key)
        last_id = last.get("id")
        if conv and last_id and conv.get("last_responded_msg_id") == last_id:
            print(f"[retomada:{origem}] Última mensagem já respondida pelo Luca conv={conversation_id}", flush=True)
            return False
        # Guard contra disparos duplicados (webhook + timer batendo perto):
        # se já existe uma retomada agendada/em andamento pra essa mesma
        # mensagem, ignora.
        if conv and last_id and conv.get("retomada_msg_id") == last_id:
            print(f"[retomada:{origem}] Já em andamento pra msg={last_id} conv={conversation_id}", flush=True)
            return False

        detalhe = get_conversation_details(conversation_id) or {}
        meta = detalhe.get("meta") or {}

        # Antes de assumir, confere quem está atribuído NO MOMENTO — se for
        # um humano de verdade (não o próprio Luca nem o dono padrão do
        # CRM), não assume por cima da atribuição humana.
        assignee_atual = meta.get("assignee")
        if assignee_atual and not eh_assignee_bot(assignee_atual):
            print(f"[retomada:{origem}] IGNORADO — conversa atribuída a humano "
                  f"({assignee_atual.get('name')}) conv={conversation_id}", flush=True)
            return False

        sender_info = meta.get("sender") or {}
        contact_name = sender_info.get("name", "")
        contact_phone = sender_info.get("phone_number", "")
        contact_inbox = detalhe.get("contact_inbox") or {}
        inbox_identifier = contact_inbox.get("source_id", "")
        contact_identifier = contact_inbox.get("pubsub_token", "")

        # Rede de segurança (27/08): confirma de novo com dado fresco, caso o
        # payload do webhook que chamou esta função não tivesse o inbox_id.
        inbox_id_fresco = contact_inbox.get("inbox_id") or detalhe.get("inbox_id")
        try:
            if inbox_id_fresco is not None and int(inbox_id_fresco) in INBOXES_IGNORADOS:
                print(f"[retomada:{origem}] IGNORADO — inbox excluído ({inbox_id_fresco}) conv={conversation_id}", flush=True)
                return False
        except (TypeError, ValueError):
            pass

        if conv_key not in conversation_histories:
            # Não existe entrada anterior nesta memória do processo — não há
            # nada de "conv" pra preservar aqui (diferente do reset por
            # conversa reaberta, onde já existe um "conv" anterior).
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
            print(f"[retomada:{origem}] Histórico sem mensagem pendente do lead conv={conversation_id}", flush=True)
            return False

        conv["last_msg_at"] = time.time()
        conv["message_count"] = max(conv.get("message_count", 0), 1)  # não é primeira mensagem
        conv["was_resolved"] = False  # histórico já sincronizado; evita reset indevido depois
        conv["retomada_msg_id"] = last_id  # marca antes de disparar — bloqueia eventos duplicados
        msg_token = time.time()
        conv["latest_msg_token"] = msg_token

        print(f"[retomada:{origem}] Luca assume conv={conversation_id}", flush=True)
        threading.Thread(
            target=_processar_resposta_luca,
            args=(conv_key, conversation_id, msg_token, last_id, False,
                  False, last.get("content", ""), contact_name,
                  inbox_identifier, contact_identifier, 2.5),
            daemon=True,
        ).start()
        return True
    except Exception as e:
        print(f"[retomada:{origem}] Erro conv={conversation_id}: {e}", flush=True)
        return False


def verificar_retomada_apos_silencio_humano():
    """Criado 25/08 ([gestor]): rede de segurança pro caso do evento
    conversation_updated do AgendorChat não disparar bem na marca de 1h
    depois de uma intervenção humana com mensagem do lead pendente. Varre
    (a cada 15 min) as conversas ativas na memória, e para cada uma que já
    passou 1h desde a última mensagem humana genuína, tenta retomar.
    Limitado a conversas com atividade nas últimas 24h, pra não varrer
    histórico morto pra sempre."""
    agora = time.time()
    for conv_key in list(conversation_histories.keys()):
        try:
            conversation_id = int(conv_key)
        except (TypeError, ValueError):
            continue
        conv = conversation_histories.get(conv_key)
        if not conv or conv.get("was_resolved") or conv.get("modo_demo"):
            continue
        last_msg_at = conv.get("last_msg_at") or 0
        if agora - last_msg_at > 86400:  # sem atividade há mais de 24h — ignora
            continue
        segundos_humano = segundos_desde_ultima_intervencao_humana(conversation_id)
        if segundos_humano is None or segundos_humano < 3600:
            continue  # nunca teve humano, ou ainda dentro da janela de 1h
        tentar_retomar_conversa(conversation_id, origem="timer_1h")


def verificar_retomada_apos_silencio_humano_safe():
    try:
        verificar_retomada_apos_silencio_humano()
    except Exception as e:
        print(f"[retomada:timer_1h] Erro geral na varredura: {e}", flush=True)


LEADS_PARADOS_MINUTOS = int(os.environ.get("LEADS_PARADOS_MINUTOS", "30"))


def verificar_leads_parados():
    """Rede de segurança adicional (06/09, [gestor]): detecta conversas com
    mensagem do lead pendente de resposta há mais de LEADS_PARADOS_MINUTOS
    minutos (padrão 30).

    Diferença importante em relação a verificar_retomada_apos_silencio_
    humano: aquela depende de segundos_desde_ultima_intervencao_humana,
    que por sua vez depende de reconhecer corretamente quem é "território
    do Luca" (eh_assignee_bot) — se uma automação nativa nova ou mal
    configurada disparar uma mensagem se passando por um humano não
    reconhecido, o Luca podia ficar calado indefinidamente (caso real:
    boas_vindas_primeiro_contato atribuindo a conversa a um funcionário
    real via Sistema de Automação, sem reconhecimento configurado —
    05-06/09). Esta varredura NÃO depende dessa checagem: usa
    tentar_retomar_conversa, que confia só em "a última mensagem real é do
    lead e ainda não foi respondida", não importa a causa do silêncio.
    Roda mais frequente (a cada 15 min) com uma janela bem mais curta (30
    min por padrão), como último recurso — mesmo limite de 24h das outras
    varreduras pra não reprocessar histórico morto."""
    agora = time.time()
    for conv_key in list(conversation_histories.keys()):
        try:
            conversation_id = int(conv_key)
        except (TypeError, ValueError):
            continue
        conv = conversation_histories.get(conv_key)
        if not conv or conv.get("modo_demo"):
            continue
        last_msg_at = conv.get("last_msg_at") or 0
        if last_msg_at <= 0:
            continue
        parado_ha = agora - last_msg_at
        if parado_ha < LEADS_PARADOS_MINUTOS * 60 or parado_ha > 86400:
            continue
        if tentar_retomar_conversa(conversation_id, origem="leads_parados"):
            print(f"[leads_parados] Lead estava parado há {int(parado_ha/60)}min, "
                  f"Luca retomou conv={conversation_id}", flush=True)


def verificar_leads_parados_safe():
    try:
        verificar_leads_parados()
    except Exception as e:
        print(f"[leads_parados] Erro geral na varredura: {e}", flush=True)


@app.route("/agendorchat/conversation-updated", methods=["POST", "OPTIONS"])
def agendorchat_conversation_updated():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 200

    if not validar_assinatura_webhook(AGENDOR_SIGNING_KEY_CONV_UPDATED):
        print("[auth] Webhook (conversation-updated) rejeitado — assinatura ausente ou incorreta", flush=True)
        return jsonify({"error": "não autorizado"}), 401

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

        # ── Inbox excluído (27/08): mesma exclusão do webhook principal ──────
        inbox_id_raw = (conversation.get("contact_inbox") or {}).get("inbox_id") or conversation.get("inbox_id")
        try:
            if inbox_id_raw is not None and int(inbox_id_raw) in INBOXES_IGNORADOS:
                print(f"[conv_updated] IGNORADO — inbox excluído ({inbox_id_raw})", flush=True)
                return jsonify({}), 200
        except (TypeError, ValueError):
            pass

        print(f"[conv_updated] conv={conversation_id} | status={status} | assignee={assignee}", flush=True)

        # Se foi resolvida, marca no histórico para detectar reabertura depois
        if status == "resolved":
            conv_key = str(conversation_id)
            if conv_key in conversation_histories:
                conversation_histories[conv_key]["was_resolved"] = True
            print(f"[conv_updated] IGNORADO — conversa resolvida", flush=True)
            return jsonify({}), 200

        # Corrigido 25/08 ([gestor]): mesma lógica do webhook principal —
        # não depende de assignee, só de quando foi a última mensagem
        # humana genuína. 1h de folga antes do Luca retomar.
        segundos_humano = segundos_desde_ultima_intervencao_humana(conversation_id)
        if segundos_humano is not None and segundos_humano < 3600:
            print(f"[conv_updated] IGNORADO — humano escreveu há {int(segundos_humano)}s, "
                  f"dentro da janela de 1h conv={conversation_id}", flush=True)
            # Mesma correção do webhook principal (01/09, caso [lead]) —
            # garante que a conversa não fica invisível pra rede de
            # segurança depois que a janela de 1h passar.
            conv_key_humano = str(conversation_id)
            conv_humano = conversation_histories.get(conv_key_humano)
            if conv_humano:
                conv_humano["was_resolved"] = False
                conv_humano["last_msg_at"] = time.time()
            return jsonify({}), 200

        # ── Desatribuída e aberta: verifica se há mensagem do lead sem resposta ─
        # Cenário: humano se atribui, conclui/abandona, conversa é desatribuída
        # com o lead pendente. O Luca assume e responde.
        if tentar_retomar_conversa(conversation_id, origem="conv_updated"):
            return jsonify({"status": "retomada"}), 200
        return jsonify({}), 200

    except Exception as e:
        print(f"[conv_updated] Erro: {e}", flush=True)
        return jsonify({"status": "error", "detail": str(e)}), 200


@app.route("/agendar", methods=["POST", "OPTIONS"])
def agendar():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
        return resp, 200

    if not validar_agendar_api_key():
        print("[auth] /agendar rejeitado — X-API-Key ausente ou incorreta", flush=True)
        return jsonify({"error": "não autorizado"}), 401

    try:
        body          = request.get_json(force=True) or {}
        lead_name     = body.get("lead_name", "Lead")
        lead_email    = body.get("lead_email", "")
        start         = body.get("start", "")
        linha_negocio = body.get("linha_negocio", "contabilidade")

        if not lead_email or not start:
            return jsonify({"error": "lead_email e start são obrigatórios"}), 400

        # A rota manual também passa pela mesma trava final anti-conflito.
        # Assim não existe um segundo caminho capaz de criar reunião por cima
        # de um evento já existente na agenda do organizador.
        dt_start = datetime.strptime(start[:19], "%Y-%m-%dT%H:%M:%S")
        with _TEAMS_AGENDAMENTO_LOCK:
            try:
                eventos = buscar_eventos_do_dia_organizador(dt_start)
            except Exception as e:
                print(f"[agendar] Não foi possível validar agenda: {e}", flush=True)
                return jsonify({
                    "error": "Não foi possível validar a disponibilidade. Validar com o time antes de agendar."
                }), 503
            fim_start = dt_start + timedelta(minutes=30)
            if any(dt_start < ev_fim and fim_start > ev_ini for ev_ini, ev_fim in eventos):
                return jsonify({
                    "error": "Horário ocupado. Validar outra disponibilidade com o time."
                }), 409
            result = create_teams_meeting(lead_name, lead_email, start, linha_negocio)
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

AGENDORCHAT_INBOX_ID = os.environ.get("AGENDORCHAT_INBOX_ID", "")  # CONFIGURE: ID do inbox comercial

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
    """Dispara um template aprovado numa conversa (funciona fora da janela).

    Corrigido em 13/08: faltava o campo 'namespace' no payload — todo
    envio automático de template que já vimos funcionar de verdade nos
    logs sempre trazia esse campo junto (ex: boas_vindas_primeiro_contato,
    automation_id=41882). Sem ele, a API do AgendorChat pode aceitar a
    requisição (retorna 200), mas o WhatsApp/Gupshup rejeita a mensagem
    por trás — caso real: [lead], D5, apareceu com alerta de erro
    no histórico do CRM mesmo com nosso log mostrando sucesso."""
    payload = {
        "content": preview,
        "template_params": {
            "name": tpl.get("name"),
            "category": tpl.get("category"),
            "language": tpl.get("language") or "pt_BR",
            "namespace": tpl.get("namespace"),
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
    """Busca o telefone/WhatsApp de uma pessoa no Agendor (com cache).

    Um 429 (limite de taxa) NUNCA guarda "" no cache — só um erro
    passageiro de rede não deve virar marca permanente de "sem telefone".
    Só guarda "" no cache quando a API respondeu de verdade (sem 429) e
    genuinamente não tinha telefone nenhum.

    O cache também nunca guarda "sem telefone" de forma permanente: só
    guarda em cache quando ENCONTRA um telefone de verdade (isso
    raramente muda). Quando não encontra, não guarda nada — reconfere na
    próxima varredura, então assim que o dado for completado no CRM, a
    próxima rodada já pega.

    Os campos verificados são "whatsapp", "mobile" e "work" — o nome real
    que a API do Agendor usa pro telefone comercial é "work" (não
    "workPhone" nem "phone", que nunca existiram na resposta real).

    Backoff entre tentativas: 5s, dobrando a cada nova tentativa — dá
    tempo do limite de taxa do Agendor liberar de novo."""
    if person_id in _phone_cache:
        return _phone_cache[person_id]
    espera = 5
    for attempt in range(3):
        try:
            r = requests.get(f"{AGENDOR_BASE}/people/{person_id}", headers=HEADERS, timeout=15)
            if r.status_code == 429:
                raise requests.exceptions.HTTPError(f"429 buscando telefone person={person_id}")
            r.raise_for_status()
            data = r.json().get("data", {}) or {}
            contato = data.get("contact") or {}
            for campo in ("whatsapp", "mobile", "work"):
                valor = (contato.get(campo) or "").strip()
                if not valor:
                    continue
                # "whatsapp" é sempre celular (nunca fixo) — sempre tem 9
                # dígitos locais (11 com DDD, 13 com +55). Se vier mais
                # curto, é dado ainda incompleto: não confia nem cacheia,
                # deixa pra reconferir na próxima varredura.
                digitos = "".join(c for c in valor if c.isdigit())
                if campo == "whatsapp" and len(digitos) not in (11, 13):
                    print(f"[lembrete] WhatsApp incompleto ({valor}, {len(digitos)} dígitos) "
                          f"person={person_id} — ignorando por ora, não cacheia", flush=True)
                    continue
                _phone_cache[person_id] = valor
                return valor
            return ""  # resposta válida, mas sem telefone preenchido AINDA — não guarda, reconfere depois
        except Exception as e:
            print(f"[lembrete] Tentativa {attempt+1}/3 falhou (telefone) person={person_id}: {e}", flush=True)
            if attempt < 2:
                time.sleep(espera)
                espera *= 2
    return ""  # esgotou tentativas — NÃO guarda no cache, tenta de novo na próxima rodada


def conversa_do_telefone(phone):
    """Localiza a conversa mais recente do lead na inbox da API oficial.

    Corrigido 20/08: sem proteção contra falha temporária (429/rede), um
    erro passageiro fazia o código concluir 'conversa não encontrada' —
    confirmado em produção, vários follow-ups e lembretes pulados à toa
    por causa disso."""
    for attempt in range(3):
        try:
            digits = "".join(c for c in phone if c.isdigit())
            contatos = []
            for q in (phone, "+" + digits, digits):
                r = requests.get(
                    f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/contacts/search",
                    headers={"api_access_token": AGENDORCHAT_TOKEN},
                    params={"q": q}, timeout=15)
                if r.status_code == 429:
                    raise requests.exceptions.HTTPError(f"429 buscando contato q={q}")
                r.raise_for_status()
                contatos = r.json().get("payload", [])
                if contatos:
                    break
            if not contatos:
                return None
            contact_id = contatos[0].get("id")
            r2 = requests.get(
                f"{AGENDORCHAT_BASE}/accounts/{AGENDORCHAT_ACCOUNT_ID}/contacts/{contact_id}/conversations",
                headers={"api_access_token": AGENDORCHAT_TOKEN}, timeout=15)
            if r2.status_code == 429:
                raise requests.exceptions.HTTPError(f"429 buscando conversas contact={contact_id}")
            r2.raise_for_status()
            convs = r2.json().get("payload", [])
            convs = [c for c in convs if str(c.get("inbox_id")) == str(AGENDORCHAT_INBOX_ID)]
            if not convs:
                return None
            return sorted(convs, key=lambda c: c.get("id") or 0)[-1]
        except Exception as e:
            print(f"[lembrete] Tentativa {attempt+1}/3 falhou ao localizar conversa "
                  f"de {phone}: {e}", flush=True)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))  # 2s, 4s — corrigido 27/08, era fixo em 3s
    return None


def deal_tem_marca(deal_id, marcador: str) -> bool:
    """Verifica se já existe uma tarefa no negócio com essa marca — usado pra
    tornar a nota e a transcrição idempotentes de verdade (sobrevive a
    restart do container e a dois containers rodando em paralelo durante um
    deploy, diferente das flags note_sent/crm_registrado, que são só RAM)."""
    try:
        date_gt = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        r = requests.get(f"{AGENDOR_BASE}/deals/{deal_id}/tasks", headers=HEADERS,
                          params={"updatedDateGt": date_gt, "per_page": 100}, timeout=15)
        tasks = r.json().get("data", [])
        return any(marcador in (t.get("text") or "") for t in tasks)
    except Exception as e:
        print(f"[crm] Erro ao checar marca no negócio {deal_id}: {e}", flush=True)
        return False  # fail-open: em erro na checagem, permite criar (não trava o fluxo)


def humano_realmente_respondeu(conversation_id: int) -> bool:
    """True se existe, em qualquer ponto da conversa, uma mensagem de saída
    escrita por um usuário humano de verdade (sender.type == 'user'), não
    pelo Bot/automação. Usado pra distinguir 'atribuído mas nunca escreveu'
    (ex: automação nativa atribuindo a um humano no instante da criação da
    conversa, sem ele ter agido) de 'humano realmente assumiu e está
    atendendo'.

    Olha a CONVERSA INTEIRA, não só o trecho após a última mensagem do
    lead — um humano pode ter escrito antes da última resposta do lead e
    ainda não ter respondido de novo; mesmo assim ele está no comando.

    Como o Luca manda mensagem pela MESMA conta que um humano usaria
    (sem licença separada), a distinção usa três critérios em conjunto:
    1. Mensagem da conta do Luca COM a marca invisível (LUCA_MARKER) é o
       próprio Luca (exclui). Sem a marca, na mesma conta, é humano
       digitando de verdade (conta como humano).
    2. Mensagem com template_params (os templates de follow-up/lembrete
       são enviados via API, nunca levam LUCA_MARKER — não dá pra marcar
       um template aprovado pela Meta) sempre conta como o próprio bot,
       nunca como humano, mesmo sem a marca.
    3. Mensagem sem conteúdo de texto nenhum (ex: evento de "concluiu o
       atendimento", que gera um registro vazio na conversa) nunca conta
       como intervenção humana — não tem o que ter sido "escrito" ali."""
    try:
        msgs = mensagens_da_conversa(conversation_id)
        dialogo = [m for m in msgs if m.get("message_type") in (0, 1, 3) and not m.get("private")
                   and (m.get("content") or "").strip()
                   and not (m.get("additional_attributes") or {}).get("automation_id")
                   and "Em breve um de nossos consultores dará andamento" not in (m.get("content") or "")]
        for m in dialogo:
            if m.get("message_type") == 1:
                sender = m.get("sender") or {}
                if sender.get("type") != "user":
                    continue
                nome_sender = (sender.get("name") or "").strip().lower()
                eh_conta_do_luca = nome_sender == LUCA_BOT_NOME_REAL.strip().lower()
                if eh_conta_do_luca:
                    tem_marca = LUCA_MARKER in (m.get("content") or "")
                    eh_template = bool((m.get("additional_attributes") or {}).get("template_params"))
                    eh_template_conhecido = eh_conteudo_de_template_luca(m.get("content"))
                    if not tem_marca and not eh_template and not eh_template_conhecido:
                        return True  # [gestor] escreveu manualmente por essa conta
                    continue
                if not eh_assignee_bot(sender):
                    return True
        return False
    except Exception as e:
        print(f"[handoff] Erro ao checar se humano respondeu conv={conversation_id}: {e}", flush=True)
        return True  # fail-safe: em erro, assume que respondeu (nunca atropela atendimento humano)


def segundos_desde_ultima_intervencao_humana(conversation_id: int):
    """Retorna quantos segundos faziam desde a ÚLTIMA mensagem humana genuína
    (mesmo esquema de detecção de humano_realmente_respondeu), ou None se
    nenhum humano jamais escreveu nessa conversa.

    Criado 25/08 ([gestor]): antes, uma vez que um humano escrevia, o Luca
    se calava PRA SEMPRE naquela conversa (bug de fundo: a checagem também
    só rodava se tivesse alguém EXPLICITAMENTE atribuído — se a conversa
    estivesse sem atribuição, o Luca podia responder na hora mesmo com
    intervenção humana recente, e se estivesse atribuída, ficava calado
    sem limite de tempo). Agora dá pra dar ao humano uma janela de espera
    (1h) antes do Luca retomar — e a checagem não depende mais de haver
    alguém atribuído, só de quando foi a última mensagem humana de
    verdade, seja quem for.

    Corrigido em 27/08: mesmo bug crítico do template sem marca (ver nota
    em humano_realmente_respondeu) — sem essa correção, todo envio de
    D1/D3/D5/D7/D10 ou lembrete de reunião via template reiniciava esse
    relógio pra 'zero segundos', fazendo o Luca achar que um humano
    tinha acabado de escrever.

    Corrigido em 01/09: mesmo bug do conteúdo vazio (ver nota em
    humano_realmente_respondeu, caso [lead]) — evento de resolução de
    atendimento sem texto nenhum não conta como intervenção humana."""
    try:
        msgs = mensagens_da_conversa(conversation_id)
        dialogo = [m for m in msgs if m.get("message_type") in (0, 1, 3) and not m.get("private")
                   and (m.get("content") or "").strip()
                   and not (m.get("additional_attributes") or {}).get("automation_id")
                   and "Em breve um de nossos consultores dará andamento" not in (m.get("content") or "")]
        ultima_humana_em = None
        for m in dialogo:
            if m.get("message_type") == 1:
                sender = m.get("sender") or {}
                if sender.get("type") != "user":
                    continue
                nome_sender = (sender.get("name") or "").strip().lower()
                eh_conta_do_luca = nome_sender == LUCA_BOT_NOME_REAL.strip().lower()
                if eh_conta_do_luca:
                    tem_marca = LUCA_MARKER in (m.get("content") or "")
                    eh_template = bool((m.get("additional_attributes") or {}).get("template_params"))
                    eh_template_conhecido = eh_conteudo_de_template_luca(m.get("content"))
                    if not tem_marca and not eh_template and not eh_template_conhecido:
                        ultima_humana_em = m.get("created_at") or ultima_humana_em
                    continue
                if not eh_assignee_bot(sender):
                    ultima_humana_em = m.get("created_at") or ultima_humana_em
        if ultima_humana_em is None:
            return None
        return time.time() - float(ultima_humana_em)
    except Exception as e:
        print(f"[handoff] Erro ao calcular tempo desde intervenção humana conv={conversation_id}: {e}", flush=True)
        return 0  # fail-safe: em erro, assume "acabou de acontecer" — nunca atropela atendimento humano


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
    if not person_id and deal_id:
        # A listagem de tasks não traz a pessoa: busca via negócio
        try:
            deal_fresco = buscar_deal_fresco(deal_id)
            dp = deal_fresco.get("person") or {}
            if dp.get("id"):
                pessoa, person_id = dp, dp.get("id")
                print(f"[lembrete] pessoa obtida via negócio: person={person_id} task={task_id}", flush=True)
        except Exception as e:
            print(f"[lembrete] Erro ao buscar pessoa via negócio {deal_id}: {e}", flush=True)
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
    # Inclui o horário da reunião na marca — se a reunião for reagendada
    # (mesma task, dueDate diferente), a marca muda e não colide com um
    # aviso antigo de um horário diferente (bug real encontrado: reagendar
    # depois de um teste em modo observação bloqueava o envio de verdade).
    marcador = f"[lembrete:{task_id}:{tipo}:{due.strftime('%Y%m%dT%H%M')}]"
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
        print("[lembrete] varredura pulada — LEMBRETES_ATIVOS desligado", flush=True)
        return
    agora_brt = datetime.utcnow() - timedelta(hours=3)
    if not (8 <= agora_brt.hour < 20):
        print(f"[lembrete] varredura pulada — fora do horário de envio ({agora_brt.strftime('%H:%M')} BRT)", flush=True)
        return  # fora da janela de envio

    tasks = tasks_cache.get("data") or []
    if not tasks:
        fetch_tasks_job()
        tasks = tasks_cache.get("data") or []

    reunioes_futuras = 0
    na_janela = 0

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
                deal_fresco = buscar_deal_fresco(deal_id)
                status = (deal_fresco.get("dealStatus") or {}).get("id")
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
            if delta > 0:
                reunioes_futuras += 1
                # Diagnóstico: mostra o dado cru de cada reunião futura
                print(f"[lembrete] futura: task={t.get('id')} dueDate_raw={t.get('dueDate')!r} "
                      f"parseado={due.isoformat()} delta={int(delta/60)}min "
                      f"campos_data={ {k: v for k, v in t.items() if 'due' in k.lower() or 'date' in k.lower()} }",
                      flush=True)
            if not (77400 <= delta <= 86400 or 900 <= delta <= 3600):
                continue
            na_janela += 1
            print(f"[lembrete] candidata: task={t.get('id')} due={t.get('dueDate')} "
                  f"delta={int(delta/60)}min pessoa={(t.get('person') or {}).get('id')} "
                  f"deal={(t.get('deal') or {}).get('id')}", flush=True)
            if not negocio_permite_lembrete(t):
                continue
            if 77400 <= delta <= 86400:          # 21h30 a 24h antes (2h30 de margem)
                processar_lembrete(t, "24h", due)
            elif 900 <= delta <= 3600:            # 15 a 60 min antes
                criada = _parse_dt(t.get("createdAt"))
                if criada and (agora - criada).total_seconds() < 7200:
                    continue  # reunião marcada há menos de 2h: lembrete redundante
                processar_lembrete(t, "1h", due)
        except Exception as e:
            print(f"[lembrete] Erro na task {t.get('id')}: {e}", flush=True)

    print(f"[lembrete] varredura concluída: {len(tasks)} tasks no cache, "
          f"{reunioes_futuras} reuniões futuras, {na_janela} na janela de disparo", flush=True)


def mover_novos_leads_para_1contato():
    """Move negócios parados em 'Novo Lead' (Funil Comercial) para
    '1º Contato (D0)' — mas só quando a saudação automática realmente já
    foi enviada de verdade (confirmado checando a conversa real, não
    assumido pelo simples fato do negócio estar em 'Novo Lead').

    Confirmado com print real da automação nativa (12/08): o gatilho dela é
    "quando um negócio chegar à etapa 1. Novo Lead" — ela dispara a
    saudação assim que o negócio é criado, ANTES desta função rodar (que só
    roda a cada 15 min). "1º Contato (D0)" é só o registro de que essa
    primeira tentativa já foi feita; não dispara nada por si só, é esta
    função quem move o rótulo depois que a saudação já saiu.

    Histórico de decisões (12/08, com [gestor]):
    - Uma versão anterior tentou checar "conversa aberta" pro telefone,
      pra evitar saudação duplicada em reconversão — não funcionava, porque
      toda saudação abre uma conversa, então a checagem bloqueava TODO lead
      novo, não só o cenário de reconversão (caso real: [lead], negócio
      nunca avançava).
    - Uma segunda versão trocou pra checar "essa pessoa já tem outro
      negócio engajado" — mas como o gatilho da automação é "Novo Lead"
      (não "1º Contato"), essa checagem não evita a saudação duplicada de
      verdade (a automação já disparou antes desta função nem rodar), só
      atrasava o rótulo à toa. Removida.
    - Decisão final: aceitar o risco de saudação duplicada em reconversão
      (raro, e mesmo quando acontece o Luca continua a conversa
      normalmente pelo histórico, sem perder contexto — só fica
      visualmente estranho pro lead ver a saudação de novo). Resolver isso
      de verdade exigiria mover o disparo da saudação pra dentro do nosso
      código via webhook on_deal_created, o que foi considerado mas
      adiado por ora."""
    FUNIL_COMERCIAL_ID = 696449
    ETAPA_NOVO_LEAD_ID = 2835663
    SEQUENCIA_1_CONTATO = 2  # posição de "1º Contato (D0)" no Funil Comercial

    deals = cache.get("deals") or []
    candidatos = [
        d for d in deals
        if ((d.get("dealStage") or {}).get("funnel") or {}).get("id") == FUNIL_COMERCIAL_ID
        and (d.get("dealStage") or {}).get("id") == ETAPA_NOVO_LEAD_ID
        and not d.get("wonAt") and not d.get("lostAt")  # exclui negócios já concluídos, mesmo
                                                          # que o campo de etapa ainda aponte pra
                                                          # Novo Lead (caso real: [lead],
                                                          # perdido há 261 dias, nunca saiu daqui —
                                                          # confirmado em 13/08, bug real corrigido)
    ]

    movidos = 0
    for d in candidatos:
        deal_id = d.get("id")
        person = d.get("person") or {}
        person_id = person.get("id")
        try:
            # Só move se a saudação automática realmente foi enviada de
            # verdade — checa a conversa real, não assume pelo simples fato
            # do negócio estar em "Novo Lead" (correção de 12/08, a [gestor]
            # pediu essa confirmação: sem isso, o rótulo podia avançar
            # mesmo que a automação nativa tivesse falhado por qualquer
            # motivo — telefone inválido, automação desativada, etc.).
            if not person_id:
                print(f"[novo_lead] Pulado — sem person_id deal={deal_id}", flush=True)
                continue
            telefone = telefone_da_pessoa(person_id)
            if not telefone:
                print(f"[novo_lead] Pulado — sem telefone person={person_id} deal={deal_id}", flush=True)
                continue
            conv = conversa_do_telefone(telefone)
            if not conv:
                print(f"[novo_lead] Pulado — conversa não encontrada telefone={telefone} deal={deal_id}", flush=True)
                continue
            msgs = mensagens_da_conversa(conv["id"])
            # Aceita QUALQUER mensagem enviada (não exige automation_id) —
            # esse campo não vem preenchido do mesmo jeito quando a mensagem
            # é buscada via histórico (endpoint diferente), então exigir ele
            # causava falso negativo mesmo com a saudação já confirmada.
            saudacao_enviada = any(
                m.get("message_type") == 1 and not m.get("private")
                for m in msgs
            )
            if not saudacao_enviada:
                print(f"[novo_lead] Pulado — saudação ainda não confirmada na conversa "
                      f"deal={deal_id}", flush=True)
                continue

            # Confere a etapa FRESCA antes de mover — o cache de negócios só
            # atualiza de hora em hora, então o negócio pode já ter avançado
            # de verdade (ex: "Contato Retornado", em tempo real, se o lead
            # respondeu) desde a última atualização do cache. Sem essa
            # checagem, este job "puxava de volta" pra 1º Contato um negócio
            # que já tinha progredido de verdade (bug real confirmado: caso
            # [lead], deal=44777245, 13/08 — avançou pra Contato Retornado
            # às 13:45, e o job rodou às 13:54 com cache de antes, movendo
            # de volta pra 1º Contato por engano).
            deal_fresco = buscar_deal_fresco(deal_id)
            etapa_fresca_id = (deal_fresco.get("dealStage") or {}).get("id")
            if etapa_fresca_id != ETAPA_NOVO_LEAD_ID:
                print(f"[novo_lead] Pulado — negócio já avançou de etapa desde o cache "
                      f"deal={deal_id} etapa_atual={etapa_fresca_id}", flush=True)
                continue

            r = requests.put(f"{AGENDOR_BASE}/deals/{deal_id}/stage",
                              headers={**HEADERS, "Content-Type": "application/json"},
                              json={"dealStage": SEQUENCIA_1_CONTATO}, timeout=15)
            print(f"[novo_lead] Movido pra 1º Contato (D0) deal={deal_id} status={r.status_code}", flush=True)
            movidos += 1
        except Exception as e:
            print(f"[novo_lead] Erro ao processar deal={deal_id}: {e}", flush=True)

    print(f"[novo_lead] varredura concluída: {len(candidatos)} em 'Novo Lead', {movidos} movidos", flush=True)


def mover_novos_leads_para_1contato_safe():
    if not _flag("MOVER_NOVOS_LEADS_ATIVO", "false"):
        print("[novo_lead] varredura pulada — MOVER_NOVOS_LEADS_ATIVO desligado", flush=True)
        return
    try:
        mover_novos_leads_para_1contato()
    except Exception as e:
        print(f"[novo_lead] Erro geral na varredura: {e}", flush=True)


def varredura_lembretes_safe():
    try:
        varredura_lembretes()
    except Exception as e:
        print(f"[lembrete] Erro geral na varredura: {e}", flush=True)


def conversa_parece_estagnada(conversation_id: int) -> bool:
    """Usa o Claude pra ler as últimas mensagens reais da conversa e decidir
    se ela está genuinamente parada no meio de uma negociação (vale puxar o
    lead de volta) ou se já teve um fechamento natural (reunião confirmada,
    despedida, lead disse que não tem interesse agora, etc — nesses casos o
    silêncio é esperado, não deve gerar o follow-up). Mais robusto que uma
    flag em memória: lê o conteúdo de verdade, direto da API, então não
    depende de estado que se perde em reset/restart."""
    try:
        msgs = mensagens_da_conversa(conversation_id)
        dialogo = [m for m in msgs if m.get("message_type") in (0, 1, 3) and not m.get("private")
                   and not (m.get("additional_attributes") or {}).get("automation_id")
                   and "Em breve um de nossos consultores dará andamento" not in (m.get("content") or "")]
        ultimas = dialogo[-8:]
        if not ultimas:
            return True  # sem contexto suficiente — mantém comportamento conservador (permite envio)

        transcript = "\n".join(
            f"{'Lead' if m.get('message_type') == 0 else 'Luca/Consultor'}: {m.get('content', '')}"
            for m in ultimas
        )
        prompt = f"""Aqui estão as últimas mensagens de uma conversa de atendimento comercial:

{transcript}

A conversa está genuinamente PARADA no meio de uma negociação (o lead ficou
sem responder algo pendente, ou sumiu no meio de um processo em aberto)?
Ou ela já teve um FECHAMENTO NATURAL (reunião confirmada, despedida, lead
disse que não tem interesse por ora, ou a última mensagem já é uma resposta
completa que não pede mais nada do lead)?

Responda apenas com uma palavra: PARADA ou FECHADA."""
        resposta = call_claude(
            [{"role": "user", "content": prompt}], max_tokens=10,
            system="Você classifica o estado de conversas comerciais. Responda só com uma palavra: PARADA ou FECHADA.",
            model="claude-haiku-4-5-20251001", tipo="classificacao"
        )
        return "PARADA" in resposta.upper()
    except Exception as e:
        print(f"[followup1h] Erro ao classificar conversa conv={conversation_id}: {e}", flush=True)
        return True  # fail-open: em erro, mantém comportamento anterior (permite envio)


def mensagem_precisa_resposta(texto: str) -> bool:
    """Criado 25/08 ([gestor]): antes de ligar o cronômetro de 4h de
    silêncio após uma mensagem humana, checa se essa mensagem específica
    realmente PEDE algo do lead (uma pergunta, uma escolha, uma
    confirmação) ou se já é uma afirmação que se encerra por si só (ex:
    'já vou providenciar o CNAE e te mando até o final do dia'). Nesse
    segundo caso, não faz sentido cobrar resposta — a conversa
    simplesmente acaba ali, sem follow-up nenhum."""
    try:
        prompt = f"""Aqui está uma mensagem que um consultor comercial mandou pro cliente:

"{texto}"

Essa mensagem PEDE algo de volta do cliente (uma resposta, escolha, confirmação,
informação)? Ou ela já é uma afirmação/aviso que se encerra por si só, sem
esperar nada do cliente (ex: uma confirmação de que algo será feito, uma
despedida, um agradecimento)?

Responda apenas com uma palavra: PRECISA ou NAO_PRECISA."""
        resposta = call_claude(
            [{"role": "user", "content": prompt}], max_tokens=10,
            system="Você classifica se uma mensagem comercial pede resposta do cliente. Responda só com uma palavra: PRECISA ou NAO_PRECISA.",
            model="claude-haiku-4-5-20251001", tipo="classificacao"
        )
        return "NAO_PRECISA" not in resposta.upper()
    except Exception as e:
        print(f"[followup4h] Erro ao classificar necessidade de resposta: {e}", flush=True)
        return True  # fail-safe: em erro, assume que precisa (comportamento anterior)


def verificar_followup_1h_silencio():
    """A cada 15 min, verifica conversas em que o Luca respondeu por último e
    o lead ficou 1h+ sem responder. Manda uma mensagem única puxando o lead
    de volta. Não envia se um humano estiver atribuído à conversa, se a
    conversa já foi resolvida, ou se o ciclo do CRM já foi registrado (ou
    seja, a reunião já foi agendada com sucesso — silêncio nesse caso é
    esperado, não é "sumiço no meio da negociação"). Baseado em memória
    (conversation_histories) — reseta se o processo reiniciar nesse meio-tempo."""
    agora = time.time()
    for conv_key, conv in list(conversation_histories.items()):
        if conv.get("modo_demo"):
            continue  # conversa de demonstração — nunca gera follow-up
        aguardando_desde = conv.get("luca_aguardando_desde")
        if not aguardando_desde or conv.get("followup_1h_enviado"):
            continue
        if conv.get("crm_registrado"):
            continue  # reunião já agendada com sucesso — silêncio é esperado
        elapsed = agora - aguardando_desde
        if elapsed < 3600:
            continue
        try:
            conversation_id = int(conv_key)
        except (TypeError, ValueError):
            continue

        detalhe = get_conversation_details(conversation_id) or {}
        meta = detalhe.get("meta") or {}
        status = detalhe.get("status")
        assignee = meta.get("assignee")
        if status == "resolved":
            continue
        if assignee and assignee.get("type") == "user" and not eh_assignee_bot(assignee):
            print(f"[followup1h] Pulado — humano atribuído conv={conversation_id}", flush=True)
            continue

        if not conversa_parece_estagnada(conversation_id):
            print(f"[followup1h] Pulado — conversa parece concluída naturalmente conv={conversation_id}", flush=True)
            continue

        nome = (conv.get("contact_name_cache") or conv.get("lead_data", {}).get("nome") or "").strip()
        primeiro_nome = nome.split(" ")[0] if nome else ""
        # Texto simplificado (25/08, [gestor]) — versão mais leve, já que a
        # versão "acho que peguei você num momento ruim" passou a ser usada
        # no follow-up de 4h após mensagem HUMANA (ver
        # verificar_followup_4h_silencio_humano abaixo).
        texto = (f"Oi, {primeiro_nome}.\nVocê ainda está por aí?") if primeiro_nome else \
                ("Oi!\nVocê ainda está por aí?")

        # Corrigido 27/08 (achado da revisão externa): esse texto é livre,
        # não template — só funciona dentro da janela de 24h desde a
        # última mensagem do lead. Checar ANTES de mandar, em vez de só
        # reagir à falha depois (message_updated), evita a tentativa
        # inútil e já avisa pra contato manual na hora certa.
        try:
            msgs_janela = mensagens_da_conversa(conversation_id)
        except Exception as e:
            print(f"[followup1h] Erro ao checar janela conv={conversation_id}: {e}", flush=True)
            msgs_janela = None
        if msgs_janela is not None and not janela_aberta(msgs_janela):
            send_private_note(conversation_id, (
                f"🔔 Follow-up de 1h NÃO enviado — janela de 24h do WhatsApp fechada. "
                f"Recomenda-se contato manual com {primeiro_nome or 'o lead'}."))
            conv["followup_1h_enviado"] = True  # evita tentar de novo a cada 15 min
            print(f"[followup1h] Pulado — janela de 24h fechada conv={conversation_id}", flush=True)
            continue

        try:
            send_agendorchat_message(conversation_id, texto)
            conv["followup_1h_enviado"] = True
            print(f"[followup1h] Enviado conv={conversation_id} nome={primeiro_nome!r}", flush=True)
        except Exception as e:
            print(f"[followup1h] Erro ao enviar conv={conversation_id}: {e}", flush=True)


def verificar_followup_1h_silencio_safe():
    try:
        verificar_followup_1h_silencio()
    except Exception as e:
        print(f"[followup1h] Erro geral na varredura: {e}", flush=True)


def verificar_followup_4h_silencio_humano():
    """Criado 25/08 ([gestor]): igual ao follow-up de 1h, mas pra quando foi
    um HUMANO de verdade (não o Luca) quem mandou a última mensagem — o
    timer é ligado no webhook principal, quando detecta uma mensagem
    outgoing genuína (ver bloco de detecção em agendorchat_webhook). Janela
    maior (4h em vez de 1h) porque um lead demora mais pra responder um
    humano de verdade do que uma mensagem automática do Luca — e só dentro
    do horário comercial (8h-20h BRT, mesma janela já usada no
    followup_dias), pra não mandar isso de madrugada."""
    agora_brt = datetime.utcnow() - timedelta(hours=3)
    if not (8 <= agora_brt.hour < 20):
        return
    agora = time.time()
    for conv_key, conv in list(conversation_histories.items()):
        if conv.get("modo_demo"):
            continue  # conversa de demonstração — nunca gera follow-up
        aguardando_desde = conv.get("humano_aguardando_desde")
        if not aguardando_desde or conv.get("followup_humano_enviado"):
            continue
        if conv.get("crm_registrado"):
            continue  # reunião já agendada com sucesso — silêncio é esperado
        elapsed = agora - aguardando_desde
        if elapsed < 14400:  # 4h
            continue
        try:
            conversation_id = int(conv_key)
        except (TypeError, ValueError):
            continue

        detalhe = get_conversation_details(conversation_id) or {}
        status = detalhe.get("status")
        if status == "resolved":
            continue

        if not conversa_parece_estagnada(conversation_id):
            print(f"[followup4h] Pulado — conversa parece concluída naturalmente conv={conversation_id}", flush=True)
            continue

        nome = (conv.get("contact_name_cache") or conv.get("lead_data", {}).get("nome") or "").strip()
        primeiro_nome = nome.split(" ")[0] if nome else ""
        texto = (f"{primeiro_nome}, acho que peguei você num momento ruim. "
                 f"Qual o melhor horário pra gente conversar?") if primeiro_nome else \
                ("Acho que peguei você num momento ruim. Qual o melhor horário pra gente conversar?")

        # Mesma checagem preventiva de janela do followup1h (27/08).
        try:
            msgs_janela = mensagens_da_conversa(conversation_id)
        except Exception as e:
            print(f"[followup4h] Erro ao checar janela conv={conversation_id}: {e}", flush=True)
            msgs_janela = None
        if msgs_janela is not None and not janela_aberta(msgs_janela):
            send_private_note(conversation_id, (
                f"🔔 Follow-up de 4h NÃO enviado — janela de 24h do WhatsApp fechada. "
                f"Recomenda-se contato manual com {primeiro_nome or 'o lead'}."))
            conv["followup_humano_enviado"] = True
            print(f"[followup4h] Pulado — janela de 24h fechada conv={conversation_id}", flush=True)
            continue

        try:
            send_agendorchat_message(conversation_id, texto)
            conv["followup_humano_enviado"] = True
            print(f"[followup4h] Enviado conv={conversation_id} nome={primeiro_nome!r}", flush=True)
        except Exception as e:
            print(f"[followup4h] Erro ao enviar conv={conversation_id}: {e}", flush=True)


def verificar_followup_4h_silencio_humano_safe():
    try:
        verificar_followup_4h_silencio_humano()
    except Exception as e:
        print(f"[followup4h] Erro geral na varredura: {e}", flush=True)


# ── Follow-up automático de silêncio (D+1 / D+3 / D+5 / D+7 / D+10) ──────────
# Desenho confirmado com [gestor] em 05/08, estendido em 25/08 (D7 e D10
# novos, 2 etapas novas criadas no Agendor) — a régua de silêncio USA as
# etapas que já existiam no Funil Comercial como o próprio estado, sem
# marcador paralelo:
#
#   Novo Lead --(boas-vindas, já existente)--> 1º Contato (D0)  [dia da criação]
#   1º Contato (D0)  --D+1 (1 dia corrido desde a criação)-->  nudge 1, move p/ 2° Contato
#   2° Contato       --D+3 (3 dias corridos desde a criação)--> nudge 2, move p/ 3° Contato
#   3° Contato       --D+5 (5 dias corridos desde a criação)--> nudge 3, move p/ 4° Contato (D5)
#   4° Contato (D5)  --D+7 (7 dias corridos desde a criação)--> nudge 4, move p/ 5° Contato (D7)
#   5° Contato (D7)  --D+10 (10 dias corridos desde a criação)--> nudge 5 (última tentativa);
#                                              se NUNCA houve humano de
#                                              verdade na conversa, fecha
#                                              como PERDIDO - SEM RETORNO
#
#   Se o lead responder enquanto o negócio está em qualquer uma das etapas
#   de contato (2° a 5°), move pra "Contato Retornado" — isso é feito em
#   tempo real no webhook, não neste job.
#
#   O Luca NUNCA move pra "Follow-up" nem "Fechamento" — isso é decisão de
#   quem está atendendo (evolução pós-reunião, ou fechamento direto).
#
# Fonte da varredura: cache["deals"] (candidatos, barato) + 1 GET fresco por
# candidato antes de agir (confirma etapa/status atuais de verdade, evita
# agir em cima de cache com até 1h de atraso). Nenhum estado em RAM.

ETAPA_NOVO_LEAD      = 2835663  # Novo Lead
ETAPA_1_CONTATO      = 3596855  # 1º Contato (D0)
ETAPA_2_CONTATO       = 3060060  # 2° Contato
ETAPA_3_CONTATO       = 3060061  # 3° Contato
ETAPA_4_CONTATO_D5    = 3866010  # 4º Contato (D5) — criada 25/08, régua estendida
ETAPA_5_CONTATO_D7    = 3866015  # 5º Contato (D7) — criada 25/08, régua estendida
ETAPA_CONTATO_RETORNADO = 2907497  # Contato Retornado
ORDEM_ETAPAS_FUNIL_COMERCIAL = [
    2835663,  # Novo Lead
    3596855,  # 1º Contato (D0)
    3060060,  # 2° Contato
    3060061,  # 3° Contato
    3866010,  # 4º Contato (D5) — criada 25/08, régua estendida
    3866015,  # 5º Contato (D7) — criada 25/08, régua estendida
    3650939,  # Perdido - sem retorno (D10) — renomeada 25/08 (era "sem contato (D5)")
    2907497,  # Contato Retornado
    2845579,  # Reunião agendada
    2835665,  # Follow-up
    2835666,  # Fechamento
    3650859,  # Perdido (genérica) — adicionada 13/08, destino do D5 quando já
              # teve contato humano em algum momento, mas não fechou
]

# IMPORTANTE: cada número é o tempo de espera DENTRO da etapa atual (não
# mais dias acumulados desde a criação do negócio) — contar acumulado
# permitia que um negócio parado disparasse D1/D3/D5/D7 tudo de uma vez.
#
# (etapa atual, dias esperando NESSA etapa pra disparar, rótulo, próxima etapa ou None)
FOLLOWUP_REGRAS = [
    (ETAPA_1_CONTATO, 1, "D1", ETAPA_2_CONTATO),
    (ETAPA_2_CONTATO,  2, "D3", ETAPA_3_CONTATO),
    (ETAPA_3_CONTATO,  2, "D5", ETAPA_4_CONTATO_D5),
    (ETAPA_4_CONTATO_D5, 2, "D7", ETAPA_5_CONTATO_D7),
    (ETAPA_5_CONTATO_D7, 3, "D10", None),  # None = última tentativa, sem próxima etapa
]
FOLLOWUP_REGRA_POR_ETAPA = {r[0]: r for r in FOLLOWUP_REGRAS}
REFORCO_D0_HORAS = 6  # horas após a criação pra mandar o reforço do mesmo dia, se ainda sem resposta


def mover_etapa_funil_comercial(deal_id: int, etapa_alvo_id: int, permitir_recuo: bool = False) -> bool:
    """Move o negócio pra etapa_alvo_id dentro do Funil Comercial, buscando
    a etapa atual FRESCA antes (nunca confia em cache) — mesmo padrão já
    usado no passo 5 de registrar_no_crm. Por padrão só avança (nunca
    rebaixa); permitir_recuo=True é o caso de 'Contato Retornado', que
    semanticamente é o lead voltando a se engajar, mesmo que a posição
    dessa etapa na lista seja anterior à de 2°/3° Contato."""
    try:
        deal_fresco = buscar_deal_fresco(deal_id)
    except Exception as e:
        print(f"[funil] Erro ao buscar negócio fresco deal={deal_id}: {e}", flush=True)
        return False
    deal_stage = deal_fresco.get("dealStage") or {}
    funil_atual_id = (deal_stage.get("funnel") or {}).get("id")
    etapa_atual_id = deal_stage.get("id")
    if funil_atual_id != FUNIL_COMERCIAL_ID:
        print(f"[funil] Etapa não movida — negócio fora do Funil Comercial deal={deal_id}", flush=True)
        return False
    if etapa_atual_id not in ORDEM_ETAPAS_FUNIL_COMERCIAL:
        print(f"[funil] Etapa atual fora da ordem mapeada (ex: já Perdido) deal={deal_id}", flush=True)
        return False
    idx_atual = ORDEM_ETAPAS_FUNIL_COMERCIAL.index(etapa_atual_id)
    idx_alvo = ORDEM_ETAPAS_FUNIL_COMERCIAL.index(etapa_alvo_id)
    if idx_atual == idx_alvo:
        # Já está exatamente na etapa alvo — não gasta chamada de API à toa.
        # Achado real em 17/08: com permitir_recuo=True (caso "Contato
        # Retornado"), a checagem abaixo nunca barrava isso, e mensagens
        # rápidas em sequência do mesmo lead geravam PUT duplicado pra
        # mesma etapa (caso real: deal=44846617, duas chamadas idênticas
        # com 1s de diferença).
        return True
    if not permitir_recuo and idx_atual >= idx_alvo:
        print(f"[funil] Etapa não movida — atual (idx={idx_atual}) já é igual/posterior ao "
              f"alvo (idx={idx_alvo}) deal={deal_id}", flush=True)
        return False
    sequencia_alvo = idx_alvo + 1  # API espera a posição 1-indexed dentro do funil
    r = requests.put(f"{AGENDOR_BASE}/deals/{deal_id}/stage",
                      headers={**HEADERS, "Content-Type": "application/json"},
                      json={"dealStage": sequencia_alvo}, timeout=15)
    # O log mostra os dois números: o ID pretendido (etapa_alvo_id) e a
    # posição de verdade que foi enviada (sequencia_alvo) — antes só
    # mostrava o ID, o que mascarou o bug real da lista incompleta
    # (corrigido em 13/08, caso [lead]/Contato Retornado).
    print(f"[funil] Etapa -> {etapa_alvo_id} (posição enviada={sequencia_alvo}) "
          f"deal={deal_id} status={r.status_code}", flush=True)
    return r.status_code in (200, 201)


def mover_para_contato_retornado_se_aplicavel(phone: str):
    """Chamado em tempo real quando o lead manda mensagem. 'Contato
    Retornado' confirma que existe uma pessoa real do outro lado, que
    respondeu a alguma mensagem nossa — não é uma etapa reservada só pra
    quem sumiu e voltou depois de escalar por silêncio. Corrigido em 12/08
    (entendimento anterior estava restrito demais, só cobria 2º/3º
    Contato): qualquer resposta do lead enquanto o negócio está em 1º, 2º
    ou 3º Contato já confirma engajamento real e move pra 'Contato
    Retornado'. Roda em thread separada, não atrasa a resposta do Luca.

    Corrigido em 18/08: faltava "Novo Lead" nessa lista — se a transição
    pra "1º Contato" ainda não tinha rodado (job de 15 em 15 min) quando
    o lead já estava respondendo ativamente, o negócio ficava preso em
    "Novo Lead" a conversa inteira, mesmo com engajamento real óbvio
    (caso real: [lead], deal=44854914, 17-18/08 — conversa longa
    de qualificação inteira aconteceu sem sair de "Novo Lead").

    Também em 18/08: inclui "Perdido - sem contato (D5)" — se mandamos a
    última mensagem da cascata e o lead responde depois disso, é um
    retorno de verdade, mesmo já tendo sido marcado como perdido por
    silêncio. NÃO inclui "Perdido" genérico (decisão de [gestor],
    18/08) — esse caso já teve contato humano e não fechou por outro
    motivo, uma resposta tardia não desfaz isso automaticamente."""
    try:
        _, deal = buscar_pessoa_e_negocio(phone)
        if not deal:
            return
        etapa_atual_id = (deal.get("dealStage") or {}).get("id")
        if etapa_atual_id in (ETAPA_NOVO_LEAD, ETAPA_1_CONTATO, ETAPA_2_CONTATO,
                               ETAPA_3_CONTATO, ETAPA_4_CONTATO_D5, ETAPA_5_CONTATO_D7,
                               ETAPA_PERDIDO_SEM_CONTATO):
            mover_etapa_funil_comercial(deal["id"], ETAPA_CONTATO_RETORNADO, permitir_recuo=True)
    except Exception as e:
        print(f"[contato_retornado] Erro phone={phone}: {e}", flush=True)


def humano_ja_atendeu_alguma_vez(conversation_id: int) -> bool:
    """True se, em QUALQUER ponto da conversa (não só depois da última
    mensagem do lead — diferente de humano_realmente_respondeu), existe uma
    mensagem de saída escrita por um humano de verdade (sender.type ==
    'user' E não é o próprio Luca — ver nota em humano_realmente_respondeu
    sobre send_agendorchat_message também usar sender.type=='user'), não
    pelo Bot/automação. Decide se um lead silencioso no D+10 é elegível a
    fechar como PERDIDO - SEM RETORNO: só é, se ninguém jamais interveio
    de verdade nessa conversa.

    Além de checar QUEM enviou, também checa se a MENSAGEM em si é um
    template automático disparado por outra conta real — um template
    automático não é intervenção humana de verdade, mesmo saindo pela
    conta de um vendedor. E usa LUCA_MARKER (mesmo esquema de
    humano_realmente_respondeu) pra distinguir mensagem da conta do
    gestor enviada pelo Luca (com marca) de mensagem que o gestor
    escreveu manualmente pela mesma conta (sem marca, conta como humano
    de verdade)."""
    try:
        msgs = mensagens_da_conversa(conversation_id)
        for m in msgs:
            if m.get("message_type") == 1 and not m.get("private") and (m.get("content") or "").strip():
                sender = m.get("sender") or {}
                if sender.get("type") != "user":
                    continue
                eh_template_automatico = bool((m.get("additional_attributes") or {}).get("automation_id")
                                               or (m.get("additional_attributes") or {}).get("template_params")
                                               or eh_conteudo_de_template_luca(m.get("content")))
                if eh_template_automatico:
                    continue
                nome_sender = (sender.get("name") or "").strip().lower()
                if nome_sender == LUCA_BOT_NOME_REAL.strip().lower():
                    if LUCA_MARKER not in (m.get("content") or ""):
                        return True  # [gestor] escreveu manualmente por essa conta
                    continue
                if not eh_assignee_bot(sender):
                    return True
        return False
    except Exception as e:
        print(f"[followup_dias] Erro ao checar intervenção humana conv={conversation_id}: {e}", flush=True)
        return True  # fail-safe: em erro, assume que já teve humano — nunca marca perdido por engano


ETAPA_PERDIDO_SEM_CONTATO = 3650939  # confirmado via JSON real da API, 12/08
ETAPA_PERDIDO_GENERICO = 3650859  # etapa "Perdido" genérica (posição 10, fim do funil) —
                                    # usada quando já teve contato humano, mas não fechou (13/08)

# Motivos de perda nativos do Agendor (confirmado via GET /v3/loss_reasons, 30/09).
LOSS_REASON_SEM_RETORNO = 3162043        # 2.1 Sem Retorno do Lead
LOSS_REASON_CONTATO_INVALIDO = 3187920   # 2.4 Contato Inválido / Dados Incorretos
LOSS_REASON_SEM_WHATSAPP = 3217904       # 2.5 Não Possui WhatsApp
LOSS_REASON_LEAD_PAROU = 3200168         # 3.5 Lead Parou de Interagir
LOSS_REASON_PRECO = 3162046              # 3.1 Preço
LOSS_REASON_CONTADOR_ATUAL = 3162049     # 1.3 Satisfeito com o Contador Atual
LOSS_REASON_CONCORRENTE = 3162048        # 3.3 Fechou com Concorrente
LOSS_REASON_DESISTIU = 3162050           # 3.4 Desistiu da negociação
LOSS_REASON_CURIOSO = 3162042            # 1.2 Curioso (sem intenção de compra)
LOSS_REASON_PRODUTO_NAO_ATENDEU = 3162047  # 3.2 Produto/Serviço não atendeu
LOSS_REASON_PRAZO = 3162045              # 2.3 Prazo (momento inadequado)
LOSS_REASON_EMPRESA_BAIXADA = 3265096     # 4.1 Empresa Baixada/Em Processo de Baixa
LOSS_REASON_CONTADOR_PARENTE = 3162041    # 1.1 Contador / Parente Contador

def marcar_negocio_perdido(deal_id: int, loss_reason_id: int = None, end_time: str = None) -> bool:
    """Marca o negócio como formalmente PERDIDO no Agendor e, opcionalmente,
    associa o motivo de perda e a data histórica de encerramento (endTime).

    Confirmado em teste real em 30/09/2026:
    - lostAt é controlado pelo Agendor e sempre recebe o momento da chamada;
    - enviar lostAt no PUT /status ou no PUT genérico é ignorado;
    - endTime é editável e preserva a data histórica desejada;
    - lossReason só deve ser associado depois que o negócio já está perdido.

    Por isso a operação continua em duas etapas: primeiro muda o status para
    lost; depois atualiza lossReason/endTime no endpoint genérico."""
    try:
        r1 = requests.put(
            f"{AGENDOR_BASE}/deals/{deal_id}/status",
            headers={**HEADERS, "Content-Type": "application/json"},
            json={"dealStatusText": "lost"}, timeout=15
        )
        print(f"[crm] Negócio marcado como Perdido (status) deal={deal_id} status={r1.status_code}", flush=True)
        if r1.status_code >= 300:
            print(f"[crm] Falha ao marcar perdido deal={deal_id}: {r1.text[:300]}", flush=True)
            return False

        payload = {}
        if loss_reason_id:
            payload["lossReason"] = {"id": loss_reason_id}
        if end_time:
            payload["endTime"] = end_time

        if payload:
            r2 = requests.put(
                f"{AGENDOR_BASE}/deals/{deal_id}",
                headers={**HEADERS, "Content-Type": "application/json"},
                json=payload, timeout=15
            )
            print(f"[crm] Complemento da perda deal={deal_id} motivo_id={loss_reason_id} "
                  f"endTime={end_time} status={r2.status_code}", flush=True)
            if r2.status_code >= 300:
                print(f"[crm] Falha ao gravar motivo/endTime deal={deal_id}: {r2.text[:300]}", flush=True)
                return False

        return True
    except Exception as e:
        print(f"[crm] Erro ao marcar negócio como perdido deal={deal_id}: {e}", flush=True)
        return False


def motivo_perda_por_texto(codigo: str):
    """Traduz o código estruturado que a IA escolheu diretamente (ex: "3.1")
    pro ID nativo do Agendor correspondente. Corrigido 30/09 (revisão
    externa apontou risco real de classificação errada): antes, isso
    tentava adivinhar por palavra-chave num RESUMO livre gerado pela IA —
    um texto tipo "não é uma questão de preço, é o prazo" continha a
    palavra "preço" e seria classificado errado como 3.1, o oposto do que
    a frase realmente diz. Agora a IA já escolhe o código diretamente
    (structured output), sem essa segunda interpretação por palavra-chave.
    Se a IA não tiver certeza, deixa o código em branco — e essa função
    retorna None (não escreve motivo nenhum, em vez de chutar um genérico:
    "3.4 Desistiu" tem significado próprio, não é um "não sei")."""
    mapa = {
        "1.1": LOSS_REASON_CONTADOR_PARENTE, "1.2": LOSS_REASON_CURIOSO,
        "1.3": LOSS_REASON_CONTADOR_ATUAL, "2.1": LOSS_REASON_SEM_RETORNO,
        "2.3": LOSS_REASON_PRAZO, "2.4": LOSS_REASON_CONTATO_INVALIDO,
        "2.5": LOSS_REASON_SEM_WHATSAPP, "3.1": LOSS_REASON_PRECO,
        "3.2": LOSS_REASON_PRODUTO_NAO_ATENDEU, "3.3": LOSS_REASON_CONCORRENTE,
        "3.4": LOSS_REASON_DESISTIU, "3.5": LOSS_REASON_LEAD_PAROU,
        "4.1": LOSS_REASON_EMPRESA_BAIXADA,
    }
    return mapa.get((codigo or "").strip())


def marcar_perdido_sem_contato(deal_id: int, end_time: str = None) -> bool:
    """Move o negócio pra etapa 'Perdido - sem contato (D5)', dentro do
    próprio Funil Comercial.

    Corrigido em 12/08: a versão anterior usava PUT /deals/{id}/status com
    dealStatus=3 + lostReason (nunca confirmado, campo/formato incerto).
    [gestor] mostrou que "Perdido - sem contato (D5)" é uma ETAPA própria
    do funil agora (confirmado via JSON real da API: id=3650939, posição 5,
    logo depois de '3° Contato (D3)'), não um status/motivo separado. Isso
    elimina toda a incerteza anterior — usa o mesmo mecanismo de mover
    etapa que já é testado e confiável em produção (mover_etapa_funil_comercial),
    em vez de um endpoint/payload que nunca foi validado."""
    ok = mover_etapa_funil_comercial(deal_id, ETAPA_PERDIDO_SEM_CONTATO)
    marcar_negocio_perdido(deal_id, LOSS_REASON_SEM_RETORNO, end_time=end_time)
    return ok


def _data_gt_para_tasks_d10(deal: dict) -> str:
    """Define um limite inferior seguro para consultar as tasks do negócio.

    A API /deals/{id}/tasks exige pelo menos um filtro de data, e esse
    filtro aceita no máximo ~30 dias no passado (confirmado em produção,
    ver fetch_tasks_job — com 60 dias a API já rejeitou a chamada). Usar
    o startTime do negócio como referência (como antes) arrisca ultrapassar
    esse limite pra negócios mais antigos, entre os que estamos corrigindo
    agora — por isso usa sempre "hoje menos 30 dias", o mesmo padrão já
    testado e funcionando em fetch_tasks_job, independente de quando o
    negócio foi criado."""
    return (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")


def data_historica_d10_da_task(deal: dict) -> str:
    """Retorna o createdAt da primeira task real de Follow-up automático D10.

    Fonte validada em 30/09/2026 no negócio 45431611:
    GET /v3/deals/45431611/tasks?createdDateGt=2026-09-23 retornou a task
    'Follow-up automático (D10) enviado ao lead' com
    createdAt=2026-09-24T14:49:55.000Z, que corresponde exatamente a
    24/09/2026 11:49:55 em Brasília, horário conhecido da entrada no D10.

    Segurança: não usa updatedAt, não usa data da etapa por inferência e não
    inventa data. Se a task D10 não existir ou vier sem createdAt válido,
    retorna None e o negócio é pulado.
    """
    deal_id = deal.get("id")
    if not deal_id:
        return None

    created_date_gt = _data_gt_para_tasks_d10(deal)
    try:
        r = requests.get(
            f"{AGENDOR_BASE}/deals/{deal_id}/tasks",
            headers=HEADERS,
            params={"createdDateGt": created_date_gt, "per_page": 100},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[reconcile-d10] Tasks deal={deal_id} status={r.status_code} "
                  f"body={r.text[:300]}", flush=True)
            return None
        tasks = r.json().get("data") or []
    except Exception as e:
        print(f"[reconcile-d10] Erro ao buscar tasks deal={deal_id}: {e}", flush=True)
        return None

    candidatos = []
    for task in tasks:
        texto = task.get("text") or ""
        if "Follow-up automático (D10)" not in texto:
            continue
        created_at = task.get("createdAt")
        if not created_at:
            continue
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            continue
        candidatos.append((dt, created_at, task.get("id")))

    if not candidatos:
        return None

    # Se houver duplicidade, a primeira task D10 representa o momento em que
    # a régua chegou ao D10 pela primeira vez. Não usamos a mais recente para
    # evitar deslocar artificialmente a data histórica por eventual reenvio.
    candidatos.sort(key=lambda x: x[0])
    dt, created_at, task_id = candidatos[0]
    iso_utc = dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    print(f"[reconcile-d10] Data histórica encontrada deal={deal_id} task={task_id} "
          f"createdAt={created_at} -> endTime={iso_utc}", flush=True)
    return iso_utc


def executar_reconciliacao_perdidos_d10() -> dict:
    """Corrige negócios travados em Perdido - sem retorno (D10).

    Um negócio só é alterado quando TODAS as condições abaixo forem verdade:
    - está no Funil Comercial;
    - está exatamente em Perdido - sem retorno (D10);
    - status formal ainda é Em andamento;
    - o GET fresco confirma novamente etapa e status;
    - existe task do próprio negócio com texto 'Follow-up automático (D10)';
    - essa task possui createdAt válido.

    Quando validado, createdAt da PRIMEIRA task D10 vira endTime, o status é
    marcado como lost e o motivo 2.1 Sem Retorno do Lead é associado. lostAt
    continua controlado pelo Agendor e registra o momento da correção.

    Se qualquer evidência faltar, NÃO altera o negócio.
    """
    deals = list(cache.get("deals") or [])
    resultado = {
        "candidatos": 0,
        "corrigidos": 0,
        "pulados_sem_task_d10": 0,
        "falhas": 0,
        "detalhes": [],
    }

    candidatos = [
        d for d in deals
        if ((d.get("dealStage") or {}).get("funnel") or {}).get("id") == FUNIL_COMERCIAL_ID
        and (d.get("dealStage") or {}).get("id") == ETAPA_PERDIDO_SEM_CONTATO
        and (d.get("dealStatus") or {}).get("id") == 1
    ]
    resultado["candidatos"] = len(candidatos)

    for deal_cache in candidatos:
        deal_id = deal_cache.get("id")
        try:
            fresco = buscar_deal_fresco(deal_id)
            if not fresco:
                resultado["falhas"] += 1
                resultado["detalhes"].append({"deal_id": deal_id, "status": "falha_busca"})
                continue

            funil_id = (((fresco.get("dealStage") or {}).get("funnel") or {}).get("id"))
            etapa_id = (fresco.get("dealStage") or {}).get("id")
            status_id = (fresco.get("dealStatus") or {}).get("id")
            if funil_id != FUNIL_COMERCIAL_ID or etapa_id != ETAPA_PERDIDO_SEM_CONTATO or status_id != 1:
                resultado["detalhes"].append({
                    "deal_id": deal_id,
                    "status": "nao_mais_elegivel",
                    "funil_id": funil_id,
                    "etapa_id": etapa_id,
                    "deal_status_id": status_id,
                })
                continue

            end_time = data_historica_d10_da_task(fresco)
            if not end_time:
                resultado["pulados_sem_task_d10"] += 1
                resultado["detalhes"].append({"deal_id": deal_id, "status": "sem_task_d10_confiavel"})
                continue

            ok = marcar_negocio_perdido(deal_id, LOSS_REASON_SEM_RETORNO, end_time=end_time)
            if ok:
                resultado["corrigidos"] += 1
                resultado["detalhes"].append({
                    "deal_id": deal_id,
                    "status": "corrigido",
                    "endTime": end_time,
                })
            else:
                resultado["falhas"] += 1
                resultado["detalhes"].append({"deal_id": deal_id, "status": "falha_atualizacao"})

            # Evita rajada de chamadas na API se houver vários travados.
            time.sleep(0.4)
        except Exception as e:
            resultado["falhas"] += 1
            resultado["detalhes"].append({
                "deal_id": deal_id,
                "status": "erro",
                "erro": str(e)[:200],
            })
            print(f"[reconcile-d10] Erro deal={deal_id}: {e}", flush=True)

    print(f"[reconcile-d10] Finalizado: {resultado}", flush=True)
    return resultado


def executar_backfill_perdidos_d10() -> dict:
    """Compatibilidade com a rota manual antiga: usa a mesma reconciliação segura."""
    return executar_reconciliacao_perdidos_d10()


def verificar_perdidos_d10_travados_safe():
    """Job de segurança: corrige automaticamente D10 que ficaram Em andamento."""
    try:
        if not cache.get("deals"):
            print("[reconcile-d10] Cache vazio, aguardando próxima execução", flush=True)
            return
        executar_reconciliacao_perdidos_d10()
    except Exception as e:
        print(f"[reconcile-d10] Erro geral: {e}", flush=True)


@app.route("/backfill-perdidos-d10", methods=["POST"])
def backfill_perdidos_d10():
    """Executa manualmente a mesma reconciliação automática dos D10 travados."""
    if not validar_agendar_api_key():
        return jsonify({"error": "unauthorized"}), 401
    if not cache.get("deals"):
        return jsonify({"error": "cache_vazio", "detail": "Execute /refresh e tente novamente."}), 409
    resultado = executar_reconciliacao_perdidos_d10()
    return jsonify(resultado), 200


def enviar_followup_dia(conversation_id, deal_id, phone, tag, nome):
    """Envia o nudge de silêncio (D1/D3/D5). Por definição a janela de 24h
    do WhatsApp certamente está fechada (o lead está silencioso há 1+ dia),
    então isso SEMPRE usa template aprovado da Meta, nunca mensagem livre.

    Só usa os templates Tech — os da Contabilidade não foram escritos e
    não são usados por enquanto.

    IMPORTANTE: o campo 'content' que mandamos pro AgendorChat é o texto
    que REALMENTE chega pro lead no WhatsApp — não é só um resumo interno.
    Precisa ser o texto completo do template, igual ao aprovado no Meta.

    Retorna (True, msg_id) se enviou de verdade (msg_id pode ser None se a
    API não trouxer o id na resposta), ou (False, None) se não enviou.
    Não avança a etapa aqui — só envia e marca como "aguardando
    confirmação". A etapa só avança depois que o webhook confirmar
    entrega de verdade (ver verificar_followup_dias_silencio), pra evitar
    negócio avançando sem a mensagem ter chegado."""
    nome_template = {"D1": "followup_silencio_d1_tech",
                      "D3": "followup_silencio_d3_tech",
                      "D5": "followup_silencio_d5_tech",
                      "D7": "followup_silencio_d7_tech",
                      "D10": "followup_silencio_d10_tech"}[tag]

    # Sem trava separada de "d10_enviado": o mecanismo de espera de
    # confirmação de entrega já cobre isso (só avança quando confirma
    # "delivered", reenvia quando confirma "failed" temporário) — ver
    # verificar_followup_dias_silencio.

    tpl = template_por_nome(nome_template)
    if not tpl:
        send_private_note(conversation_id, (
            f"🔁 Follow-up {tag} NÃO enviado — template '{nome_template}' não encontrado/aprovado "
            f"no Meta Business Suite."))
        print(f"[followup_dias] {tag} SEM TEMPLATE conv={conversation_id}", flush=True)
        return False, None

    nome_saudacao = nome or "tudo bem"
    texto_completo = {
        "D1": (f"Oi, {nome_saudacao}!\n\nVi que nossa conversa ficou parada. Seja abrindo do jeito "
               f"certo ou ajustando o que já existe, tem como reduzir bastante o imposto da sua PJ "
               f"com o regime tributário certo. Posso te mostrar como?"),
        "D3": (f"Oi, {nome_saudacao}!\n\nPassando de novo por aqui porque talvez você tenha ficado "
               f"sem tempo para continuar antes. Caso já possua CNPJ, cada mês sem o enquadramento "
               f"certo é imposto pago a mais, sem precisar. Se deseja abrir, já podemos começar do "
               f"jeito certo.\n\nQual o melhor momento para falarmos?"),
        "D5": (f"Oi, {nome_saudacao}, sabia que o CNAE certo é a diferença entre pagar 15,5% ou 6% "
               f"de imposto?\n\nSeparei algo que pode te ajudar. Confere aqui: "
               f"https://lucralize.com.br/cnae-dev/\n\nDepois de ver, me conta o que achou e te "
               f"ajudo a entender o que faz sentido pro seu caso."),
        "D7": (f"Já que vimos como o CNAE certo importa, {nome_saudacao}, que tal descobrir quanto "
               f"isso representa no seu bolso?\n\nCalculadora rápida aqui: "
               f"https://lucralize.com.br/calculadora-dev/\n\nSe o número te surpreender (é comum "
               f"surpreender!), me chama que a gente resolve isso juntos."),
        "D10": (f"Já vou indo por aqui, {nome_saudacao}, mas antes recapitulando as duas ferramentas "
                f"que te mandei:\n\nCNAE certo: https://lucralize.com.br/cnae-dev/\nQuanto você "
                f"pagaria: https://lucralize.com.br/calculadora-dev/\n\nEssa é minha última "
                f"mensagem. Se fizer sentido economizar, responde só um \"sim\" que eu retomo "
                f"com você."),
    }[tag]
    resp = enviar_template_conversa(conversation_id, tpl, {"1": nome_saudacao}, texto_completo)
    msg_id_enviado = (resp or {}).get("id")
    marcador_pendente = f" [followup:aguardando_confirmacao:{tag}:{msg_id_enviado}]" if msg_id_enviado else ""
    send_private_note(conversation_id,
        f"🔁 Follow-up {tag} enviado ao lead via template — aguardando confirmação de entrega antes "
        f"de avançar a etapa.{marcador_pendente}")
    espelho_crm(deal_id, f"🤖 Follow-up automático ({tag}) enviado ao lead — silêncio de {tag[1:]} dia(s).")
    print(f"[followup_dias] ✅ {tag} enviado conv={conversation_id} deal={deal_id} msg_id={msg_id_enviado}", flush=True)
    return True, msg_id_enviado


def status_da_mensagem(conversation_id, msg_id):
    """Consulta o status atual (delivered/failed/sent/etc.) de uma mensagem
    específica na conversa, usado pra confirmar entrega antes de avançar
    a etapa do follow-up (criado 08/09, [gestor]). Retorna None se não
    encontrar a mensagem ou em erro de rede."""
    if not msg_id:
        return None
    try:
        msgs = mensagens_da_conversa(conversation_id)
        msg = next((m for m in msgs if m.get("id") == msg_id), None)
        return msg.get("status") if msg else None
    except Exception as e:
        print(f"[followup_dias] Erro ao checar status da mensagem {msg_id} conv={conversation_id}: {e}", flush=True)
        return None


def dias_desde_referencia(deal_fresco: dict, etapa_atual_id: int, conversation_id: int, deal_id: int):
    """Retorna quantos dias completos se passaram desde a ENTRADA NA ETAPA
    ATUAL — não desde a criação do negócio (mudança de 08/09, a pedido de
    [gestor]: contar a partir da criação permitia que um negócio parado
    por dias, ao ser corrigido, disparasse D1/D3/D5/D7 tudo de uma vez —
    caso real confirmado em produção no mesmo dia, logo após o fix do
    AGENDORCHAT_INBOX_ID).

    Duas fontes de referência, dependendo de como o negócio chegou nesta
    etapa:
    - Entrada AUTOMÁTICA (o próprio robo moveu, marcador
      [followup:etapa_normal:{id}] presente): usa o horário exato desse
      marcador como início da contagem desta etapa.
    - Entrada MANUAL (alguém moveu o card, marcador ausente): decisão de
      [gestor] em 08/09 — dispara o follow-up desta etapa IMEDIATAMENTE
      (sem esperar o intervalo), e a partir daí reinicia a contagem
      normalmente pra próxima etapa. Guarda um marcador
      [followup:regua_reiniciada:{etapa_id}:{timestamp}] (agora com o id
      da etapa embutido — antes era um marcador genérico por conversa,
      que podia reaproveitar por engano o timestamp de um reset antigo de
      outra etapa) pra não disparar de novo a cada varredura.

    ETAPA_1_CONTATO é a única exceção: como é a entrada natural no funil,
    sempre usa a criação do negócio (startTime) — não existe "entrada
    manual" conceitual nela, é sempre o ponto de partida.

    Retorna None se não for possível calcular (sem startTime, por ex.).
    """
    try:
        msgs = mensagens_da_conversa(conversation_id)
    except Exception as e:
        print(f"[followup_dias] Erro ao ler conversa pra checar reset deal={deal_id}: {e}", flush=True)
        msgs = None

    if etapa_atual_id == ETAPA_1_CONTATO:
        start_time = deal_fresco.get("startTime")
        if not start_time:
            return None
        try:
            criado_em = datetime.strptime(start_time[:10], "%Y-%m-%d")
        except Exception:
            return None
        return (datetime.utcnow() - criado_em).days

    marcador_normal = f"[followup:etapa_normal:{etapa_atual_id}]"
    if msgs:
        msg_normal = next((m for m in msgs if marcador_normal in (m.get("content") or "")), None)
        if msg_normal and msg_normal.get("created_at"):
            entrada_em = datetime.utcfromtimestamp(msg_normal["created_at"])
            return (datetime.utcnow() - entrada_em).days

    # Entrada manual detectada nesta etapa — procura marcador de reset
    # já existente ESPECÍFICO desta etapa (pega o mais recente, caso
    # exista mais de um ao longo do tempo).
    marcador_reset_prefix = f"[followup:regua_reiniciada:{etapa_atual_id}:"
    if msgs:
        resets_encontrados = []
        for m in msgs:
            content = m.get("content") or ""
            if marcador_reset_prefix in content:
                try:
                    ts_str = content.split(marcador_reset_prefix)[1].split("]")[0]
                    resets_encontrados.append(float(ts_str))
                except Exception:
                    continue  # marcador corrompido, ignora
        if resets_encontrados:
            reset_em = datetime.utcfromtimestamp(max(resets_encontrados))
            return (datetime.utcnow() - reset_em).days

    # Primeira vez notando essa entrada manual nesta etapa — marca agora
    # e força o disparo IMEDIATO do follow-up desta etapa (retornando o
    # próprio limite de dias dela, que sempre bate na checagem seguinte).
    try:
        send_private_note(conversation_id, (
            f"🔁 Movimentação manual detectada nesta etapa — disparando o follow-up "
            f"correspondente agora, sem esperar. {marcador_reset_prefix}{time.time()}]"))
    except Exception as e:
        print(f"[followup_dias] Erro ao criar marcador de reset deal={deal_id}: {e}", flush=True)
    regra_etapa = FOLLOWUP_REGRA_POR_ETAPA.get(etapa_atual_id)
    return regra_etapa[1] if regra_etapa else 0


def verificar_followup_dias_silencio():
    if not _flag("FOLLOWUP_ATIVOS", "false"):
        print("[followup_dias] varredura pulada — FOLLOWUP_ATIVOS desligado", flush=True)
        return
    agora_brt = datetime.utcnow() - timedelta(hours=3)
    if not (8 <= agora_brt.hour < 20):
        print(f"[followup_dias] pulado — fora do horário de envio ({agora_brt.strftime('%H:%M')} BRT)", flush=True)
        return

    deals = cache.get("deals") or []
    candidatos = [
        d for d in deals
        if ((d.get("dealStage") or {}).get("funnel") or {}).get("id") == FUNIL_COMERCIAL_ID
        and (d.get("dealStage") or {}).get("id") in FOLLOWUP_REGRA_POR_ETAPA
        and (d.get("dealStatus") or {}).get("id") == 1  # só negócios ainda em andamento
    ]

    enviados = 0
    for d in candidatos:
        deal_id = d.get("id")
        try:
            # Etapa fresca, não a do cache (pode ter até 1h de atraso) —
            # evita agir duas vezes em cima de uma etapa que já mudou.
            deal_fresco = buscar_deal_fresco(deal_id)
        except Exception as e:
            print(f"[followup_dias] Erro ao buscar negócio fresco deal={deal_id}: {e}", flush=True)
            continue

        etapa_atual_id = (deal_fresco.get("dealStage") or {}).get("id")
        regra = FOLLOWUP_REGRA_POR_ETAPA.get(etapa_atual_id)
        if not regra or (deal_fresco.get("dealStatus") or {}).get("id") != 1:
            continue  # já saiu dessa etapa, ou não está mais em andamento

        # ── Reforço do mesmo dia (D0), antes do primeiro marco D+1 ──────────
        # Ainda dentro do dia da criação, sem stage-move (fica em 1º Contato
        # mesmo). Usa marcador em nota privada (não dá pra usar a etapa como
        # estado aqui, já que tanto o reforço quanto o D+1 partem da mesma
        # etapa). Mensagem livre (não precisa de template Meta): como é no
        # mesmo dia, a janela de 24h do WhatsApp ainda deve estar aberta.
        if etapa_atual_id == ETAPA_1_CONTATO:
            start_time_raw = deal_fresco.get("startTime")
            criado_ts = None
            if start_time_raw:
                try:
                    criado_ts = datetime.strptime(start_time_raw[:19], "%Y-%m-%dT%H:%M:%S")
                except Exception:
                    criado_ts = None
            if criado_ts:
                horas_desde_criacao = (datetime.utcnow() - criado_ts).total_seconds() / 3600
                ainda_no_mesmo_dia = (datetime.utcnow() - timedelta(hours=3)).date() == \
                                     (criado_ts - timedelta(hours=3)).date()
                if ainda_no_mesmo_dia:
                    # Ainda no dia da criação — só o reforço pode se aplicar
                    # aqui, o D+1 nunca dispara no mesmo dia. Sempre "continue"
                    # ao final deste bloco (nada mais a fazer nesta passada).
                    if horas_desde_criacao >= REFORCO_D0_HORAS:
                        person_r = deal_fresco.get("person") or {}
                        person_id_r = person_r.get("id")
                        if person_id_r:
                            try:
                                phone_r = telefone_da_pessoa(person_id_r)
                                conv_r = conversa_do_telefone(phone_r) if phone_r else None
                                if conv_r and conv_r.get("status") == "open":
                                    conv_id_r = conv_r.get("id")
                                    msgs_r = mensagens_da_conversa(conv_id_r)
                                    if not marcador_existe(msgs_r, "[followup:reforco_d0]") \
                                       and conversa_parece_estagnada(conv_id_r):
                                        nome_r = (person_r.get("name") or "").strip().split(" ")[0] \
                                                 if person_r.get("name") else ""
                                        texto = (f"Oi{', ' + nome_r if nome_r else ''}! Acho que te peguei num "
                                                 f"momento ruim. Qual o melhor horário pra a gente continuar "
                                                 f"esse papo hoje?")
                                        send_agendorchat_message(conv_id_r, texto)
                                        send_private_note(conv_id_r, "🔁 Reforço do mesmo dia (D0) enviado ao "
                                                                       "lead. [followup:reforco_d0]")
                                        print(f"[followup_dias] ✅ reforço D0 enviado deal={deal_id}", flush=True)
                            except Exception as e:
                                print(f"[followup_dias] Erro no reforço D0 deal={deal_id}: {e}", flush=True)
                    continue
                # Se não é mais o mesmo dia (ainda_no_mesmo_dia == False), cai
                # pro fluxo normal abaixo, que vai avaliar o marco D+1.

        _, dias_limite, tag, proxima_etapa = regra

        person = deal_fresco.get("person") or {}
        person_id = person.get("id")
        nome = (person.get("name") or "").strip().split(" ")[0] if person.get("name") else ""
        if not person_id:
            print(f"[followup_dias] Pulado — sem person_id deal={deal_id}", flush=True)
            continue
        try:
            phone = telefone_da_pessoa(person_id)
            if not phone:
                print(f"[followup_dias] Pulado — sem telefone person={person_id} deal={deal_id}", flush=True)
                continue
            conv = conversa_do_telefone(phone)
            if not conv:
                print(f"[followup_dias] Pulado — conversa não encontrada deal={deal_id}", flush=True)
                continue
            conversation_id = conv.get("id")

            # Relógio contado a partir da ENTRADA NA ETAPA ATUAL (mudança de
            # 08/09, [gestor] — ver docstring de dias_desde_referencia pro
            # motivo), não mais da criação do negócio nem do silêncio do lead.
            dias_desde_criacao = dias_desde_referencia(deal_fresco, etapa_atual_id, conversation_id, deal_id)
            if dias_desde_criacao is None:
                print(f"[followup_dias] Pulado — sem startTime deal={deal_id}", flush=True)
                continue
            if dias_desde_criacao < dias_limite:
                print(f"[followup_dias] Pulado — ainda não atingiu {dias_limite}d "
                      f"(tem {dias_desde_criacao}d) deal={deal_id} etapa={etapa_atual_id}", flush=True)
                continue

            # Não exige conv.status == "open": muitas conversas ficam
            # "resolved" no AgendorChat por inatividade, mesmo o negócio
            # continuando ativo — isso não significa que o lead terminou,
            # é justamente o cenário que esse follow-up existe pra cobrir
            # (decisão de [gestor], 13/08, confirmado com dados reais do
            # funil: vários negócios de dias atrás com conversa fechada).
            # Enviar mensagem reabre a conversa automaticamente; depois do
            # envio, resolve de novo pra manter a caixa "abertas" limpa.

            if not conversa_parece_estagnada(conversation_id):
                print(f"[followup_dias] Pulado — conversa parece concluída naturalmente "
                      f"conv={conversation_id}", flush=True)
                continue

            # Corrigido 08/09 ([gestor]): antes a etapa avançava logo após o
            # POST de envio, sem esperar confirmação de entrega de verdade —
            # se a mensagem falhasse depois (ex: erro 131049, "número
            # inválido/sem WhatsApp"), o negócio já tinha avançado sem o
            # lead ter recebido nada. Agora: primeiro checa se já existe um
            # envio pendente de confirmação PRA ESTA ETAPA; se sim, só avança
            # quando a entrega for confirmada (nunca reenvia enquanto espera
            # — só volta a tentar de novo na próxima varredura).
            try:
                msgs_pendencia = mensagens_da_conversa(conversation_id)
            except Exception as e:
                print(f"[followup_dias] Erro ao checar pendência conv={conversation_id}: {e}", flush=True)
                msgs_pendencia = []

            marcador_pendente_prefix = f"[followup:aguardando_confirmacao:{tag}:"
            candidatos_pendentes = [m for m in msgs_pendencia
                                     if marcador_pendente_prefix in (m.get("content") or "")]
            # Corrigido 09/09 ([gestor]): depois de um reenvio, passam a
            # existir DOIS marcadores desse tag na conversa (o antigo, já
            # falho, e o novo) — pegar o primeiro por ordem de iteração
            # arriscava travar pra sempre checando o antigo. Pega sempre o
            # de MAIOR id de mensagem (o mais recente).
            msg_pendente = max(candidatos_pendentes, key=lambda m: m.get("id", 0)) if candidatos_pendentes else None

            if msg_pendente:
                try:
                    msg_id_pendente = msg_pendente["content"].split(marcador_pendente_prefix)[1].split("]")[0]
                    msg_id_pendente = int(msg_id_pendente)
                except Exception:
                    msg_id_pendente = None
                status_entrega = status_da_mensagem(conversation_id, msg_id_pendente)
                if status_entrega in ("delivered", "read"):
                    # "read" implica que já foi entregue antes (o WhatsApp
                    # progride sent -> delivered -> read), então conta igual.
                    print(f"[followup_dias] {tag} confirmado como entregue deal={deal_id} — avançando etapa", flush=True)
                elif status_entrega == "failed":
                    # Falha temporária (131049/saldo) não move mais a etapa —
                    # tenta reenviar sozinho na próxima varredura. Mas com
                    # teto de 3 tentativas: na 4ª falha seguida, para e
                    # sinaliza pra verificação manual, em vez de insistir
                    # pra sempre sem sucesso.
                    if len(candidatos_pendentes) >= 3:
                        send_private_note(conversation_id,
                            f"⚠️ Follow-up {tag} falhou 3 vezes seguidas (motivo temporário, "
                            f"tipo limite de engajamento ou saldo) — parando de reenviar "
                            f"automaticamente. Precisa de verificação manual.")
                        print(f"[followup_dias] {tag} falhou 3x seguidas deal={deal_id} — "
                              f"parou de reenviar, precisa de verificação manual", flush=True)
                        continue
                    print(f"[followup_dias] {tag} tinha falhado (mensagem anterior não entregue) "
                          f"deal={deal_id} — tentando reenviar", flush=True)
                    enviado, _msg_id = enviar_followup_dia(conversation_id, deal_id, phone, tag, nome)
                    if not enviado:
                        continue
                    print(f"[followup_dias] {tag} reenviado deal={deal_id} — aguardando nova "
                          f"confirmação de entrega antes de avançar etapa", flush=True)
                    continue
                else:
                    print(f"[followup_dias] Aguardando confirmação de entrega do {tag} "
                          f"(status={status_entrega}) deal={deal_id} — não reenvia, "
                          f"tenta de novo na próxima varredura", flush=True)
                    continue
                print(f"[followup_dias] {tag} confirmado como entregue deal={deal_id} — avançando etapa", flush=True)
            else:
                enviado, _msg_id = enviar_followup_dia(conversation_id, deal_id, phone, tag, nome)
                if not enviado:
                    continue
                print(f"[followup_dias] {tag} enviado deal={deal_id} — aguardando confirmação de "
                      f"entrega antes de avançar etapa (verifica na próxima varredura)", flush=True)
                continue

            enviados += 1
            resolver_conversa_agendorchat(conversation_id)

            if proxima_etapa:
                if mover_etapa_funil_comercial(deal_id, proxima_etapa):
                    # Marca essa entrada como progressão NORMAL do robô — usado
                    # por dias_desde_referencia (25/08) pra distinguir de um
                    # retorno manual, que deveria reiniciar o relógio.
                    send_private_note(conversation_id,
                        f"🔁 Régua avançou automaticamente para esta etapa. [followup:etapa_normal:{proxima_etapa}]")
            else:
                # D+10 na 5° Contato (D7) — última tentativa esgotada.
                # Para os negócios NOVOS, já grava endTime com a data real da
                # task D10. Se a API de tasks falhar pontualmente, usa o
                # momento atual — aqui isso é seguro porque estamos justamente
                # formalizando a perda agora, não reconstruindo um caso antigo.
                end_time_d10 = data_historica_d10_da_task(deal_fresco)
                if not end_time_d10:
                    end_time_d10 = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    print(f"[followup_dias] Task D10 não encontrada no momento da perda deal={deal_id}; "
                          f"usando horário atual como endTime={end_time_d10}", flush=True)

                if not humano_ja_atendeu_alguma_vez(conversation_id):
                    if marcar_perdido_sem_contato(deal_id, end_time=end_time_d10):
                        send_private_note(conversation_id,
                            "🔁 Follow-up D10 esgotado, sem intervenção humana em nenhum momento "
                            "— negócio marcado PERDIDO - SEM RETORNO.")
                else:
                    # Já teve contato humano em algum momento, mas não fechou —
                    # move pra "Perdido" genérica (não "sem contato", porque
                    # teve contato sim). Evita também que o negócio fique
                    # parado em 5º Contato pra sempre, batendo a mesma regra
                    # D10 de novo na próxima rodada (decisão de [gestor], 13/08).
                    if mover_etapa_funil_comercial(deal_id, ETAPA_PERDIDO_GENERICO):
                        marcar_negocio_perdido(deal_id, LOSS_REASON_LEAD_PAROU, end_time=end_time_d10)
                        send_private_note(conversation_id,
                            "🔁 Follow-up D10 esgotado. Já teve contato humano em algum momento, "
                            "mas não fechou — negócio marcado PERDIDO (genérico).")
                    print(f"[followup_dias] D10 enviado, humano já interveio — movido pra "
                          f"Perdido genérico, deal={deal_id}", flush=True)

        except Exception as e:
            print(f"[followup_dias] Erro deal={deal_id}: {e}", flush=True)

    print(f"[followup_dias] varredura concluída: {len(candidatos)} candidatos, "
          f"{enviados} follow-ups enviados", flush=True)


def verificar_followup_dias_silencio_safe():
    try:
        verificar_followup_dias_silencio()
    except Exception as e:
        print(f"[followup_dias] Erro geral na varredura: {e}", flush=True)


def limpar_memoria_conversas_inativas():
    """Remove da RAM conversas sem atividade há 48h e locks órfãos.

    Não apaga histórico no AgendorChat. Se o contato voltar depois, o fluxo
    normal recupera o histórico remoto. A janela de 48h preserva com folga
    todas as varreduras/follow-ups de curto prazo que dependem da memória.
    """
    agora = time.time()
    limite = 48 * 3600
    removidas = 0
    for conv_key in list(conversation_histories.keys()):
        conv = conversation_histories.get(conv_key) or {}
        last_msg_at = conv.get("last_msg_at") or 0
        if last_msg_at and agora - last_msg_at > limite:
            conversation_histories.pop(conv_key, None)
            with _conv_response_locks_guard:
                _conv_response_locks.pop(conv_key, None)
            removidas += 1
    if removidas:
        print(f"[memoria] conversas_inativas_removidas={removidas} "
              f"ativas_em_ram={len(conversation_histories)} locks={len(_conv_response_locks)}", flush=True)


def limpar_memoria_conversas_inativas_safe():
    try:
        limpar_memoria_conversas_inativas()
    except Exception as e:
        print(f"[memoria] erro na limpeza: {e}", flush=True)


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULER + MAIN
# ═════════════════════════════════════════════════════════════════════════════

scheduler = BackgroundScheduler()
scheduler.add_job(fetch_deals_safe, "interval", hours=1, id="fetch_recorrente")
scheduler.add_job(fetch_tasks_job, "interval", hours=2, id="tasks_recorrente")
scheduler.add_job(varredura_lembretes_safe, "interval", minutes=15, id="lembretes_reuniao")
def log_resumo_usage():
    """Imprime no log um resumo do consumo de tokens acumulado até agora
    (desde o último boot), com custo estimado em USD.

    Corrigido 24/08: 'classificacao' (Haiku 4.5) usa uma tabela de preço
    BEM mais barata que Sonnet — usar a tabela do Sonnet pra essa categoria
    inflaria o número artificialmente e esconderia a economia real da
    troca. ATENÇÃO [gestor]: os valores de PRECO_HAIKU_* abaixo são minha
    melhor referência, mas não tenho 100% de certeza que batem exatamente
    com a tabela atual da Anthropic — vale conferir rapidinho em
    console.anthropic.com (ou docs.claude.com/pricing) antes de usar esse
    número pra decisão financeira importante. Os preços do Sonnet
    (PRECO_INPUT etc.) esses sim já confirmamos antes."""
    PRECO_INPUT       = 3.00  / 1_000_000
    PRECO_OUTPUT       = 15.00 / 1_000_000
    PRECO_CACHE_WRITE = 3.75  / 1_000_000
    PRECO_CACHE_READ  = 0.30  / 1_000_000

    # Haiku 4.5 — CONFIRME em docs.claude.com/pricing antes de confiar 100%
    PRECO_HAIKU_INPUT       = 1.00  / 1_000_000
    PRECO_HAIKU_OUTPUT      = 5.00  / 1_000_000
    PRECO_HAIKU_CACHE_WRITE = 1.25  / 1_000_000
    PRECO_HAIKU_CACHE_READ  = 0.10  / 1_000_000

    total_usd = 0.0
    for tipo, s in USAGE_STATS.items():
        if tipo == "classificacao":
            custo = (s["input"] * PRECO_HAIKU_INPUT + s["output"] * PRECO_HAIKU_OUTPUT
                      + s["cache_write"] * PRECO_HAIKU_CACHE_WRITE + s["cache_read"] * PRECO_HAIKU_CACHE_READ)
        else:
            custo = (s["input"] * PRECO_INPUT + s["output"] * PRECO_OUTPUT
                      + s["cache_write"] * PRECO_CACHE_WRITE + s["cache_read"] * PRECO_CACHE_READ)
        total_usd += custo
        print(f"[usage-hora] tipo={tipo} chamadas={s['chamadas']} input={s['input']} "
              f"output={s['output']} cache_read={s['cache_read']} cache_write={s['cache_write']} "
              f"custo_estimado=${custo:.4f}", flush=True)
    print(f"[usage-hora] TOTAL desde o boot: ${total_usd:.4f}", flush=True)


scheduler.add_job(mover_novos_leads_para_1contato_safe, "interval", minutes=15, id="mover_novos_leads")
scheduler.add_job(log_resumo_usage, "interval", hours=1, id="usage_resumo_horario")
scheduler.add_job(limpar_memoria_conversas_inativas_safe, "interval", hours=6, id="limpeza_memoria_conversas")
scheduler.add_job(verificar_followup_1h_silencio_safe, "interval", minutes=15, id="followup_1h_silencio")
scheduler.add_job(verificar_followup_4h_silencio_humano_safe, "interval", minutes=15, id="followup_4h_silencio_humano")
scheduler.add_job(verificar_retomada_apos_silencio_humano_safe, "interval", minutes=15, id="retomada_apos_silencio_humano")
scheduler.add_job(verificar_leads_parados_safe, "interval", minutes=15, id="leads_parados")
scheduler.add_job(verificar_followup_dias_silencio_safe, "interval", hours=3, id="followup_dias_silencio")
scheduler.add_job(verificar_perdidos_d10_travados_safe, "interval", hours=3, id="reconciliar_perdidos_d10")
scheduler.add_job(verificar_perdidos_d10_travados_safe, "date", run_date=datetime.now() + timedelta(minutes=2), id="reconciliar_perdidos_d10_inicial")
scheduler.add_job(fetch_deals_safe, "date", run_date=datetime.now() + timedelta(seconds=5), id="fetch_inicial")
scheduler.add_job(_rd_retro_auto_retomar_se_necessario, "interval", minutes=5, id="rd_retro_auto_retomada")
scheduler.add_job(_rd_retro_auto_retomar_se_necessario, "date", run_date=datetime.now() + timedelta(minutes=10), id="rd_retro_auto_retomada_inicial")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
