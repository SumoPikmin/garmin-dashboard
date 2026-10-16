"""Local web dashboard.

    .venv\\Scripts\\python dashboard.py          your data  -> http://127.0.0.1:8765
    .venv\\Scripts\\python dashboard.py --demo   synthetic demo data
"""
from __future__ import annotations

import sys
import threading
import traceback
import webbrowser
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from garmin_dash import analytics, db
from garmin_dash.config import DASHBOARD_PORT

STATIC = Path(__file__).resolve().parent / "static"


def create_app(demo: bool = False) -> Starlette:
    if demo:
        db.use_demo()
    sync_state = {"running": False, "log": [], "result": None}

    async def index(_):
        return FileResponse(STATIC / "index.html")

    async def dashboard(_):
        try:
            conn = db.connect(readonly=True)
        except Exception:
            return JSONResponse({"empty": True, "message": "No data yet — run login.py and sync.py first."})
        try:
            return JSONResponse(analytics.build_dashboard(conn))
        except Exception as e:
            traceback.print_exc()
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
        finally:
            conn.close()

    def run_sync():
        from garmin_dash.sync import sync
        try:
            sync_state["result"] = sync(log=lambda m: sync_state["log"].append(m))
        except Exception as e:
            sync_state["result"] = {"errors": ["sync"], "message": str(e)}
        finally:
            sync_state["running"] = False

    async def start_sync(_):
        if demo:
            return JSONResponse({"ok": False, "message": "Demo mode — sync is disabled."})
        if not sync_state["running"]:
            sync_state.update(running=True, log=[], result=None)
            threading.Thread(target=run_sync, daemon=True).start()
        return JSONResponse({"ok": True})

    async def sync_status(_):
        return JSONResponse({**sync_state, "log": sync_state["log"][-12:]})

    async def no_cache(request, call_next):  # always serve the latest UI after an update
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"
        return response

    return Starlette(middleware=[Middleware(BaseHTTPMiddleware, dispatch=no_cache)], routes=[
        Route("/", index),
        Route("/api/dashboard", dashboard),
        Route("/api/sync", start_sync, methods=["POST"]),
        Route("/api/sync", sync_status, methods=["GET"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ])


if __name__ == "__main__":
    import uvicorn
    demo = "--demo" in sys.argv
    url = f"http://127.0.0.1:{DASHBOARD_PORT}/"
    print(f"Garmin dashboard{' (demo data)' if demo else ''}: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(demo=demo), host="127.0.0.1", port=DASHBOARD_PORT, log_level="warning")
