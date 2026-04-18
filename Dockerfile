FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY k8s_backend.py .

EXPOSE 5000

CMD gunicorn k8s_backend:app \
    --workers 1 \
    --bind 0.0.0.0:$PORT \
    --timeout 30 \
    --access-logfile - \
    --error-logfile -
