"""
Kubernetes Pod Health Dashboard — Python Backend
Requires: pip install kubernetes flask flask-cors gunicorn

Local run:  python k8s_backend.py
Render:     gunicorn k8s_backend:app  (set KUBECONFIG_DATA env var)

Environment variables:
  KUBECONFIG_DATA  — base64-encoded kubeconfig (required on Render)
  PORT             — port to listen on (Render sets this automatically)
"""

from flask import Flask, jsonify
from flask_cors import CORS
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import base64
import datetime
import os
import sys
import tempfile

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────────────────────
# Kubeconfig loader — supports local file and Render env var
# ─────────────────────────────────────────────────────────────
_kubeconfig_path = None   # cached temp file path (Render only)

def load_kube():
    """
    Load Kubernetes config from one of two sources:

    1. KUBECONFIG_DATA env var (base64-encoded) — used on Render.
       Decodes the kubeconfig and writes it to a temp file once,
       then reuses that file on every subsequent call.

    2. ~/.kube/config on disk — used for local development.
    """
    global _kubeconfig_path

    kube_data = os.environ.get("KUBECONFIG_DATA")

    if kube_data:
        # Render / cloud mode — decode base64 kubeconfig env var
        if _kubeconfig_path is None or not os.path.exists(_kubeconfig_path):
            try:
                decoded = base64.b64decode(kube_data)
                tmp = tempfile.NamedTemporaryFile(
                    delete=False, suffix=".yaml", mode="wb"
                )
                tmp.write(decoded)
                tmp.flush()
                tmp.close()
                _kubeconfig_path = tmp.name
                print(f"  [OK] kubeconfig written to temp file: {_kubeconfig_path}")
            except Exception as e:
                print(f"  [ERROR] Failed to decode KUBECONFIG_DATA: {e}")
                sys.exit(1)
        config.load_kube_config(config_file=_kubeconfig_path)
    else:
        # Local mode — read from ~/.kube/config
        config.load_kube_config()


# ─────────────────────────────────────────────────────────────
# Startup check — verify cluster is reachable before serving
# ─────────────────────────────────────────────────────────────
def verify_cluster():
    try:
        load_kube()
        v1 = client.CoreV1Api()
        v1.list_namespace(_request_timeout=5)
        print("  [OK] Cluster connection verified")
    except config.ConfigException as e:
        print("\n  [ERROR] Could not load kubeconfig.")
        if os.environ.get("KUBECONFIG_DATA"):
            print("  Check that KUBECONFIG_DATA is set correctly on Render.")
        else:
            print("  Local: run  minikube start --driver=docker")
        print(f"\n  Detail: {e}")
        sys.exit(1)
    except ApiException as e:
        print(f"\n  [ERROR] Kubernetes API error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n  [ERROR] Cannot reach the cluster: {e}")
        if not os.environ.get("KUBECONFIG_DATA"):
            print("  Local: minikube start --driver=docker")
            print("  Render: verify KUBECONFIG_DATA env var is set")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────
# Metrics fetcher
# ─────────────────────────────────────────────────────────────
def get_metrics():
    """
    Fetch CPU and memory from the metrics-server API.
    Returns dict keyed by "namespace/pod-name" →
        { "cpu_percent": int, "mem_mib": float }
    Falls back to empty dict if metrics-server is unavailable.
    GKE has metrics-server built in — no addon needed.
    """
    metrics_map = {}
    try:
        custom = client.CustomObjectsApi()
        pod_metrics = custom.list_cluster_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="pods",
        )

        v1 = client.CoreV1Api()
        nodes = v1.list_node()
        if not nodes.items:
            return metrics_map

        # Sum all node capacity (GKE may have multiple nodes)
        total_cpu_m = sum(parse_cpu(n.status.allocatable.get("cpu", "0")) for n in nodes.items)
        total_mem_b = sum(parse_mem(n.status.allocatable.get("memory", "0")) for n in nodes.items)

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
            print("  [WARN] metrics-server not available")
        else:
            print(f"  [WARN] Metrics API error ({e.status}): {e.reason}")
    except Exception as e:
        print(f"  [WARN] Could not fetch metrics: {e}")

    return metrics_map


def parse_cpu(value):
    value = str(value).strip()
    if value.endswith("m"):  return float(value[:-1])
    if value.endswith("n"):  return float(value[:-1]) / 1e6
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
    pod_list = v1.list_pod_for_all_namespaces(watch=False)
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
    alerts   = [p for p in pods if p["status"] in ("CrashLoopBackOff", "Pending") or p["restarts"] >= 5]

    return jsonify({
        "pods":    pods,
        "summary": {"total": len(pods), "running": running, "crashing": crashing, "pending": pending},
        "alerts":    alerts,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    })


@app.route("/api/health")
def api_health():
    try:
        load_kube()
        v1 = client.CoreV1Api()
        v1.list_namespace(_request_timeout=3)
        return jsonify({"status": "ok", "cluster": "reachable"})
    except Exception as e:
        return jsonify({"status": "error", "cluster": "unreachable", "detail": str(e)}), 503


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mode = "RENDER (cloud)" if os.environ.get("KUBECONFIG_DATA") else "LOCAL"
    print("╔══════════════════════════════════════════════╗")
    print(f"║  K8s Pod Health Dashboard — {mode:<16} ║")
    print("║  Verifying cluster connection...             ║")
    print("╚══════════════════════════════════════════════╝")

    verify_cluster()

    port = int(os.environ.get("PORT", 5000))
    print("╔══════════════════════════════════════════════╗")
    print(f"║  Listening on port {port:<26} ║")
    print("╚══════════════════════════════════════════════╝")

    app.run(debug=False, host="0.0.0.0", port=port)
