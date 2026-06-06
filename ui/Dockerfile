# --- build frontend ---
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build           # vite build.outDir = 'dist' -> /web/dist

# --- runtime ---
FROM python:3.12-slim AS runtime
WORKDIR /srv
COPY pyproject.toml ./
COPY app/ ./app/
RUN pip install --no-cache-dir .
COPY --from=web /web/dist ./app/static
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/auth/me',timeout=4).status==200 else 1)"
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
