# Property Hunter app: FastAPI + SQLite, one container, one volume for data/. The public site stays on GitHub Pages.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hunter ./hunter
COPY tools ./tools
COPY docs ./docs
ENV PH_ENV=production PH_DATA_DIR=/data PH_DB_PATH=/data/property_hunter.db PH_LICENSE_ENFORCED=1 PH_AI_ENABLED=0
VOLUME ["/data"]
EXPOSE 8234
CMD ["python", "-m", "uvicorn", "hunter.api:app", "--host", "0.0.0.0", "--port", "8234"]
