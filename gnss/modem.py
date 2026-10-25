"""Quectel AT command channel for GNSS control."""
import re
import threading
import time

import serial

FINAL_RE = re.compile(r"^(OK|ERROR|\+CME ERROR:.*|\+CMS ERROR:.*)$", re.M)

# AT+QGPSDEL delete types (Quectel EC2x/EM05 GNSS manual)
DEL_TYPES = {"cold": 0, "hot": 1, "warm": 2}

CFG_KEYS = ["outport", "nmeasrc", "gpsnmeatype", "glonassnmeatype", "galileonmeatype",
            "beidounmeatype", "gsvextnmeatype", "gnssconfig", "dpoenable", "fixfreq", "autogps", "1pps"]


class ATPort:
    def __init__(self, port, baud=115200):
        self.port = port
        self.baud = baud
        self.lock = threading.Lock()
        self.history = []

    def send(self, cmd, timeout=3.0):
        cmd = cmd.strip()
        with self.lock:
            t0 = time.time()
            try:
                with serial.Serial(self.port, self.baud, timeout=0.1) as s:
                    s.reset_input_buffer()
                    s.write((cmd + "\r").encode())
                    buf = ""
                    end = time.time() + timeout
                    while time.time() < end:
                        chunk = s.read(1024)
                        if chunk:
                            buf += chunk.decode("ascii", "replace")
                            if FINAL_RE.search(buf.replace("\r", "")):
                                break
                resp = buf.replace("\r", "").strip()
                if resp.startswith(cmd):
                    resp = resp[len(cmd):].strip()
                ok = bool(re.search(r"(^|\n)OK$", resp))
                if not resp:
                    resp, ok = "(超时无响应)", False
            except Exception as e:  # noqa: BLE001
                resp, ok = "串口错误: %s" % e, False
            item = {"t": time.time(), "cmd": cmd, "resp": resp, "ok": ok, "ms": int((time.time() - t0) * 1000)}
            self.history.append(item)
            del self.history[:-200]
            return item

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def value(resp, prefix):
        m = re.search(re.escape(prefix) + r"\s*(.*)", resp)
        return m.group(1).strip() if m else None

    def gnss_state(self):
        r = self.send("AT+QGPS?")
        v = self.value(r["resp"], "+QGPS:")
        return None if v is None else v.split(",")[0] == "1"

    def gnss_on(self):
        return self.send("AT+QGPS=1")

    def gnss_off(self):
        return self.send("AT+QGPSEND")

    def restart(self, mode):
        """mode: cold / warm / hot. Returns list of AT results."""
        out = [self.gnss_off()]
        time.sleep(0.5)
        if mode in DEL_TYPES:
            out.append(self.send("AT+QGPSDEL=%d" % DEL_TYPES[mode]))
        out.append(self.gnss_on())
        return out

    def read_config(self):
        cfg = {}
        for k in CFG_KEYS:
            r = self.send('AT+QGPSCFG="%s"' % k, timeout=2)
            v = self.value(r["resp"], '+QGPSCFG: "%s",' % k)
            if v is None and r["ok"]:
                v = self.value(r["resp"], "+QGPSCFG:")
                v = v.split("\n")[0].strip('"') if v else None
            cfg[k] = v
        cfg["qgps"] = self.gnss_state()
        info = self.send("ATI")
        cfg["module"] = " ".join(l for l in info["resp"].split("\n") if l and l != "OK")
        return cfg
