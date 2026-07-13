"""
live_monitor/market_monitor.py —— 实时市场监控常驻服务（纸上模拟/研究用途）

【范围声明】本服务不接任何真实交易所下单接口，只做"发现领导者候选
信号"的计算、留痕（MySQL）与广播（Redis Stream），供 live_monitor/api.py
+ 前端大屏消费展示。不构成任何实盘交易建议或执行。

【设计说明：非对称过滤】
    合约主线（高频）：订阅 Binance U 本位永续合约 trade 逐笔成交流（不是
        aggTrade，见下方"实测排障记录"），对短(5)/中(20)/长(60) tick 三档
        窗口分别独立投票（价格方向 + 量能是否显著放大），多数（>=2/3）
        同向共振才算"合约触网"。
    现货防线（低频防御）：合约触网后，暂停放行，回溯检查过去
        SPOT_LOOKBACK_SECONDS 秒内现货市场是否出现同向的真实大单成交
        （按名义金额 SPOT_LARGE_ORDER_NOTIONAL_USDT 门槛判定）。现货未
        确认则判定为"合约瞬时洗盘噪声"，直接丢弃，不落库、不广播。

【实测排障记录：合约侧为什么用 trade 不用 aggTrade】
    连续多轮独立真实连接实验（combined URL 拼接写法、单流 /ws/<stream>
    写法、裸 /ws 端点 + 显式 SUBSCRIBE 指令，BTC/ETH 两个币种）稳定复现：
    合约(fstream.binance.com) 的 aggTrade、markPrice@1s 两个流持续收到
    0 条消息，而合约 bookTicker、合约 trade（原始逐笔，不聚合）、现货
    aggTrade 在同一网络环境下全部正常（bookTicker 甚至一秒内推送数百条）。
    排除了 URL 拼接语法问题（改用显式 SUBSCRIBE 指令结果一致），锁定为
    当前网络环境对合约 aggTrade/markPrice 这两类"成交派生数据"存在选择性
    限流/丢弃。trade 与 aggTrade 共享 p/q/m/T 字段语义，直接换用不影响
    本模块的价格方向+量能投票逻辑。如果部署环境变化，建议重新跑一次这个
    排障流程再决定要不要切回 aggTrade。

【设计说明：与 state_machine 引擎的关系】
    本模块的"多窗口投票"是 state_machine.engine.StateMachineEngine 里
    多窗口共振投票思路的实时/tick级简化版，不是同一套代码——CCSDetector
    的 CS 得分计算依赖日线级 OHLCV 大宽表做批量向量化计算，天然是离线/
    批量的，无法直接下沉到逐笔成交的实时流处理场景。这里用价格方向 +
    量能放大的轻量级代理指标重新实现了一版能在 tick 级增量计算的版本，
    是刻意的简化，不等价于回测沙盒里那套经过网格扫描校准的 CS 得分。

【已知限制，如实说明】
    - active_leaders（当前活跃领导者集合）只保存在进程内存里，服务重启
      会丢失，生产化需要从 Redis/MySQL 启动时恢复。
    - WebSocket 长连接稳定性已实测验证（现货流连续 33 秒收到 95 条真实
      成交、合约 bookTicker 连续 25 秒收到 1 万+条，均无断线），之前认为
      "沙盒环境无法长时间维持连接"的说法是错的，已订正。真正验证过的
      限制是上面"实测排障记录"里的合约 aggTrade/markPrice 选择性限流，
      不是连接层面的问题。

运行前需要安装：
    pip install websockets pymysql redis dbutils

用法：
    python -m live_monitor.market_monitor
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

import pymysql
import redis
import websockets
from dbutils.pooled_db import PooledDB

logger = logging.getLogger("alphaforge.live_monitor")

# ---------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------

FUTURES_WS_BASE = "wss://fstream.binance.com/stream"
SPOT_WS_BASE = "wss://stream.binance.com:9443/stream"

# 合约用 trade（aggTrade 在当前网络环境被选择性限流，见模块顶部"实测
# 排障记录"），现货用 aggTrade（实测正常，维持不变）
FUTURES_STREAM_SUFFIX = "trade"
SPOT_STREAM_SUFFIX = "aggTrade"

# 唯一主战场：只盯永续合约算信号；现货流只用于大单验证，不产生独立信号
SYMBOLS: tuple[str, ...] = ("btcusdt", "ethusdt", "solusdt", "bnbusdt", "dogeusdt")

SHORT_WINDOW = 5
MID_WINDOW = 20
LONG_WINDOW = 60
VOTE_WINDOWS: tuple[int, ...] = (SHORT_WINDOW, MID_WINDOW, LONG_WINDOW)
MAJORITY_NEEDED = 2  # 3 档窗口里至少 2 档同向共振才算合约触网

VOLUME_RESONANCE_MULTIPLIER = 1.8  # 窗口成交量超过自身基线均值的倍数才计入共振

SPOT_LOOKBACK_SECONDS = 10  # 现货大单回溯窗口
SPOT_LARGE_ORDER_NOTIONAL_USDT = 50_000  # 现货"大单"名义金额门槛（USDT）
SPOT_CACHE_MAXLEN = 200  # 现货大单缓存队列上限，防止内存无限增长

RECONNECT_DELAY_SECONDS = 5

# 与 Java 执行端共用同一套本地 MySQL/Redis，数据库名/账号密码需要跟
# Java 侧 application.yml 完全一致（jdbc:mysql://localhost:3306/alphaforge_lite,
# root/123456）。serverTimezone/useSSL/allowPublicKeyRetrieval 是 JDBC
# 驱动专属参数，pymysql 没有对应配置项，不需要在这里体现。
MYSQL_CONFIG: dict = dict(
    host="localhost",
    port=3306,
    user="root",
    password="123456",
    database="alphaforge_lite",
    charset="utf8mb4",
    autocommit=True,
)
# timeout=10s 对齐 Java 侧 redis.timeout 配置；本地无密码 Redis 不传 password，
# 有密码环境请通过环境变量注入，不要把密码硬编码进代码。
REDIS_CONFIG: dict = dict(
    host="127.0.0.1", port=6379, db=0, socket_connect_timeout=10, socket_timeout=10
)
REDIS_STREAM_KEY = "stream:strategy:signals"


# ---------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Tick:
    price: float
    qty: float
    is_buyer_maker: bool  # Binance aggTrade 语义：True = 主动卖方成交（taker 是卖方）
    ts_ms: int


class TickWindow:
    """
    单个资产的合约逐笔成交滑动窗口。deque(maxlen=LONG_WINDOW) 严格限定
    最大长度，防止长时间运行后内存无限增长。
    """

    def __init__(self, maxlen: int = LONG_WINDOW):
        self.trades: deque[Tick] = deque(maxlen=maxlen)

    def push(self, tick: Tick) -> None:
        self.trades.append(tick)

    def vote(self, window: int) -> int:
        """单个窗口尺度的独立投票：+1 买方共振 / -1 卖方共振 / 0 未共振"""
        if len(self.trades) < window:
            return 0
        all_trades = list(self.trades)
        recent = all_trades[-window:]

        baseline_avg_qty = sum(t.qty for t in all_trades) / len(all_trades)
        baseline_volume = baseline_avg_qty * window
        volume_sum = sum(t.qty for t in recent)
        if baseline_volume <= 0 or volume_sum < baseline_volume * VOLUME_RESONANCE_MULTIPLIER:
            return 0

        direction = recent[-1].price - recent[0].price
        if direction > 0:
            return 1
        if direction < 0:
            return -1
        return 0

    def resonance(self) -> int:
        """多窗口共振投票聚合：多数（>=MAJORITY_NEEDED）同向才算触网"""
        votes = [self.vote(w) for w in VOTE_WINDOWS]
        buy_votes = sum(1 for v in votes if v == 1)
        sell_votes = sum(1 for v in votes if v == -1)
        if buy_votes >= MAJORITY_NEEDED:
            return 1
        if sell_votes >= MAJORITY_NEEDED:
            return -1
        return 0


class SpotLargeOrderCache:
    """
    现货大单内存缓存：只保留最近 SPOT_LOOKBACK_SECONDS 秒内、名义金额
    超过 SPOT_LARGE_ORDER_NOTIONAL_USDT 的真实大单成交，用于验证合约
    触网信号是否有现货真实资金支撑。
    """

    def __init__(self):
        self._recent_large_trades: dict[str, deque[Tick]] = {}

    def push(self, symbol: str, tick: Tick) -> None:
        notional = tick.price * tick.qty
        if notional < SPOT_LARGE_ORDER_NOTIONAL_USDT:
            return
        buf = self._recent_large_trades.setdefault(symbol, deque(maxlen=SPOT_CACHE_MAXLEN))
        buf.append(tick)
        self._evict_stale(symbol)

    def _evict_stale(self, symbol: str) -> None:
        buf = self._recent_large_trades.get(symbol)
        if not buf:
            return
        cutoff_ms = time.time() * 1000 - SPOT_LOOKBACK_SECONDS * 1000
        while buf and buf[0].ts_ms < cutoff_ms:
            buf.popleft()

    def confirm(self, symbol: str, direction: int) -> bool:
        """direction: 1=需要现货大单买盘确认，-1=需要现货大单卖盘确认"""
        self._evict_stale(symbol)
        buf = self._recent_large_trades.get(symbol)
        if not buf:
            return False
        for t in buf:
            taker_is_buy = not t.is_buyer_maker
            if direction == 1 and taker_is_buy:
                return True
            if direction == -1 and not taker_is_buy:
                return True
        return False


class SignalSink:
    """信号落库（MySQL 连接池）+ 广播（Redis Stream）+ 今日活跃/退出集合维护"""

    def __init__(self, mysql_config: dict | None = None, redis_config: dict | None = None):
        self._pool = PooledDB(
            creator=pymysql, maxconnections=10, blocking=True, **(mysql_config or MYSQL_CONFIG)
        )
        self._redis = redis.Redis(**(redis_config or REDIS_CONFIG), decode_responses=True)

    def persist_and_broadcast(self, asset: str, signal_type: str) -> str:
        """
        生成动态唯一波段 signal_uuid（uuid4，物理防重，不用"分钟时间戳
        拼接"这种在剧烈波段里可能一分钟内二次变盘导致漏单的方案），
        依次落库 MySQL（INSERT IGNORE，signal_uuid 唯一键兜底防重复）、
        广播 Redis Stream（XADD 可靠队列，供 Java 消费组消费）、更新
        Redis 里"今日活跃领导者/今日已退出"集合（供大屏预聚合读取，
        避免大屏每次刷新都去 MySQL 现算 COUNT/GROUP BY）。
        """
        signal_uuid = str(uuid.uuid4())

        try:
            conn = self._pool.connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT IGNORE INTO strategy_signals (asset, signal_type, signal_uuid) "
                        "VALUES (%s, %s, %s)",
                        (asset, signal_type, signal_uuid),
                    )
            finally:
                conn.close()
        except Exception:
            logger.exception(
                "[SignalSink] MySQL 落库失败：asset=%s type=%s uuid=%s", asset, signal_type, signal_uuid
            )

        try:
            self._redis.xadd(REDIS_STREAM_KEY, {"asset": asset, "type": signal_type, "uuid": signal_uuid})
            today_key = dt.date.today().isoformat()
            if signal_type == "DISCOVERY":
                self._redis.sadd(f"leaders:active:{today_key}", asset)
                self._redis.srem(f"leaders:exited:{today_key}", asset)
            else:
                self._redis.sadd(f"leaders:exited:{today_key}", asset)
                self._redis.srem(f"leaders:active:{today_key}", asset)
        except Exception:
            logger.exception(
                "[SignalSink] Redis 广播/预聚合更新失败：asset=%s type=%s uuid=%s",
                asset, signal_type, signal_uuid,
            )

        return signal_uuid


# ---------------------------------------------------------------------
# 主监控服务
# ---------------------------------------------------------------------


class MarketMonitor:
    def __init__(self, symbols: tuple[str, ...] = SYMBOLS, sink: SignalSink | None = None):
        self.symbols = symbols
        self.futures_windows: dict[str, TickWindow] = {s: TickWindow() for s in symbols}
        self.spot_cache = SpotLargeOrderCache()
        self.sink = sink or SignalSink()
        # 进程内活跃领导者集合，见模块顶部"已知限制"说明
        self.active_leaders: set[str] = set()

    async def run(self) -> None:
        await asyncio.gather(
            # 合约侧用 trade（原始逐笔成交），不用 aggTrade——实测过 aggTrade/
            # markPrice 在合约(fstream)网关上被选择性限流，连续多轮独立实验
            # （combined URL、单流 /ws/、显式 SUBSCRIBE 指令）稳定收到 0 条，
            # 而合约 trade 与合约 bookTicker 完全正常。见 FUTURES_STREAM_SUFFIX
            # 顶部注释，不要改回 aggTrade。
            self._run_stream(FUTURES_WS_BASE, FUTURES_STREAM_SUFFIX, self._on_futures_message, "合约"),
            # 现货侧 aggTrade 实测正常（33 秒收到 95 条），维持不变
            self._run_stream(SPOT_WS_BASE, SPOT_STREAM_SUFFIX, self._on_spot_message, "现货"),
        )

    async def _run_stream(self, base_url: str, stream_suffix: str, handler, label: str) -> None:
        streams = "/".join(f"{s}@{stream_suffix}" for s in self.symbols)
        url = f"{base_url}?streams={streams}"
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info("[MarketMonitor] %s %s 流已连接", label, stream_suffix)
                    async for raw in ws:
                        try:
                            handler(raw)
                        except Exception:
                            logger.exception("[MarketMonitor] %s 消息处理异常，已跳过该条消息", label)
            except Exception:
                logger.exception(
                    "[MarketMonitor] %s 行情流断开，%d 秒后重连", label, RECONNECT_DELAY_SECONDS
                )
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    @staticmethod
    def _parse_trade(raw: str) -> tuple[str, Tick] | None:
        """兼容解析 trade（合约侧）和 aggTrade（现货侧）两种事件，用到的字段
        p/q/m/T 在两种事件里含义、字段名完全一致。"""
        msg = json.loads(raw)
        data = msg.get("data")
        if not data or data.get("e") not in ("trade", "aggTrade"):
            return None
        symbol = data["s"].lower()
        tick = Tick(
            price=float(data["p"]),
            qty=float(data["q"]),
            is_buyer_maker=bool(data["m"]),
            ts_ms=int(data["T"]),
        )
        return symbol, tick

    def _on_futures_message(self, raw: str) -> None:
        parsed = self._parse_trade(raw)
        if parsed is None:
            return
        symbol, tick = parsed
        window = self.futures_windows.get(symbol)
        if window is None:
            return
        window.push(tick)

        direction = window.resonance()
        if direction == 0:
            return

        signal_type = "DISCOVERY" if direction == 1 else "EXIT"
        # 非对称过滤第一层：状态语义过滤——DISCOVERY 只在尚未是活跃领导者
        # 时才有意义，EXIT 只在当前是活跃领导者时才有意义，避免重复触发。
        if signal_type == "DISCOVERY" and symbol in self.active_leaders:
            return
        if signal_type == "EXIT" and symbol not in self.active_leaders:
            return

        # 非对称过滤第二层：现货大单确认，未确认判定为合约瞬时洗盘噪声，直接丢弃
        if not self.spot_cache.confirm(symbol, direction):
            logger.debug(
                "[MarketMonitor] %s 合约触网（%s）但现货未确认，判定为洗盘噪声，丢弃",
                symbol, signal_type,
            )
            return

        asset = symbol.upper()
        signal_uuid = self.sink.persist_and_broadcast(asset, signal_type)
        if signal_type == "DISCOVERY":
            self.active_leaders.add(symbol)
        else:
            self.active_leaders.discard(symbol)
        logger.info("[MarketMonitor] 信号确认：asset=%s type=%s uuid=%s", asset, signal_type, signal_uuid)

    def _on_spot_message(self, raw: str) -> None:
        parsed = self._parse_trade(raw)
        if parsed is None:
            return
        symbol, tick = parsed
        self.spot_cache.push(symbol, tick)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    monitor = MarketMonitor()
    asyncio.run(monitor.run())


if __name__ == "__main__":
    main()
