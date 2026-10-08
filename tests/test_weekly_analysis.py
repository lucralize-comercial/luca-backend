"""Testes sem rede nem consumo de tokens para o contrato do relatório semanal."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

from reports_service.weekly_view import renderizar_semanal_html
from reports_service.weekly_analysis import (
    _validar_analise, _parse_ai_json, _week_window, _open_pipeline,
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
                "ganhos": {"valor": 3, "anterior": 3, "variacao_pct": 0.0},
                "perdidos": {"valor": 49, "anterior": 101, "variacao_pct": -51.5},
            },
        }
        rendered = montar_apresentacao_semanal(
            pacote, {"analise": ANALISE, "modelo": "teste", "uso": {}}
        )
        self.assertEqual(rendered["versao_layout"], 1)
        self.assertEqual(rendered["titulo"], "ACOMPANHAMENTO COMERCIAL")
        self.assertEqual([x["titulo"] for x in rendered["cards"]],
                         ["Leads recebidos", "Reuniões", "Ganhos", "Perdidos"])
        self.assertEqual(rendered["cards"][0]["comparacao"], "↓ 25,6%")
        self.assertEqual(rendered["sinal_proxima_semana"],
                         ANALISE["sinal_proxima_semana"])

    def test_parse_json_with_trailing_commentary(self):
        import json
        raw = json.dumps(ANALISE, ensure_ascii=False) + "\\n\\nObservação adicional"
        self.assertEqual(_parse_ai_json(raw), ANALISE)

    def test_parse_json_with_markdown_and_two_objects(self):
        import json
        raw = "Segue análise:\\n```json\\n" + json.dumps(ANALISE) + "\\n```\\n" + '{"extra":1}'
        self.assertEqual(_parse_ai_json(raw), ANALISE)

    def test_parse_rejects_incomplete_json(self):
        with self.assertRaises(ValueError):
            _parse_ai_json('{"leitura_gestor": "texto", "performando": [')

    def test_html_preview_and_escaping(self):
        presentation = {
            "titulo": "ACOMPANHAMENTO COMERCIAL",
            "periodo": {"inicio": "2026-10-01", "fim": "2026-10-07"},
            "cards": [
                {"titulo": "Leads", "valor": 58, "comparacao": "↓ 25,6%", "anterior": 78},
                {"titulo": "Reuniões", "valor": 7, "comparacao": "↓ 12,5%", "anterior": 8},
                {"titulo": "Ganhos", "valor": 3, "comparacao": "→ 0,0%", "anterior": 3},
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

    def test_html_requires_four_cards(self):
        with self.assertRaises(ValueError):
            renderizar_semanal_html({"cards": []})

    def test_weekly_visual_requires_key_even_with_test_bypass(self):
        from reports_service import app as app_module
        with patch.object(app_module, "REPORT_ALLOW_UNAUTHENTICATED_TEST", True), \
             patch.object(app_module, "REPORT_TEST_KEY", "test-secret"):
            client = app_module.app.test_client()
            self.assertEqual(
                client.get("/reports/semanal/visual/teste?ia=1").status_code, 401
            )
            self.assertEqual(
                client.get("/reports/semanal/visual/teste?ia=1",
                           headers={"X-API-Key": "wrong"}).status_code, 401
            )
            self.assertEqual(
                client.get("/reports/semanal/visual/teste",
                           headers={"X-API-Key": "test-secret"}).status_code, 400
            )

    def test_authenticated_end_to_end_html_without_api_calls(self):
        from reports_service import app as app_module
        presentation = {
            "titulo": "ACOMPANHAMENTO COMERCIAL",
            "periodo": {"inicio": "2026-10-01", "fim": "2026-10-07"},
            "cards": [
                {"titulo": "Leads recebidos", "valor": 58, "comparacao": "↓ 25,6%", "anterior": 78},
                {"titulo": "Reuniões", "valor": 7, "comparacao": "↓ 12,5%", "anterior": 8},
                {"titulo": "Ganhos", "valor": 3, "comparacao": "→ 0,0%", "anterior": 3},
                {"titulo": "Perdidos", "valor": 49, "comparacao": "↓ 51,5%", "anterior": 101},
            ],
            **ANALISE,
        }
        with patch.object(app_module, "REPORT_TEST_KEY", "test-secret"), \
             patch.object(app_module, "gerar_relatorio_semanal",
                          return_value={"apresentacao": presentation}) as gerar:
            response = app_module.app.test_client().get(
                "/reports/semanal/visual/teste?ia=1",
                headers={"X-API-Key": "test-secret"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"text/html", response.content_type.encode())
        self.assertIn("58", response.get_data(as_text=True))
        self.assertIn("Ações recomendadas", response.get_data(as_text=True))
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertIn("default-src 'none'",
                      response.headers.get("Content-Security-Policy", ""))
        gerar.assert_called_once_with(usar_ia=True)

    def test_visual_endpoint_safe_error_does_not_leak_secrets(self):
        from reports_service import app as app_module
        with patch.object(app_module, "REPORT_TEST_KEY", "test-secret"), \
             patch.object(app_module, "gerar_relatorio_semanal",
                          side_effect=RuntimeError("sensitive internal error")):
            response = app_module.app.test_client().get(
                "/reports/semanal/visual/teste?ia=1",
                headers={"X-API-Key": "test-secret"},
            )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("sensitive internal error", response.get_data(as_text=True))

    def test_controlled_execution_rejects_unconfirmed_or_unauthorized(self):
        from reports_service import app as module
        with patch.object(module, "REPORT_TEST_KEY", "secret"):
            client = module.app.test_client()
            self.assertEqual(client.post("/reports/semanal/executar").status_code, 401)
            self.assertEqual(client.post(
                "/reports/semanal/executar", headers={"X-API-Key": "secret"}
            ).status_code, 400)

    def test_controlled_execution_is_cost_guarded_and_no_send(self):
        from reports_service import app as module
        presentation = {
            "titulo": "ACOMPANHAMENTO COMERCIAL",
            "periodo": {"inicio": "2026-10-01", "fim": "2026-10-07"},
            "cards": [
                {"titulo": "Leads", "valor": 58, "comparacao": "↓ 25,6%", "anterior": 78},
                {"titulo": "Reuniões", "valor": 7, "comparacao": "↓ 12,5%", "anterior": 8},
                {"titulo": "Ganhos", "valor": 3, "comparacao": "→ 0,0%", "anterior": 3},
                {"titulo": "Perdidos", "valor": 49, "comparacao": "↓ 51,5%", "anterior": 101},
            ],
            **ANALISE,
        }
        report = {"apresentacao": presentation,
                  "ia": {"modelo": "mock", "uso": {"input_tokens": 3}}}
        headers = {"X-API-Key": "secret", "X-Confirm-Execution": "EXECUTAR_SEMANAL"}
        with patch.object(module, "REPORT_TEST_KEY", "secret"), \
             patch.object(module, "_weekly_last_run", None), \
             patch.object(module, "gerar_relatorio_semanal", return_value=report) as gerar:
            client = module.app.test_client()
            first = client.post("/reports/semanal/executar", headers=headers)
            second = client.post("/reports/semanal/executar", headers=headers)
            self.assertEqual(first.status_code, 200)
            self.assertFalse(first.json["sent"])
            self.assertEqual(second.status_code, 429)
            gerar.assert_called_once_with(usar_ia=True)

    def test_ten_days_inclusive(self):
        results = _open_pipeline([{
            "dealStage": {"name": "Follow-up"},
            "updatedAt": "2026-09-28T12:00:00-03:00",
        }], datetime(2026, 10, 8, 12, tzinfo=ZoneInfo("America/Sao_Paulo")))
        self.assertEqual(results["sem_atualizacao_10d_por_etapa"],
                         [{"etapa": "Follow-up", "quantidade": 1}])


    def test_weekly_delivery_deduplicates_without_claude(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from reports_service.weekly_delivery import executar_envio_semanal
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict("os.environ", {"REPORT_SEND_ENABLED": "true",
                        "TEAMS_WEBHOOK_SEMANAL_PRIVADO": "https://example.test/teams"}):
            generate = Mock(return_value={"apresentacao": {
                "periodo": {"inicio": "2026-10-05", "fim": "2026-10-11"},
                "cards": [{"titulo": x, "valor": 1, "comparacao": "0%",
                           "anterior": 1} for x in ("Leads", "Reuniões", "Ganhos", "Perdidos")],
                **ANALISE}})
            post = Mock(return_value=SimpleNamespace(raise_for_status=lambda: None))
            now = datetime(2026, 10, 12, 8, 15, tzinfo=ZoneInfo("America/Sao_Paulo"))
            first = executar_envio_semanal(now=now, generate=generate, post=post,
                                           db_path=str(Path(folder) / "weekly.sqlite"))
            second = executar_envio_semanal(now=now, generate=generate, post=post,
                                            db_path=str(Path(folder) / "weekly.sqlite"))
            self.assertEqual(first["status"], "sent")
            self.assertEqual(second["status"], "already_attempted")
            generate.assert_called_once()
            post.assert_called_once()

    def test_weekly_delivery_disabled_never_calls_ai(self):
        from reports_service.weekly_delivery import executar_envio_semanal
        from unittest.mock import Mock
        with patch.dict("os.environ", {"REPORT_SEND_ENABLED": "false"}):
            generate = Mock()
            self.assertEqual(executar_envio_semanal(generate=generate)["status"], "disabled")
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
