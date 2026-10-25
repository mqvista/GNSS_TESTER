#!/usr/bin/env python3
"""GNSS module test web app (Quectel EM05/EC2x series).

Run:  python3 app.py [--host 0.0.0.0] [--port 8080]
"""
import argparse
import csv
import io
import json
import os
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from gnss.modem import ATPort, CFG_KEYS
from gnss.nmea import NmeaReader
from gnss.tester import DEFAULT_PARAMS, TestRunner

BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE, "settings.json")
LOG_DIR = os.path.join(BASE, "logs")
DEFAULT_SETTINGS = {"nmea_port": "/dev/ttyUSB1", "at_port": "/dev/ttyUSB3", "baud": 115200,
                    "auto_on": True, "test": DEFAULT_PARAMS}


def load_settings():
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        with open(SETTINGS_FILE) as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


settings = load_settings()
app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")
reader = NmeaReader(settings["nmea_port"], settings["baud"], LOG_DIR)
at = ATPort(settings["at_port"])
tester = TestRunner(reader, at, os.path.join(BASE, "results.json"))


def busy_guard():
    if tester.running:
        return jsonify({"ok": False, "error": "自动测试进行中, 请先停止测试"}), 409
    return None


@app.after_request
def no_cache(resp):
    if request.path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ------------------------------------------------------------------ live data
@app.get("/api/status")
def api_status():
    snap = reader.snapshot()
    snap["test_state"] = tester.status.get("state")
    snap["server_time"] = time.time()
    return jsonify(snap)


@app.get("/api/nmea")
def api_nmea():
    since = int(request.args.get("since", 0))
    lines, seq = reader.raw_since(since)
    return jsonify({"seq": seq, "lines": [[s, round(t, 3), l] for s, t, l in lines]})


@app.get("/api/track")
def api_track():
    return jsonify(reader.track_points(int(request.args.get("limit", 1800))))


@app.post("/api/track/reset")
def api_track_reset():
    reader.reset_track()
    return jsonify({"ok": True})


# ------------------------------------------------------------------- control
@app.post("/api/at")
def api_at():
    cmd = (request.json or {}).get("cmd", "").strip()
    if not cmd:
        return jsonify({"ok": False, "error": "空命令"}), 400
    if not cmd.upper().startswith("AT"):
        cmd = "AT" + cmd
    return jsonify(at.send(cmd, float((request.json or {}).get("timeout", 5))))


@app.get("/api/at/history")
def api_at_history():
    return jsonify(at.history[-50:])


@app.post("/api/gnss")
def api_gnss():
    g = busy_guard()
    if g:
        return g
    action = (request.json or {}).get("action")
    if action == "on":
        res = [at.gnss_on()]
    elif action == "off":
        res = [at.gnss_off()]
    elif action in ("cold", "warm", "hot"):
        res = at.restart(action)
        reader.reset_track()
    elif action == "multi":
        res = [at.gnss_off()]
        time.sleep(0.3)
        for k, v in [("gnssconfig", 4), ("dpoenable", 0), ("gpsnmeatype", 31), ("glonassnmeatype", 7),
                     ("galileonmeatype", 1), ("beidounmeatype", 3), ("autogps", 1)]:
            res.append(at.send('AT+QGPSCFG="%s",%d' % (k, v)))
        res.append(at.gnss_on())
    else:
        return jsonify({"ok": False, "error": "未知操作"}), 400
    return jsonify({"ok": all(r["ok"] for r in res), "results": res, "state": at.gnss_state()})


@app.get("/api/config")
def api_config():
    cfg = at.read_config()
    return jsonify({"module": cfg, "settings": settings})


@app.post("/api/config")
def api_config_set():
    """body: {key: value, ...}  keys from CFG_KEYS. Value sent verbatim."""
    g = busy_guard()
    if g:
        return g
    body = request.json or {}
    out = []
    for k, v in body.items():
        if k not in CFG_KEYS:
            continue
        v = str(v).strip()
        if k == "outport" and not v.startswith('"'):
            v = '"%s"' % v
        out.append(at.send('AT+QGPSCFG="%s",%s' % (k, v)))
    return jsonify({"ok": all(r["ok"] for r in out), "results": out})


@app.get("/api/settings")
def api_settings_get():
    return jsonify(settings)


@app.post("/api/settings")
def api_settings():
    body = request.json or {}
    changed_port = False
    for k in ("nmea_port", "at_port", "baud", "auto_on"):
        if k in body:
            v = int(body[k]) if k == "baud" else body[k]
            if settings.get(k) != v:
                settings[k] = v
                changed_port |= k in ("nmea_port", "baud")
    if "test" in body:
        settings["test"] = {**DEFAULT_PARAMS, **body["test"]}
    save_settings(settings)
    at.port = settings["at_port"]
    if changed_port:
        reader.set_port(settings["nmea_port"], settings["baud"])
    return jsonify({"ok": True, "settings": settings})


@app.get("/api/ports")
def api_ports():
    import glob
    ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyS[0-9]*"))
    byid = {}
    for p in glob.glob("/dev/serial/by-id/*"):
        byid[os.path.realpath(p)] = os.path.basename(p)
    return jsonify([{"dev": p, "desc": byid.get(p, "")} for p in ports])


# ---------------------------------------------------------------- recording
@app.post("/api/record")
def api_record():
    on = (request.json or {}).get("on")
    name = reader.start_record() if on else reader.stop_record()
    return jsonify({"ok": True, "file": name, "recording": reader.rec_name})


@app.get("/api/logs")
def api_logs():
    os.makedirs(LOG_DIR, exist_ok=True)
    files = []
    for f in sorted(os.listdir(LOG_DIR), reverse=True):
        p = os.path.join(LOG_DIR, f)
        files.append({"name": f, "size": os.path.getsize(p), "mtime": os.path.getmtime(p)})
    return jsonify(files)


@app.get("/logs/<path:name>")
def get_log(name):
    return send_from_directory(LOG_DIR, name, as_attachment=True)


@app.delete("/api/logs/<path:name>")
def del_log(name):
    if name == reader.rec_name:
        return jsonify({"ok": False, "error": "正在录制"}), 409
    p = os.path.join(LOG_DIR, os.path.basename(name))
    if os.path.exists(p):
        os.remove(p)
    return jsonify({"ok": True})


# --------------------------------------------------------------------- tests
@app.post("/api/test/start")
def api_test_start():
    params = request.json or {}
    try:
        tester.start(params)
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    settings["test"] = {**DEFAULT_PARAMS, **{k: v for k, v in params.items() if k in DEFAULT_PARAMS}}
    save_settings(settings)
    return jsonify({"ok": True})


@app.post("/api/test/stop")
def api_test_stop():
    tester.stop()
    return jsonify({"ok": True})


@app.get("/api/test")
def api_test():
    st = dict(tester.status)
    st["running"] = tester.running
    st["now"] = time.time()
    return jsonify(st)


@app.get("/api/results")
def api_results():
    return jsonify(tester.results[::-1])


@app.delete("/api/results")
def api_results_clear():
    tester.clear_results()
    return jsonify({"ok": True})


@app.get("/api/results.csv")
def api_results_csv():
    cols = ["time", "label", "mode", "pass", "ttff", "first_signal", "sats_used_avg", "sats_view_max",
            "snr_top4_avg", "snr_avg", "snr_max", "hdop_avg", "pdop_avg", "cep50", "cep95", "fix_rate",
            "fix3d_rate", "lat", "lon", "alt", "alt_std", "aborted", "error"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in tester.results:
        row = []
        for c in cols:
            v = r.get(c)
            row.append(round(v, 7 if c in ("lat", "lon") else 2) if isinstance(v, float) else ("" if v is None else v))
        w.writerow(row)
    data = "\ufeff" + buf.getvalue()  # BOM so Excel opens UTF-8 correctly
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=gnss_results_%s.csv" % time.strftime("%Y%m%d_%H%M%S")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    reader.start()
    if settings.get("auto_on"):
        try:
            for k, v in [("gnssconfig", 4), ("dpoenable", 0), ("gpsnmeatype", 31),
                         ("glonassnmeatype", 7), ("galileonmeatype", 1), ("beidounmeatype", 3)]:
                at.send('AT+QGPSCFG="%s",%d' % (k, v))
            if at.gnss_state() is False:
                at.gnss_on()
        except Exception:
            pass
    app.run(host=a.host, port=a.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
