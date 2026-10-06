import os

AGENDOR_BASE = "https://api.agendor.com.br/v3"
AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN", "")
FUNIL_COMERCIAL_ID = int(os.environ.get("REPORT_FUNIL_COMERCIAL_ID", "696449"))
REPORT_TEST_KEY = os.environ.get("REPORT_TEST_KEY", "")
REPORT_ALLOW_UNAUTHENTICATED_TEST = os.environ.get("REPORT_ALLOW_UNAUTHENTICATED_TEST", "false").lower() == "true"

AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")

# Só serão usados na fase de envio real.
TEAMS_COMERCIAL_TEAM_ID = os.environ.get("TEAMS_COMERCIAL_TEAM_ID", "")
TEAMS_COMERCIAL_CHANNEL_ID = os.environ.get("TEAMS_COMERCIAL_CHANNEL_ID", "")
TEAMS_GESTAO_TEAM_ID = os.environ.get("TEAMS_GESTAO_TEAM_ID", "")
TEAMS_GESTAO_CHANNEL_ID = os.environ.get("TEAMS_GESTAO_CHANNEL_ID", "")

# Segurança: o serviço nasce sempre sem envio real.
REPORT_SEND_ENABLED = os.environ.get("REPORT_SEND_ENABLED", "false").lower() == "true"
