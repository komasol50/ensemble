# 画面をビルドし、APIと同じサーバーから配る。鍵は入れない（環境変数で渡す）。
FROM node:24-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
# 必要なものだけを名指しで入れる（.env や手元のDBが混ざらないように）。
COPY backend/ensemble ./backend/ensemble
COPY data ./data
COPY --from=frontend /app/frontend/dist ./frontend/dist
RUN useradd --create-home app && mkdir logs && chown app logs
USER app
# Render は PORT を環境変数で渡す。
CMD ["sh", "-c", "exec uvicorn ensemble.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-10000}"]
