FROM python:3.12-slim

WORKDIR /app

# System dependencies for psycopg2 and lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Playwright: instala Chromium headless (usado apenas em projetos com "Site JS" ativo)
RUN python -m playwright install --with-deps chromium

COPY . .

RUN chmod +x start.sh

EXPOSE 5000

CMD ["./start.sh"]
