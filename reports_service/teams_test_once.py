"""Executado SOMENTE por invocação manual de teste (nunca pelo gunicorn)."""
import argparse
from .service import gerar_preview
from .teams_preview import enviar_teste


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destino", choices=("comercial", "gestao", "ambos"), required=True)
    parser.add_argument("--confirmacao", required=True)
    args = parser.parse_args()
    preview = gerar_preview()
    for destino in (("comercial", "gestao") if args.destino == "ambos" else (args.destino,)):
        status = enviar_teste(preview, destino, confirmacao=args.confirmacao)
        print(f"TESTE_RELATORIO_{destino.upper()}_HTTP={status}", flush=True)


if __name__ == "__main__":
    main()
