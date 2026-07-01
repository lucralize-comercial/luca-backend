# Gunicorn configuration — forçar 1 worker para compartilhar memória entre webhooks
# Isso garante que message_created e conversation_updated usem o mesmo conversation_histories
workers = 1
timeout = 120
bind = "0.0.0.0:8080"
