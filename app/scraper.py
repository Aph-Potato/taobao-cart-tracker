"""Playwright 淘宝购物车抓取器"""
import asyncio
import json as json_module
import os
import re
import logging
from playwright.async_api import async_playwright

from .database import (
    upsert_product, add_price_record, set_product_inactive,
    get_all_products
)

logger = logging.getLogger(__name__)

# 浏览器用户数据目录
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
BROWSER_PROFILE_DIR = os.path.join(DATA_DIR, "browser_profile")
os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)

CART_URL = "https://cart.taobao.com/cart.htm"
LOGIN_URL = "https://login.taobao.com/"


async def _extract_cart_items_from_api(page) -> list[dict]:
    """
    通过拦截淘宝 MTOP API 获取购物车数据（最可靠）。
    拦截 mtop.trade.query.bag 响应，解析 JSON 提取商品。
    """
    import json as json_module

    captured_responses = []

    async def on_response(response):
        if "mtop.trade.query.bag" in response.url and response.status == 200:
            try:
                body = await response.text()
                captured_responses.append(body)
            except Exception:
                pass

    page.on("response", on_response)

    # 刷新页面以触发 API 调用
    await page.reload(wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(6)

    page.remove_listener("response", on_response)

    if not captured_responses:
        logger.info("[Scraper] 未捕获到 cart API 响应，回退到 DOM 方式")
        return await _extract_cart_items_from_dom(page)

    # 解析 API 响应
    body = captured_responses[0]
    try:
        resp = json_module.loads(body)
    except json_module.JSONDecodeError:
        # 可能是 JSONP 格式 mtopjsonpX({...})
        match = re.search(r'mtopjsonp\d+\((.*)\)\s*$', body, re.DOTALL)
        if match:
            try:
                resp = json_module.loads(match.group(1))
            except Exception:
                logger.error("[Scraper] API 响应解析失败")
                return []
        else:
            logger.error("[Scraper] API 响应格式未知")
            return []

    # 路径: resp['data']['data'] -> item_* 键
    dd = resp.get("data", {}).get("data", {})
    item_keys = [k for k in dd if k.startswith("item_")]

    if not item_keys:
        logger.info("[Scraper] API 中未找到商品，回退到 DOM 方式")
        return await _extract_cart_items_from_dom(page)

    items = []
    for key in item_keys:
        try:
            item = dd[key]
            fields = item.get("fields", {})
            hidden = item.get("hidden", {}).get("extensionMap", {})

            cart_id = fields.get("cartId", "")
            item_id = fields.get("itemId", "")
            name = fields.get("title", "")
            pic = fields.get("pic", "")
            shop_name = fields.get("shopTitle", "")
            url = fields.get("outerUrl", "")

            if not name or not cart_id:
                continue

            # 图片 URL 补全
            if pic and pic.startswith("//"):
                pic = "https:" + pic

            # 价格提取: 优先用 extensionMap 中的折扣价，否则用 pay 数据
            # hidden.extensionMap.queryDiscountedPrice 是折扣后价格（分）
            # pay.now 是原价（分）
            pay = fields.get("pay", {})
            unit_price = hidden.get("unitPrice")  # 单位价格（分）
            discounted_price = hidden.get("queryDiscountedPrice")  # 折扣价（分）
            discounted_title = hidden.get("queryDiscountedTitle", "")
            shop_promo_title = pay.get("shopPromotionPriceTitle", "")

            # 确定当前实际价格
            if discounted_price and discounted_price > 0:
                price = round(float(discounted_price) / 100, 2)
            elif shop_promo_title:
                pm = re.search(r'[\d.]+', shop_promo_title)
                price = float(pm.group()) if pm else round(float(pay.get("now", 0)) / 100, 2)
            else:
                price = round(float(pay.get("now", 0)) / 100, 2)

            if price <= 0:
                continue

            # 原价（划线价）
            original_price = None
            if unit_price and unit_price > 0:
                orig = round(float(unit_price) / 100, 2)
                if orig > price:
                    original_price = orig
            if not original_price:
                now_price = round(float(pay.get("now", 0)) / 100, 2)
                if now_price > price:
                    original_price = now_price

            # 判断商品状态
            can_check = fields.get("canCheck", True)
            is_invalid = fields.get("isInvalid", False)
            code_msg = fields.get("codeMsg", "")
            if (not can_check) or is_invalid:
                item_status = "unavailable"
                item_reason = code_msg or ("已下架" if not can_check else "缺货")
            else:
                item_status = "available"
                item_reason = ""

            items.append({
                "taobao_id": str(cart_id),
                "item_id": str(item_id),
                "name": name,
                "price": price,
                "original_price": original_price,
                "image_url": pic,
                "shop_name": shop_name,
                "url": url or f"https://item.taobao.com/item.htm?id={item_id}",
                "status": item_status,
                "unavailable_reason": item_reason,
            })
        except Exception as e:
            logger.warning(f"[Scraper] 解析商品 {key} 失败: {e}")
            continue

    logger.info(f"[Scraper] API 提取到 {len(items)} 个商品")
    return items


async def _extract_cart_items_from_dom(page) -> list[dict]:
    """
    JS DOM 方式提取（API 拦截失败时的降级方案）。
    """
    raw_items = await page.evaluate("""
        () => {
            const results = [];
            // 尝试所有全局变量
            for (const key of Object.getOwnPropertyNames(window)) {
                try {
                    const val = window[key];
                    if (val && typeof val === 'object' && val.items && Array.isArray(val.items)) {
                        return val.items.map(i => ({
                            name: i.title || i.name || '',
                            price_text: String(i.price || i.actualPrice || ''),
                            img: i.pic || i.image || '',
                            url: (i.url || i.href || ''),
                            taobao_id: String(i.itemId || i.auctionId || ''),
                            shop: i.shopName || i.seller || '',
                            original_price_text: i.originalPrice ? String(i.originalPrice) : null,
                        }));
                    }
                } catch(e) {}
            }
            // 降级：找页面上所有商品链接
            const links = document.querySelectorAll('a[href*="item.taobao.com"], a[href*="detail.tmall.com"]');
            const seen = new Set();
            for (const link of links) {
                const href = link.href;
                const idMatch = href.match(/[?&]id=(\\\\d+)/);
                if (!idMatch || seen.has(idMatch[1])) continue;
                seen.add(idMatch[1]);
                const parent = link.closest('div,li,tr,section') || link;
                const text = parent.textContent.trim();
                const priceMatch = text.match(/[\\\\u00A5\\\\uFFE5]\\\\s*([\\\\d.]+)/);
                if (priceMatch && text.length > 5) {
                    results.push({
                        name: (link.getAttribute('title') || link.textContent || '').trim().substring(0, 200),
                        price_text: priceMatch[1],
                        taobao_id: idMatch[1],
                        img: (parent.querySelector('img[src]') || {}).src || '',
                        url: href,
                        shop: '',
                        original_price_text: null,
                    });
                }
            }
            return results;
        }
    """)

    items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        price_text = str(item.get("price_text", ""))
        price_match = re.search(r'[\d.]+', price_text)
        if not name or not price_match:
            continue
        price = float(price_match.group())
        if price <= 0:
            continue

        original_price = None
        if item.get("original_price_text"):
            om = re.search(r'[\d.]+', str(item["original_price_text"]))
            if om:
                original_price = float(om.group())

        items.append({
            "taobao_id": str(item.get("taobao_id", f"tb_{hash(name + str(price))}")),
            "name": name,
            "price": price,
            "original_price": original_price,
            "image_url": str(item.get("img", "")),
            "shop_name": str(item.get("shop", "")),
            "url": str(item.get("url", "")),
        })

    logger.info(f"[Scraper] DOM 提取到 {len(items)} 个商品")
    return items


async def check_login_status(page) -> bool:
    """检查是否已登录"""
    current_url = page.url
    if "login" in current_url:
        return False

    # 检查页面是否有登录按钮
    try:
        login_btn = await page.query_selector(".tb-login, #login, .J_Login")
        if login_btn:
            return False
    except Exception:
        pass

    return True


async def scrape_cart() -> dict:
    """
    执行一次购物车抓取。
    返回: {"success": bool, "items_found": int, "items_new": int, "items_updated": int, "message": str, "errors": list}
    """
    result = {
        "success": False,
        "items_found": 0,
        "items_new": 0,
        "items_updated": 0,
        "message": "",
        "errors": [],
    }

    async with async_playwright() as p:
        try:
            # 启动持久化浏览器上下文
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=BROWSER_PROFILE_DIR,
                headless=False,  # 必须非无头模式，否则容易触发风控
                channel="msedge",  # 使用系统自带的 Microsoft Edge 浏览器
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )

            page = browser.pages[0] if browser.pages else await browser.new_page()

            # 屏蔽自动化检测
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            # 导航到购物车
            logger.info("正在访问淘宝购物车...")
            await page.goto(CART_URL, wait_until="domcontentloaded", timeout=30000)

            # 等待一段时间让页面完全加载
            await asyncio.sleep(3)

            # 检查登录状态
            is_logged_in = await check_login_status(page)
            if not is_logged_in:
                logger.warning("[Scraper] 未登录淘宝！请在浏览器窗口中手动扫码登录。")
                result["message"] = "需要登录淘宝，请在打开的浏览器窗口中扫码登录"
                result["errors"].append("未登录")
                # 等待用户手动登录
                try:
                    await page.wait_for_url(
                        "**/cart.taobao.com/**",
                        timeout=120000  # 等2分钟让用户扫码
                    )
                    logger.info("[Scraper] 登录成功，继续抓取...")
                    await asyncio.sleep(3)
                    is_logged_in = True
                except Exception:
                    logger.error("登录超时")
                    result["message"] = "登录超时，请重试"
                    await browser.close()
                    return result

            # 通过 API 拦截方式提取购物车数据（内部会刷新页面等待 API 响应）
            items = await _extract_cart_items_from_api(page)
            result["items_found"] = len(items)

            if not items:
                result["message"] = result["message"] or "未找到购物车商品，请确认购物车中有商品"
                await browser.close()
                return result

            # 批量存储数据
            from .database import init_db, get_all_products, set_product_inactive
            init_db()

            current_taobao_ids = set()
            for item in items:
                current_taobao_ids.add(item["taobao_id"])

                # 更新商品信息（upsert_product 会自动设 is_active=1）
                product_id = upsert_product(
                    taobao_id=item["taobao_id"],
                    name=item["name"],
                    image_url=item.get("image_url"),
                    shop_name=item.get("shop_name"),
                    url=item.get("url"),
                    status=item.get("status", "available"),
                    unavailable_reason=item.get("unavailable_reason", ""),
                    item_id=item.get("item_id", ""),
                )

                # 记录价格
                last_price = None
                from .database import get_latest_price
                latest = get_latest_price(product_id)
                if latest:
                    last_price = latest["price"]

                # 只有当价格变化时才记录新记录（去重）
                if last_price is None or abs(last_price - item["price"]) > 0.001:
                    add_price_record(
                        product_id=product_id,
                        price=item["price"],
                        original_price=item.get("original_price"),
                        source="playwright",
                    )
                    if last_price is None:
                        result["items_new"] += 1
                    else:
                        result["items_updated"] += 1

                # 检查价格提醒
                from .database import check_and_trigger_alerts
                triggered = check_and_trigger_alerts(product_id, item["price"])
                if triggered:
                    from .notifier import send_price_alerts
                    send_price_alerts(product_id, item["price"], triggered)

            # 将不在购物车中的商品标记为非活跃
            all_products = get_all_products(active_only=False)
            for p in all_products:
                if p["taobao_id"] not in current_taobao_ids:
                    from .database import set_product_inactive
                    set_product_inactive(p["taobao_id"])

            result["success"] = True
            result["message"] = f"成功抓取 {len(items)} 个商品"

            logger.info(
                f"[Scraper] 抓取完成: {len(items)} 个商品, "
                f"{result['items_new']} 新增, {result['items_updated']} 价格变化"
            )

            await browser.close()

        except Exception as e:
            error_msg = str(e)
            logger.error(f"抓取失败: {error_msg}")
            result["errors"].append(error_msg)
            result["message"] = f"抓取失败: {error_msg}"

    return result


def scrape_cart_sync() -> dict:
    """
    同步版抓取 —— 使用 Playwright 同步 API。
    设计为在 asyncio.to_thread() 中运行，绕过 Windows ProactorEventLoop 不支持子进程的问题。
    """
    from playwright.sync_api import sync_playwright
    import json as json_module

    result = {
        "success": False,
        "items_found": 0,
        "items_new": 0,
        "items_updated": 0,
        "message": "",
        "errors": [],
    }

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=BROWSER_PROFILE_DIR,
                headless=False,
                channel="msedge",
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )

            page = browser.pages[0] if browser.pages else browser.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            # 通过 API 拦截方式提取——先挂监听再加载页面
            captured = []

            def on_response(response):
                if "mtop.trade.query.bag" in response.url and response.status == 200:
                    try:
                        captured.append(response.text())
                    except Exception:
                        pass

            page.on("response", on_response)

            # 访问购物车
            logger.info("[Scraper] 正在访问淘宝购物车...")
            page.goto(CART_URL, timeout=30000)
            page.wait_for_load_state("domcontentloaded")

            # 检查登录状态
            current_url = page.url
            if "login" in current_url:
                logger.warning("[Scraper] 未登录，等待用户扫码...")
                result["message"] = "请在打开的浏览器窗口中扫码登录淘宝"
                result["errors"].append("未登录")
                try:
                    page.wait_for_url("**/cart.taobao.com/**", timeout=120000)
                    logger.info("[Scraper] 登录成功")
                    page.wait_for_timeout(3000)
                except Exception:
                    logger.error("[Scraper] 登录超时")
                    result["message"] = "登录超时，请重试"
                    browser.close()
                    return result

            # reload 一次确保所有 API 请求都被捕获（goto 后的首次加载可能命中浏览器缓存）
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            logger.info(f"[Scraper] reload 完成, 已捕获 {len(captured)} 个 API 响应")

            # 等初始加载完成，再用滚动触发后续分页
            page.wait_for_timeout(3000)

            # 有限次滚动——连续 3 轮无新响应即停
            last_captured = 0
            no_new = 0

            for scroll_round in range(20):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)  # 给 API 足够时间响应

                new_count = len(captured)
                logger.info(f"[Scraper] 滚动第{scroll_round+1}轮, 已捕获{new_count}个API响应")

                if new_count == last_captured:
                    no_new += 1
                    if no_new >= 3:
                        logger.info(f"[Scraper] 连续{no_new}轮未捕获新数据，停止")
                        break
                else:
                    no_new = 0

                last_captured = new_count

            page.wait_for_timeout(2000)

            page.remove_listener("response", on_response)

            logger.info(f"[Scraper] 共捕获 {len(captured)} 个购物车 API 响应")

            # 解析所有响应，合并商品
            all_items_raw = []
            for body in captured:
                try:
                    try:
                        resp_data = json_module.loads(body)
                    except json_module.JSONDecodeError:
                        match = re.search(r'mtopjsonp\d+\((.*)\)\s*$', body, re.DOTALL)
                        if match:
                            resp_data = json_module.loads(match.group(1))
                        else:
                            continue

                    dd = resp_data.get("data", {}).get("data", {})
                    data_keys = [k for k in dd if k.startswith("item_") or k.startswith("bundleHeader_")]
                    for key in data_keys:
                        all_items_raw.append((key, dd[key]))
                except Exception as ex:
                    logger.warning(f"[Scraper] 解析API响应失败: {ex}")

            # 分离 item 和 bundleHeader，按 cartId 去重 item
            bundle_entries = [(k, v) for k, v in all_items_raw if k.startswith("bundleHeader_")]
            item_entries = [(k, v) for k, v in all_items_raw if k.startswith("item_")]

            seen_ids = set()
            unique_items = []
            for key, item in item_entries:
                cart_id = item.get("fields", {}).get("cartId", item.get("id", key))
                if cart_id not in seen_ids:
                    seen_ids.add(cart_id)
                    unique_items.append((key, item))

            logger.info(f"[Scraper] API返回 {len(unique_items)} 个不重复商品, {len(bundle_entries)} 个店铺信息")

            # 建立 bundleId → shop_name 映射
            shop_map = {}
            for key, item in bundle_entries:
                fields = item.get("fields", {})
                bid = fields.get("bundleId", "")
                title = fields.get("title", "") or fields.get("seller", "")
                if bid and title:
                    shop_map[bid] = title
            logger.info(f"[Scraper] 已收集 {len(shop_map)} 个店铺映射")

            items = []
            for key, item in unique_items:
                try:
                    fields = item.get("fields", {})
                    hidden = item.get("hidden", {}).get("extensionMap", {})

                    cart_id = fields.get("cartId", "")
                    item_id = fields.get("itemId", "")
                    name = fields.get("title", "")
                    if not name or not cart_id:
                        continue

                    pic = fields.get("pic", "")
                    if pic and pic.startswith("//"):
                        pic = "https:" + pic

                    pay = fields.get("pay", {})
                    unit_price = hidden.get("unitPrice")
                    discounted = hidden.get("queryDiscountedPrice")
                    shop_promo = pay.get("shopPromotionPriceTitle", "")

                    if discounted and discounted > 0:
                        price = round(float(discounted) / 100, 2)
                    elif shop_promo:
                        m = re.search(r'[\d.]+', shop_promo)
                        price = float(m.group()) if m else round(float(pay.get("now", 0)) / 100, 2)
                    else:
                        price = round(float(pay.get("now", 0)) / 100, 2)

                    if price <= 0:
                        continue

                    original_price = None
                    if unit_price and unit_price > 0:
                        orig = round(float(unit_price) / 100, 2)
                        if orig > price:
                            original_price = orig
                    if not original_price:
                        now_p = round(float(pay.get("now", 0)) / 100, 2)
                        if now_p > price:
                            original_price = now_p

                    # 判断商品状态：可购买 / 缺货 / 下架
                    can_check = fields.get("canCheck", True)
                    is_invalid = fields.get("isInvalid", False)
                    code_msg = fields.get("codeMsg", "")
                    if (not can_check) or is_invalid:
                        item_status = "unavailable"
                        item_reason = code_msg or ("已下架" if not can_check else "缺货")
                    else:
                        item_status = "available"
                        item_reason = ""

                    items.append({
                        "taobao_id": str(cart_id),       # 唯一键存 cartId
                        "item_id": str(item_id),          # 淘宝商品 itemId
                        "name": name,
                        "price": price,
                        "original_price": original_price,
                        "image_url": pic,
                        "shop_name": fields.get("shopTitle", "")
                                     or shop_map.get(fields.get("bundleId", ""), ""),
                        "url": fields.get("outerUrl", "") or f"https://item.taobao.com/item.htm?id={item_id}",
                        "status": item_status,
                        "unavailable_reason": item_reason,
                    })
                except Exception as ex:
                    logger.warning(f"[Scraper] 解析商品 {key} 失败: {ex}")

            if not items:
                result["message"] = result["message"] or "未找到购物车商品"
                browser.close()
                return result

            result["items_found"] = len(items)
            result["success"] = True
            result["message"] = f"成功抓取 {len(items)} 个商品"

            # 入库
            from .database import init_db, upsert_product, add_price_record, \
                get_latest_price, get_lowest_price, get_all_products, \
                set_product_inactive, check_and_trigger_alerts, cleanup_old_records, add_scrape_log

            init_db()
            deleted = cleanup_old_records(days=365)
            if deleted:
                logger.info(f"[Scraper] 清理了 {deleted} 条旧价格记录")

            current_ids = set()
            for item in items:
                current_ids.add(item["taobao_id"])
                # upsert_product: taobao_id=cartId(唯一), item_id=淘宝商品ID
                pid = upsert_product(
                    taobao_id=item["taobao_id"], name=item["name"],
                    image_url=item.get("image_url"), shop_name=item.get("shop_name"),
                    url=item.get("url"),
                    status=item.get("status", "available"),
                    unavailable_reason=item.get("unavailable_reason", ""),
                    item_id=item.get("item_id", ""),
                )

                latest = get_latest_price(pid)
                last_price = latest["price"] if latest else None
                if last_price is None or abs(last_price - item["price"]) > 0.001:
                    add_price_record(pid, item["price"], item.get("original_price"), source="playwright")
                    if last_price is None:
                        result["items_new"] += 1
                    else:
                        result["items_updated"] += 1

                # 全局降价检测：根据设置中的阈值判断是否提醒
                if last_price and last_price > 0 and item["price"] < last_price:
                    from .database import get_setting
                    threshold_pct = float(get_setting("price_drop_pct", "0"))
                    drop_pct = (last_price - item["price"]) / last_price * 100
                    lowest = get_lowest_price(pid)
                    is_lowest = lowest and item["price"] <= lowest["lowest_price"]

                    if is_lowest or (threshold_pct > 0 and drop_pct >= threshold_pct):
                        reason = "历史最低价" if is_lowest else f"降价 {drop_pct:.1f}%"
                        from .notifier import send_price_drop_notification
                        send_price_drop_notification(pid, item["name"], last_price, item["price"], reason)

                triggered = check_and_trigger_alerts(pid, item["price"])
                if triggered:
                    from .notifier import send_price_alerts
                    send_price_alerts(pid, item["price"], triggered)

            # 标记不在购物车中的商品为不活跃
            all_products = get_all_products(active_only=True)
            for p in all_products:
                if p["taobao_id"] not in current_ids:
                    set_product_inactive(p["taobao_id"])

            logger.info(f"[Scraper] 抓取完成: {len(items)} 个商品, "
                        f"{result['items_new']} 新增, {result['items_updated']} 价格变化")

            # 记录抓取日志
            add_scrape_log(result["success"], result["items_found"], result["message"])

            try:
                browser.close()
            except Exception:
                pass

        except Exception as ex:
            error_msg = str(ex) or type(ex).__name__
            logger.error(f"[Scraper] 抓取失败: {error_msg}")
            result["errors"].append(error_msg)
            result["message"] = f"抓取失败: {error_msg}"
            # 记录失败的抓取
            from .database import add_scrape_log
            add_scrape_log(False, 0, error_msg)

    return result


async def quick_scrape() -> dict:
    """快速抓取，定时任务调用（在线程中运行同步版）"""
    import asyncio
    return await asyncio.to_thread(scrape_cart_sync)
