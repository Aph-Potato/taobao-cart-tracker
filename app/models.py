"""Pydantic 数据模型"""
from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class ProductBase(BaseModel):
    taobao_id: str
    name: str
    image_url: Optional[str] = None
    shop_name: Optional[str] = None
    url: Optional[str] = None


class Product(ProductBase):
    id: int
    is_active: bool = True
    created_at: Optional[str] = None


class PriceRecord(BaseModel):
    id: int
    product_id: int
    price: float
    original_price: Optional[float] = None
    source: str = "playwright"
    recorded_at: Optional[str] = None


class PriceStats(BaseModel):
    total_records: int
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    avg_price: Optional[float] = None
    current_price: Optional[float] = None
    current_original_price: Optional[float] = None


class CartItem(BaseModel):
    """Playwright 提交的购物车商品"""
    taobao_id: str
    name: str
    price: float
    original_price: Optional[float] = None
    image_url: Optional[str] = None
    shop_name: Optional[str] = None
    url: Optional[str] = None
    source: str = "playwright"


class CartData(BaseModel):
    """购物车数据批量提交"""
    items: list[CartItem]


class AlertCreate(BaseModel):
    product_id: int
    alert_type: str = "lowest_price"
    threshold_value: Optional[float] = None


class Alert(AlertCreate):
    id: int
    is_triggered: bool = False
    triggered_at: Optional[str] = None
    created_at: Optional[str] = None
    product_name: Optional[str] = None


class SettingsUpdate(BaseModel):
    """可更新的设置项"""
    scrape_interval_hours: Optional[str] = None
    price_drop_pct: Optional[str] = None


class ScrapeResult(BaseModel):
    """抓取结果"""
    success: bool
    items_found: int
    items_new: int = 0
    items_updated: int = 0
    message: str = ""
    errors: list[str] = []
