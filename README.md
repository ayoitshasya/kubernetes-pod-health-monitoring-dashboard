# Kubernetes Pod Health Dashboard

A real-time Kubernetes pod monitoring dashboard built with Python (Flask) and vanilla HTML/CSS/JS. Connects to a live Minikube cluster, reads pod data every 5 seconds, and displays status, CPU usage, memory (in MiB), restart counts, and live alerts for failing pods.

---

## Project Structure

```
k8s-pod-dashboard/
├── k8s_backend.py        # Python Flask backend — reads live K8s API
├── k8s_dashboard.html    # Frontend dashboard (standalone, no build step)
├── requirements.txt      # Python dependencies
├── demo_setup.bat        # Windows 11 one-click setup + launch
└── README.md
```

---

## Windows 11 Quick Start

### Prerequisites

- [Python 3.8+](https://www.python.org/downloads/) — tick **Add Python to PATH** during install
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — must be running before Minikube starts
- [Minikube](https://minikube.sigs.k8s.io/docs/start/) — use the Docker driver (required on Windows 11 Home)

> **Windows 11 Home users:** Hyper-V is not available. Always use `--driver=docker` with Minikube.

---

### Step 1 — Start Minikube

```bash
minikube start --driver=docker
```

Verify the cluster is up:

```bash
kubectl get nodes
# NAME       STATUS   ROLES           AGE   VERSION
# minikube   Ready    control-plane   1m    v1.33.0
```

---

### Step 2 — Enable metrics-server

Required for real CPU and memory values to appear on the dashboard:

```bash
minikube addons enable metrics-server
```

Wait ~60 seconds, then confirm it's working:

```bash
kubectl top pods
```

You should see CPU (cores) and MEMORY (bytes) columns with real values.

---

### Step 3 — Deploy pods

```bash
kubectl create deployment api-server --image=nginx  --replicas=2
kubectl create deployment db-replica --image=redis  --replicas=1
kubectl create deployment cache-svc  --image=busybox -- sleep 3600
```

Deploy the CrashLoopBackOff demo pod (for live alerts):

```bash
kubectl run crash-demo --image=busybox --restart=Always -- /bin/sh -c "exit 1"
```

Check pods are running:

```bash
kubectl get pods
```

---

### Step 4 — Install Python dependencies

```bash
pip install -r requirements.txt
```

---

### Step 5 — Start the backend

```bash
python k8s_backend.py
```

Expected output:

```
╔══════════════════════════════════════════════╗
║  K8s Pod Health Dashboard — Backend          ║
║  Verifying cluster connection...             ║
╚══════════════════════════════════════════════╝
  [OK] Cluster connection verified
╔══════════════════════════════════════════════╗
║  Listening on http://localhost:5000          ║
╚══════════════════════════════════════════════╝
```

Leave this terminal open.

---

### Step 6 — Connect the frontend

Open `k8s_dashboard.html` in a text editor and update the `API_URL` line near the top of the `<script>` block:

```javascript
// Change this:
const API_URL = null;

// To this:
const API_URL = 'http://localhost:5000/api/pods';
```

Save the file, then double-click `k8s_dashboard.html` to open it in your browser.

---

### Step 7 — Verify

Open a new terminal and run:

```bash
curl http://localhost:5000/api/health
```

Expected response:

```json
{"status": "ok", "cluster": "reachable"}
```

The dashboard will now show your live pods updating every 5 seconds, with real CPU % and memory in MiB sourced directly from the metrics-server.

---

## Using demo_setup.bat (Automated)

On Windows 11, double-click `demo_setup.bat` to run all steps automatically.

| Command | What it does |
|---|---|
| `demo_setup.bat` | Full setup — installs deps, starts Minikube, deploys pods, opens dashboard |
| `demo_setup.bat --demo` | Deploy fresh crash pod just before presenting |
| `demo_setup.bat --status` | Check Docker / Minikube / backend are all running |
| `demo_setup.bat --stop` | Stop backend and Minikube |

> `demo_setup.bat` requires Docker Desktop to be running before execution.

---

## Architecture

```
┌──────────────────┐     API      ┌────────────────────┐    REST    ┌──────────────────┐
│   K8s Cluster    │ ──────────→  │  Python Backend     │ ────────→  │ HTML Dashboard   │
│                  │              │  Flask  port 5000    │           │                  │
│  kubectl / K8s   │              │  • Reads K8s API    │           │  • Pod grid       │
│  API server      │              │  • metrics-server   │           │  • CPU % bars     │
│                  │              │  • Alert engine     │           │  • Memory Mi bars │
│  metrics-server  │              │  • REST /api/pods   │           │  • Alert panel    │
│  (addon)         │              └────────────────────┘           │  • NS filter      │
└──────────────────┘                                                └──────────────────┘
```

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/pods` | GET | All pods with status, CPU %, memory MiB, restarts, alerts |
| `/api/health` | GET | Backend + cluster liveness check |

### Sample `/api/pods` Response

```json
{
  "pods": [
    {
      "name":      "api-server-5dddd5f695-2tzmx",
      "namespace": "default",
      "status":    "Running",
      "restarts":  0,
      "cpu":       1,
      "memory":    10.0,
      "age":       "19m"
    },
    {
      "name":      "crash-demo",
      "namespace": "default",
      "status":    "CrashLoopBackOff",
      "restarts":  7,
      "cpu":       0,
      "memory":    0,
      "age":       "4m"
    }
  ],
  "summary": { "total": 4, "running": 3, "crashing": 1, "pending": 0 },
  "alerts":  [ ... ],
  "timestamp": "2025-04-18T14:00:00Z"
}
```

> `cpu` is a percentage of node allocatable CPU. `memory` is raw MiB as reported by metrics-server.

---

## Dashboard Features

| Feature | Description |
|---|---|
| **Summary cards** | Total / Running / Failing / Pending pod counts |
| **Pod grid** | Color-coded cards — status badge, restart count, CPU % bar, Memory MiB bar |
| **CrashLoop detection** | Red pulsing border + alert on `CrashLoopBackOff` pods |
| **Pending detection** | Amber highlight + alert for pods stuck in `Pending` |
| **High restart alert** | Alert fires when restart count ≥ 5 |
| **Namespace filter** | One-click filter by namespace |
| **Pod search** | Live search by pod name or namespace |
| **Auto-refresh** | Polls backend every 5 seconds, last-refresh timestamp shown |

---

## Live Demo Script (for Presentation)

**Step 1 — Show healthy cluster**
Dashboard loads with Running pods, green summary numbers, and real CPU/memory values.

**Step 2 — Delete a pod live and watch it respawn**
```bash
kubectl delete pod <pod-name>
```
Dashboard detects the pod going down and a replacement spinning up within 5 seconds.

**Step 3 — Show CrashLoop alert**
```bash
kubectl run crash-demo --image=busybox --restart=Always -- /bin/sh -c "exit 1"
```
Watch the restart counter climb and the alert panel fire in real time.

**Step 4 — Scale a deployment**
```bash
kubectl scale deployment api-server --replicas=4
```
Two new pod cards appear on the dashboard automatically.

**Step 5 — Filter by namespace**
Click `default`, `monitoring`, or any namespace button to isolate pods.

---

## Module Mapping (Syllabus Alignment)

| Concept | Where it appears |
|---|---|
| **Pod** (Module 3.3) | Every card in the pod grid |
| **ReplicaSet** | Replacement pod spin-up after `kubectl delete` |
| **Deployment** (Module 3.3) | Pods created via `kubectl create deployment` |
| **Namespace** (Module 3) | Filter bar, per-pod namespace label |
| **CrashLoopBackOff** | Live alert + red pulsing card border |
| **Resource pooling** (Module 2) | CPU % and memory MiB bars from metrics-server |
| **YAML / kubectl** (Module 3.3) | Pod creation and cluster management commands |

---

## Troubleshooting

**`ModuleNotFoundError: flask`**
Run: `pip install -r requirements.txt`

**Backend exits immediately on start**
Minikube is not running. Run: `minikube start --driver=docker`

**Dashboard shows `0Mi` for all memory**
metrics-server is not enabled or not ready yet. Run: `minikube addons enable metrics-server` then wait 60 seconds and restart the backend.

**`API_URL` is null — dashboard shows no real pods**
Open `k8s_dashboard.html` and set `const API_URL = 'http://localhost:5000/api/pods'`

**`Connection refused` on frontend**
Make sure `python k8s_backend.py` is running in a separate terminal and the backend window shows `Listening on http://localhost:5000`

**CORS error in browser**
Check `API_URL` uses `http://` not `https://`. The backend includes `flask-cors` automatically.

**Memory showing `0Mi` for some pods**
Pods with very low memory usage (e.g. busybox sleeping) genuinely use ~0Mi. This is correct — `kubectl top pods` will confirm.

---

## License

MIT — free to use, modify, and present.
