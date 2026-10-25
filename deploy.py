#!/usr/bin/env python3
"""Deploy to the target board via SSH (requires: pip install paramiko).

Usage: python deploy.py [--host 10.11.0.105] [--user pi] [--password pi] [--no-restart]
"""
import argparse
import os

import paramiko

FILES = ["app.py", "gnss/__init__.py", "gnss/nmea.py", "gnss/modem.py", "gnss/tester.py",
         "static/index.html", "static/style.css", "static/app.js", "gnss-test.service"]
REMOTE = "/home/pi/gnss_test"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="10.11.0.105")
    ap.add_argument("--user", default="pi")
    ap.add_argument("--password", default="pi")
    ap.add_argument("--no-restart", action="store_true")
    a = ap.parse_args()
    base = os.path.dirname(os.path.abspath(__file__))
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(a.host, username=a.user, password=a.password, timeout=10)

    def run(cmd):
        _, o, e = c.exec_command(cmd)
        out = o.read().decode() + e.read().decode()
        if out.strip():
            print(out.strip())

    run("mkdir -p %s/gnss %s/static %s/logs" % ((REMOTE,) * 3))
    s = c.open_sftp()
    for f in FILES:
        s.put(os.path.join(base, f), REMOTE + "/" + f)
        print("uploaded", f)
    s.close()
    if not a.no_restart:
        pw = a.password
        run("echo %s | sudo -S cp %s/gnss-test.service /etc/systemd/system/ && "
            "echo %s | sudo -S systemctl daemon-reload && "
            "echo %s | sudo -S systemctl enable gnss-test && "
            "echo %s | sudo -S systemctl restart gnss-test" % (pw, REMOTE, pw, pw, pw))
        run("sleep 2; systemctl is-active gnss-test")
    print("http://%s:8080/" % a.host)
    c.close()


if __name__ == "__main__":
    main()
