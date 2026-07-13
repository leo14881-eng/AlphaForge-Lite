"""
live_monitor/api.py —— 大屏只读 API + 前端页面（纸上模拟/研究用途，不含任何下单接口）

页面：
    GET /                         大屏页面本身（static/dashboard.html）

三个数据接口：
    GET /api/v1/market/ticker    全市场主流资产实时价格 + 24H 涨跌幅
                                  （代理 Binance 官方 24hr ticker，公开接口，无需 API Key）
    GET /api/v1/signals/daily    今日活跃领导者 / 今日已退出，从 Redis 预聚合集合读取
                                  （SCARD/SMEMBERS，O(1)~O(n) 内存操作，不去 MySQL 现算）
    GET /api/v1/signals/history  历史信号流水分页查询，UNION 热表(strategy_signals)
                                  + 冷表(strategy_signals_archive)，保证冷热分离后
                                  历史全量数据依然可查

运行前需要安装：
    pip install fastapi uvicorn pymysql redis dbutils httpx

用法：
    uvicorn live_monitor.api:app --host 0.0.0.0 --port 8090
    然后浏览器打开 http://127.0.0.1:8090/
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pymysql
import redis
from dbutils.pooled_db import PooledDB
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from live_monitor.market_monitor import MYSQL_CONFIG, REDIS_CONFIG, SYMBOLS

app = FastAPI(
    title="AlphaForge-Lite Live Monitor API",
    description="纸上模拟/研究用途的实时信号大屏只读接口，不提供任何下单能力",
    version="0.1.0-research",
)

_pool = PooledDB(creator=pymysql, maxconnections=10, blocking=True, **MYSQL_CONFIG)
_redis = redis.Redis(**REDIS_CONFIG, decode_responses=True)

_BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"
_DASHBOARD_HTML_PATH = Path(__file__).parent / "static" / "dashboard.html"


@app.get("/")
def get_dashboard() -> FileResponse:
    """大屏页面本身，单文件、无需构建工具链，浏览器直接打开 http://127.0.0.1:8090/ 即可"""
    return FileResponse(_DASHBOARD_HTML_PATH)


@app.get("/api/v1/market/ticker")
async def get_market_ticker() -> list[dict]:
    """全市场主流资产实时价格 + 24H 涨跌幅（代理 Binance 公开行情接口）"""
    symbols_param = "[" + ",".join(f'"{s.upper()}"' for s in SYMBOLS) + "]"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(_BINANCE_TICKER_URL, params={"symbols": symbols_param})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"行情源不可用: {exc}") from exc

    data = resp.json()
    return [
        {
            "asset": item["symbol"],
            "price": float(item["lastPrice"]),
            "change24hPct": float(item["priceChangePercent"]),
        }
        for item in data
    ]


def _today_key(prefix: str) -> str:
    return f"{prefix}:{dt.date.today().isoformat()}"


@app.get("/api/v1/signals/daily")
def get_daily_signals() -> dict:
    """今日活跃领导者 / 今日已退出——全部从 Redis 预聚合集合读取，不查 MySQL"""
    active = sorted(_redis.smembers(_today_key("leaders:active")) or set())
    exited = sorted(_redis.smembers(_today_key("leaders:exited")) or set())
    return {
        "date": dt.date.today().isoformat(),
        "activeLeaderCount": len(active),
        "activeLeaders": active,
        "exitedTodayCount": len(exited),
        "exitedToday": exited,
    }


@app.get("/api/v1/signals/history")
def get_signal_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> dict:
    """历史信号流水分页查询，UNION 热表 + 冷表，保证冷热分离后历史全量数据依然可查"""
    offset = (page - 1) * page_size
    conn = _pool.connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM ("
                "  SELECT id FROM strategy_signals"
                "  UNION ALL"
                "  SELECT id FROM strategy_signals_archive"
                ") AS combined"
            )
            total = cur.fetchone()["total"]

            cur.execute(
                "SELECT id, asset, signal_type, signal_uuid, created_time FROM ("
                "  SELECT id, asset, signal_type, signal_uuid, created_time FROM strategy_signals"
                "  UNION ALL"
                "  SELECT id, asset, signal_type, signal_uuid, created_time FROM strategy_signals_archive"
                ") AS combined "
                "ORDER BY created_time DESC "
                "LIMIT %s OFFSET %s",
                (page_size, offset),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    for row in rows:
        row["created_time"] = row["created_time"].isoformat()

    return {"page": page, "pageSize": page_size, "total": total, "items": rows}
