"""Testes sem rede nem consumo de tokens para o contrato do relatório semanal."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from reports_service.weekly_view import renderizar_semanal_html
from reports_service.weekly_analysis import (
    _validar_analise, _week_window, _open_pipeline,
    montar_apresentacao_semanal,
)

ANALISE = {
    "leitura_gestor": "Volume diminuiu; investigar qualidade dos leads.",
    "performando": ["Reuniões se mantiveram próximas à semana anterior."],
    "prejudicando": ["Contatos inválidos exigem revisão."],
    "acoes_recomendadas": ["Auditar a captura dos contatos inválidos."],
    "sinal_proxima_semana": "Monitorar novos contatos inválidos.",
    "confianca": "media",
    "ressalvas": [],
}


class WeeklyAnalysisTests(unittest.TestCase):
    def test_window_october_8(self):
        current, end, previous, previous_end = _week_window(
            datetime(2026, 10, 8, 10, tzinfo=ZoneInfo("America/Sao_Paulo"))
        )
        self.assertEqual((str(current), str(end), str(previous), str(previous_end)),
                         ("2026-10-01", "2026-10-07", "2026-09-24", "2026-09-30"))

    def test_analysis_must_be_complete(self):
        self.assertEqual(_validar_analise(ANALISE), ANALISE)
        with self.assertRaises(ValueError):
            _validar_analise({**ANALISE, "acoes_recomendadas": "texto"})
        with self.assertRaises(ValueError):
            _validar_analise({**ANALISE, "performando": ["1", "2", "3", "4"]})
        with self.assertRaises(ValueError):
            _validar_analise({**ANALISE, "leitura_gestor": ""})

    def test_presentation_layout(self):
        pacote = {
            "periodo": {"semana_atual": {"inicio": "2026-10-01", "fim": "2026-10-07"}},
            "indicadores": {
                "leads": {"valor": 58, "anterior": 78, "variacao_pct": -25.6},
                "reunioes": {"valor": 7, "anterior": 8, "variacao_pct": -12.5},
                "perdidos": {"valor": 49, "anterior": 101, "variacao_pct": -51.5},
            },
        }
        rendered = montar_apresentacao_semanal(
            pacote, {"analise": ANALISE, "modelo": "teste", "uso": {}}
        )
        self.assertEqual(rendered["versao_layout"], 1)
        self.assertEqual(rendered["titulo"], "ACOMPANHAMENTO COMERCIAL")
        self.assertEqual([x["titulo"] for x in rendered["cards"]],
                         ["Leads recebidos", "Reuniões", "Perdidos"])
        self.assertEqual(rendered["cards"][0]["comparacao"], "↓ 25,6%")
        self.assertEqual(rendered["sinal_proxima_semana"],
                         ANALISE["sinal_proxima_semana"])

    def test_html_preview_and_escaping(self):
        presentation = {
            "titulo": "ACOMPANHAMENTO COMERCIAL",
            "periodo": {"inicio": "2026-10-01", "fim": "2026-10-07"},
            "cards": [
                {"titulo": "Leads", "valor": 58, "comparacao": "↓ 25,6%", "anterior": 78},
                {"titulo": "Reuniões", "valor": 7, "comparacao": "↓ 12,5%", "anterior": 8},
                {"titulo": "Perdidos", "valor": 49, "comparacao": "↓ 51,5%", "anterior": 101},
            ],
            **ANALISE,
            "leitura_gestor": "<script>alert(1)</script>",
        }
        html = renderizar_semanal_html(presentation)
        self.assertIn("ACOMPANHAMENTO COMERCIAL", html)
        self.assertIn("58", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("Ações recomendadas", html)

    def test_html_requires_three_cards(self):
        with self.assertRaises(ValueError):
            renderizar_semanal_html({"cards": []})

    def test_ten_days_inclusive(self):
        results = _open_pipeline([{
            "dealStage": {"name": "Follow-up"},
            "updatedAt": "2026-09-28T12:00:00-03:00",
        }], datetime(2026, 10, 8, 12, tzinfo=ZoneInfo("America/Sao_Paulo")))
        self.assertEqual(results["sem_atualizacao_10d_por_etapa"],
                         [{"etapa": "Follow-up", "quantidade": 1}])


if __name__ == "__main__":
    unittest.main()
