# SPDX-License-Identifier: Apache-2.0
"""Make sure the MousePods controller (Bluetooth + web UI + game bridge) is running.

Anyone on the machine can listen on 127.0.0.1:<port>. To know the server there is our own
controller, the controller writes a random instance token to a per-user 0600 file in
XDG_RUNTIME_DIR and returns it from /api/ping; we only trust a server that echoes it.
"""
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTROLLER = os.path.join(HERE, "airpods_mouse_controller.py")


class ForeignServer(RuntimeError):
    """Something that is not our controller answers on the MousePods port."""


def token_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, f"mousepods-{os.getuid()}.token")


def read_token():
    try:
        with open(token_path(), encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        return ""


def ping(port):
    """"ours" (our controller), "foreign" (someone else answers), or "down"."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            body = json.loads(r.read() or b"{}")
    except (OSError, ValueError):
        return "down"
    mine = read_token()
    theirs = str(body.get("instance", "")) if isinstance(body, dict) else ""
    return "ours" if mine and hmac.compare_digest(mine, theirs) else "foreign"


def ensure_running(cfg_path, port, log_path, wait_s=10.0):
    """None if our controller already answers; else start one and return its Popen.
    Raises ForeignServer if another program holds the port."""
    state = ping(port)
    if state == "ours":
        return None
    if state == "foreign":
        raise ForeignServer(f"port {port} is used by another program")
    log = open(log_path, "a")   # noqa: SIM115  (handed to the child, closed by it)
    proc = subprocess.Popen([sys.executable, CONTROLLER, "-c", cfg_path, "--port", str(port)],
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    tries = int(wait_s / 0.2)
    while tries > 0:
        state = ping(port)
        if state == "ours":
            break
        if state == "foreign":
            raise ForeignServer(f"port {port} is used by another program")
        time.sleep(0.2)
        tries -= 1
    return proc
