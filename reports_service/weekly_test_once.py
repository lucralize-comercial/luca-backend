from __future__ import annotations

import json

from .weekly_analysis import gerar_relatorio_semanal


def main():
    result = gerar_relatorio_semanal(usar_ia=True)
    print("WEEKLY_ANALYSIS_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
