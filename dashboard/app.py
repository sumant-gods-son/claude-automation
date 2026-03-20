"""
GreenTec Automation Dashboard - dashboard/app.py
Flask web server. Runs on Render.com. Shows run logs, triggers agent manually.
"""
import os, json, glob, threading
from datetime import datetime
from flask import Flask, render_template, jsonify, request
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lead-agent"))

app = Flask(__name__)
LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "lead-agent", "logs")
_running = {"status": False, "started": None}


def _bg_run(leads_per_run):
    try:
        from agent import run_agent
        _running["status"] = True
        _running["started"] = datetime.utcnow().isoformat()
        result = run_agent(leads_per_run=leads_per_run)
        _running["last_result"] = result
    except Exception as e:
        _running["error"] = str(e)
    finally:
        _running["status"] = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    return jsonify({
        "running":     _running.get("status", False),
        "started":     _running.get("started"),
        "last_result": _running.get("last_result"),
        "error":       _running.get("error"),
    })


@app.route("/api/run", methods=["POST"])
def trigger_run():
    if _running.get("status"):
        return jsonify({"ok": False, "msg": "Already running"}), 409
    leads = int(request.json.get("leads_per_run", 50))
    t = threading.Thread(target=_bg_run, args=(leads,), daemon=True)
    t.start()
    return jsonify({"ok": True, "msg": f"Agent started - targeting {leads} leads"})


@app.route("/api/logs")
def get_logs():
    os.makedirs(LOGS_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(LOGS_DIR, "run_*.json")), reverse=True)
    runs = []
    for f in files[:20]:
        try:
            with open(f) as fp:
                data = json.load(fp)
            runs.append({
                "file":    os.path.basename(f),
                "started": data.get("started",""),
                "total":   data.get("total", 0),
                "leads":   data.get("leads", [])[:5],
            })
        except:
            pass
    return jsonify(runs)


@app.route("/api/logs/<fname>")
def get_log_detail(fname):
    path = os.path.join(LOGS_DIR, fname)
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    with open(path) as f:
        return jsonify(json.load(f))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
