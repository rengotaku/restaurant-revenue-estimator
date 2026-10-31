"""FastAPI アプリケーション定義: structlog / SQLModel / 画面ルート を統合."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from restaurant_revenue_estimator.config import get_settings
from restaurant_revenue_estimator.database import init_db
from restaurant_revenue_estimator.logging_config import configure_logging, get_logger
from restaurant_revenue_estimator.routes import api as api_routes
from restaurant_revenue_estimator.routes import pages as page_routes

# python-web/ ルートディレクトリ (src/restaurant_revenue_estimator/app.py の 3 つ上)
_BASE_DIR = Path(__file__).parent.parent.parent


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """アプリ起動時の初期化 (logging + DB)."""
    configure_logging()
    init_db()
    get_logger("restaurant-revenue-estimator").info(
        "startup", env=get_settings().app_env
    )
    yield


app = FastAPI(title="restaurant-revenue-estimator", lifespan=_lifespan)

# Jinja2 テンプレート
_templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))
page_routes.set_templates(_templates)

# 静的ファイル
_static_dir = _BASE_DIR / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# ルーター登録
app.include_router(page_routes.router)
app.include_router(api_routes.router)
