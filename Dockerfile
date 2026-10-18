FROM node:24.11.0-bookworm-slim AS frontend
WORKDIR /frontend
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 LINEMEDIC_DEPLOYED=true LINEMEDIC_PDF_MANIFEST=output/pdf/manifest.json
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY data/ ./data/
COPY output/pdf/ ./output/pdf/
COPY evals/published/ ./evals/published/
COPY --from=frontend /frontend/dist/ ./web/dist/
RUN useradd --create-home --uid 10001 linemedic
USER linemedic
EXPOSE 8080
CMD ["sh", "-c", "exec python -m uvicorn src.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --no-access-log"]
