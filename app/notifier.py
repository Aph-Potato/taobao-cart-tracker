"""网页通知模块"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

web_notifications: list[dict] = []


def send_price_drop_notification(product_id: int, product_name: str,
                                  old_price: float, new_price: float, reason: str):
    """添加网页降价通知"""
    drop = old_price - new_price
    pct = drop / old_price * 100
    web_notifications.append({
        "id": len(web_notifications) + 1, "title": f"{reason}！{product_name[:30]}",
        "body": f"从 ¥{old_price:.2f} 降到 ¥{new_price:.2f}（-{pct:.1f}%）",
        "product_id": product_id, "url": "", "read": False,
        "time": datetime.now().isoformat(),
    })
    if len(web_notifications) > 100:
        web_notifications.pop(0)


def send_price_alerts(product_id: int, current_price: float, triggered_alerts: list[dict]):
    try:
        from .database import get_product_by_id
        product = get_product_by_id(product_id)
        product_name = product["name"] if product else f"商品#{product_id}"
    except:
        product_name = f"商品#{product_id}"
    for alert in triggered_alerts:
        title = f"历史最低价！{product_name[:20]}" if alert["alert_type"] == "lowest_price" else f"价格下降！{product_name[:20]}"
        body = f"当前价格 ¥{current_price}"
        web_notifications.append({
            "id": len(web_notifications) + 1, "title": title, "body": body,
            "product_id": product_id, "url": "", "read": False,
            "time": datetime.now().isoformat(),
        })
        if len(web_notifications) > 100:
            web_notifications.pop(0)


def get_web_notifications(unread_only=False, limit=50):
    result = [n for n in web_notifications if not unread_only or not n["read"]]
    return result[-limit:][::-1]


def mark_notification_read(nid: int):
    for n in web_notifications:
        if n["id"] == nid: n["read"] = True


def clear_notifications():
    web_notifications.clear()
