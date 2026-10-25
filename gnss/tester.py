"""Automated GNSS test: restart -> TTFF -> observation -> pass/fail report."""
import json
import math
import os
import threading
import time

from .nmea import cep_stats

DEFAULT_PARAMS = {
    "mode": "cold",          # cold / warm / hot / none
    "ttff_timeout": 180,     # s, give up waiting for first fix
    "observe": 60,           # s, observation period after fix
    "max_ttff": 60,          # s, criterion (0 = ignore)
    "min_sats": 6,           # criterion: average satellites used
    "min_snr": 35,           # dB-Hz, criterion: average of top-4 C/N0
    "max_cep50": 5,          # m, criterion (0 = ignore)
    "min_fix_rate": 95,      # %, criterion
    "label": "",             # SN / free text
}


class TestRunner:
    def __init__(self, reader, at, results_file="results.json"):
        self.reader = reader
        self.at = at
        self.results_file = results_file
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.status = {"state": "idle"}
        self.results = self._load()

    def _load(self):
        try:
            with open(self.results_file) as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self):
        tmp = self.results_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.results[-500:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.results_file)

    def clear_results(self):
        with self.lock:
            self.results = []
            self._save()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, params):
        if self.running:
            raise RuntimeError("测试正在进行中")
        p = dict(DEFAULT_PARAMS)
        for k, v in (params or {}).items():
            if k in p:
                p[k] = v if isinstance(p[k], str) else float(v)
        self._stop.clear()
        self.status = {"state": "preparing", "params": p, "started": time.time(), "log": []}
        self._thread = threading.Thread(target=self._run, args=(p,), daemon=True, name="tester")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _log(self, msg):
        self.status.setdefault("log", []).append([time.time(), msg])

    def _run(self, p):
        st = self.status
        res = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "label": p["label"], "mode": p["mode"], "params": p}
        try:
            # 1. restart engine
            if p["mode"] in ("cold", "warm", "hot"):
                self._log("执行%s启动..." % {"cold": "冷", "warm": "温", "hot": "热"}[p["mode"]])
                for r in self.at.restart(p["mode"]):
                    self._log("%s -> %s" % (r["cmd"], r["resp"].replace("\n", " ")))
            else:
                if not self.at.gnss_state():
                    r = self.at.gnss_on()
                    self._log("%s -> %s" % (r["cmd"], r["resp"]))
            t0 = time.time()
            self.reader.reset_track()
            st.update(state="acquiring", t0=t0)
            self._log("等待首次定位 (超时 %ds)" % p["ttff_timeout"])

            # 2. wait for first fix
            ttff = None
            first_sat_t = None
            while not self._stop.is_set():
                now = time.time()
                snap = self.reader.snapshot()
                if first_sat_t is None and any(s["snr"] for s in snap["satellites"]):
                    first_sat_t = now
                    self._log("首次捕获卫星信号 %.1fs" % (now - t0))
                with self.reader.lock:
                    lf = self.reader.last_fix_ts
                if lf > t0:
                    ttff = lf - t0
                    break
                st["elapsed"] = now - t0
                st["live"] = _brief(snap)
                if now - t0 > p["ttff_timeout"]:
                    break
                time.sleep(0.2)
            if self._stop.is_set():
                raise _Abort()
            res["ttff"] = ttff
            res["first_signal"] = (first_sat_t - t0) if first_sat_t else None
            if ttff is None:
                self._log("定位超时")
                res["observe_done"] = 0
            else:
                self._log("首次定位 TTFF = %.1fs" % ttff)
                # 3. observation
                st.update(state="observing", obs_t0=time.time())
                self.reader.reset_track()
                samples = []
                t1 = time.time()
                while not self._stop.is_set() and time.time() - t1 < p["observe"]:
                    snap = self.reader.snapshot()
                    samples.append(snap_sample(snap))
                    st["elapsed"] = time.time() - t0
                    st["obs_elapsed"] = time.time() - t1
                    st["live"] = _brief(snap)
                    self._stop.wait(1.0)
                if self._stop.is_set():
                    raise _Abort()
                res.update(summarize(samples, self.reader))
            res.update(evaluate(res, p))
            self._log("测试完成: %s" % ("通过" if res["pass"] else "不通过"))
            st["state"] = "done"
        except _Abort:
            res.update(evaluate(res, p))
            res["pass"] = False
            res["aborted"] = True
            st["state"] = "aborted"
            self._log("测试已中止")
        except Exception as e:  # noqa: BLE001
            res.update({"pass": False, "error": str(e), "checks": []})
            st["state"] = "error"
            self._log("错误: %s" % e)
        st["result"] = res
        st["finished"] = time.time()
        with self.lock:
            self.results.append(res)
            self._save()


class _Abort(Exception):
    pass


def _brief(snap):
    return {k: snap.get(k) for k in ("fix_valid", "fix_type", "sats_used", "sats_view", "snr_top4", "snr_max", "hdop")}


def snap_sample(snap):
    snrs = [s["snr"] for s in snap["satellites"] if s["snr"]]
    return {
        "t": time.time(), "fix": snap["fix_valid"], "fix_type": snap["fix_type"],
        "used": snap["sats_used"] or 0, "view": snap["sats_view"],
        "snr_top4": snap["snr_top4"], "snr_max": snap["snr_max"],
        "snr_avg": (sum(snrs) / len(snrs)) if snrs else None,
        "hdop": snap["hdop"], "pdop": snap["pdop"],
        "per_sys": snap["per_sys"],
    }


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def summarize(samples, reader):
    n = len(samples) or 1
    with reader.lock:
        pts = list(reader.track)
    cep = cep_stats([(p[1], p[2]) for p in pts])
    alts = [p[3] for p in pts if p[3] is not None]
    alt_std = None
    if alts:
        m = sum(alts) / len(alts)
        alt_std = math.sqrt(sum((a - m) ** 2 for a in alts) / len(alts))
    sys_max = {}
    for s in samples:
        for k, v in (s["per_sys"] or {}).items():
            cur = sys_max.setdefault(k, {"view": 0, "used": 0})
            cur["view"] = max(cur["view"], v["view"])
            cur["used"] = max(cur["used"], v["used"])
    return {
        "observe_done": len(samples),
        "fix_rate": 100.0 * sum(1 for s in samples if s["fix"]) / n,
        "fix3d_rate": 100.0 * sum(1 for s in samples if s["fix"] and s["fix_type"] == 3) / n,
        "sats_used_avg": _avg([s["used"] for s in samples]),
        "sats_used_min": min((s["used"] for s in samples), default=None),
        "sats_view_max": max((s["view"] for s in samples), default=None),
        "snr_top4_avg": _avg([s["snr_top4"] for s in samples]),
        "snr_avg": _avg([s["snr_avg"] for s in samples]),
        "snr_max": max((s["snr_max"] or 0 for s in samples), default=None),
        "hdop_avg": _avg([s["hdop"] for s in samples]),
        "pdop_avg": _avg([s["pdop"] for s in samples]),
        "cep50": cep["cep50"] if cep else None,
        "cep95": cep["cep95"] if cep else None,
        "lat": cep["mean_lat"] if cep else None,
        "lon": cep["mean_lon"] if cep else None,
        "alt": (sum(alts) / len(alts)) if alts else None,
        "alt_std": alt_std,
        "per_sys": sys_max,
    }


def evaluate(res, p):
    checks = []

    def chk(name, val, limit, ok, unit):
        checks.append({"name": name, "value": val, "limit": limit, "pass": bool(ok), "unit": unit})

    ttff = res.get("ttff")
    chk("定位成功", "是" if ttff is not None else "否", "是", ttff is not None, "")
    if p["max_ttff"] > 0:
        chk("TTFF", ttff, "≤ %g" % p["max_ttff"], ttff is not None and ttff <= p["max_ttff"], "s")
    if ttff is not None:
        if p["min_sats"] > 0:
            v = res.get("sats_used_avg")
            chk("平均使用卫星数", v, "≥ %g" % p["min_sats"], v is not None and v >= p["min_sats"], "")
        if p["min_snr"] > 0:
            v = res.get("snr_top4_avg")
            chk("Top4 平均载噪比", v, "≥ %g" % p["min_snr"], v is not None and v >= p["min_snr"], "dB-Hz")
        if p["max_cep50"] > 0:
            v = res.get("cep50")
            chk("CEP50", v, "≤ %g" % p["max_cep50"], v is not None and v <= p["max_cep50"], "m")
        if p["min_fix_rate"] > 0:
            v = res.get("fix_rate")
            chk("定位保持率", v, "≥ %g" % p["min_fix_rate"], v is not None and v >= p["min_fix_rate"], "%")
    return {"checks": checks, "pass": all(c["pass"] for c in checks)}
