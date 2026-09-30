import os

# Gunicorn configuration — forçar 1 worker para compartilhar memória entre webhooks
# Isso garante que message_created e conversation_updated usem o mesmo conversation_histories
workers = 1
timeout = 300
bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
