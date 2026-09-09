"""
sharepoint_logger.py — registra erros do Luca numa lista do SharePoint via Microsoft Graph.

Princípios:
  - Fire-and-forget: nunca bloqueia nem levanta exceção no chamador.
  - Agrupa por fingerprint: rajadas de retry incrementam um contador em vez de
    criar N itens (evita throttling do Graph e lista poluída).
  - Autenticação app-only (client credentials), sem usuário logado.

Uso:
    from sharepoint_logger import log_erro

    try:
        enviar_whatsapp(lead)
    except Exception as e:
        log_erro(
            etapa="envio_whatsapp",
            plataforma="whatsapp",
            erro=e,
            lead_id=lead.get("id"),
            telefone=lead.get("telefone"),
            payload=lead,
        )
        raise   # o logger não muda o fluxo; você decide o que fazer com o erro

Variáveis de ambiente obrigatórias (definir no Railway, nunca no código):
    SPLOG_TENANT_ID
    SPLOG_CLIENT_ID
    SPLOG_CLIENT_SECRET
Opcionais:
    SPLOG_SITE_PATH      default "lucralize.sharepoint.com:/Operacional"
    SPLOG_LIST_NAME      default "Luca - Log de Erros"
    SPLOG_DEDUP_MINUTES  default "15"  (janela de agrupamento por fingerprint)
    SPLOG_ENABLED        default "1"   ("0" desliga sem mexer no código)
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests

_log = logging.getLogger("sharepoint_logger")

GRAPH = "https://graph.microsoft.com/v1.0"
_PAYLOAD_MAX = 30_000          # campo multilinha do SharePoint não é infinito
_MSG_MAX = 255                 # campo de texto simples
_QUEUE_MAX = 500               # backpressure: descarta em vez de crescer sem limite
_HTTP_TIMEOUT = 20


# --------------------------------------------------------------------------
# Fingerprint
# --------------------------------------------------------------------------

_NORM_PATTERNS = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<uuid>"),
    (re.compile(r"\b\d{10,15}\b"), "<num>"),          # telefones, ids longos
    (re.compile(r"0x[0-9a-f]+", re.I), "<hex>"),
    (re.compile(r"\b\d+\b"), "<n>"),
]


def _normalizar(msg: str) -> str:
    """Remove partes variáveis da mensagem para que o mesmo erro colapse num fingerprint."""
    out = msg or ""
    for pat, repl in _NORM_PATTERNS:
        out = pat.sub(repl, out)
    return " ".join(out.split())[:500]


def _fingerprint(etapa: str, tipo_erro: str, mensagem: str) -> str:
    base = f"{etapa}|{tipo_erro}|{_normalizar(mensagem)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------
# Cliente Graph (token + ids em cache)
# --------------------------------------------------------------------------

class _GraphClient:
    def __init__(self) -> None:
        self.tenant = os.environ["SPLOG_TENANT_ID"]
        self.client_id = os.environ["SPLOG_CLIENT_ID"]
        self.secret = os.environ["SPLOG_CLIENT_SECRET"]
        self.site_path = os.getenv("SPLOG_SITE_PATH", "lucralize.sharepoint.com:/Operacional")
        self.list_name = os.getenv("SPLOG_LIST_NAME", "Luca - Log de Erros")

        self._token: str | None = None
        self._token_exp = 0.0
        self._site_id: str | None = None
        self._list_id: str | None = None
        self._session = requests.Session()

    # -- auth ---------------------------------------------------------------

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 120:
            return self._token
        r = self._session.post(
            f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.secret,
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + int(body.get("expires_in", 3600))
        return self._token

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        """Chamada ao Graph com refresh de token em 401 e respeito a Retry-After em 429."""
        for tentativa in range(4):
            headers = kw.pop("headers", {}) or {}
            headers["Authorization"] = f"Bearer {self._get_token()}"
            resp = self._session.request(method, url, headers=headers, timeout=_HTTP_TIMEOUT, **kw)

            if resp.status_code == 401 and tentativa == 0:
                self._token = None          # token velho/revogado: força refresh
                continue
            if resp.status_code in (429, 503):
                espera = int(resp.headers.get("Retry-After", 2 ** tentativa))
                time.sleep(min(espera, 30))
                continue
            return resp
        return resp

    # -- resolução de ids ---------------------------------------------------

    @property
    def site_id(self) -> str:
        if not self._site_id:
            r = self._request("GET", f"{GRAPH}/sites/{self.site_path}")
            r.raise_for_status()
            self._site_id = r.json()["id"]
        return self._site_id

    @property
    def list_id(self) -> str:
        if not self._list_id:
            r = self._request(
                "GET",
                f"{GRAPH}/sites/{self.site_id}/lists",
                params={"$select": "id,displayName", "$top": "200"},
            )
            r.raise_for_status()
            for lst in r.json().get("value", []):
                if lst["displayName"] == self.list_name:
                    self._list_id = lst["id"]
                    break
            if not self._list_id:
                raise RuntimeError(f"Lista '{self.list_name}' não encontrada no site {self.site_path}")
        return self._list_id

    # -- escrita ------------------------------------------------------------

    def _buscar_por_fingerprint(self, fingerprint: str, desde: datetime) -> dict | None:
        """Procura item recente com o mesmo fingerprint. Exige a coluna indexada."""
        r = self._request(
            "GET",
            f"{GRAPH}/sites/{self.site_id}/lists/{self.list_id}/items",
            params={
                "$filter": f"fields/Fingerprint eq '{fingerprint}'",
                "$orderby": "fields/Timestamp desc",
                "$top": "1",
                "$expand": "fields",
            },
            headers={"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"},
        )
        if not r.ok:
            return None
        itens = r.json().get("value", [])
        if not itens:
            return None
        item = itens[0]
        ts = item.get("fields", {}).get("Timestamp")
        if not ts:
            return None
        try:
            visto = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        return item if visto >= desde else None

    def gravar(self, campos: dict, dedup_minutes: int) -> None:
        fingerprint = campos["Fingerprint"]

        if dedup_minutes > 0:
            desde = datetime.now(timezone.utc) - timedelta(minutes=dedup_minutes)
            existente = self._buscar_por_fingerprint(fingerprint, desde)
            if existente:
                atuais = existente.get("fields", {}).get("Ocorrencias") or 1
                patch = self._request(
                    "PATCH",
                    f"{GRAPH}/sites/{self.site_id}/lists/{self.list_id}/items/{existente['id']}/fields",
                    json={
                        "Ocorrencias": int(atuais) + 1,
                        "Timestamp": campos["Timestamp"],
                    },
                )
                if patch.ok:
                    return
                # se o PATCH falhar, cai no POST abaixo — perder o agrupamento
                # é melhor que perder o registro

        resp = self._request(
            "POST",
            f"{GRAPH}/sites/{self.site_id}/lists/{self.list_id}/items",
            json={"fields": campos},
        )
        if not resp.ok:
            raise RuntimeError(f"Graph {resp.status_code}: {resp.text[:300]}")


# --------------------------------------------------------------------------
# Worker em background
# --------------------------------------------------------------------------

_fila: "queue.Queue[dict | None]" = queue.Queue(maxsize=_QUEUE_MAX)
_worker: threading.Thread | None = None
_lock = threading.Lock()
_cliente: _GraphClient | None = None
_descartados = 0


def _loop() -> None:
    global _cliente
    dedup = int(os.getenv("SPLOG_DEDUP_MINUTES", "15"))
    while True:
        campos = _fila.get()
        if campos is None:
            _fila.task_done()
            return
        try:
            if _cliente is None:
                _cliente = _GraphClient()
            _cliente.gravar(campos, dedup)
        except Exception:
            # Falha ao logar nunca escala. Vai pro stdout, que o Railway captura.
            _log.warning("sharepoint_logger falhou:\n%s", traceback.format_exc())
        finally:
            _fila.task_done()


def _garantir_worker() -> None:
    global _worker
    if _worker and _worker.is_alive():
        return
    with _lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, name="sharepoint-logger", daemon=True)
        _worker.start()


def flush(timeout: float = 10.0) -> None:
    """Espera a fila esvaziar. Chame antes de encerrar o processo de propósito."""
    fim = time.time() + timeout
    while not _fila.empty() and time.time() < fim:
        time.sleep(0.1)


atexit.register(flush, 5.0)


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------

def log_erro(
    etapa: str,
    erro: BaseException | str,
    plataforma: str = "",
    lead_id: str | int | None = None,
    telefone: str | None = None,
    payload: object = None,
    extra: dict | None = None,
) -> None:
    """
    Enfileira um erro para gravação no SharePoint. Retorna imediatamente e nunca
    levanta exceção — se algo der errado aqui, o problema fica no stdout.

    etapa: onde no fluxo quebrou. Use valores fixos para o agrupamento funcionar:
        webhook_rd | envio_whatsapp | chamada_claude | criacao_chatwoot
        criacao_agendor | agendamento | followup
    """
    global _descartados
    try:
        if os.getenv("SPLOG_ENABLED", "1") != "1":
            return

        if isinstance(erro, BaseException):
            tipo_erro = type(erro).__name__
            mensagem = str(erro) or tipo_erro
            stack = "".join(traceback.format_exception(type(erro), erro, erro.__traceback__))
        else:
            tipo_erro = "Erro"
            mensagem = str(erro)
            stack = ""

        if payload is not None:
            try:
                corpo = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            except Exception:
                corpo = repr(payload)
        else:
            corpo = ""

        detalhe = "\n\n".join(p for p in (stack, corpo) if p)[:_PAYLOAD_MAX]
        agora = datetime.now(timezone.utc).isoformat()
        fp = _fingerprint(etapa, tipo_erro, mensagem)

        campos = {
            "Title": f"[{etapa}] {tipo_erro}"[:_MSG_MAX],
            "Timestamp": agora,
            "Etapa": etapa,
            "Plataforma": plataforma,
            "TipoErro": tipo_erro,
            "Mensagem": mensagem[:_MSG_MAX],
            "LeadId": str(lead_id) if lead_id is not None else "",
            "Telefone": telefone or "",
            "Payload": detalhe,
            "Fingerprint": fp,
            "Ocorrencias": 1,
        }
        if extra:
            campos.update({k: v for k, v in extra.items() if k not in campos})

        _garantir_worker()
        try:
            _fila.put_nowait(campos)
        except queue.Full:
            _descartados += 1
            if _descartados % 50 == 1:
                _log.warning("sharepoint_logger: fila cheia, %d registros descartados", _descartados)
    except Exception:
        _log.warning("sharepoint_logger: erro no próprio logger\n%s", traceback.format_exc())


def log_falha_silenciosa(etapa: str, motivo: str, **kw) -> None:
    """Atalho para o caso sem exceção: nada estourou, mas o esperado não aconteceu.
    Ex.: log_falha_silenciosa('agendamento', 'lead confirmou horário mas nenhuma reunião criada',
                              lead_id=42, telefone='5511...')"""
    log_erro(etapa=etapa, erro=motivo, **kw)


def testar() -> None:
    """Grava um item de teste de forma síncrona e imprime o resultado.
    Rode uma vez após configurar as variáveis: python -c 'import sharepoint_logger as s; s.testar()'"""
    cliente = _GraphClient()
    print("site_id:", cliente.site_id)
    print("list_id:", cliente.list_id)
    agora = datetime.now(timezone.utc).isoformat()
    cliente.gravar(
        {
            "Title": "[teste] Verificacao",
            "Timestamp": agora,
            "Etapa": "teste",
            "Plataforma": "n/a",
            "TipoErro": "Teste",
            "Mensagem": "item de teste do sharepoint_logger",
            "LeadId": "",
            "Telefone": "",
            "Payload": "",
            "Fingerprint": _fingerprint("teste", "Teste", "item de teste"),
            "Ocorrencias": 1,
        },
        dedup_minutes=0,
    )
    print("OK — item gravado na lista.")
