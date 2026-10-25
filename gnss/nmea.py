"""NMEA 0183 reader / parser with a thread-safe live GNSS state."""
import math
import os
import threading
import time
from collections import deque

import serial

TALKER_SYS = {
    "GP": "GPS", "GL": "GLONASS", "GA": "Galileo", "BD": "BeiDou", "GB": "BeiDou",
    "PQ": "BeiDou", "GQ": "QZSS", "QZ": "QZSS", "GI": "NavIC", "GN": None,
}
SYSID = {1: "GPS", 2: "GLONASS", 3: "Galileo", 4: "BeiDou", 5: "QZSS", 6: "NavIC"}
FIX_QUALITY = {0: "无效", 1: "GPS", 2: "DGPS", 3: "PPS", 4: "RTK固定", 5: "RTK浮点", 6: "估算", 7: "手动", 8: "模拟"}


def prn_system(prn):
    if 1 <= prn <= 32:
        return "GPS"
    if 33 <= prn <= 64 or 120 <= prn <= 158:
        return "SBAS"
    if 65 <= prn <= 96:
        return "GLONASS"
    if 193 <= prn <= 200:
        return "QZSS"
    if 201 <= prn <= 263 or 401 <= prn <= 463:
        return "BeiDou"
    if 301 <= prn <= 336:
        return "Galileo"
    return "GNSS"


def norm_prn(system, prn):
    """Normalize PRN numbering so GSV and GSA sentences match."""
    if system == "BeiDou":
        if prn > 400:
            return prn - 400
        if prn > 200:
            return prn - 200
    if system == "Galileo" and prn > 300:
        return prn - 300
    if system == "QZSS" and prn > 192:
        return prn - 192
    return prn


def checksum_ok(line):
    if not line.startswith("$") or "*" not in line:
        return False
    body, cs = line[1:].rsplit("*", 1)
    c = 0
    for ch in body:
        c ^= ord(ch)
    try:
        return c == int(cs[:2], 16)
    except ValueError:
        return False


def _f(v):
    try:
        return float(v) if v not in ("", None) else None
    except ValueError:
        return None


def _i(v):
    try:
        return int(v) if v not in ("", None) else None
    except ValueError:
        return None


def nmea_coord(v, hemi):
    if not v:
        return None
    try:
        d = float(v)
    except ValueError:
        return None
    deg = int(d / 100)
    r = deg + (d - deg * 100) / 60.0
    return -r if hemi in ("S", "W") else r


def enu_offsets(points):
    """points: [(lat, lon)] -> [(east_m, north_m)] relative to their mean."""
    if not points:
        return [], None
    lat0 = sum(p[0] for p in points) / len(points)
    lon0 = sum(p[1] for p in points) / len(points)
    k_lat = 111132.954 - 559.822 * math.cos(2 * math.radians(lat0))
    k_lon = 111319.488 * math.cos(math.radians(lat0))
    return [((p[1] - lon0) * k_lon, (p[0] - lat0) * k_lat) for p in points], (lat0, lon0)


def percentile(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def cep_stats(points):
    offs, mean = enu_offsets(points)
    if not offs:
        return None
    d = [math.hypot(e, n) for e, n in offs]
    return {"cep50": percentile(d, 0.5), "cep95": percentile(d, 0.95), "max": max(d),
            "mean_lat": mean[0], "mean_lon": mean[1], "n": len(d)}


class NmeaReader:
    STALE = 5.0  # seconds before GSV/GSA data is considered stale

    def __init__(self, port, baud=115200, log_dir="logs"):
        self.port = port
        self.baud = baud
        self.log_dir = log_dir
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._ser = None
        self.raw = deque(maxlen=500)
        self.seq = 0
        self.track = deque(maxlen=3600)
        self.rec_file = None
        self.rec_name = None
        self._reset_state()

    # ------------------------------------------------------------------ state
    def _reset_state(self):
        self.connected = False
        self.error = None
        self.total = 0
        self.bad_cs = 0
        self.last_rx = 0.0
        self.rate_win = deque(maxlen=200)
        self.utc_time = None
        self.utc_date = None
        self.lat = self.lon = self.alt = self.geoid = None
        self.quality = 0
        self.num_sats_gga = None
        self.hdop = self.pdop = self.vdop = None
        self.fix_type = 1
        self.rmc_status = "V"
        self.speed_kmh = None
        self.course = None
        self.last_fix_ts = 0.0
        self.first_fix_ts = 0.0
        self._gsv_buf = {}
        self._gsv = {}       # (talker, sysid, sig) -> (ts, [sats])
        self._used = {}      # system -> (ts, set(prn))
        self.gns_mode = None
        self.num_sats_gns = None
        self._reset_filter()

    def _reset_filter(self):
        self.flt_track = deque(maxlen=3600)
        self.flt_lat = self.flt_lon = self.flt_alt = None
        self.sv_n = 0
        self.sv_w = 0.0
        self.sv_lat = self.sv_lon = self.sv_alt = 0.0
        self.sv_m2_e = self.sv_m2_n = 0.0

    def reset_track(self):
        with self.lock:
            self.track.clear()
            self._reset_filter()

    # ----------------------------------------------------------------- thread
    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="nmea")
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=3)

    def set_port(self, port, baud=None):
        self.stop()
        self.port = port
        if baud:
            self.baud = baud
        with self.lock:
            self._reset_state()
        self.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._ser = serial.Serial(self.port, self.baud, timeout=1)
                with self.lock:
                    self.connected, self.error = True, None
                buf = b""
                while not self._stop.is_set():
                    chunk = self._ser.read(self._ser.in_waiting or 1)
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.decode("ascii", "replace").strip()
                        if line:
                            self._handle(line)
                    if len(buf) > 4096:
                        buf = b""
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    self.connected, self.error = False, str(e)
            finally:
                try:
                    if self._ser:
                        self._ser.close()
                except Exception:
                    pass
            self._stop.wait(2)

    # ---------------------------------------------------------------- record
    def start_record(self):
        os.makedirs(self.log_dir, exist_ok=True)
        with self.lock:
            if self.rec_file:
                return self.rec_name
            self.rec_name = time.strftime("nmea_%Y%m%d_%H%M%S.log")
            self.rec_file = open(os.path.join(self.log_dir, self.rec_name), "a", buffering=1)
            return self.rec_name

    def stop_record(self):
        with self.lock:
            if self.rec_file:
                self.rec_file.close()
            self.rec_file, name, self.rec_name = None, self.rec_name, None
            return name

    # ---------------------------------------------------------------- parsing
    def _handle(self, line):
        now = time.time()
        with self.lock:
            self.seq += 1
            self.raw.append((self.seq, now, line))
            self.total += 1
            self.last_rx = now
            self.rate_win.append(now)
            if self.rec_file:
                try:
                    self.rec_file.write(line + "\n")
                except Exception:
                    pass
            if not line.startswith("$"):
                return
            if not checksum_ok(line):
                self.bad_cs += 1
                return
            fields = line[1:line.rindex("*")].split(",")
            head = fields[0]
            talker, kind = head[:2], head[-3:]
            try:
                fn = getattr(self, "_p_" + kind, None)
                if fn:
                    fn(talker, fields, now)
            except (IndexError, ValueError):
                pass

    def _p_GGA(self, talker, f, now):
        self.utc_time = f[1] or self.utc_time
        q = _i(f[6]) or 0
        self.quality = q
        self.num_sats_gga = _i(f[7])
        self.hdop = _f(f[8]) if f[8] else self.hdop
        if q > 0:
            lat, lon = nmea_coord(f[2], f[3]), nmea_coord(f[4], f[5])
            if lat is not None and lon is not None:
                self.lat, self.lon = lat, lon
                self.alt, self.geoid = _f(f[9]), _f(f[11])
                self.last_fix_ts = now
                if not self.first_fix_ts:
                    self.first_fix_ts = now
                self.track.append((now, lat, lon, self.alt))
                self._update_filter(now, lat, lon, self.alt)

    def _p_GNS(self, talker, f, now):
        if len(f) > 7:
            self.gns_mode = f[6] or self.gns_mode
            self.num_sats_gns = _i(f[7]) or self.num_sats_gns

    def _update_filter(self, now, lat, lon, alt):
        hdop = max(0.4, self.hdop or 1.0)
        sats = self.num_sats_gns or self.num_sats_gga or 6
        spd = self.speed_kmh or 0.0
        # 1. Adaptive static/kinematic filter (EMA with zero-velocity clamping)
        if self.flt_lat is None:
            self.flt_lat, self.flt_lon, self.flt_alt = lat, lon, alt
        else:
            k_lat = 111132.954
            k_lon = 111319.488 * math.cos(math.radians(lat))
            step_m = math.hypot((lat - self.flt_lat) * k_lat, (lon - self.flt_lon) * k_lon)
            if spd < 0.3 and step_m < 3.5:
                # Static lock: heavy smoothing weighted by HDOP quality
                alpha = min(0.15, 0.04 / hdop)
            elif spd < 1.0 and step_m < 8.0:
                alpha = 0.25
            else:
                alpha = 0.85
            self.flt_lat += alpha * (lat - self.flt_lat)
            self.flt_lon += alpha * (lon - self.flt_lon)
            if alt is not None:
                self.flt_alt = alt if self.flt_alt is None else (self.flt_alt + alpha * (alt - self.flt_alt))
        self.flt_track.append((now, self.flt_lat, self.flt_lon, self.flt_alt))

        # 2. Survey-In weighted convergence (weight = sats / hdop^2)
        if spd < 0.8 and hdop <= 2.5:
            w = min(50.0, sats / (hdop * hdop))
            self.sv_n += 1
            self.sv_w += w
            r = w / self.sv_w
            k_lat = 111132.954
            k_lon = 111319.488 * math.cos(math.radians(lat))
            de = (lon - self.sv_lon) * k_lon if self.sv_n > 1 else 0.0
            dn = (lat - self.sv_lat) * k_lat if self.sv_n > 1 else 0.0
            self.sv_lat = lat if self.sv_n == 1 else (self.sv_lat + r * (lat - self.sv_lat))
            self.sv_lon = lon if self.sv_n == 1 else (self.sv_lon + r * (lon - self.sv_lon))
            if alt is not None:
                self.sv_alt = alt if self.sv_n == 1 else (self.sv_alt + r * (alt - self.sv_alt))
            if self.sv_n > 1:
                de2 = (lon - self.sv_lon) * k_lon
                dn2 = (lat - self.sv_lat) * k_lat
                self.sv_m2_e += w * de * de2
                self.sv_m2_n += w * dn * dn2

    def _p_RMC(self, talker, f, now):
        self.utc_time = f[1] or self.utc_time
        self.rmc_status = f[2] or "V"
        kn = _f(f[7])
        self.speed_kmh = kn * 1.852 if kn is not None else None
        self.course = _f(f[8])
        self.utc_date = f[9] or self.utc_date

    def _p_VTG(self, talker, f, now):
        if len(f) > 7 and f[7]:
            self.speed_kmh = _f(f[7])
        if f[1]:
            self.course = _f(f[1])

    def _p_GSA(self, talker, f, now):
        self.fix_type = _i(f[2]) or 1
        prns = [_i(x) for x in f[3:15] if x]
        self.pdop, self.hdop, self.vdop = _f(f[15]), _f(f[16]) or self.hdop, _f(f[17])
        sysid = _i(f[18]) if len(f) > 18 else None
        tsys = TALKER_SYS.get(talker)
        groups = {}
        for p in prns:
            if p is None:
                continue
            if sysid in SYSID:
                s = SYSID[sysid]
            elif tsys and not (tsys == "GPS" and p > 32):
                s = tsys
            else:
                s = prn_system(p)
            groups.setdefault(s, set()).add(norm_prn(s, p))
        if not groups:
            s = SYSID.get(sysid) or tsys
            if s:
                groups[s] = set()
        for s, ps in groups.items():
            self._used[s] = (now, ps)

    def _p_GSV(self, talker, f, now):
        total, num = _i(f[1]) or 1, _i(f[2]) or 1
        body = f[4:]
        sig = None
        sysid = None
        if len(body) % 4 == 2:
            sig, sysid = body[-2], _i(body[-1])
            body = body[:-2]
        elif len(body) % 4 == 1:
            sig = body[-1]
            body = body[:-1]
        key = (talker, sysid, sig)
        if num == 1:
            self._gsv_buf[key] = []
        buf = self._gsv_buf.setdefault(key, [])
        tsys = SYSID.get(sysid) or TALKER_SYS.get(talker)
        for i in range(0, len(body) - 3, 4):
            prn = _i(body[i])
            if prn is None:
                continue
            s = tsys if (tsys and not (tsys == "GPS" and prn > 32)) else prn_system(prn)
            buf.append({"sys": s, "prn": norm_prn(s, prn), "el": _i(body[i + 1]),
                        "az": _i(body[i + 2]), "snr": _i(body[i + 3]), "sig": sig})
        if num == total:
            self._gsv[key] = (now, buf)
            self._gsv_buf.pop(key, None)

    # --------------------------------------------------------------- snapshot
    def satellites(self, now=None):
        now = now or time.time()
        used = {}
        for s, (ts, ps) in self._used.items():
            if now - ts < self.STALE:
                used[s] = ps
        merged = {}
        for _key, (ts, sats) in self._gsv.items():
            if now - ts >= self.STALE:
                continue
            for sat in sats:
                k = (sat["sys"], sat["prn"])
                cur = merged.get(k)
                if cur is None:
                    merged[k] = dict(sat)
                else:
                    if (sat["snr"] or 0) > (cur["snr"] or 0):
                        cur["snr"] = sat["snr"]
                    if cur["el"] is None:
                        cur["el"], cur["az"] = sat["el"], sat["az"]
        out = []
        for (s, p), sat in merged.items():
            sat["used"] = p in used.get(s, ())
            out.append(sat)
        # Satellites in use but missing from GSV
        for s, ps in used.items():
            for p in ps:
                if (s, p) not in merged:
                    out.append({"sys": s, "prn": p, "el": None, "az": None, "snr": None, "sig": None, "used": True})
        order = ["GPS", "GLONASS", "Galileo", "BeiDou", "QZSS", "NavIC", "SBAS", "GNSS"]
        out.sort(key=lambda x: (order.index(x["sys"]) if x["sys"] in order else 99, x["prn"]))
        return out

    def snapshot(self):
        now = time.time()
        with self.lock:
            sats = self.satellites(now)
            fix_valid = (now - self.last_fix_ts) < 3.0
            rate = len([t for t in self.rate_win if now - t < 5]) / 5.0
            snrs = sorted([s["snr"] for s in sats if s["snr"]], reverse=True)
            per_sys = {}
            for s in sats:
                d = per_sys.setdefault(s["sys"], {"view": 0, "used": 0})
                d["view"] += 1
                d["used"] += 1 if s["used"] else 0
            sv_std = None
            if self.sv_n > 1 and self.sv_w > 0:
                # Standard error of the converged Survey-In mean (meters)
                rms = math.sqrt((self.sv_m2_e + self.sv_m2_n) / self.sv_w)
                sv_std = rms / math.sqrt(self.sv_n)
            return {
                "port": self.port, "baud": self.baud, "connected": self.connected, "error": self.error,
                "total": self.total, "bad_cs": self.bad_cs,
                "data_age": (now - self.last_rx) if self.last_rx else None, "line_rate": rate,
                "utc_time": self.utc_time, "utc_date": self.utc_date,
                "fix_valid": fix_valid, "quality": self.quality if fix_valid else 0,
                "quality_text": FIX_QUALITY.get(self.quality if fix_valid else 0, str(self.quality)),
                "fix_type": self.fix_type if fix_valid else 1,
                "gns_mode": self.gns_mode,
                "lat": self.lat, "lon": self.lon, "alt": self.alt, "geoid": self.geoid,
                "flt_lat": self.flt_lat, "flt_lon": self.flt_lon, "flt_alt": self.flt_alt,
                "survey": {
                    "n": self.sv_n,
                    "lat": self.sv_lat if self.sv_n else None,
                    "lon": self.sv_lon if self.sv_n else None,
                    "alt": self.sv_alt if self.sv_n else None,
                    "std_m": sv_std,
                },
                "last_fix_age": (now - self.last_fix_ts) if self.last_fix_ts else None,
                "speed_kmh": self.speed_kmh, "course": self.course,
                "hdop": self.hdop, "pdop": self.pdop, "vdop": self.vdop,
                "sats_used": sum(1 for s in sats if s["used"]) or (self.num_sats_gga if fix_valid else 0),
                "sats_view": len(sats), "per_sys": per_sys,
                "snr_max": snrs[0] if snrs else None,
                "snr_top4": (sum(snrs[:4]) / len(snrs[:4])) if snrs else None,
                "satellites": sats,
                "recording": self.rec_name,
                "track_len": len(self.track),
            }

    def raw_since(self, since, limit=300):
        with self.lock:
            lines = [r for r in self.raw if r[0] > since]
            return lines[-limit:], self.seq

    def track_points(self, limit=1800):
        with self.lock:
            pts = list(self.track)[-limit:]
            fpts = list(self.flt_track)[-limit:]
            sv = (self.sv_lat, self.sv_lon) if self.sv_n else None
        if not pts:
            return {"points": [], "flt_points": [], "stats": None, "flt_stats": None}
        lat0, lon0 = sv if sv else (sum(p[1] for p in pts) / len(pts), sum(p[2] for p in pts) / len(pts))
        k_lat = 111132.954 - 559.822 * math.cos(2 * math.radians(lat0))
        k_lon = 111319.488 * math.cos(math.radians(lat0))
        offs = [((p[2] - lon0) * k_lon, (p[1] - lat0) * k_lat) for p in pts]
        foffs = [((p[2] - lon0) * k_lon, (p[1] - lat0) * k_lat) for p in fpts]
        stats = cep_stats([(p[1], p[2]) for p in pts])
        fstats = cep_stats([(p[1], p[2]) for p in fpts])
        alts = [p[3] for p in pts if p[3] is not None]
        if alts and stats:
            m = sum(alts) / len(alts)
            stats["alt_mean"] = m
            stats["alt_std"] = math.sqrt(sum((a - m) ** 2 for a in alts) / len(alts))
        return {
            "points": [[round(e, 3), round(n, 3)] for e, n in offs],
            "flt_points": [[round(e, 3), round(n, 3)] for e, n in foffs],
            "stats": stats,
            "flt_stats": fstats,
        }
