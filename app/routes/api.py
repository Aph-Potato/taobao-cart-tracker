"""REST API 路由"""
import logging
import asyncio
from fastapi import APIRouter, HTTPException

from ..database import (
    get_all_products, get_products_grouped, get_product_by_id, get_price_history,
    get_latest_price, get_lowest_price, get_price_stats,
    upsert_product, add_price_record, set_product_inactive,
    check_and_trigger_alerts, get_all_settings, set_setting, get_setting,
)
from ..models import CartData, ScrapeResult, SettingsUpdate
from ..notifier import (
    get_web_notifications, mark_notification_read, clear_notifications,
    send_price_drop_notification,
)
from ..scraper import scrape_cart_sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["API"])


# ========== 购物车数据 ==========

@router.post("/cart-data", response_model=ScrapeResult)
async def receive_cart_data(data: CartData):
    if not data.items:
        return ScrapeResult(success=False, items_found=0, message="没有收到商品数据")
    new_count = updated_count = 0
    current_ids = set()
    for item in data.items:
        current_ids.add(item.taobao_id)
        pid = upsert_product(item.taobao_id, item.name, item.image_url, item.shop_name, item.url,
                             item_id=getattr(item, 'item_id', ''))
        latest = get_latest_price(pid)
        if latest is None or abs(latest["price"] - item.price) > 0.001:
            add_price_record(pid, item.price, item.original_price, source=item.source)
            if latest is None: new_count += 1
            else: updated_count += 1
        triggered = check_and_trigger_alerts(pid, item.price)
        if triggered:
            send_price_drop_notification(pid, item.name, latest["price"] if latest else item.price,
                                         item.price, "历史最低价")
    for p in get_all_products(active_only=True):
        if p["taobao_id"] not in current_ids:
            set_product_inactive(p["taobao_id"])
    return ScrapeResult(success=True, items_found=len(data.items), items_new=new_count,
                        items_updated=updated_count, message=f"成功接收 {len(data.items)} 个商品")


# ========== 商品 ==========

def _enrich(products):
    result = []
    for p in products:
        stats = get_price_stats(p["id"])
        lowest = get_lowest_price(p["id"])
        result.append({**p, "current_price": stats["current_price"] if stats else None,
                       "current_original_price": stats["current_original_price"] if stats else None,
                       "lowest_price": lowest["lowest_price"] if lowest else None,
                       "lowest_price_time": lowest["recorded_at"] if lowest else None,
                       "total_records": stats["total_records"] if stats else 0,
                       "avg_price": round(stats["avg_price"], 2) if stats and stats["avg_price"] else None})
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result


@router.get("/products")
async def list_products(grouped: bool = True):
    if grouped:
        a, u = get_products_grouped(active_only=True)
        return {"available": _enrich(a), "unavailable": _enrich(u),
                "available_count": len(a), "unavailable_count": len(u)}
    return _enrich(get_all_products(active_only=True))


@router.get("/products/{product_id}")
async def get_product(product_id: int):
    p = get_product_by_id(product_id)
    if not p: raise HTTPException(status_code=404, detail="商品不存在")
    stats = get_price_stats(product_id)
    lowest = get_lowest_price(product_id)
    return {**p, "current_price": stats["current_price"] if stats else None,
            "lowest_price": lowest["lowest_price"] if lowest else None,
            "avg_price": round(stats["avg_price"], 2) if stats and stats["avg_price"] else None}


@router.get("/products/{product_id}/history")
async def get_history(product_id: int, limit: int = 9999):
    p = get_product_by_id(product_id)
    if not p: raise HTTPException(status_code=404, detail="商品不存在")
    history = get_price_history(product_id, limit=limit)
    history.reverse()
    return {"product_id": product_id, "product_name": p["name"], "records": history}


# ========== 抓取 ==========

@router.post("/trigger-scrape", response_model=ScrapeResult)
async def trigger_scrape():
    try:
        result = await asyncio.to_thread(scrape_cart_sync)
        return ScrapeResult(**result)
    except Exception as e:
        error_msg = str(e) or type(e).__name__
        logger.error(f"[API] 抓取异常: {error_msg}", exc_info=True)
        return ScrapeResult(success=False, items_found=0, message=f"抓取失败: {error_msg}", errors=[error_msg])


# ========== 通知 ==========

@router.get("/notifications")
async def list_notifications(unread_only: bool = False):
    return get_web_notifications(unread_only=unread_only)


@router.post("/notifications/{nid}/read")
async def read_notification(nid: int):
    mark_notification_read(nid)
    return {"message": "ok"}


@router.post("/notifications/clear")
async def clear_all():
    clear_notifications()
    return {"message": "ok"}


# ========== 设置 ==========

@router.get("/settings")
async def get_settings():
    return get_all_settings()


@router.put("/settings")
async def update_settings(settings: SettingsUpdate):
    for key, value in settings.model_dump(exclude_none=True).items():
        if value is not None:
            set_setting(key, str(value))
    from ..scheduler import reschedule_scrape_job
    reschedule_scrape_job()
    return {"message": "设置已更新", "settings": get_all_settings()}
