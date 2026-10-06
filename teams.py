"""Integração Teams isolada.

Na Fase 1 este módulo não é chamado para envio. O envio só será habilitado após
validarmos números + IDs dos canais e definir REPORT_SEND_ENABLED=true.
"""
import time
import requests

from .config import (
    AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID, REPORT_SEND_ENABLED,
)

_token_cache = {"token": None, "expira_em": 0}


def obter_token_azure() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expira_em"] - 300:
        return _token_cache["token"]
    if not all((AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID)):
        raise RuntimeError("Credenciais Azure não configuradas no serviço de reports")
    r = requests.post(
        f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id": AZURE_CLIENT_ID,
            "client_secret": AZURE_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expira_em"] = time.time() + int(data.get("expires_in", 3600))
    return _token_cache["token"]


def enviar_mensagem_canal(team_id: str, channel_id: str, html: str):
    if not REPORT_SEND_ENABLED:
        raise RuntimeError("Envio bloqueado: REPORT_SEND_ENABLED=false")
    if not team_id or not channel_id:
        raise RuntimeError("Destino Teams não configurado")
    token = obter_token_azure()
    r = requests.post(
        f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels/{channel_id}/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"body": {"contentType": "html", "content": html}},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()
