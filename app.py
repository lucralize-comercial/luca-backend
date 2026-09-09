from flask import Flask, jsonify, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
import sharepoint_logger as splog
from datetime import datetime, timedelta, timezone
import requests
import re
import os
import time
import threading
import json

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type"]}})

AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN", "")  # CONFIGURE via variável de ambiente
AGENDOR_BASE = "https://api.agendor.com.br/v3"
HEADERS = {"Authorization": f"Token {AGENDOR_TOKEN}"}
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

Planos: Essencial (até 15k/mês, a partir de R$147/mês), Exclusivo (até 35k/mês), Plus (até 100k/mês). Pode informar o valor inicial "a partir de R$147/mês" como âncora SOMENTE quando o lead perguntar diretamente sobre preço, não se antecipe oferecendo esse valor por conta própria ao conectar benefícios ou responder outras dúvidas. NUNCA informe os valores exatos dos planos Exclusivo e Plus, nem o valor final que o lead pagaria (isso varia por perfil e o especialista detalha na reunião).

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
- Lead em momento incerto (aguardando contrato, decisão, etc.): não force o agendamento. Use: "O que eu sugiro: vamos te deixar aqui em nosso acompanhamento. Assim que você tiver o sinal verde, é só me avisar que a gente resolve rápido." NUNCA diga "lista de espera". Após esse encerramento, NÃO faça mais nenhuma pergunta. Deixe a conversa terminar naturalmente.

PEDIDO PRA FALAR COM ATENDENTE/HUMANO (regra crítica, NÃO é uma objeção a contornar):
Corrigido 01/09 (caso real: lead frustrado com uma confusão de horário pediu "falar com atendente humano" TRÊS vezes antes do Luca parar de insistir em resolver sozinho — isso piorou a frustração numa hora crítica).
Diferente das resistências acima, aqui a regra é: quanto mais direto e urgente o pedido, MENOS você resiste.
- Primeira vez que o lead pede, de forma leve/casual, bem no início da conversa (sem sinal de frustração): pode responder no estilo da seção "SE PERGUNTAREM SE VOCÊ É IA" — explicar que o especialista humano é justamente quem ele vai encontrar na reunião, e perguntar se prefere continuar com você ou já ser encaminhado. Uma pergunta, não mais que isso.
- Se o lead insistir de novo, repetir o pedido, ou já demonstrar qualquer frustração/insatisfação (reclamação, "péssimo atendimento", tom alterado, maiúsculas, etc.): PARE de oferecer alternativas ou fazer perguntas de qualificação. Confirme em UMA frase curta que está encaminhando, e não pergunte mais nada além do estritamente necessário pro contexto (ex: nunca pergunte de novo se ele quer continuar com você). Isso vale mesmo no meio de um processo (agendamento, reagendamento, etc.) — a prioridade nesse momento é a pessoa, não completar o fluxo.
Use "alguém do nosso time" ou "alguém do time aqui", não "um atendente" nem "um humano" — soa mais natural. Exemplo: "Claro, já vou te conectar com alguém do nosso time. Um instante!"
- NUNCA repita a mesma pergunta/oferta depois que o lead já pediu humano mais de uma vez — cada repetição do pedido dele sem handoff de verdade piora a frustração.

RECUSA EXPLÍCITA ("não tenho interesse", "não quero", "não preciso"):
Trate como objeção, não como encerramento automático. Primeiro, tente entender o motivo ou ofereça algo mais leve, sem pressão de agendamento: "Tudo bem! Posso te ajudar com alguma dúvida específica, sem compromisso de marcar nada?"
Se a resposta do lead deixar QUALQUER abertura (ele responde, faz uma pergunta, demonstra curiosidade), continue ajudando normalmente, sem forçar volta ao roteiro de qualificação.
Só recue de verdade quando a recusa for clara e repetida, sem nenhuma abertura. Nesse caso: "Sem problemas, [lead]! Qualquer coisa, estou por aqui." E pare. Não insista, não pergunte de novo sobre segmento ou agendamento.
Avalie pelo contexto, não por uma contagem fixa de recusas. O sinal é se a porta ficou aberta ou fechada na resposta do lead.

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
    permissão, etc.), assume disponível — nunca bloqueia o agendamento por
    causa de um erro técnico aqui."""
    try:
        eventos = buscar_eventos_do_dia_organizador(dt_pedido)
    except Exception as e:
        print(f"[disponibilidade] Erro ao buscar agenda real, assumindo disponível: {e}", flush=True)
        return True, []

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
fetch_started_at = None
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
        if page % 10 == 0:
            cache["deals"] = list(all_deals)
            cache["total"] = total_count or len(all_deals)
            cache["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not data.get("links", {}).get("next") or len(page_deals) == 0:
            break
        page += 1
        # Corrigido 03/09: 0.2s não bastava — 429 continuava aparecendo
        # regularmente nas páginas mais pesadas (produção confirmou). Subindo
        # pra 0.4s como medida preventiva, não só reativa (o backoff em
        # fetch_page já cobre o caso de falhar mesmo assim).
        time.sleep(0.4)
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
        due_fmt = due.strftime("%d/%m às %H:%M") if due else "data indefinida"
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


def parse_preferencia_datetime(preferencia: str, tipo: str = "agendamento"):
    """Converte a preferência do lead ('terça às 12h10') em ISO usando o Claude.
    tipo: rótulo pro rastreamento de custo por hora (ver [usage-hora]) —
    "agendamento" quando chamado no fechamento do CRM, "disponibilidade"
    quando chamado na checagem em tempo real de agenda (11/08), pra não
    misturar esse custo com o de "chat" nem esconder ele lá dentro.

    Corrigido 26/08 (bug real, confirmado com o lead [lead]): o
    docstring antigo desta função dizia que o Claude 'já recebe a data
    atual de Brasília no system', mas isso nunca foi implementado de
    verdade — a chamada usava o system padrão (SYSTEM_PROMPT, o persona
    do Luca, sem nenhuma data ou fuso horário). Sem esse ancoramento, o
    Haiku confirmou 14h com o lead na conversa mas devolveu '17:00' no
    ISO — exatamente +3h, o deslocamento BRT->UTC — provavelmente por
    inferir (sem instrução em contrário) que um horário em formato ISO
    deveria estar em UTC. Agora o prompt fixa explicitamente a data/hora
    atual em Brasília E deixa claro que o horário pedido já é local
    (BRT), sem conversão nenhuma."""
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
            "aplicar nenhum deslocamento de fuso. Se a preferência não tiver informação "
            "suficiente para determinar data e hora, responda apenas INDEFINIDA.\n"
            "Responda APENAS o ISO ou INDEFINIDA, nada mais.\n\n"
            f"Preferência: {preferencia}"
        )
        resp = call_claude([{"role": "user", "content": prompt}], max_tokens=30, tipo="classificacao",
                           model="claude-haiku-4-5-20251001").strip()
        if "INDEFINIDA" in resp.upper():
            return None
        datetime.strptime(resp[:16], "%Y-%m-%dT%H:%M")
        return resp[:16]
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
    3. Reunião [Luca] atribuída ao dono do negócio (se houver preferência)
    4. Campo personalizado 'Reunião agendada por' = Luca
    5. Move o negócio para a etapa 'Reunião agendada' no Funil Comercial,
       só se ainda estiver numa etapa anterior (nunca rebaixa)"""
    try:
        if conv.get("crm_registrado"):
            return
        phone = conv.get("phone", "")
        if not phone:
            print(f"[crm] Sem telefone na conversa {conversation_id} — registro pulado", flush=True)
            return
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
                return
        deal_id = deal.get("id")

        # ── Guard durável contra restart do processo (bug real: caso [lead],
        # 11/08) ──────────────────────────────────────────────────────────
        # conv["crm_registrado"] é só RAM — se o processo reiniciar (ex:
        # redeploy) entre o registro original e uma retomada da mesma
        # conversa, esse flag reseta e o ciclo inteiro roda de novo: duplica
        # a tarefa de reunião no Agendor E, pior, cria uma SEGUNDA reunião
        # real no Teams e manda um SEGUNDO link pro lead, diferente do que
        # já foi combinado (aconteceu de verdade). A marca da nota (que
        # sobrevive no Agendor, não em RAM) serve de prova durável de que
        # esse ciclo já rodou antes — se existir, pula tudo, sem excecão.
        nota_marcador = f"[luca:nota:{conversation_id}]"
        if deal_tem_marca(deal_id, nota_marcador):
            print(f"[crm] Já registrado antes (marca de nota já existe no Agendor) "
                  f"deal={deal_id} conv={conversation_id} — pulando ciclo inteiro, "
                  f"inclusive criação de reunião no Teams", flush=True)
            conv["crm_registrado"] = True
            return

        # ── Completa nome/e-mail da pessoa no Agendor, se estiverem faltando
        # ou genéricos (ex: nome só do WhatsApp, sem e-mail) ─────────────────
        nome_pessoa = d.get("nome") or contact_name
        email_pessoa = d.get("email", "")
        atualizar_pessoa_se_incompleta(person, nome_pessoa, email_pessoa)

        # ── 1. Nota: resumo do lead ───────────────────────────────────────
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
        if preferencia:
            owner_id = (deal.get("owner") or {}).get("id")
            dt_iso = parse_preferencia_datetime(preferencia)
            owner_id_int = int(owner_id) if owner_id else None
            teams_join_url = None
            if dt_iso:
                dt_pedido = datetime.strptime(dt_iso, "%Y-%m-%dT%H:%M")
                dt_local, ajustado = ajustar_horario_reuniao(dt_pedido, owner_id_int)
                texto_reuniao = ("[Luca] Reunião com especialista — pré-agendada pelo Luca via WhatsApp, "
                                 f"aguardando confirmação do consultor. Preferência do lead: {preferencia}")
                if ajustado:
                    texto_reuniao += (f" (horário ajustado de {dt_pedido.strftime('%H:%M')} para "
                                       f"{dt_local.strftime('%H:%M')} para evitar conflito de agenda)")

                # ── Cria a reunião real no Teams e manda o link pro lead ─────
                # Só quando há data/hora de verdade confirmada (não no
                # fallback "HORÁRIO A CONFIRMAR" do else abaixo) e o lead já
                # deu e-mail — sem e-mail não dá pra convidar ele pro Teams.
                # Falha aqui NUNCA bloqueia o resto do registro no CRM (fica
                # no mesmo fluxo manual de antes: consultor confirma e manda
                # o link depois).
                email_lead = (d.get("email") or "").strip()
                if email_lead:
                    try:
                        linha_negocio = detectar_linha_negocio(d.get("segmento", ""))
                        nome_reuniao = d.get("nome") or contact_name or "Lead"
                        start_teams = dt_local.strftime("%Y-%m-%dT%H:%M:%S")
                        resultado_teams = create_teams_meeting(nome_reuniao, email_lead, start_teams, linha_negocio)
                        teams_join_url = resultado_teams.get("join_url")
                        if teams_join_url:
                            texto_reuniao += f"\nLink da reunião (Teams): {teams_join_url}"
                            print(f"[crm] ✅ Reunião Teams criada deal={deal_id} "
                                  f"linha={linha_negocio} join_url={teams_join_url}", flush=True)
                            mensagem_link = (
                                f"Consegui deixar tudo pronto, {nome_reuniao.split(' ')[0]}! Aqui está o link "
                                f"da nossa videochamada:\n{teams_join_url}\n\nQualquer dúvida antes, estou por aqui."
                            )
                            send_agendorchat_message(conversation_id, remover_travessao(mensagem_link))
                        else:
                            print(f"[crm] Reunião Teams criada mas sem join_url deal={deal_id}", flush=True)
                    except Exception as e:
                        print(f"[crm] Erro ao criar reunião no Teams deal={deal_id}: {e} — "
                              f"seguindo sem o link automático (consultor confirma manualmente)", flush=True)
                        splog.log_erro(
                            etapa="agendamento", erro=e, plataforma="teams",
                            lead_id=deal_id,
                            extra={"Telefone": "", "ConsultorId": str(owner_id_int or "")},
                        )
                else:
                    print(f"[crm] Sem e-mail do lead — não foi possível criar reunião automática "
                          f"no Teams deal={deal_id} (consultor confirma manualmente)", flush=True)
                    splog.log_falha_silenciosa(
                        etapa="agendamento",
                        motivo="lead confirmou preferência de horário mas não tem e-mail cadastrado",
                        plataforma="agendor", lead_id=deal_id,
                    )
            else:
                prox = datetime.utcnow() - timedelta(hours=3) + timedelta(days=1)
                while prox.weekday() >= 5:
                    prox += timedelta(days=1)
                dt_pedido = datetime(prox.year, prox.month, prox.day, 9, 0)
                dt_local, ajustado = ajustar_horario_reuniao(dt_pedido, owner_id_int)
                texto_reuniao = ("[Luca] Reunião com especialista — HORÁRIO A CONFIRMAR com o lead. "
                                 f"Preferência informada: {preferencia}")
                if ajustado:
                    texto_reuniao += f" (horário provisório ajustado para {dt_local.strftime('%H:%M')})"
            # IMPORTANTE: a API do Agendor espera o horário LOCAL de Brasília
            # SEM indicação de timezone (nem "Z", nem offset) — ela mesma faz a
            # conversão pra UTC internamente (+3h). Mandar já convertido (com "Z"
            # ou offset) faz a API somar +3h de novo, duplicando o deslocamento
            # e atrasando a reunião em 3h. Confirmado por teste direto na API.
            due = dt_local.strftime("%Y-%m-%dT%H:%M:%S")
            payload_reuniao = {"text": texto_reuniao, "type": "reuniao", "due_date": due}
            if owner_id:
                payload_reuniao["assigned_users"] = [int(owner_id)]
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

            if funil_atual_id == FUNIL_COMERCIAL_ID:
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
        f"Objetivo: {objetivo}",
        f"Motivo do contato: {motivo}",
        f"Principal dúvida: {duvida}",
        f"Dor identificada: {dor}",
        f"Urgência: {urgencia}",
        f"Próxima ação esperada: {proxima_acao}",
        f"Resumo da conversa: {resumo_conversa}",
    ]
    note = "\n".join(lines)
    note += f"\n\nStatus: {status}"
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
  "resumo_conversa": "",
  "status": ""
}}

Campos de DADOS DO LEAD (extração direta, factual):
"empresa_situacao" = se o lead já tem CNPJ aberto ou vai abrir (ex: "Já possui CNPJ", "Vai abrir novo CNPJ", "Segundo CNPJ").
"faturamento_aproximado" = valor ou faixa que o lead mencionou (ex: "~R$8mil/mês"). Só se ele disse um número, nunca estime.
"contador_atual" = "Sim" ou "Não" se o lead mencionou ter contador atualmente; inclua o motivo de troca só se ele disse explicitamente (ex: "Sim, mas contador demora pra responder").

Campos de INTELIGÊNCIA COMERCIAL (exigem mais cuidado — só preencha com evidência clara e literal na conversa):
"objetivo" = o RESULTADO que o lead espera alcançar (ex: "Abrir um CNPJ", "Trocar de contabilidade", "Reduzir carga tributária", "Entender melhor enquadramento").
"necessidade" = o MOTIVO/gatilho que levou o lead a procurar a Lucralize agora (ex: "Cliente passou a exigir nota fiscal", "Contador demora pra responder"). Diferente de "objetivo": motivo é a causa, objetivo é o resultado desejado.
"duvida_principal" = a dúvida ou preocupação específica que o lead levantou (ex: "quanto vai pagar de imposto").
"dor_identificada" = só preencha se o lead expressou uma insatisfação ou problema de forma EXPLÍCITA (ex: lead disse "meu contador nunca responde"). NUNCA infira dor a partir do tom geral da conversa — se não houver uma frase clara indicando isso, deixe em branco.
"urgencia" = "Alta", "Média" ou "Baixa" — só preencha se houver sinal EXPLÍCITO de prazo/pressa (ex: lead disse "preciso disso essa semana" = Alta). Sem sinal claro de tempo, deixe em branco — não deduza urgência pelo tom.
"proxima_acao_consultor" = uma sugestão curta e concreta do que o consultor deveria fazer na reunião (ex: "Simular tributação com faturamento de 8k/mês", "Explicar processo de migração"), baseada só no que já foi discutido — não invente uma ação genérica se não houver base clara na conversa.
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
        extra_disponibilidade = ""
        if parece_ter_horario(message_text):
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
                                f"não pode em nenhuma das duas, aceite o horário original mesmo assim, "
                                f"sem insistir mais."
                            )
                        else:
                            extra_disponibilidade = (
                                f"\n\nATENÇÃO (checagem real de agenda, não mencione isso ao lead): não "
                                f"achei horário livre nesse dia pra sugerir. Aceite a preferência do lead "
                                f"normalmente."
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
            if conv.get("note_sent"):
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

                # Detecta encerramento por acompanhamento
                termos_encerramento = ["acompanhamento", "sinal verde", "é só me avisar", "estou por aqui"]
                conversa_encerrada = any(t in reply.lower() for t in termos_encerramento)

                if (dados_completos or conversa_encerrada) and not conv.get("note_sent") and not conv.get("modo_demo"):
                    d["telefone"] = conv.get("phone") or d.get("telefone", "Não informado")
                    note_text = build_lead_note(d)
                    send_private_note(conversation_id, note_text)
                    conv["note_sent"] = True
                    print(f"[note] Nota enviada conv={conversation_id} | completo={dados_completos} | encerrado={conversa_encerrada}", flush=True)
                    # Fecha o ciclo no CRM: nota, transcrição, reunião [Luca] e campo
                    registrar_no_crm(conv, conversation_id, contact_name)

                    # Corrigido em 18/08 (bug real, confirmado em produção): o
                    # campo "status" às vezes vem "Perdido para concorrente"
                    # (a IA reconhece a perda pelo texto, ex: "já segui com
                    # outra empresa"), mas isso ficava só escrito no
                    # resumo/nota — nada movia a etapa de verdade no funil
                    # (caso real: [lead], deal=44854914, 18/08, ficou
                    # preso em "Novo Lead" mesmo marcado como perdido na
                    # nota). Agora, se o status indicar perda, move pra
                    # "Perdido" genérico de verdade.
                    status_extraido = (d.get("status") or "").strip().lower()
                    if status_extraido.startswith("perdido"):
                        try:
                            _, deal_perdido = buscar_pessoa_e_negocio(d["telefone"])
                            if deal_perdido and deal_perdido.get("id"):
                                if mover_etapa_funil_comercial(deal_perdido["id"], ETAPA_PERDIDO_GENERICO):
                                    print(f"[note] Status indica perda — negócio movido pra "
                                          f"Perdido genérico deal={deal_perdido['id']}", flush=True)
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
            # Corrigido 22/08 (bug real, confirmado em produção): o Chatwoot
            # dispara 'message_updated' MAIS DE UMA VEZ pra mesma mensagem
            # (ex: uma vez ao marcar como falha, outra ao anexar o motivo do
            # erro) — sem essa trava, a mesma falha gerava nota duplicada
            # na conversa a cada disparo (visto 2-3x seguidas no mesmo msg).
            marcador_falha = f"[msg_falha:{msg_id}]"
            msgs = mensagens_da_conversa(conversation_id)
            if marcador_existe(msgs, marcador_falha):
                return jsonify({}), 200
            msg_atual = next((m for m in msgs if m.get("id") == msg_id), None)
            if not msg_atual or msg_atual.get("status") != "failed":
                return jsonify({}), 200
            motivo = (msg_atual.get("content_attributes") or {}).get("external_error", "motivo não informado")
            meta_sender = (conversation.get("meta") or {}).get("sender") or {}
            phone = meta_sender.get("phone_number", "")
            print(f"[msg_falhou] Mensagem falhou conv={conversation_id} msg={msg_id} "
                  f"phone={phone} motivo={motivo}", flush=True)
            if phone:
                _, deal = buscar_pessoa_e_negocio(phone)
                if deal:
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

                        # Corrigido 31/08 (bug real, caso fsantoslima825): a
                        # checagem acima só via "é recente?", sem checar se o
                        # Luca já tinha respondido ALGUMA coisa de verdade
                        # antes. Se essa saudação atrasada cai bem na
                        # PRIMEIRA mensagem do lead (ainda dentro do delay de
                        # 90s antes do Luca responder pela 1ª vez), dizer
                        # "continue com sua última pergunta" não faz sentido
                        # — não existe conversa em andamento ainda pra
                        # continuar, e a pessoa fica sem resposta nenhuma até
                        # o Luca responder de verdade. Nesse caso, usa um
                        # texto que só confirma o recebimento, sem sugerir
                        # que já tinha algo rolando.
                        ja_teve_resposta_real_do_luca = any(
                            m.get("message_type") == 1 and not m.get("private")
                            and not (m.get("additional_attributes") or {}).get("automation_id")
                            for m in msgs_previas
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
                # Corrigido 01/09 (bug real, caso [lead]): content vazio/null
                # geralmente é reação de emoji ou atualização de status, não
                # uma mensagem de verdade — sem esse guard, o classificador
                # recebia string vazia e (por segurança, no erro) tendia a
                # dizer "precisa de resposta", ligando o timer de 4h à toa.
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
        # Corrigido 25/08 ([gestor]): não depende mais de haver alguém
        # EXPLICITAMENTE atribuído — o que importa é só quando foi a
        # última mensagem humana genuína, não quem está no campo
        # "assignee". Dá 1h de folga pro humano continuar sem o Luca
        # atropelar; depois disso, Luca retoma normalmente.
        segundos_humano = segundos_desde_ultima_intervencao_humana(conversation_id)
        if segundos_humano is not None and segundos_humano < 3600:
            print(f"[webhook] IGNORADO — humano escreveu há {int(segundos_humano)}s, "
                  f"dentro da janela de 1h conv={conversation_id}", flush=True)
            # Corrigido 01/09 (bug real, caso [lead] — atraso de 7h): sem
            # isso, essa conversa fica marcada was_resolved=True pra sempre
            # (se um humano tinha resolvido antes), e a rede de segurança
            # (verificar_retomada_apos_silencio_humano) pula ela indefinida-
            # mente por causa desse mesmo flag — o retorno do Luca só
            # acontecia se algum evento externo do AgendorChat (ex:
            # desatribuição automática por inatividade) disparasse de novo
            # o conversation_updated. Agora, mesmo ficando quieto aqui,
            # garante que a conversa fica "viva" pra rede de segurança
            # conseguir retomar assim que passar a janela de 1h.
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
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 200

    try:
        body          = request.get_json(force=True) or {}
        lead_name     = body.get("lead_name", "Lead")
        lead_email    = body.get("lead_email", "")
        start         = body.get("start", "")
        linha_negocio = body.get("linha_negocio", "contabilidade")

        if not lead_email or not start:
            return jsonify({"error": "lead_email e start são obrigatórios"}), 400

        result = create_teams_meeting(lead_name, lead_email, start, linha_negocio)
        return jsonify(result), 200

    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 0
        detail = ""
        try:
            detail = e.response.json().get("error", {}).get("message", "")
        except Exception:
            pass
        splog.log_erro(
            etapa="agendamento", erro=e, plataforma="teams",
            extra={"StatusHTTP": str(status), "Detalhe": detail},
        )
        if status == 403:
            return jsonify({
                "error": "Permissão Calendars.ReadWrite ainda não concedida no Azure AD.",
                "detail": detail,
                "action": "Solicite ao administrador do tenant que conceda a permissão e faça grant de admin consent."
            }), 503
        return jsonify({"error": str(e), "detail": detail}), 500

    except Exception as e:
        splog.log_erro(etapa="agendamento", erro=e, plataforma="teams")
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

    Corrigido em 18/08 (bug real, confirmado em produção): um 429
    transitório (limite de taxa do Agendor) caía no except e MESMO ASSIM
    guardava "" no cache PRA SEMPRE — um erro passageiro virava uma marca
    permanente de "sem telefone", e esse lead nunca mais era processado
    certinho pelo follow-up até o container reiniciar (caso real:
    person=70717332, deal=44802711, 18/08). Agora só guarda "" quando a
    API respondeu de verdade (sem 429) e realmente não tinha telefone —
    falha de rede/limite de taxa não polui o cache."""
    if person_id in _phone_cache:
        return _phone_cache[person_id]
    for attempt in range(3):
        try:
            r = requests.get(f"{AGENDOR_BASE}/people/{person_id}", headers=HEADERS, timeout=15)
            if r.status_code == 429:
                raise requests.exceptions.HTTPError(f"429 buscando telefone person={person_id}")
            r.raise_for_status()
            data = r.json().get("data", {}) or {}
            contato = data.get("contact") or {}
            for campo in ("whatsapp", "mobile", "phone", "workPhone"):
                valor = (contato.get(campo) or "").strip()
                if valor:
                    _phone_cache[person_id] = valor
                    return valor
            _phone_cache[person_id] = ""  # resposta válida, mas sem nenhum telefone preenchido
            return ""
        except Exception as e:
            print(f"[lembrete] Tentativa {attempt+1}/3 falhou (telefone) person={person_id}: {e}", flush=True)
            if attempt < 2:
                time.sleep(3)
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
    conversa, sem ele ter agido — caso [lead]/[dono padrão]) de 'humano
    realmente assumiu e está atendendo'.

    Corrigido em 13/08: a versão anterior só olhava mensagens DEPOIS da
    última mensagem do lead — mas se o humano escreveu ANTES dessa última
    mensagem (ex: já vinha conversando, o lead respondeu de novo, e o
    humano ainda não teve tempo de responder essa última), a mensagem dele
    ficava fora da checagem, e o Luca respondia por cima de um atendimento
    humano já em andamento (caso real: [especialista]/[lead], 13/08 — [especialista]
    já tinha se apresentado e feito uma pergunta, mas o Luca respondeu a
    próxima mensagem da lead mesmo assim). Uma vez que um humano de
    verdade escreveu QUALQUER coisa na conversa, ele está no comando —
    olha a conversa inteira, não só o trecho após a última msg do lead.

    Corrigido em 25/08 (achado real de [gestor]): mesmo com a correção
    acima, o Luca continuava respondendo por cima de mensagens que o
    próprio [gestor] escrevia manualmente — porque ele usa a MESMA conta
    que o Luca usa pra enviar (sem licença sobrando pra separar). Agora
    usa a marca invisível (LUCA_MARKER) pra distinguir: uma mensagem da
    conta do [gestor] COM a marca é o próprio Luca (exclui); uma mensagem
    da MESMA conta SEM a marca é o [gestor] digitando de verdade (conta
    como humano). Pra qualquer outra conta (ex: dono padrão do CRM), a
    exclusão continua sendo total, já que o Luca nunca envia por ela.

    Corrigido em 27/08 (bug crítico, achado por revisão externa da
    documentação, nunca confirmado em produção mas plausível pelo código):
    a régua D1/D3/D5/D7/D10 e os lembretes de reunião são enviados via
    enviar_template_conversa, NÃO via send_agendorchat_message — logo
    NUNCA levam LUCA_MARKER (não dá pra adicionar caractere a um template
    aprovado sem quebrar o conteúdo aprovado pela Meta). Sem essa
    distinção, TODO envio de template pela conta do Luca era lido como
    '[gestor] escreveu manualmente', fazendo humano_realmente_respondeu
    retornar True logo depois de qualquer follow-up automático ser
    enviado — o que faria o Luca se calar sozinho, ou o D10 classificar
    negócios sem contato real como 'já teve contato humano'. Agora,
    mensagem da conta do Luca com template_params (havendo marca ou não)
    também conta como o próprio bot, não como humano.

    Corrigido em 01/09 (bug real, caso [lead]: D1 enviado às 10:26, conta
    do [gestor]/Luca 'concluiu o atendimento' logo em seguida — esse
    evento de resolução aparentemente gera um registro na conversa sem
    conteúdo de texto, sem marca e sem template_params, e por isso
    passava pelos dois filtros acima como se fosse o [gestor] escrevendo
    de verdade. Resultado: o Luca ficou 1h+ sem responder o [lead] mesmo
    ele reengajando com 'Oi, pode mostrar' depois do D1). Mensagem sem
    conteúdo nenhum nunca conta como intervenção humana — não tem o que
    ter sido "escrito" ali."""
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
                splog.log_falha_silenciosa(
                    etapa="primeiro_contato", motivo="deal sem person_id vinculado",
                    plataforma="agendor", lead_id=deal_id,
                )
                continue
            telefone = telefone_da_pessoa(person_id)
            if not telefone:
                print(f"[novo_lead] Pulado — sem telefone person={person_id} deal={deal_id}", flush=True)
                splog.log_falha_silenciosa(
                    etapa="primeiro_contato", motivo="pessoa sem telefone cadastrado",
                    plataforma="agendor", lead_id=deal_id,
                    extra={"PersonId": str(person_id)},
                )
                continue
            conv = conversa_do_telefone(telefone)
            if not conv:
                print(f"[novo_lead] Pulado — conversa não encontrada telefone={telefone} deal={deal_id}", flush=True)
                splog.log_falha_silenciosa(
                    etapa="primeiro_contato", motivo="nenhuma conversa encontrada no Chatwoot para este telefone",
                    plataforma="agendorchat", lead_id=deal_id, telefone=telefone,
                )
                continue
            msgs = mensagens_da_conversa(conv["id"])
            # Aceita QUALQUER mensagem enviada (não só as com automation_id
            # no additional_attributes) — descoberto em 13/08, corrigido
            # depois de confirmar com [gestor]: é a MESMA automação nativa
            # de sempre (41882, "Novo Lead -> Enviar WhatsApp"), não uma
            # automação diferente. O problema é que o campo automation_id
            # aparece no payload do webhook em tempo real, mas não vem
            # preenchido do mesmo jeito quando a mensagem é buscada depois
            # via histórico (endpoint diferente da mesma plataforma) — caso
            # real: Sandra Carina, deal=44745236, saudação confirmada
            # enviada na tela do CRM, mas a checagem antiga não reconhecia
            # por exigir esse campo específico no histórico.
            saudacao_enviada = any(
                m.get("message_type") == 1 and not m.get("private")
                for m in msgs
            )
            if not saudacao_enviada:
                print(f"[novo_lead] Pulado — saudação ainda não confirmada na conversa "
                      f"deal={deal_id}", flush=True)
                # Cada varredura roda a cada 15 min; se este deal aparecer aqui
                # de novo em varreduras seguintes, o Ocorrencias na lista do
                # SharePoint sobe — Ocorrencias alto é o sinal de que a
                # automação nativa do Agendor não disparou pra este lead
                # (1 ocorrência isolada é normal, é só o deal ainda não ter
                # sido processado pela automação).
                splog.log_falha_silenciosa(
                    etapa="primeiro_contato",
                    motivo="saudação automática do Agendor ainda não confirmada na conversa",
                    plataforma="agendorchat", lead_id=deal_id, telefone=telefone,
                )
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

# (etapa atual, dias corridos desde a criação do negócio pra disparar, rótulo, próxima etapa ou None)
FOLLOWUP_REGRAS = [
    (ETAPA_1_CONTATO, 1, "D1", ETAPA_2_CONTATO),
    (ETAPA_2_CONTATO,  3, "D3", ETAPA_3_CONTATO),
    (ETAPA_3_CONTATO,  5, "D5", ETAPA_4_CONTATO_D5),
    (ETAPA_4_CONTATO_D5, 7, "D7", ETAPA_5_CONTATO_D7),
    (ETAPA_5_CONTATO_D7, 10, "D10", None),  # None = última tentativa, sem próxima etapa
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

    Corrigido em 19/08 (bug real, confirmado em produção): a checagem
    olhava só QUEM enviou (excluindo Luca/Luiz), mas não olhava se a
    MENSAGEM em si era um template automático disparado por outra conta
    real (ex: [especialista]) — como 'prosseguimento_solicitacao' ou
    'retomada_de_contato_everton'. Um template automático não é uma
    intervenção humana de verdade, mesmo saindo pela conta de um vendedor
    real (caso real: [lead], deal ligado a person=70521617,
    o template 'prosseguimento_solicitacao' saiu pela conta do [especialista] e
    fez esse negócio nunca fechar como 'sem contato', mesmo sem nenhuma
    palavra escrita de verdade por ninguém).

    Corrigido em 25/08: mesmo esquema do humano_realmente_respondeu — usa
    LUCA_MARKER pra distinguir mensagem da conta do [gestor] enviada pelo
    Luca (com marca) de mensagem que o [gestor] escreveu manualmente pela
    mesma conta (sem marca, conta como humano de verdade)."""
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


def marcar_perdido_sem_contato(deal_id: int) -> bool:
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
    return mover_etapa_funil_comercial(deal_id, ETAPA_PERDIDO_SEM_CONTATO)


def enviar_followup_dia(conversation_id, deal_id, phone, tag, nome) -> bool:
    """Envia o nudge de silêncio (D1/D3/D5). Por definição a janela de 24h
    do WhatsApp certamente está fechada (o lead está silencioso há 1+ dia),
    então isso SEMPRE usa template aprovado da Meta, nunca mensagem livre.

    Só usa os templates Tech (12/08, decisão de [gestor]: manter só Tech por
    ora, os da Contabilidade não foram escritos e não serão usados agora).

    ⚠️ Os templates abaixo (followup_silencio_d1/d3/d5_tech) já existem e
    estão aprovados no Meta Business Suite (confirmado 13/08).

    Corrigido em 13/08 (bug real, grave): o campo 'content' que a gente
    manda pro AgendorChat é o que realmente chega pro lead no WhatsApp —
    não é só um resumo pra exibição interna do CRM, como eu tinha
    assumido errado antes. A versão anterior mandava um texto curto de
    prévia ali (ex: "Ainda por aqui, se quiser retomar é só me chamar.")
    em vez do texto completo do template aprovado — confirmado no caso
    real do Ricardo Ribeiro (D3), que recebeu essa frase curta genérica
    em vez da mensagem de verdade que fechamos com o [gestor]. Agora
    manda o texto completo, igual ao que está aprovado no Meta."""
    nome_template = {"D1": "followup_silencio_d1_tech",
                      "D3": "followup_silencio_d3_tech",
                      "D5": "followup_silencio_d5_tech",
                      "D7": "followup_silencio_d7_tech",
                      "D10": "followup_silencio_d10_tech"}[tag]

    # Corrigido em 19/08 (bug real, confirmado em produção), estendido em
    # 25/08 quando a régua ganhou D7 e D10: pra última tentativa (hoje
    # D10, antes era D5), não existe próxima etapa "natural" pra saída
    # automática — se a mudança de etapa (mover_etapa_funil_comercial)
    # falhar por qualquer motivo depois do envio, o negócio fica preso e
    # a régua bate de novo dias depois, reenviando a MESMA mensagem final
    # (caso real: [lead], D5 enviado em 14/08 e de novo em
    # 19/08 — 5 dias de diferença, na época em que D5 era a última). Um
    # marcador único na própria conversa garante que a última tentativa
    # NUNCA sai duas vezes, mesmo que a etapa não tenha mudado com sucesso.
    if tag == "D10":
        marcador_d10 = "[followup:d10_enviado]"
        try:
            msgs_existentes = mensagens_da_conversa(conversation_id)
            if marcador_existe(msgs_existentes, marcador_d10):
                print(f"[followup_dias] D10 já tinha sido enviado antes (marcador encontrado) "
                      f"conv={conversation_id} deal={deal_id} — não reenvia", flush=True)
                return False
        except Exception as e:
            print(f"[followup_dias] Erro ao checar marcador D10 conv={conversation_id}: {e}", flush=True)

    tpl = template_por_nome(nome_template)
    if not tpl:
        send_private_note(conversation_id, (
            f"🔁 Follow-up {tag} NÃO enviado — template '{nome_template}' não encontrado/aprovado "
            f"no Meta Business Suite."))
        print(f"[followup_dias] {tag} SEM TEMPLATE conv={conversation_id}", flush=True)
        return False

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
    enviar_template_conversa(conversation_id, tpl, {"1": nome_saudacao}, texto_completo)
    marcador_nota = " [followup:d10_enviado]" if tag == "D10" else ""
    send_private_note(conversation_id, f"🔁 Follow-up {tag} enviado ao lead via template.{marcador_nota}")
    espelho_crm(deal_id, f"🤖 Follow-up automático ({tag}) enviado ao lead — silêncio de {tag[1:]} dia(s).")
    print(f"[followup_dias] ✅ {tag} enviado conv={conversation_id} deal={deal_id}", flush=True)
    return True


def dias_desde_referencia(deal_fresco: dict, etapa_atual_id: int, conversation_id: int, deal_id: int):
    """Retorna quantos dias completos se passaram desde a referência
    correta pra contar a régua de silêncio nesta etapa.

    Normalmente é a CRIAÇÃO do negócio (startTime) — decisão confirmada
    com [gestor] em 05/08. Mas se a entrada na etapa atual NÃO veio da
    progressão normal do próprio robô (marcador [followup:etapa_normal:
    {id}] ausente na conversa), é sinal de que o negócio foi movido
    manualmente de volta pra essa etapa (ex: respondeu depois de
    'Contato Retornado', e [gestor] trouxe de volta pra '3° Contato' pra
    reativar a régua) — criado 25/08, a pedido de [gestor]. Nesse caso, o
    relógio reinicia a partir de AGORA (a primeira vez que o robô nota a
    entrada manual), guardando um marcador
    [followup:regua_reiniciada:{timestamp}] pra usar o mesmo ponto de
    partida em passagens futuras, sem reiniciar de novo a cada varredura.

    Retorna None se não for possível calcular (sem startTime, por ex.)."""
    marcador_normal = f"[followup:etapa_normal:{etapa_atual_id}]"
    try:
        msgs = mensagens_da_conversa(conversation_id)
    except Exception as e:
        print(f"[followup_dias] Erro ao ler conversa pra checar reset deal={deal_id}: {e}", flush=True)
        msgs = None

    entrada_normal = True if msgs is None else marcador_existe(msgs, marcador_normal)

    if entrada_normal or etapa_atual_id == ETAPA_1_CONTATO:
        start_time = deal_fresco.get("startTime")
        if not start_time:
            return None
        try:
            criado_em = datetime.strptime(start_time[:10], "%Y-%m-%d")
        except Exception:
            return None
        return (datetime.utcnow() - criado_em).days

    # Entrada manual detectada — procura marcador de reset já existente
    marcador_reset_prefix = "[followup:regua_reiniciada:"
    if msgs:
        for m in msgs:
            content = m.get("content") or ""
            if marcador_reset_prefix in content:
                try:
                    ts_str = content.split(marcador_reset_prefix)[1].split("]")[0]
                    reset_em = datetime.utcfromtimestamp(float(ts_str))
                    return (datetime.utcnow() - reset_em).days
                except Exception:
                    continue  # marcador corrompido, tenta o próximo

    # Nenhum marcador de reset ainda — cria um agora, dia 0
    try:
        send_private_note(conversation_id, (
            f"🔁 Retorno manual detectado nesta etapa — reiniciando a contagem de dias "
            f"da régua de silêncio a partir de agora. {marcador_reset_prefix}{time.time()}]"))
    except Exception as e:
        print(f"[followup_dias] Erro ao criar marcador de reset deal={deal_id}: {e}", flush=True)
    return 0


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

            # Relógio contado a partir da CRIAÇÃO do negócio (startTime), não
            # do silêncio do lead — confirmado com [gestor] em 05/08. Ex.:
            # criado segunda 03/08 -> D+1 dispara terça 04/08. EXCETO se a
            # entrada na etapa atual foi um retorno manual ([gestor] movendo
            # de volta), caso em que o relógio reinicia a partir de agora —
            # ver dias_desde_referencia (25/08).
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

            enviado = enviar_followup_dia(conversation_id, deal_id, phone, tag, nome)
            if not enviado:
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
                # D+10 na 5° Contato (D7) — última tentativa esgotada
                if not humano_ja_atendeu_alguma_vez(conversation_id):
                    if marcar_perdido_sem_contato(deal_id):
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
scheduler.add_job(verificar_followup_1h_silencio_safe, "interval", minutes=15, id="followup_1h_silencio")
scheduler.add_job(verificar_followup_4h_silencio_humano_safe, "interval", minutes=15, id="followup_4h_silencio_humano")
scheduler.add_job(verificar_retomada_apos_silencio_humano_safe, "interval", minutes=15, id="retomada_apos_silencio_humano")
scheduler.add_job(verificar_leads_parados_safe, "interval", minutes=15, id="leads_parados")
scheduler.add_job(verificar_followup_dias_silencio_safe, "interval", hours=3, id="followup_dias_silencio")
scheduler.add_job(fetch_deals_safe, "date", run_date=datetime.now() + timedelta(seconds=5), id="fetch_inicial")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
