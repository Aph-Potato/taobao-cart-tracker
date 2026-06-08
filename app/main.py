"""淘宝购物车价格追踪器 - FastAPI 主应用"""
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import init_db, cleanup_old_records
from .routes.api import router as api_router
from .routes.pages import router as pages_router
from .scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[PriceTracker] 启动中...")
    init_db()
    cleanup_old_records(days=365)
    try:
        start_scheduler()
    except Exception as e:
        logger.warning(f"[PriceTracker] 调度器启动失败: {e}")
    logger.info("[PriceTracker] 访问 http://localhost:8000")
    yield
    stop_scheduler()


app = FastAPI(title="淘宝购物车价格追踪器", version="1.1.0", lifespan=lifespan)

app.include_router(api_router)
app.include_router(pages_router)

if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health_check():
    return {"status": "ok"}
