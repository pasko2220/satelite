# Serveur d'analyse de la veille satellite, pour Render (ou tout hébergeur Docker).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VEILLE_DONNEES=/donnees
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app
COPY web web
RUN useradd --create-home veille && mkdir -p /donnees && chown veille /donnees
USER veille

# Render fournit le port dans $PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8765} --proxy-headers --forwarded-allow-ips='*'"]
