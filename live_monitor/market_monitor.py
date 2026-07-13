"""
live_monitor/market_monitor.py —— 实时市场监控常驻服务（纸上模拟/研究用途）

【范围声明】本服务不接任何真实交易所下单接口，只做"发现领导者候选
信号"的计算、留痕（MySQL）与广播（Redis Stream），供 live_monitor/api.py
+ 前端大屏消费展示。不构成任何实盘交易建议或执行。

【设计说明：非对称过滤】
    合约主线（高频）：订阅 Binance U 本位永续合约 trade 逐笔成交流（不是
        aggTrade，见下方"实测排障记录"），对短(5s)/中(20s)/长(60s) 三档
        真实时间窗口分别独立投票（价格方向 + 量能是否显著放大），多数
        （>=2/3）同向共振才算"合约触网"。
    现货防线（低频防御）：合约触网后，暂停放行，回溯检查过去
        SPOT_LOOKBACK_SECONDS 秒内现货市场是否出现同向的真实大单成交
        （按名义金额 SPOT_LARGE_ORDER_NOTIONAL_USDT 门槛判定）。现货未
        确认则判定为"合约瞬时洗盘噪声"，直接丢弃，不落库、不广播。

【设计说明：全市场覆盖 + 合约独有资产的二次补偿校验】
    main() 启动时用 fetch_live_symbol_universe() 动态拉取币安合约 USDT
    本位永续 TRADING 状态全量 symbol（实测 530 个），不再只盯 5 个样例
    币种——项目最高目标是"尽早发现资金正在聚集的非共识资产"，只盯
    BTC/ETH 这类最主流资产本身就跟这个目标错位。
    其中 169 个合约独有、没有对应现货挂牌（如 1000PEPEUSDT/GOATUSDT，
    恰好是"妖币"论最关心的资产类型），"现货大单确认"这道防线结构性
    不适用。这部分资产不是直接跳过防御，而是走**二次合约资金强度补偿
    校验**：量能放大门槛在常规 1.8 倍基础上再乘 1.3（约 2.34 倍），
    多数票要求从"3 档窗口 2 档同意"提高到"3 档窗口全部一致"，两个更严
    格的合约侧维度一起弥补失去现货交叉验证的风险。见
    CONTRACT_ONLY_DISCOVERY_VOLUME_MULTIPLIER_BONUS/CONTRACT_ONLY_EXIT_VOLUME_MULTIPLIER 等。

【实测排障记录：合约侧为什么用 trade 不用 aggTrade】
    连续多轮独立真实连接实验（combined URL 拼接写法、单流 /ws/<stream>
    写法、裸 /ws 端点 + 显式 SUBSCRIBE 指令，BTC/ETH 两个币种）稳定复现：
    合约(fstream.binance.com) 的 aggTrade、markPrice@1s 两个流持续收到
    0 条消息，而合约 bookTicker、合约 trade（原始逐笔，不聚合）、现货
    aggTrade 在同一网络环境下全部正常。已改用 trade，两种事件共享
    p/q/m/T 字段语义，不影响价格方向+量能投票逻辑。

【设计说明：与 state_machine 引擎的关系】
    本模块的"多窗口投票"是 state_machine.engine.StateMachineEngine 里
    多窗口共振投票思路的实时/tick级简化版，不是同一套代码——CCSDetector
    的 CS 得分计算依赖日线级 OHLCV 大宽表做批量向量化计算，天然是离线/
    批量的，无法直接下沉到逐笔成交的实时流处理场景。

【重构记录：时间尺度断层修复（TickWindow）】
    旧版 TickWindow 用 deque(maxlen=60) 严格按"最近 N 条 tick"切片短/中/
    长窗口，基线均值也是对整个 deque 取平均——这是按 tick 数量而不是按
    物理时间切片：深夜横盘每 60 个 tick 可能横跨 20 分钟，暴动闪崩时 60
    个 tick 可能只有 0.1 秒，用同一套"tick 数量窗口"在两种行情节奏下算出
    的"基线均值"含义完全不可比，会导致触发阈值在高频期/低频期错乱（深夜
    一笔普通散户单可能因为前面极度缩量被误判成"1.8倍机构大单"）。
    已改为**真实时间窗口**：SHORT/MID/LONG 三档窗口语义从"最近 N 条" 变成
    "最近 N 秒"，基线均值改成"过去 BASELINE_WINDOW_SECONDS 秒内的成交量
    速率（qty/秒）"，逐笔投票时把"最近 N 秒实际成交量"与"基线速率 × N 秒"
    比较，是否放大用的是同一个物理时间尺度下的速率对比，不再受行情节奏
    影响。

【重构记录：异步鲁棒性加固】
    - WebSocket 连接套了指数退避重连状态机（初始 1 秒，每次失败翻倍，
      封顶 60 秒，连接成功后重置），防止交易所 24 小时强制断线或网络
      抖动时被高频重连打成事实上的 DoS 而遭遇 IP 限流/封禁。
    - ping_interval=20/ping_timeout=10 显式心跳保活；max_size 调大到
      4MB，防止极端行情下 combined 多流单帧过大被 websockets 默认 1MB
      上限拒收。
    - 主接收循环 `async for raw in ws` 收到消息后立刻用 asyncio.create_task
      把处理任务弹射出去，自己马上回去 recv() 下一条，不等处理完成——
      保持主循环极致轻量。真正触发信号时的 MySQL/Redis 同步写盘操作用
      asyncio.to_thread 隔离到系统线程池，确保这类阻塞 I/O 不会卡住
      事件循环（否则高频 trade 流下每次触发信号都会让整条流的消息处理
      短暂停摆）。
    - 弹射出去的 task 显式持有强引用（self._background_tasks），避免
      asyncio 在没有其他引用时提前垃圾回收正在执行的任务这个已知坑。
    - 在把信号相关的 active_leaders 状态更新挪到了"await 线程offload
      之前"完成——create_task 弹射意味着同一资产可能有多条消息处理在
      并发在途，如果状态更新放在 await 之后，两条消息都可能在写盘期间
      看到"还不是活跃领导者"从而重复触发同一个信号，因为 signal_uuid
      每次都是新生成的 uuid4，MySQL 唯一键防重对这种"内容不同的重复
      信号"完全无效。提前占位关闭了这个竞态窗口。

【重构记录：安全与可靠性加固】
    - ALPHA_RUN_MODE 环境变量安全锁：只有显式设置 ALPHA_RUN_MODE=LIVE
      时，SignalSink.persist_and_broadcast 才会真正落库/广播；否则直接
      跳过并只记日志。防止新成员开发、本地单测、未来某个脚本 import 这
      个模块时不小心触发真实信号写入生产 MySQL/Redis Stream。信号的
      计算逻辑本身（TickWindow/非对称过滤）不受这个锁影响，照常运行，
      只是"最后一步落地"被拦住——这样即使误运行了整个服务，也只是空转，
      不会污染下游。
    - Redis Stream 加了 MAXLEN（近似裁剪，XADD ... MAXLEN ~ N），防止
      服务常驻运行数月/数年后 Stream 无限增长拖垮 Memurai/Redis 内存，
      最终引发 OOM。
    - 本地信号审计日志（logs/live_monitor_signal_audit.log，JSON Lines
      格式，CRITICAL 级别）：在尝试写 MySQL/Redis **之前**就先落一条本地
      日志，即使网络、MySQL、Redis 同时故障，只要 Python 进程还活着，
      这条记录就已经落盘，可以在 Java 端凭这份日志做信号对账、人工补单，
      不会因为下游中间件抖动而发生完全静默的信号丢失。

【已知限制，如实说明】
    - active_leaders（当前活跃领导者集合）只保存在进程内存里，服务重启
      会丢失，生产化需要从 Redis/MySQL 启动时恢复。
    - WebSocket 长连接稳定性已实测验证（现货流连续 33 秒收到 95 条真实
      成交、合约 bookTicker 连续 25 秒收到 1 万+条，均无断线）。
    - divergence_windows/confirm_streak（state_machine 侧）、CORE/MEME
      画像权重的生命周期错配问题，属于策略方法论层面的开放问题，不是
      market_monitor.py 这层工程 bug，本轮不在这里动，留给 Reviewer 决策
      是否要重新网格扫描校准或做样本外验证，详见 project_manifest.md
      "诚实声明"章节。

运行前需要安装：
    pip install websockets pymysql redis dbutils

用法：
    # 只有显式设置 ALPHA_RUN_MODE=LIVE，信号才会真正落库/广播
    ALPHA_RUN_MODE=LIVE python -m live_monitor.market_monitor
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import time
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pymysql
import redis
import websockets
from dbutils.pooled_db import PooledDB

logger = logging.getLogger("alphaforge.live_monitor")

# ---------------------------------------------------------------------
# 本地信号审计日志：独立 logger + 文件 handler，JSON Lines 格式，
# 与主日志分开存放，方便下游直接按行解析做信号对账
# ---------------------------------------------------------------------
_SIGNAL_AUDIT_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "live_monitor_signal_audit.log"
signal_audit_logger = logging.getLogger("alphaforge.live_monitor.signal_audit")
signal_audit_logger.setLevel(logging.CRITICAL)
signal_audit_logger.propagate = False
if not signal_audit_logger.handlers:
    _SIGNAL_AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _audit_handler = logging.FileHandler(_SIGNAL_AUDIT_LOG_PATH, encoding="utf-8")
    _audit_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    signal_audit_logger.addHandler(_audit_handler)

# ---------------------------------------------------------------------
# 【临时调试】原始 WebSocket 消息落盘日志——排查用，用户确认不需要后
# 会移除，不要把这段当成正式功能长期维护
# ---------------------------------------------------------------------
_RAW_MESSAGE_DEBUG_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "live_monitor_raw_ws_debug.log"
raw_ws_debug_logger = logging.getLogger("alphaforge.live_monitor.raw_ws_debug")
raw_ws_debug_logger.setLevel(logging.DEBUG)
raw_ws_debug_logger.propagate = False
if not raw_ws_debug_logger.handlers:
    _RAW_MESSAGE_DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _raw_debug_formatter = logging.Formatter("%(asctime)s %(message)s")
    _raw_debug_file_handler = logging.FileHandler(_RAW_MESSAGE_DEBUG_LOG_PATH, encoding="utf-8")
    _raw_debug_file_handler.setFormatter(_raw_debug_formatter)
    raw_ws_debug_logger.addHandler(_raw_debug_file_handler)
    # 应用户要求同时打到控制台，方便直接盯着终端看实时推送，不用另开
    # 窗口 tail 文件——同样是临时调试用途，跟文件 handler 一起在确认
    # 不需要后移除。
    _raw_debug_console_handler = logging.StreamHandler()
    _raw_debug_console_handler.setFormatter(_raw_debug_formatter)
    raw_ws_debug_logger.addHandler(_raw_debug_console_handler)

# ---------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------

FUTURES_WS_BASE = "wss://fstream.binance.com/stream"
SPOT_WS_BASE = "wss://stream.binance.com:9443/stream"

# 合约用 trade（aggTrade 在当前网络环境被选择性限流，见模块顶部"实测
# 排障记录"），现货用 aggTrade（实测正常，维持不变）
FUTURES_STREAM_SUFFIX = "trade"
SPOT_STREAM_SUFFIX = "aggTrade"

# 唯一主战场：只盯永续合约算信号；现货流只用于大单验证，不产生独立信号。
# 这是安全兜底默认值（小样本，供本地开发/单测/fetch_live_symbol_universe
# 拉取失败时降级使用），真正全市场覆盖由 main() 启动时动态拉取替换。
SYMBOLS: tuple[str, ...] = ("btcusdt", "ethusdt", "solusdt", "bnbusdt", "dogeusdt")

FUTURES_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
SPOT_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"


def fetch_live_symbol_universe() -> tuple[tuple[str, ...], frozenset[str]]:
    """
    启动时调用一次（同步阻塞 REST 调用，发生在事件循环真正跑起来之前，
    不影响后续热路径的异步性）：拉取币安合约 USDT 本位永续 TRADING 状态
    全量 symbol，与现货 TRADING 状态 symbol 集合比对，划分出"有现货可
    交叉验证"和"合约独有、没有现货"两类（实测过：530 个合约 TRADING
    永续里，169 个没有对应现货挂牌，例如 1000PEPEUSDT/GOATUSDT 这类）。

    返回 (全量合约 symbol 元组(小写), 合约独有 symbol 集合(小写))。
    网络失败时不阻塞服务启动，抛出异常交给调用方决定是否降级。
    """

    def _get_json(url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "AlphaForge-Lite/live_monitor"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    futures_info = _get_json(FUTURES_EXCHANGE_INFO_URL)
    futures_symbols = [
        s["symbol"] for s in futures_info["symbols"]
        if s["status"] == "TRADING" and s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"
    ]
    spot_info = _get_json(SPOT_EXCHANGE_INFO_URL)
    spot_symbols = {s["symbol"] for s in spot_info["symbols"] if s["status"] == "TRADING"}

    all_symbols = tuple(s.lower() for s in futures_symbols)
    contract_only_symbols = frozenset(s.lower() for s in futures_symbols if s not in spot_symbols)
    return all_symbols, contract_only_symbols


# 三档判定窗口——真实时间窗口（秒），不是 tick 数量，见模块顶部"重构记录"
SHORT_WINDOW_SECONDS = 5
MID_WINDOW_SECONDS = 20
LONG_WINDOW_SECONDS = 60
VOTE_WINDOWS_SECONDS: tuple[int, ...] = (SHORT_WINDOW_SECONDS, MID_WINDOW_SECONDS, LONG_WINDOW_SECONDS)
MAJORITY_NEEDED = 2  # 3 档窗口里至少 2 档同向共振才算合约触网

# 【合约独有币种的二次补偿校验——建仓/离场必须拆开，不能共用一套门槛】
# 没有现货可交叉验证的资产（如 1000PEPEUSDT/GOATUSDT），"现货大单确认"
# 这道防线结构性不适用，用合约侧自身更强的确定性做补偿。但"建仓要严、
# 离场要松"是两码事：建仓误判只是错过一次机会，离场误判（真正暴跌/
# 归零时卡在"已经是活跃领导者"退不出来）是实打实的亏损扩大。两套门槛
# 因此完全独立：
#
# 建仓（DISCOVERY）——维持严格，防误判：
#   1. 量能放大门槛在原有 1.8 倍基础上再提高 30%（约 2.34 倍）
#   2. 多数票要求从"3 档窗口里 2 档同意即可"提高到"3 档窗口全部一致"
#
# 离场（EXIT）——大幅放宽，宁可错杀也别错过真正的暴跌/归零：
#   1. 量能门槛直接用 1.0（不要求放量，只要达到基线速率推算值即可）
#   2. 多数票维持常规"3 档窗口 2 档同意"（不要求全部一致，真实暴跌
#      往往是跳空式下砸，不同窗口量能表现不均匀，"全部一致"反而会让
#      离场信号更难触发）
#
# TODO: 这两组数字（1.3/1.0 倍数、majority 档位）目前都是经验值，没有
# 经过网格扫描或历史数据回测校准——跟 divergence_windows/confirm_streak
# 是同一类"盲盒拍脑袋"参数。留待 backtest 轨道补上样本外滚动验证框架
# 之后，再用同样的方法论重新校准这两组数字，不要在没有验证的情况下
# 直接当成"调好的参数"使用。
CONTRACT_ONLY_DISCOVERY_VOLUME_MULTIPLIER_BONUS = 1.3
CONTRACT_ONLY_DISCOVERY_MAJORITY_NEEDED = len(VOTE_WINDOWS_SECONDS)
CONTRACT_ONLY_EXIT_VOLUME_MULTIPLIER = 1.0
CONTRACT_ONLY_EXIT_MAJORITY_NEEDED = MAJORITY_NEEDED

# 基线成交量速率的统计窗口，必须比最长的判定窗口更长，保证基线本身有
# 足够样本、不会被判定窗口自己的突发放量污染
BASELINE_WINDOW_SECONDS = 300
# 内存里最多缓存的 tick 条数硬上限：正常情况下由 BASELINE_WINDOW_SECONDS
# 按时间淘汰，这个只是防止极端闪崩下 tick 密度爆炸导致内存无限增长的
# 兜底安全阀
TICK_BUFFER_MAXLEN = 5000

VOLUME_RESONANCE_MULTIPLIER = 1.8  # 窗口成交量超过基线速率推算值的倍数才计入共振

SPOT_LOOKBACK_SECONDS = 10  # 现货大单回溯窗口
SPOT_LARGE_ORDER_NOTIONAL_USDT = 50_000  # 现货"大单"名义金额门槛（USDT）
SPOT_CACHE_MAXLEN = 200  # 现货大单缓存队列上限，防止内存无限增长

# WebSocket 连接参数：心跳保活 + 极端行情缓冲区放大
PING_INTERVAL_SECONDS = 20
PING_TIMEOUT_SECONDS = 10
WS_MAX_MESSAGE_BYTES = 4 * 1024 * 1024  # 4MB，高于 websockets 默认 1MB 上限

# 断线重连指数退避：初始 1 秒，每次失败翻倍，封顶 60 秒，连接成功后重置。
# 防止交易所 24 小时强制断线或网络抖动触发的高频重连被判定为异常流量。
RECONNECT_INITIAL_BACKOFF_SECONDS = 1.0
RECONNECT_MAX_BACKOFF_SECONDS = 60.0
RECONNECT_BACKOFF_MULTIPLIER = 2.0

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
# Stream 近似裁剪上限：'~' 近似模式让 Redis 用惰性删除、不用每次都精确
# 裁到 N 条，写入性能开销接近 O(1)。10 万条对信号这种低频数据已经是
# 非常宽裕的历史窗口，避免服务常驻数月/数年后 Stream 无限增长拖垮内存。
REDIS_STREAM_MAXLEN = 100_000

# 生产安全锁：只有显式设置这个环境变量为 LIVE，信号才会真正落库/广播
LIVE_MODE_ENV_VAR = "ALPHA_RUN_MODE"
LIVE_MODE_ENV_VALUE = "LIVE"


def _is_live_mode() -> bool:
    return os.getenv(LIVE_MODE_ENV_VAR) == LIVE_MODE_ENV_VALUE


# ---------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Tick:
    price: float
    qty: float
    is_buyer_maker: bool  # Binance trade/aggTrade 语义：True = 主动卖方成交（taker 是卖方）
    ts_ms: int


class TickWindow:
    """
    单个资产的合约逐笔成交滑动窗口，按**真实时间**（不是 tick 数量）维护
    数据与判定基线，见模块顶部"重构记录：时间尺度断层修复"。
    """

    def __init__(self, maxlen: int = TICK_BUFFER_MAXLEN):
        self.trades: deque[Tick] = deque(maxlen=maxlen)

    def push(self, tick: Tick) -> None:
        self.trades.append(tick)
        self._evict_stale()

    def _evict_stale(self) -> None:
        if not self.trades:
            return
        cutoff_ms = self.trades[-1].ts_ms - BASELINE_WINDOW_SECONDS * 1000
        while self.trades and self.trades[0].ts_ms < cutoff_ms:
            self.trades.popleft()

    def vote(self, window_seconds: int, volume_multiplier: float = VOLUME_RESONANCE_MULTIPLIER) -> int:
        """
        单个时间窗口尺度的独立投票：+1 买方共振 / -1 卖方共振 / 0 未共振。
        基线是"过去 BASELINE_WINDOW_SECONDS 秒内的成交量速率（qty/秒）"，
        拿这个速率乘以 window_seconds 得到"这个窗口理论上应该有多少成交
        量"，跟窗口内实际成交量比较——同一个物理时间尺度下的速率对比，
        不受行情节奏（深夜横盘 vs 暴动闪崩）影响。

        volume_multiplier 可调：合约独有、没有现货交叉验证的资产用更高
        的倍数（见 CONTRACT_ONLY_DISCOVERY_VOLUME_MULTIPLIER_BONUS/CONTRACT_ONLY_EXIT_VOLUME_MULTIPLIER），作为
        补偿校验的一部分。
        """
        if len(self.trades) < 2:
            return 0
        all_trades = self.trades
        latest_ts = all_trades[-1].ts_ms
        recent_cutoff_ms = latest_ts - window_seconds * 1000
        recent_trades = [t for t in all_trades if t.ts_ms >= recent_cutoff_ms]
        if len(recent_trades) < 2:
            return 0

        baseline_span_seconds = (all_trades[-1].ts_ms - all_trades[0].ts_ms) / 1000
        if baseline_span_seconds <= 0:
            return 0
        baseline_volume_rate = sum(t.qty for t in all_trades) / baseline_span_seconds

        recent_volume = sum(t.qty for t in recent_trades)
        expected_recent_volume = baseline_volume_rate * window_seconds
        if expected_recent_volume <= 0 or recent_volume < expected_recent_volume * volume_multiplier:
            return 0

        direction = recent_trades[-1].price - recent_trades[0].price
        if direction > 0:
            return 1
        if direction < 0:
            return -1
        return 0

    def resonance(
        self,
        volume_multiplier: float = VOLUME_RESONANCE_MULTIPLIER,
        majority_needed: int = MAJORITY_NEEDED,
    ) -> int:
        """
        多窗口共振投票聚合：多数（>=majority_needed）同向才算触网。
        合约独有币种传入更高的 volume_multiplier + majority_needed=3
        （全窗口一致，建仓/离场各自独立门槛），见
        CONTRACT_ONLY_DISCOVERY_VOLUME_MULTIPLIER_BONUS/CONTRACT_ONLY_EXIT_VOLUME_MULTIPLIER。
        """
        votes = [self.vote(w, volume_multiplier=volume_multiplier) for w in VOTE_WINDOWS_SECONDS]
        buy_votes = sum(1 for v in votes if v == 1)
        sell_votes = sum(1 for v in votes if v == -1)
        if buy_votes >= majority_needed:
            return 1
        if sell_votes >= majority_needed:
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
        拼接"这种在剧烈波段里可能一分钟内二次变盘导致漏单的方案）。

        安全锁：只有 ALPHA_RUN_MODE=LIVE 时才真正落库/广播，否则只记
        日志、直接返回，防止误运行时污染生产 MySQL/Redis Stream。

        落库前先写一条本地 CRITICAL 审计日志（logs/live_monitor_signal_
        audit.log），即使随后 MySQL/Redis 都写失败，这条本地记录也已经
        落盘，可以事后凭日志人工对账补单，不会发生完全静默的信号丢失。

        依次落库 MySQL（INSERT IGNORE，signal_uuid 唯一键兜底防重复）、
        广播 Redis Stream（XADD 可靠队列 + MAXLEN 近似裁剪防止无限增长，
        供 Java 消费组消费）、更新 Redis 里"今日活跃领导者/今日已退出"
        集合（供大屏预聚合读取，避免大屏每次刷新都去 MySQL 现算
        COUNT/GROUP BY）。
        """
        signal_uuid = str(uuid.uuid4())

        if not _is_live_mode():
            logger.warning(
                "[SignalSink] 当前不是 LIVE 模式（%s 未设置为 %s），信号已计算但不落库/不广播："
                "asset=%s type=%s uuid=%s",
                LIVE_MODE_ENV_VAR, LIVE_MODE_ENV_VALUE, asset, signal_type, signal_uuid,
            )
            return signal_uuid

        signal_audit_logger.critical(
            json.dumps(
                {"asset": asset, "signal_type": signal_type, "signal_uuid": signal_uuid},
                ensure_ascii=False,
            )
        )

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
            self._redis.xadd(
                REDIS_STREAM_KEY,
                {"asset": asset, "type": signal_type, "uuid": signal_uuid},
                maxlen=REDIS_STREAM_MAXLEN,
                approximate=True,
            )
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
    def __init__(
        self,
        symbols: tuple[str, ...] = SYMBOLS,
        sink: SignalSink | None = None,
        contract_only_symbols: frozenset[str] = frozenset(),
    ):
        self.symbols = symbols
        # 合约独有、没有现货可交叉验证的资产集合——这些资产走"二次合约
        # 资金强度补偿校验"分支（建仓/离场各自独立门槛），见
        # CONTRACT_ONLY_DISCOVERY_VOLUME_MULTIPLIER_BONUS/CONTRACT_ONLY_EXIT_VOLUME_MULTIPLIER。默认空集合，兼容单测/小样本
        # 场景（不传就等于"全部资产都有现货可用"，行为跟改造前一致）。
        self.contract_only_symbols = contract_only_symbols
        self.futures_windows: dict[str, TickWindow] = {s: TickWindow() for s in symbols}
        self.spot_cache = SpotLargeOrderCache()
        self.sink = sink or SignalSink()
        # 进程内活跃领导者集合，见模块顶部"已知限制"说明
        self.active_leaders: set[str] = set()
        # 持有 create_task 弹射出去的任务的强引用，防止 asyncio 在没有
        # 其他引用时把还在跑的任务提前垃圾回收（官方文档明确警告过的坑）
        self._background_tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        if not _is_live_mode():
            logger.warning(
                "[MarketMonitor] 当前不是 LIVE 模式（%s 未设置为 %s），"
                "服务会正常连接行情并计算信号，但不会落库/广播任何信号——"
                "仅用于验证信号计算逻辑本身是否工作正常。",
                LIVE_MODE_ENV_VAR, LIVE_MODE_ENV_VALUE,
            )
        # 现货侧只订阅"有真实现货挂牌"的子集——合约独有资产没有对应现货
        # symbol，订阅了也收不到数据（实测过：combined stream 里混入不
        # 存在的流名不会拖垮整条连接，但白白多订阅没有意义）。
        spot_symbols = tuple(s for s in self.symbols if s not in self.contract_only_symbols)
        logger.info(
            "[MarketMonitor] 本次监控 %d 个合约标的，其中 %d 个有现货可交叉验证、"
            "%d 个合约独有（走二次合约资金强度补偿校验）",
            len(self.symbols), len(spot_symbols), len(self.contract_only_symbols),
        )
        await asyncio.gather(
            # 合约侧用 trade（原始逐笔成交），不用 aggTrade——见模块顶部
            # "实测排障记录"，不要改回 aggTrade。合约侧订阅全量 symbols。
            self._run_stream(FUTURES_WS_BASE, FUTURES_STREAM_SUFFIX, self._on_futures_message, "合约", self.symbols),
            # 现货侧 aggTrade 实测正常，维持不变；只订阅有现货挂牌的子集
            self._run_stream(SPOT_WS_BASE, SPOT_STREAM_SUFFIX, self._on_spot_message, "现货", spot_symbols),
        )

    async def _run_stream(
        self, base_url: str, stream_suffix: str, handler, label: str, symbols: tuple[str, ...]
    ) -> None:
        """
        外层指数退避重连状态机。except Exception 已经天然覆盖
        websockets.exceptions.ConnectionClosed（它就是 Exception 的
        子类），没有再单独列一个重复分支。
        """
        streams = "/".join(f"{s}@{stream_suffix}" for s in symbols)
        url = f"{base_url}?streams={streams}"
        backoff = RECONNECT_INITIAL_BACKOFF_SECONDS
        while True:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=PING_INTERVAL_SECONDS,
                    ping_timeout=PING_TIMEOUT_SECONDS,
                    max_size=WS_MAX_MESSAGE_BYTES,
                ) as ws:
                    logger.info("[MarketMonitor] %s %s 流已连接", label, stream_suffix)
                    backoff = RECONNECT_INITIAL_BACKOFF_SECONDS  # 连接成功，重置退避计时
                    async for raw in ws:
                        raw_ws_debug_logger.debug("[%s] %s", label, raw)  # 临时调试日志，见文件头说明
                        self._spawn_dispatch(handler, raw, label)
            except Exception:
                logger.exception(
                    "[MarketMonitor] %s 行情流断开，%.1f 秒后重连（指数退避）", label, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * RECONNECT_BACKOFF_MULTIPLIER, RECONNECT_MAX_BACKOFF_SECONDS)

    def _spawn_dispatch(self, handler, raw: str, label: str) -> None:
        """把消息处理任务弹射成独立 task，主接收循环不等它完成，立刻回去收下一条消息"""
        task = asyncio.create_task(self._dispatch_message(handler, raw, label))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _dispatch_message(self, handler, raw: str, label: str) -> None:
        try:
            await handler(raw)
        except Exception:
            logger.exception("[MarketMonitor] %s 消息处理异常，已跳过该条消息", label)

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

    async def _on_futures_message(self, raw: str) -> None:
        parsed = self._parse_trade(raw)
        if parsed is None:
            return
        symbol, tick = parsed
        window = self.futures_windows.get(symbol)
        if window is None:
            return
        window.push(tick)

        is_contract_only = symbol in self.contract_only_symbols
        if is_contract_only:
            # 情况B：没有现货可交叉验证。用"当前是否已经是活跃领导者"
            # 决定这次要判定的是建仓还是离场——这个信息在算共振之前就
            # 已经知道，不需要先算出 direction 再反过来判断（direction
            # 的正负本来就是由这里选的门槛决定的，不能倒因为果）。
            if symbol in self.active_leaders:
                # 已经持仓，这次是"要不要离场"——门槛大幅放宽，宁可
                # 错杀也别错过真正的暴跌/归零。
                direction = window.resonance(
                    volume_multiplier=CONTRACT_ONLY_EXIT_VOLUME_MULTIPLIER,
                    majority_needed=CONTRACT_ONLY_EXIT_MAJORITY_NEEDED,
                )
            else:
                # 还没持仓，这次是"要不要建仓"——门槛维持严格，防止把
                # 噪声误判成真实资金聚集。
                direction = window.resonance(
                    volume_multiplier=VOLUME_RESONANCE_MULTIPLIER * CONTRACT_ONLY_DISCOVERY_VOLUME_MULTIPLIER_BONUS,
                    majority_needed=CONTRACT_ONLY_DISCOVERY_MAJORITY_NEEDED,
                )
        else:
            # 情况A：有现货可交叉验证，走原有的常规门槛
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

        if is_contract_only:
            # 情况B：结构性没有现货市场，跳过现货确认——已经在上面用
            # "建仓严、离场松"两套独立门槛做了补偿，这里不再二次校验。
            logger.debug(
                "[MarketMonitor] %s 无现货可交叉验证，已通过合约侧%s校验触网：%s",
                symbol, "建仓强化" if signal_type == "DISCOVERY" else "离场放宽", signal_type,
            )
        else:
            # 非对称过滤第二层：现货大单确认，未确认判定为合约瞬时洗盘噪声，直接丢弃
            if not self.spot_cache.confirm(symbol, direction):
                logger.debug(
                    "[MarketMonitor] %s 合约触网（%s）但现货未确认，判定为洗盘噪声，丢弃",
                    symbol, signal_type,
                )
                return

        # 提前占位更新 active_leaders，必须在下面 await 让出事件循环之前
        # 完成——create_task 弹射消息处理后，同一资产可能有多条消息处理
        # 并发在途，如果状态更新放在 await（MySQL/Redis 写盘）之后，两条
        # 消息都可能在写盘期间看到"还不是活跃领导者"从而重复触发同一个
        # 信号（signal_uuid 每次都是新生成的 uuid4，MySQL 唯一键防重对
        # 这种"内容不同的重复信号"完全无效）。提前占位关闭这个竞态窗口。
        if signal_type == "DISCOVERY":
            self.active_leaders.add(symbol)
        else:
            self.active_leaders.discard(symbol)

        asset = symbol.upper()
        # 同步写 MySQL/Redis 隔离到系统线程池，绝不阻塞事件循环主线程
        signal_uuid = await asyncio.to_thread(self.sink.persist_and_broadcast, asset, signal_type)
        logger.info("[MarketMonitor] 信号确认：asset=%s type=%s uuid=%s", asset, signal_type, signal_uuid)

    async def _on_spot_message(self, raw: str) -> None:
        # 现货侧没有任何 await，写成 async 只是为了跟 _dispatch_message
        # 的统一调用接口保持一致（两个 handler 签名必须一致）。
        parsed = self._parse_trade(raw)
        if parsed is None:
            return
        symbol, tick = parsed
        self.spot_cache.push(symbol, tick)


def _check_infra_connectivity() -> None:
    """
    启动时主动测一次 MySQL/Redis 连通性并把结果明确打进日志——
    PooledDB/redis.Redis 都是懒连接，构造时不报错不代表真的连得上，
    不主动测一次的话，启动是否成功只能靠"程序有没有崩"来猜，运维时
    看不到任何明确的"连上了/没连上"信号。这里的失败只记日志，不阻止
    服务继续启动（信号计算逻辑本身不依赖这两个连接是否可用）。
    """
    try:
        conn = pymysql.connect(connect_timeout=5, **MYSQL_CONFIG)
        conn.close()
        logger.info("[MarketMonitor] 启动自检：MySQL 连接正常（%s:%s/%s）",
                    MYSQL_CONFIG["host"], MYSQL_CONFIG["port"], MYSQL_CONFIG["database"])
    except Exception:
        logger.exception("[MarketMonitor] 启动自检：MySQL 连接失败，信号落库会持续报错，请检查配置/服务")

    try:
        r = redis.Redis(**REDIS_CONFIG)
        r.ping()
        r.close()
        logger.info("[MarketMonitor] 启动自检：Redis 连接正常（%s:%s db=%s）",
                    REDIS_CONFIG["host"], REDIS_CONFIG["port"], REDIS_CONFIG["db"])
    except Exception:
        logger.exception("[MarketMonitor] 启动自检：Redis 连接失败，信号广播会持续报错，请检查配置/服务")


def configure_logging() -> None:
    """
    主日志（控制台 + 落盘文件）统一配置入口。抽成独立函数是因为现在有
    两条启动路径都需要它：`market_monitor.py` 单独跑（不带大屏），或者
    `live_monitor/api.py` 的 FastAPI lifespan 把这个采集/计算服务当成
    后台任务挂在同一个进程里跑（见 api.py 顶部说明，一条 uvicorn 命令
    启动数据采集 + REST 接口 + 大屏页面）——两条路径都需要同一套日志
    配置，不应该各写一份。
    """
    log_path = Path(__file__).resolve().parent.parent / "logs" / "live_monitor_service.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path, encoding="utf-8")],
    )
    logger.info("[MarketMonitor] 服务启动中，主日志落盘路径：%s", log_path)


def build_monitor_from_live_universe() -> MarketMonitor:
    """
    启动自检 + 全市场标的拉取（失败降级为内置小样本）+ 构造 MarketMonitor
    实例，同样是 market_monitor.py 独立运行和 api.py lifespan 两条路径
    共用的初始化逻辑。
    """
    if not _is_live_mode():
        logger.warning(
            "启动提示：环境变量 %s 未设置为 %s，本次运行信号只计算不落库/不广播。"
            "真正对接 Java 执行端前，请显式设置 %s=%s。",
            LIVE_MODE_ENV_VAR, LIVE_MODE_ENV_VALUE, LIVE_MODE_ENV_VAR, LIVE_MODE_ENV_VALUE,
        )

    _check_infra_connectivity()

    try:
        all_symbols, contract_only_symbols = fetch_live_symbol_universe()
        logger.info(
            "[MarketMonitor] 全市场标的拉取成功：合约 TRADING 永续共 %d 个，"
            "其中 %d 个无对应现货挂牌",
            len(all_symbols), len(contract_only_symbols),
        )
    except Exception:
        logger.exception(
            "[MarketMonitor] 全市场标的拉取失败，降级使用内置小样本 %s", SYMBOLS
        )
        all_symbols, contract_only_symbols = SYMBOLS, frozenset()

    return MarketMonitor(symbols=all_symbols, contract_only_symbols=contract_only_symbols)


def main() -> None:
    """
    独立运行数据采集/信号计算服务，不带大屏和 REST 接口——适合只想要
    信号计算能力、不需要看板的部署场景（比如没有图形界面的服务器）。
    想要"一条命令启动全部"（采集 + 接口 + 大屏），运行
    `uvicorn live_monitor.api:app` 即可，见 live_monitor/api.py。
    """
    configure_logging()
    monitor = build_monitor_from_live_universe()
    asyncio.run(monitor.run())


if __name__ == "__main__":
    main()
