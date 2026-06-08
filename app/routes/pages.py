"""页面路由"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
import os

from ..database import get_products_grouped, get_product_by_id, get_price_history, get_price_stats, get_lowest_price, get_last_scrape
from ..notifier import get_web_notifications

router = APIRouter(tags=["Pages"])

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                         autoescape=select_autoescape(["html"]), cache_size=0)
templates = Jinja2Templates(env=jinja_env)


def _enrich(products):
    enriched = []
    for p in products:
        stats = get_price_stats(p["id"])
        lowest = get_lowest_price(p["id"])
        enriched.append({
            **p, "current_price": stats["current_price"] if stats else None,
            "current_original_price": stats["current_original_price"] if stats else None,
            "lowest_price": lowest["lowest_price"] if lowest else None,
            "lowest_price_time": lowest["recorded_at"] if lowest else None,
            "total_records": stats["total_records"] if stats else 0,
            "avg_price": round(stats["avg_price"], 2) if stats and stats["avg_price"] else None,
        })
    return enriched


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    available, unavailable = get_products_grouped()
    notifications = get_web_notifications(unread_only=False, limit=10)

    all_products = _enrich(available) + _enrich(unavailable)
    all_products.sort(key=lambda p: (p.get("shop_name", ""), p.get("status", "") != "unavailable", p.get("name", "")))

    shops = {}
    for p in all_products:
        shop = p.get("shop_name", "") or "其他店铺"
        if shop not in shops:
            shops[shop] = {"available": [], "unavailable": []}
        if p.get("status") == "unavailable":
            shops[shop]["unavailable"].append(p)
        else:
            shops[shop]["available"].append(p)
    shops = dict(sorted(shops.items(), key=lambda x: -(len(x[1]["available"]) + len(x[1]["unavailable"]))))

    avail_count = sum(1 for p in all_products if p.get("status") != "unavailable")
    unavail_count = sum(1 for p in all_products if p.get("status") == "unavailable")

    last_scrape = get_last_scrape()

    return templates.TemplateResponse(request, "dashboard.html", {
        "shops": shops, "avail_count": avail_count, "unavail_count": unavail_count,
        "notifications": notifications, "last_scrape": last_scrape,
    })


@router.get("/product/{product_id}", response_class=HTMLResponse)
async def product_detail(request: Request, product_id: int):
    product = get_product_by_id(product_id)
    if not product:
        return templates.TemplateResponse(request, "404.html", {"request": request}, status_code=404)
    history = get_price_history(product_id, limit=9999)
    history.reverse()
    return templates.TemplateResponse(request, "product_detail.html", {
        "request": request, "product": product, "history": history,
        "stats": get_price_stats(product_id), "lowest": get_lowest_price(product_id),
    })


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    from ..database import get_all_settings
    return templates.TemplateResponse(request, "settings.html", {"request": request, "settings": get_all_settings()})
