"""
Kubernetes Pod Health Dashboard — Python Backend

Local run:  python k8s_backend.py
Render:     gunicorn k8s_backend:app

Environment variables:
  KUBECONFIG_DATA   — base64-encoded kubeconfig (required on Render/GKE)
  GCLOUD_PROJECT    — GCP project ID (for token refresh on Render)
  GCLOUD_CLUSTER    — GKE cluster name (default: k8s-dashboard)
  GCLOUD_REGION     — GKE cluster region (default: us-central1)
  PORT              — port to listen on (Render sets this automatically)
"""

from flask import Flask, jsonify
from flask_cors import CORS
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import base64
import datetime
import os
import subprocess
import sys
import tempfile
import threading
import time
import yaml

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ─────────────────────────────────────────────────────────────
# Token refresh state
# ─────────────────────────────────────────────────────────────
_kubeconfig_path  = None   # path to decoded temp kubeconfig file
_token_refreshed  = None   # datetime of last successful token refresh
_refresh_lock     = threading.Lock()

# GKE access tokens expire after 60 minutes — refresh every 45 min
TOKEN_REFRESH_INTERVAL = 45 * 60


# ─────────────────────────────────────────────────────────────
# Token refresh — rewrites the token in the kubeconfig file
# ─────────────────────────────────────────────────────────────
def refresh_gke_token():
    """
    Fetch a fresh GKE access token using google-auth and rewrite
    the token field in the kubeconfig temp file.
    Called automatically every 45 minutes by a background thread.
    Also called on 401 Unauthorized responses.
    """
    global _token_refreshed

    if not os.environ.get("KUBECONFIG_DATA"):
        return   # local mode — no token refresh needed

    if _kubeconfig_path is None or not os.path.exists(_kubeconfig_path):
        print("  [WARN] Token refresh skipped — kubeconfig not loaded yet")
        return

    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
        new_token = credentials.token

        # Read current kubeconfig, update token, write back
        with open(_kubeconfig_path, "r") as f:
            kube_cfg = yaml.safe_load(f)

        for user in kube_cfg.get("users", []):
            if "user" in user:
                user["user"]["token"] = new_token

        with open(_kubeconfig_path, "w") as f:
            yaml.dump(kube_cfg, f)

        _token_refreshed = datetime.datetime.utcnow()
        print(f"  [OK] Token refreshed at {_token_refreshed.strftime('%H:%M:%S UTC')}")

    except Exception as e:
        print(f"  [WARN] Token refresh failed: {e}")
        print("  [WARN] Will retry on next refresh cycle")


def token_refresh_loop():
    """Background thread — refreshes the GKE token every 45 minutes."""
    while True:
        time.sleep(TOKEN_REFRESH_INTERVAL)
        with _refresh_lock:
            print("  [INFO] Background token refresh starting...")
            refresh_gke_token()


# ─────────────────────────────────────────────────────────────
# Kubeconfig loader
# ─────────────────────────────────────────────────────────────
def load_kube():
    """
    Load kubeconfig from:
    1. KUBECONFIG_DATA env var (base64) — Render/GKE
    2. ~/.kube/config — local development
    Writes decoded kubeconfig to a temp file once and reuses it.
    Token refresh updates the same file in place.
    """
    global _kubeconfig_path

    kube_data = os.environ.get("KUBECONFIG_DATA", "").strip()

    if kube_data:
        if _kubeconfig_path is None or not os.path.exists(_kubeconfig_path):
            try:
                # Handle both padded and unpadded base64
                padding = 4 - len(kube_data) % 4
                if padding != 4:
                    kube_data += "=" * padding
                decoded = base64.b64decode(kube_data)
                tmp = tempfile.NamedTemporaryFile(
                    delete=False, suffix=".yaml", mode="wb"
                )
                tmp.write(decoded)
                tmp.flush()
                tmp.close()
                _kubeconfig_path = tmp.name
                print(f"  [OK] kubeconfig decoded → {_kubeconfig_path}")

                # Do an immediate token refresh on first load
                refresh_gke_token()

            except Exception as e:
                print(f"  [ERROR] Failed to decode KUBECONFIG_DATA: {e}")
                sys.exit(1)

        config.load_kube_config(config_file=_kubeconfig_path)
    else:
        config.load_kube_config()


# ─────────────────────────────────────────────────────────────
# K8s API call wrapper — auto-retries on 401 with token refresh
# ─────────────────────────────────────────────────────────────
def k8s_call(fn, *args, **kwargs):
    """
    Call a kubernetes API function. If it returns 401 Unauthorized
    (expired token), refresh the token and retry once.
    """
    try:
        return fn(*args, **kwargs)
    except ApiException as e:
        if e.status == 401:
            print("  [WARN] 401 Unauthorized — refreshing token and retrying")
            with _refresh_lock:
                refresh_gke_token()
            load_kube()
            return fn(*args, **kwargs)
        raise


# ─────────────────────────────────────────────────────────────
# Startup check
# ─────────────────────────────────────────────────────────────
def verify_cluster():
    try:
        load_kube()
        v1 = client.CoreV1Api()
        k8s_call(v1.list_namespace, _request_timeout=10)
        print("  [OK] Cluster connection verified")
    except config.ConfigException as e:
        print(f"\n  [ERROR] kubeconfig error: {e}")
        print("  Render: check KUBECONFIG_DATA is set correctly")
        print("  Local:  run minikube start --driver=docker")
        sys.exit(1)
    except ApiException as e:
        print(f"\n  [ERROR] Kubernetes API error ({e.status}): {e.reason}")
        sys.exit(1)
    except Exception as e:
        print(f"\n  [ERROR] Cannot reach cluster: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────
# Metrics fetcher
# ─────────────────────────────────────────────────────────────
def get_metrics():
    metrics_map = {}
    try:
        custom = client.CustomObjectsApi()
        pod_metrics = k8s_call(
            custom.list_cluster_custom_object,
            group="metrics.k8s.io",
            version="v1beta1",
            plural="pods",
        )

        v1 = client.CoreV1Api()
        nodes = k8s_call(v1.list_node)
        if not nodes.items:
            return metrics_map

        total_cpu_m = sum(
            parse_cpu(n.status.allocatable.get("cpu", "0"))
            for n in nodes.items
        )
        total_mem_b = sum(
            parse_mem(n.status.allocatable.get("memory", "0"))
            for n in nodes.items
        )

        for item in pod_metrics.get("items", []):
            ns       = item["metadata"]["namespace"]
            pod_name = item["metadata"]["name"]

            pod_cpu_m = 0
            pod_mem_b = 0
            for c in item.get("containers", []):
                usage = c.get("usage", {})
                pod_cpu_m += parse_cpu(usage.get("cpu", "0"))
                pod_mem_b += parse_mem(usage.get("memory", "0"))

            cpu_pct = int((pod_cpu_m / total_cpu_m) * 100) if total_cpu_m else 0
            mem_mib = round(pod_mem_b / (1024 ** 2), 1)

            metrics_map[f"{ns}/{pod_name}"] = {
                "cpu_percent": max(1, min(99, cpu_pct)),
                "mem_mib":     mem_mib,
            }

    except ApiException as e:
        if e.status == 404:
            print("  [WARN] metrics API not available yet")
        else:
            print(f"  [WARN] Metrics API error ({e.status}): {e.reason}")
    except Exception as e:
        print(f"  [WARN] Could not fetch metrics: {e}")

    return metrics_map


def parse_cpu(value):
    value = str(value).strip()
    if value.endswith("m"): return float(value[:-1])
    if value.endswith("n"): return float(value[:-1]) / 1e6
    try: return float(value) * 1000
    except ValueError: return 0.0


def parse_mem(value):
    value = str(value).strip()
    for suffix, mult in [("Ki", 1024), ("Mi", 1024**2), ("Gi", 1024**3),
                         ("Ti", 1024**4), ("K", 1000), ("M", 1000**2),
                         ("G", 1000**3), ("T", 1000**4)]:
        if value.endswith(suffix):
            try: return float(value[:-len(suffix)]) * mult
            except ValueError: return 0.0
    try: return float(value)
    except ValueError: return 0.0


# ─────────────────────────────────────────────────────────────
# Pod reader
# ─────────────────────────────────────────────────────────────
def get_pods():
    load_kube()
    v1 = client.CoreV1Api()
    pod_list = k8s_call(v1.list_pod_for_all_namespaces, watch=False)
    metrics_map = get_metrics()

    pods = []
    for p in pod_list.items:
        container_statuses = p.status.container_statuses or []
        restarts = sum(cs.restart_count for cs in container_statuses)
        phase    = p.status.phase or "Unknown"

        for cs in container_statuses:
            if cs.state and cs.state.waiting:
                if cs.state.waiting.reason == "CrashLoopBackOff":
                    phase = "CrashLoopBackOff"
                    break

        creation   = p.metadata.creation_timestamp
        age_secs   = (datetime.datetime.now(datetime.timezone.utc) - creation).total_seconds()
        hours, rem = divmod(int(age_secs), 3600)
        minutes    = rem // 60
        age_str    = f"{hours}h {minutes}m" if hours else f"{minutes}m"

        key     = f"{p.metadata.namespace}/{p.metadata.name}"
        metrics = metrics_map.get(key, {})

        pods.append({
            "name":      p.metadata.name,
            "namespace": p.metadata.namespace,
            "status":    phase,
            "restarts":  restarts,
            "cpu":       metrics.get("cpu_percent", 0),
            "memory":    metrics.get("mem_mib", 0),
            "age":       age_str,
        })

    return pods


# ─────────────────────────────────────────────────────────────
# REST Endpoints
# ─────────────────────────────────────────────────────────────
@app.route("/api/pods")
def api_pods():
    try:
        pods = get_pods()
    except Exception as e:
        return jsonify({"error": str(e), "hint": "Check cluster connectivity"}), 503

    running  = sum(1 for p in pods if p["status"] == "Running")
    crashing = sum(1 for p in pods if p["status"] == "CrashLoopBackOff")
    pending  = sum(1 for p in pods if p["status"] == "Pending")
    alerts   = [
        p for p in pods
        if p["status"] in ("CrashLoopBackOff", "Pending") or p["restarts"] >= 5
    ]

    return jsonify({
        "pods":    pods,
        "summary": {
            "total":    len(pods),
            "running":  running,
            "crashing": crashing,
            "pending":  pending,
        },
        "alerts":    alerts,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    })


@app.route("/api/health")
def api_health():
    try:
        load_kube()
        v1 = client.CoreV1Api()
        k8s_call(v1.list_namespace, _request_timeout=5)

        # Include token refresh status in health response
        refresh_info = (
            _token_refreshed.strftime("%H:%M:%S UTC")
            if _token_refreshed else "not yet refreshed"
        )
        return jsonify({
            "status":         "ok",
            "cluster":        "reachable",
            "token_refreshed": refresh_info,
        })
    except Exception as e:
        return jsonify({
            "status":  "error",
            "cluster": "unreachable",
            "detail":  str(e)
        }), 503


@app.route("/api/token/refresh")
def api_token_refresh():
    """Manual token refresh endpoint — call if dashboard shows auth errors."""
    try:
        with _refresh_lock:
            refresh_gke_token()
        return jsonify({
            "status":  "ok",
            "refreshed_at": _token_refreshed.strftime("%H:%M:%S UTC") if _token_refreshed else "unknown"
        })
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mode = "RENDER/GKE" if os.environ.get("KUBECONFIG_DATA") else "LOCAL"
    print("╔══════════════════════════════════════════════╗")
    print(f"║  K8s Pod Health Dashboard  [{mode}]")
    print("║  Verifying cluster connection...             ║")
    print("╚══════════════════════════════════════════════╝")

    verify_cluster()

    # Start background token refresh thread
    if os.environ.get("KUBECONFIG_DATA"):
        t = threading.Thread(target=token_refresh_loop, daemon=True)
        t.start()
        print(f"  [OK] Token auto-refresh started (every {TOKEN_REFRESH_INTERVAL//60} minutes)")

    port = int(os.environ.get("PORT", 5000))
    print(f"  Listening on port {port}")
    app.run(debug=False, host="0.0.0.0", port=port)
