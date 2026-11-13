FROM node:22-alpine AS frontend
WORKDIR /client
COPY client/package*.json ./
RUN npm ci
COPY client/ ./
RUN npm run build

FROM python:3.11-slim-trixie
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY common common
COPY server server
COPY problems problems
COPY --from=frontend /client/dist /app/client/dist
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 JH_DB=/data/judgehive.sqlite3
EXPOSE 8000 9000
CMD ["python", "-m", "server.main"]
