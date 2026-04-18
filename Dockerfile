FROM python:3.11-slim

# Install system dependencies + gke-gcloud-auth-plugin
RUN apt-get update && apt-get install -y \
    curl \
    apt-transport-https \
    ca-certificates \
    gnupg \
    && curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | \
       gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] \
       https://packages.cloud.google.com/apt cloud-sdk main" | \
       tee /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update && apt-get install -y \
       google-cloud-cli \
       google-cloud-cli-gke-gcloud-auth-plugin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY k8s_backend.py .

# Tell kubectl to use the gke auth plugin
ENV USE_GKE_GCLOUD_AUTH_PLUGIN=True

EXPOSE 5000

CMD gunicorn k8s_backend:app \
    --workers 1 \
    --bind 0.0.0.0:$PORT \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
