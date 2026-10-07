from __future__ import annotations

import json
import os

from .weekly_analysis import gerar_relatorio_semanal


def main():
    result = gerar_relatorio_semanal(usar_ia=True)
    print("WEEKLY_ANALYSIS_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
    os.execvp("gunicorn", ["gunicorn", "reports_service.app:app"])


if __name__ == "__main__":
    main()
