"""店舗一覧ルート: 現時点では店舗が 1 件も無いことを表示するだけ."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates: Jinja2Templates | None = None


def set_templates(tmpl: Jinja2Templates) -> None:
    """テンプレートエンジンをルーターに注入する (app.py から呼ばれる)."""
    global templates  # noqa: PLW0603
    templates = tmpl


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """トップページ (店舗一覧)."""
    assert templates is not None
    return templates.TemplateResponse(request, "index.html", {})
