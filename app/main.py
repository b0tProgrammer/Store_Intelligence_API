import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.anomalies import compute_anomalies
from app.anomalies import router as anomalies_router
from app.database import init_db
from app.funnel import compute_funnel
from app.funnel import router as funnel_router
from app.health import compute_health
from app.health import router as health_router
from app.heatmap import router as heatmap_router
from app.ingestion import router as ingest_router
from app.logging_config import get_logger
from app.metrics import compute_metrics
from app.metrics import router as metrics_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("database_initialized")
    yield


app = FastAPI(title="Store Intelligence API", version="1.0.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(ingest_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomalies_router)
app.include_router(health_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid4())
    request.state.trace_id = trace_id
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - start) * 1000)
    logger.info(
        "request",
        extra={
            "trace_id": trace_id,
            "endpoint": request.url.path,
            "method": request.method,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        },
    )
    return response


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error(
        "unhandled_exception",
        extra={"trace_id": trace_id, "path": str(request.url.path), "error": str(exc)},
    )
    return JSONResponse(status_code=503, content={"error": "Service temporarily unavailable"})


# --- Dashboard routes ---

@app.get("/dashboard")
async def dashboard_default(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request, "store_id": "STORE_1"})


@app.get("/dashboard/{store_id}")
async def dashboard(request: Request, store_id: str):
    return templates.TemplateResponse("dashboard.html", {"request": request, "store_id": store_id})


# --- SSE live feed ---

@app.get("/sse/metrics/{store_id}")
async def sse_metrics(store_id: str, request: Request):
    async def generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                metrics = await compute_metrics(store_id)
                funnel = await compute_funnel(store_id)
                anomalies = await compute_anomalies(store_id)
                payload = {
                    "metrics": metrics.model_dump(mode="json"),
                    "funnel": funnel.model_dump(mode="json"),
                    "anomalies": anomalies.model_dump(mode="json"),
                    "server_time": datetime.now(timezone.utc).isoformat(),
                }
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception as exc:
                logger.error("sse_error", extra={"store_id": store_id, "error": str(exc)})
                yield f"data: {json.dumps({'error': 'computation error'})}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
