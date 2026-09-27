"""本機 agent 伺服器：開 http://localhost:8787 就是旅程產生器，按「產生行程」會在這台電腦跑 plan_trip.py。

  python agent_server.py            （或雙擊 windows/start_agent.bat）

- 只接受本機連線（127.0.0.1），用你已登入的 Claude Code 訂閱額度，不需要 API 金鑰。
- 同一時間只跑一趟；結果寫進 trips/<slug>/，不會 commit 或 push。
- API：GET /api/health、POST /api/plan、GET /api/jobs/<id>、POST /api/jobs/<id>/cancel
  POST 必須是 JSON 且 Origin 是這台伺服器，避免其他網站在背景叫你的電腦跑 agent（會用掉訂閱額度）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("TRIP_AGENT_PORT", "8787"))
HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


def validate(req: dict) -> dict:
    """檢查表單；不合格丟 ValueError（訊息直接顯示給使用者）。"""
    dest = str(req.get("destination") or "").strip()
    if not dest or len(dest) > 80:
        raise ValueError("目的地要填，且在 80 字內")
    for k in ("start", "end"):
        if not DATE.match(str(req.get(k) or "")):
            raise ValueError("日期格式要是 YYYY-MM-DD")
    d0, d1 = dt.date.fromisoformat(req["start"]), dt.date.fromisoformat(req["end"])
    if not 1 <= (d1 - d0).days <= 13:
        raise ValueError("回程要晚於去程，一次最多 14 天")
    n = int(req.get("travelers") or 0)
    if not 1 <= n <= 9:
        raise ValueError("人數 1–9 人")
    for k in ("arriveBy", "departAfter"):
        v = str(req.get(k) or "")
        if v and v != "不限" and not HM.match(v):
            raise ValueError("時間格式要是 HH:MM")
    if not re.fullmatch(r"[A-Za-z]{3}", str(req.get("origin") or "TPE")):
        raise ValueError("出發機場請填 IATA 三碼，例如 TPE")
    return {**req, "destination": dest, "travelers": n}


def build_args(req: dict, slug: str) -> list[str]:
    """表單 → plan_trip.py 參數（list，不經過 shell）。"""
    notes = "；".join(x for x in [
        f"步調：{req.get('pace')}" if req.get("pace") else "",
        f"興趣：{'、'.join(req.get('interests') or [])}" if req.get("interests") else "",
        f"行李：{req.get('luggage')}" if req.get("luggage") else "",
        f"航空：{req.get('airline')}" if req.get("airline") else "",
        str(req.get("notes") or "").strip()] if x)
    args = ["plan_trip.py", "--slug", slug, "--dest", req["destination"], "--start", req["start"],
            "--end", req["end"], "--travelers", str(req["travelers"]),
            "--origin", str(req.get("origin") or "TPE").upper(), "--budget", str(int(req.get("budget") or 60000)),
            "--notes", notes[:500]]
    if HM.match(str(req.get("arriveBy") or "")):
        args += ["--arrive-by", req["arriveBy"]]
    if HM.match(str(req.get("departAfter") or "")):
        args += ["--depart-after", req["departAfter"]]
    if int(req.get("hotelBudget") or 0) > 0:
        args += ["--hotel-budget", str(int(req["hotelBudget"]))]
    bags = re.findall(r"(\d+)\s*件", str(req.get("luggage") or ""))
    if bags:
        args += ["--bags", str(max(map(int, bags)))]
    if "紅眼" in str(req.get("airline") or ""):
        args.append("--allow-redeye")
    return args


def origin_ok(origin: str | None, port: int) -> bool:
    return origin in (f"http://localhost:{port}", f"http://127.0.0.1:{port}")


def start_job(req: dict) -> dict:
    with LOCK:
        if any(j["status"] == "running" for j in JOBS.values()):
            raise ValueError("已經有一趟在產生中，請等它完成")
        slug = f"trip-{req['start'].replace('-', '')}-{secrets.token_hex(2)}"
        job = {"id": secrets.token_hex(6), "slug": slug, "status": "running", "log": [],
               "started": time.time(), "dest": req["destination"]}
        JOBS[job["id"]] = job
    p = subprocess.Popen([sys.executable, *build_args(req, slug)], cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    job["proc"] = p

    def pump():
        for line in p.stdout:
            job["log"].append(line.rstrip()[:300])
            del job["log"][:-200]
        code = p.wait()
        if job["status"] == "running":
            job["status"] = "done" if code == 0 and (ROOT / "trips" / slug / "trip.json").exists() else "failed"
        job["elapsed"] = round(time.time() - job["started"])

    threading.Thread(target=pump, daemon=True).start()
    return job


def cancel_job(job):
    p = job.get("proc")
    if p and p.poll() is None:
        job["status"] = "cancelled"
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()


def public(job):
    return {k: job[k] for k in ("id", "slug", "status", "dest")} | {
        "log": job["log"][-15:], "elapsed": job.get("elapsed", round(time.time() - job["started"]))}


class Handler(SimpleHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_response(self, code, message=None):
        self._code = code
        super().send_response(code, message)

    def end_headers(self):
        # 產生完馬上看得到新的 trip.json；只加在成功的回應上：
        # 404（例如還沒有 data/hotels.json）加了 no-store，Chromium 會讓那個請求一直掛著、頁面停在載入中
        if not self.path.startswith("/api/") and getattr(self, "_code", 200) < 400:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/health":
            return self._json(200, {"ok": True, "busy": any(j["status"] == "running" for j in JOBS.values())})
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{12})", self.path)
        if m:
            job = JOBS.get(m[1])
            return self._json(200, public(job)) if job else self._json(404, {"error": "找不到這個工作"})
        return super().do_GET()

    def do_POST(self):
        if not origin_ok(self.headers.get("Origin"), PORT) or \
                not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(403, {"error": "只接受從 http://localhost:%d 發出的請求" % PORT})
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{12})/cancel", self.path)
        if m and m[1] in JOBS:
            cancel_job(JOBS[m[1]])
            return self._json(200, public(JOBS[m[1]]))
        if self.path != "/api/plan":
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = validate(json.loads(self.rfile.read(min(n, 20000)).decode("utf-8")))
            return self._json(200, public(start_job(req)))
        except ValueError as e:
            return self._json(400, {"error": str(e)})

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/plan") or "cancel" in self.path:
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


def main():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Handler, directory=str(ROOT)))
    print(f"旅程產生器（本機 agent）：http://localhost:{PORT}/   關閉請按 Ctrl+C", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for j in JOBS.values():
            cancel_job(j)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
