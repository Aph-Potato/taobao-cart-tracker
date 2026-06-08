"""定时任务调度器"""
import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .database import get_setting
from .scraper import quick_scrape

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def get_scrape_interval_hours() -> int:
    """从设置中获取抓取间隔（小时），默认6小时"""
    try:
        return int(get_setting("scrape_interval_hours", "6"))
    except (ValueError, TypeError):
        return 6


async def scheduled_scrape_job():
    """定时抓取任务"""
    logger.info("[Scheduler] 定时抓取任务开始...")
    try:
        result = await quick_scrape()
        logger.info(f"[Scheduler] 定时抓取完成: {result['message']}")
        # scrape 内部已记录日志到数据库
    except Exception as e:
        logger.error(f"[Scheduler] 定时抓取失败: {e}")
        from .database import add_scrape_log
        add_scrape_log(False, 0, str(e))


def start_scheduler():
    """启动调度器"""
    interval_hours = get_scrape_interval_hours()

    # 添加定时任务
    scheduler.add_job(
        scheduled_scrape_job,
        trigger="interval",
        hours=interval_hours,
        id="scrape_cart",
        name="抓取淘宝购物车",
        replace_existing=True,
        # 在启动后延迟10秒执行第一次，给应用启动时间
        next_run_time=None,
    )

    scheduler.start()
    logger.info(f"[Scheduler] 调度器已启动，每 {interval_hours} 小时抓取一次")


def reschedule_scrape_job():
    """根据最新设置重新安排定时任务"""
    interval_hours = get_scrape_interval_hours()

    job = scheduler.get_job("scrape_cart")
    if job:
        scheduler.reschedule_job(
            "scrape_cart",
            trigger="interval",
            hours=interval_hours,
        )
        logger.info(f"[Scheduler] 抓取间隔已更新为 {interval_hours} 小时")
    else:
        scheduler.add_job(
            scheduled_scrape_job,
            trigger="interval",
            hours=interval_hours,
            id="scrape_cart",
            name="抓取淘宝购物车",
        )
        logger.info(f"[Scheduler] 新增定时任务，每 {interval_hours} 小时执行")


def stop_scheduler():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("调度器已停止")
