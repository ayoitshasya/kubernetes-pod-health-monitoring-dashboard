"""
Kubernetes Pod Health Dashboard — Python Backend
Requires: pip install kubernetes flask flask-cors

Run: python k8s_backend.py
Serves live pod data on http://localhost:5000

Prerequisites:
  - Minikube running       →  minikube start --driver=docker
  - metrics-server enabled →  minikube addons enable metrics-server
  - kubectl working        →  kubectl get nodes  (should show Ready)
"""

from flask import Flask, jsonify
from flask_cors import CORS
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import datetime
import sys

app = Flask(__name__)
CORS(app)


# ─────────────────────────────────────────────────────────────
# Startup check — verify cluster is reachable before serving
# ─────────────────────────────────────────────────────────────
def verify_cluster():
    """
    Load kubeconfig and do a quick connectivity check.
    Exits immediately with a clear message if Minikube is not running.
    """
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
        v1.list_namespace(_request_timeout=5)
        print("  [OK] Cluster connection verified")
    except config.ConfigException as e:
        print("\n  [ERROR] Could not load kubeconfig.")
        print("  Minikube may not be started. Run:")
        print("    minikube start --driver=docker")
        print(f"\n  Detail: {e}")
        sys.exit(1)
    except ApiException as e:
        print("\n  [ERROR] Kubernetes API returned an error.")
        print(f"  Detail: {e}")
        sys.exit(1)
    except Exception as e:
        print("\n  [ERROR] Cannot reach the cluster.")
        print("  Step 1:  minikube start --driver=docker")
        print("  Step 2:  kubectl get nodes   (wait for Ready)")
        print("  Step 3:  python k8s_backend.py")
        print(f"\n  Detail: {e}")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────
# Metrics fetcher — pulls real CPU + memory from metrics-server
# ─────────────────────────────────────────────────────────────
def get_metrics():
    """
    Query the metrics-server via the custom metrics API.
    Returns a dict keyed by "namespace/pod-name" →
        { "cpu_percent": int, "mem_mib": int }

    Falls back to empty dict if metrics-server is not enabled,
    not ready yet, or returns incomplete data — the pod list
    will still render with 0% rather than crashing.

    Enable with:  minikube addons enable metrics-server
    Verify with:  kubectl top pods
    """
    metrics_map = {}
    try:
        custom = client.CustomObjectsApi()

        # Fetch pod-level metrics from metrics.k8s.io
        pod_metrics = custom.list_cluster_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="pods",
        )

        # Also fetch node capacity so we can express CPU/mem as % of node
        v1 = client.CoreV1Api()
        nodes = v1.list_node()
        if not nodes.items:
            return metrics_map

        # Use the first (and only) Minikube node's allocatable capacity
        node = nodes.items[0]
        node_cpu_millicores = parse_cpu(node.status.allocatable.get("cpu", "0"))
        node_mem_bytes      = parse_mem(node.status.allocatable.get("memory", "0"))

        for item in pod_metrics.get("items", []):
            ns        = item["metadata"]["namespace"]
            pod_name  = item["metadata"]["name"]
            containers = item.get("containers", [])

            total_cpu_m = 0
            total_mem_b = 0
            for c in containers:
                usage = c.get("usage", {})
                total_cpu_m += parse_cpu(usage.get("cpu", "0"))
                total_mem_b += parse_mem(usage.get("memory", "0"))

            cpu_pct = int((total_cpu_m / node_cpu_millicores) * 100) if node_cpu_millicores else 0
            mem_mib = round(total_mem_b / (1024 ** 2), 1)

            metrics_map[f"{ns}/{pod_name}"] = {
                "cpu_percent": max(1, min(99, cpu_pct)),
                "mem_mib":     mem_mib,
            }

    except ApiException as e:
        if e.status == 404:
            # metrics-server not installed or not ready yet
            print("  [WARN] metrics-server not available — run: minikube addons enable metrics-server")
        else:
            print(f"  [WARN] Metrics API error ({e.status}): {e.reason}")
    except Exception as e:
        print(f"  [WARN] Could not fetch metrics: {e}")

    return metrics_map


def parse_cpu(value: str) -> float:
    """
    Convert Kubernetes CPU strings to millicores (float).
    Examples:  "250m" → 250.0,  "1" → 1000.0,  "2" → 2000.0
    """
    value = str(value).strip()
    if value.endswith("m"):
        return float(value[:-1])
    if value.endswith("n"):               # nanocores
        return float(value[:-1]) / 1e6
    try:
        return float(value) * 1000        # whole cores → millicores
    except ValueError:
        return 0.0


def parse_mem(value: str) -> float:
    """
    Convert Kubernetes memory strings to bytes (float).
    Examples:  "128Mi" → 134217728,  "1Gi" → 1073741824,  "512Ki" → 524288
    """
    value = str(value).strip()
    units = {
        "Ki": 1024,
        "Mi": 1024 ** 2,
        "Gi": 1024 ** 3,
        "Ti": 1024 ** 4,
        "K":  1000,
        "M":  1000 ** 2,
        "G":  1000 ** 3,
        "T":  1000 ** 4,
    }
    for suffix, multiplier in units.items():
        if value.endswith(suffix):
            try:
                return float(value[:-len(suffix)]) * multiplier
            except ValueError:
                return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


# ─────────────────────────────────────────────────────────────
# Live pod reader
# ─────────────────────────────────────────────────────────────
def get_pods():
    """
    Fetch all pods across all namespaces and merge with live metrics.
    Returns a list of pod dicts ready for JSON serialisation.
    """
    config.load_kube_config()
    v1 = client.CoreV1Api()
    pod_list = v1.list_pod_for_all_namespaces(watch=False)

    # Fetch metrics once per poll cycle (graceful fallback if unavailable)
    metrics_map = get_metrics()

    pods = []
    for p in pod_list.items:
        container_statuses = p.status.container_statuses or []

        # Total restart count across all containers in the pod
        restarts = sum(cs.restart_count for cs in container_statuses)

        # Base phase: Running / Pending / Failed / Succeeded / Unknown
        phase = p.status.phase or "Unknown"

        # Override to CrashLoopBackOff if any container is waiting with that reason
        for cs in container_statuses:
            if cs.state and cs.state.waiting:
                if cs.state.waiting.reason == "CrashLoopBackOff":
                    phase = "CrashLoopBackOff"
                    break

        # Human-readable pod age
        creation   = p.metadata.creation_timestamp
        age_secs   = (datetime.datetime.now(datetime.timezone.utc) - creation).total_seconds()
        hours, rem = divmod(int(age_secs), 3600)
        minutes    = rem // 60
        age_str    = f"{hours}h {minutes}m" if hours else f"{minutes}m"

        # Real CPU / memory from metrics-server (0 if not available or pod not Running)
        key     = f"{p.metadata.namespace}/{p.metadata.name}"
        metrics = metrics_map.get(key, {})
        cpu     = metrics.get("cpu_percent", 0)
        memory  = metrics.get("mem_mib", 0)

        pods.append({
            "name":      p.metadata.name,
            "namespace": p.metadata.namespace,
            "status":    phase,
            "restarts":  restarts,
            "cpu":       cpu,
            "memory":    memory,  # raw MiB
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
        return jsonify({
            "error": str(e),
            "hint":  "Is Minikube still running? Try: minikube status"
        }), 503

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
    """Quick liveness check — also verifies cluster is still reachable."""
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
        v1.list_namespace(_request_timeout=3)
        return jsonify({"status": "ok", "cluster": "reachable"})
    except Exception as e:
        return jsonify({"status": "error", "cluster": "unreachable", "detail": str(e)}), 503


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("╔══════════════════════════════════════════════╗")
    print("║  K8s Pod Health Dashboard — Backend          ║")
    print("║  Verifying cluster connection...             ║")
    print("╚══════════════════════════════════════════════╝")

    verify_cluster()

    print("╔══════════════════════════════════════════════╗")
    print("║  Listening on http://localhost:5000          ║")
    print("╚══════════════════════════════════════════════╝")

    app.run(debug=False, port=5000)
