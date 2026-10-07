import hmac
import os

from flask import Flask, jsonify, request

from .anthropic_usage import gerar_resumo_consumo
from .config import REPORT_ALLOW_UNAUTHENTICATED_TEST, REPORT_TEST_KEY
from .service import gerar_preview
from .weekly_analysis import gerar_relatorio_semanal

app = Flask(__name__)


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
