# 淘宝购物车价格追踪器

自动抓取淘宝购物车商品价格，记录价格趋势，低价时提醒。

## 功能

- 🔄 **自动抓取** — Playwright 定时抓取淘宝购物车的所有商品价格
- 📈 **价格趋势** — 7天/30天/90天/180天/全部 五档时间范围查看
- 📉 **降价提醒** — 商品降价达到设定比例或历史最低价时通知
- 🔍 **搜索过滤** — 按商品名或店铺名实时搜索
- 📦 **按店铺分组** — 与淘宝购物车相同的分组方式
- ⚠️ **失效标记** — 缺货/下架商品自动识别并标记

## 安装

```bash
# 1. 克隆项目
git clone https://github.com/[用户]/taobao-cart-tracker.git
cd taobao-cart-tracker

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动
python run.py
```

浏览器访问 **http://localhost:8000** 即可使用。

首次点击「🔄 立即抓取」会弹出 Edge 浏览器窗口，扫描二维码登录淘宝后自动完成抓取。

## 依赖

- Python 3.10+
- Microsoft Edge 浏览器（Windows 系统自带）
- 淘宝账号

## 项目结构

```
app/             # 后端
  main.py        # FastAPI 入口
  database.py    # SQLite 操作
  scraper.py     # Playwright 抓取
  scheduler.py   # 定时任务
  notifier.py    # 网页通知
  routes/        # API + 页面路由
  templates/     # Jinja2 模板
run.py           # 启动脚本
```

## License

MIT
