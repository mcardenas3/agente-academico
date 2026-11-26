# Imagen de la aplicación: la API de FastAPI + la interfaz de React ya compilada.
# Se construye en dos etapas para que la imagen final no lleve Node ni sus dependencias.

# ---------- Etapa 1: compilar la interfaz ----------
FROM node:24-alpine AS web
WORKDIR /web
# Primero solo los archivos de dependencias: si no cambian, Docker reusa esta capa
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------- Etapa 2: la API ----------
FROM python:3.13-slim
# uv, el mismo administrador de paquetes que se usa en desarrollo
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv
# Compila a bytecode al instalar (arranque más rápido) y usa el Python de la imagen, sin bajar otro
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app/api
COPY api/pyproject.toml api/uv.lock api/.python-version ./
RUN uv sync --frozen --no-dev
COPY api/*.py api/esquema.sql ./

# Lo necesario para crear el índice si está vacío (preparar_indice.py), sin Docling
COPY ingesta/crear_indice.py ingesta/indexar.py ingesta/preparar_indice.py /app/ingesta/
COPY datos/procesado/fragmentos.jsonl /app/datos/procesado/fragmentos.jsonl

COPY --from=web /web/dist /app/web/dist

ENV PATH="/app/api/.venv/bin:$PATH" CARPETA_WEB=/app/web/dist PYTHONUNBUFFERED=1
EXPOSE 8080
# Al arrancar: deja listo el índice (no hace nada si ya está) y levanta la API
CMD ["sh", "-c", "python /app/ingesta/preparar_indice.py && exec uvicorn main:app --host 0.0.0.0 --port 8080"]
