"""Contrato de idempotência para a fase de envio.

A Fase 1 não envia mensagens. Na ativação, este módulo receberá persistência
DURÁVEL (não arquivo local efêmero) para garantir 1 envio por destino/data.
"""


def chave_execucao(report: str, data_ref: str, destino: str) -> str:
    return f"{report}:{data_ref}:{destino}"
