import hmac
import os
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, request, Response

from .anthropic_usage import gerar_resumo_consumo
from .config import REPORT_ALLOW_UNAUTHENTICATED_TEST, REPORT_TEST_KEY
from .service import gerar_preview
from .weekly_analysis import gerar_relatorio_semanal
from .weekly_view import renderizar_semanal_html

app = Flask(__name__)
_weekly_lock = threading.Lock()
_weekly_last_run = None



def _authorized() -> bool:
    if REPORT_ALLOW_UNAUTHENTICATED_TEST:
        return True
    if not REPORT_TEST_KEY:
        return False
    supplied = request.headers.get("X-API-Key", "")
    return hmac.compare_digest(supplied, REPORT_TEST_KEY)


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "luca-reports", "mode": "test-no-send"})


@app.get("/reports/teste")
def reports_test():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    try:
        return jsonify(gerar_preview())
    except Exception as exc:
        app.logger.exception("Falha ao gerar preview")
        return jsonify({"error": str(exc)}), 500


@app.get("/reports/semanal/teste")
def semanal_test():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    try:
        usar_ia = request.args.get("ia", "0").lower() in ("1", "true", "yes", "sim")
        return jsonify(gerar_relatorio_semanal(usar_ia=usar_ia))
    except Exception as exc:
        app.logger.exception("Falha ao gerar relatório semanal")
        return jsonify({"error": str(exc)}), 500


# Preview visual reservado à gestão; não envia mensagens nem agenda execuções.
@app.get("/reports/semanal/visual/teste")
def semanal_visual_test():
    # Relatório gerencial: nunca permite bypass por REPORT_ALLOW_UNAUTHENTICATED_TEST.
    supplied = request.headers.get("X-API-Key", "")
    if not REPORT_TEST_KEY or not hmac.compare_digest(supplied, REPORT_TEST_KEY):
        return jsonify({"error": "unauthorized"}), 401
    try:
        # A chamada com IA só ocorre mediante parâmetro explícito.
        if request.args.get("ia", "0").lower() not in ("1", "true", "yes", "sim"):
            return jsonify({"error": "informe ia=1 para gerar a visualizacao"}), 400
        result = gerar_relatorio_semanal(usar_ia=True)
        html = renderizar_semanal_html(result["apresentacao"])
        response = Response(html, mimetype="text/html")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        return response
    except Exception:
        app.logger.exception("Falha ao gerar visual semanal")
        return jsonify({"error": "falha ao gerar visual semanal"}), 500


@app.post("/reports/semanal/executar")
def executar_semanal_controlado():
    """Execução sob demanda, sem envio e com proteção simples contra repetição."""
    global _weekly_last_run
    supplied = request.headers.get("X-API-Key", "")
    if not REPORT_TEST_KEY or not hmac.compare_digest(supplied, REPORT_TEST_KEY):
        return jsonify({"error": "unauthorized"}), 401
    if request.headers.get("X-Confirm-Execution") != "EXECUTAR_SEMANAL":
        return jsonify({"error": "confirmation_required"}), 400
    if not _weekly_lock.acquire(blocking=False):
        return jsonify({"error": "already_running"}), 409
    try:
        now = datetime.now(timezone.utc)
        if _weekly_last_run and (now - _weekly_last_run).total_seconds() < 3600:
            return jsonify({"error": "cooldown_active", "retry_after_seconds": 3600}), 429
        # O bloqueio limita concorrência neste processo; múltiplas réplicas exigem
        # armazenamento compartilhado para garantir idempotência global.
        _weekly_last_run = now
        report = gerar_relatorio_semanal(usar_ia=True)
        presentation = report["apresentacao"]
        renderizar_semanal_html(presentation)
        return jsonify({
            "ok": True, "sent": False,
            "periodo": presentation["periodo"],
            "cards": len(presentation["cards"]),
            "acoes": len(presentation["acoes_recomendadas"]),
            "confianca": presentation["confianca"],
            "modelo": report["ia"]["modelo"],
            "uso": report["ia"]["uso"],
        })
    except Exception:
        app.logger.exception("Falha na execução controlada semanal")
        return jsonify({"error": "weekly_execution_failed"}), 500
    finally:
        _weekly_lock.release()


@app.get("/reports/consumo/teste")
def consumo_test():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    try:
        return jsonify(gerar_resumo_consumo())
    except Exception as exc:
        app.logger.exception("Falha ao gerar resumo de consumo Anthropic")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
