"""
live_monitor 子系统的单元测试。

只测试不依赖真实 MySQL/Redis/WebSocket 连接的纯逻辑部分：
    - TickWindow 的多窗口共振投票（真实时间窗口版，见 market_monitor.py
      "重构记录：时间尺度断层修复"）
    - SpotLargeOrderCache 的现货大单确认
    - MarketMonitor 的非对称过滤决策链路（SignalSink 用 Mock 替换，
      不发起真实网络/数据库调用；_on_futures_message/_on_spot_message
      现在是 async 方法，用 asyncio.run() 驱动）
    - SignalSink 的 ALPHA_RUN_MODE 生产安全锁（用 Mock 替换 _pool/_redis，
      验证 LIVE 模式内/外是否真的发起了底层调用，不依赖真实基础设施）

真实的 WebSocket 长连接、MySQL 写入、Redis 广播需要在真实环境里跑
一段时间验证，不在这里测。指数退避重连状态机（_run_stream）和
create_task 消息弹射（_spawn_dispatch）是纯 I/O 编排逻辑，同样需要
真实长连接场景才能有意义地验证，本文件同样不测。
"""
import asyncio
import json
import time
from unittest.mock import MagicMock

from live_monitor.market_monitor import (
    MarketMonitor,
    SignalSink,
    SpotLargeOrderCache,
    Tick,
    TickWindow,
    SPOT_LARGE_ORDER_NOTIONAL_USDT,
)

# 固定基准时间戳（而不是用真实系统时间），保证时间窗口相关测试可重复、
# 不受测试运行速度/系统时钟影响
BASE_TS_MS = 1_000_000_000_000


def _make_tick(price: float, qty: float, is_buyer_maker: bool, ts_ms: int | None = None) -> Tick:
    return Tick(price=price, qty=qty, is_buyer_maker=is_buyer_maker, ts_ms=ts_ms or int(time.time() * 1000))


class TestTickWindow:
    def test_vote_requires_at_least_two_trades(self):
        window = TickWindow()
        window.push(_make_tick(100.0, 1.0, False, ts_ms=BASE_TS_MS))
        assert window.vote(5) == 0  # 只有 1 笔成交，算不出基线速率，直接投反对票

    def test_resonance_detects_buy_side_with_volume_amplification(self):
        window = TickWindow()
        # 先填 120 秒基线成交（每秒 1 笔，qty=1.0，价格不动，模拟安静行情）
        for i in range(120):
            window.push(_make_tick(100.0, 1.0, False, ts_ms=BASE_TS_MS + i * 1000))
        # 最近 16 秒连续放量上涨（每秒 1 笔，qty=6.0，价格持续走高）：
        # 短窗口(5s)/中窗口(20s)的"最近 N 秒"会被这批放量完全覆盖，实际
        # 成交量远超基线速率推算值的 1.8 倍；长窗口(60s) 因为"最近 60 秒"
        # 里仍混了 45 笔基线成交被稀释，达不到放大门槛——这正是"多数
        # （2/3）而非全体窗口都要同意"设计的意义所在。数值已用脚本验证：
        # vote(5): recent_vol=36.0 threshold=14.4 -> 触发
        # vote(20): recent_vol=101.0 threshold=57.6 -> 触发
        # vote(60): recent_vol=141.0 threshold=172.8 -> 不触发
        last_baseline_ts = BASE_TS_MS + 119 * 1000
        for i in range(16):
            window.push(_make_tick(100.0 + (i + 1) * 0.5, 6.0, False, ts_ms=last_baseline_ts + (i + 1) * 1000))

        assert window.vote(5) == 1
        assert window.vote(20) == 1
        assert window.vote(60) == 0  # 长窗口被稀释，不投票
        assert window.resonance() == 1  # 2/3 窗口同意，构成多数

    def test_resonance_returns_zero_without_volume_amplification(self):
        window = TickWindow()
        # 价格持续缓慢上涨，但成交量始终维持基线速率（没有放量），120 秒内
        # 每秒 1 笔、qty 恒为 1.0
        for i in range(120):
            window.push(_make_tick(100.0 + i * 0.1, 1.0, False, ts_ms=BASE_TS_MS + i * 1000))
        assert window.resonance() == 0  # 没有量能放大，任何窗口都不投票

    def test_time_scale_consistency_quiet_vs_frantic_regime(self):
        """
        回归测试：验证"时间尺度断层"修复——同样是 60 笔成交，横跨 20 分钟
        的安静行情 和 横跨几秒的暴动闪崩，触发门槛不应该因为行情节奏不同
        而错乱（旧版按 tick 数量切片会有这个问题，新版按真实时间切片不会）。
        """
        # 安静行情：60 笔成交，每 20 秒 1 笔（横跨约 20 分钟），价格温和上涨、
        # 量能不放大 -> 不应触发任何窗口
        quiet_window = TickWindow()
        for i in range(60):
            quiet_window.push(_make_tick(100.0 + i * 0.01, 1.0, False, ts_ms=BASE_TS_MS + i * 20_000))
        assert quiet_window.resonance() == 0

        # 暴动闪崩：60 笔成交，每 0.1 秒 1 笔（横跨约 6 秒），价格温和上涨、
        # 量能同样不放大（跟基线速率成比例）-> 同样不应触发
        frantic_window = TickWindow()
        for i in range(60):
            frantic_window.push(_make_tick(100.0 + i * 0.01, 1.0, False, ts_ms=BASE_TS_MS + int(i * 100)))
        assert frantic_window.resonance() == 0


class TestSpotLargeOrderCache:
    def test_confirm_false_when_no_large_trades(self):
        cache = SpotLargeOrderCache()
        cache.push("btcusdt", _make_tick(100.0, 1.0, False))  # notional=100，远小于门槛
        assert cache.confirm("btcusdt", direction=1) is False

    def test_confirm_true_for_matching_direction_large_trade(self):
        cache = SpotLargeOrderCache()
        large_qty = (SPOT_LARGE_ORDER_NOTIONAL_USDT / 100.0) + 1  # 名义金额超过门槛
        cache.push("btcusdt", _make_tick(100.0, large_qty, is_buyer_maker=False))  # taker 是买方
        assert cache.confirm("btcusdt", direction=1) is True  # 买方共振需要买盘大单确认
        assert cache.confirm("btcusdt", direction=-1) is False  # 卖方共振方向不匹配

    def test_confirm_evicts_stale_trades(self):
        cache = SpotLargeOrderCache()
        large_qty = (SPOT_LARGE_ORDER_NOTIONAL_USDT / 100.0) + 1
        stale_ts = int(time.time() * 1000) - 20_000  # 20 秒前，超过 10 秒回溯窗口
        cache.push("btcusdt", _make_tick(100.0, large_qty, is_buyer_maker=False, ts_ms=stale_ts))
        assert cache.confirm("btcusdt", direction=1) is False  # 已过期，不应确认


class TestMarketMonitorAsymmetricFilter:
    def _build_monitor_with_mock_sink(self) -> tuple[MarketMonitor, MagicMock]:
        mock_sink = MagicMock()
        mock_sink.persist_and_broadcast.return_value = "fake-uuid"
        monitor = MarketMonitor(symbols=("btcusdt",), sink=mock_sink)
        return monitor, mock_sink

    @staticmethod
    def _pump_to_resonance(window: TickWindow, base_ts_ms: int, burst_seconds: int = 15) -> int:
        """
        把窗口灌到"短+中窗口共振买方"的状态：120 秒基线 + burst_seconds 秒
        放量（剩 1 秒留给测试自己通过 _on_futures_message 送入最后一条
        消息，模拟"最后一条到达时触发信号"）。返回最后这条消息应该使用的
        时间戳和对应价格所需的信息——调用方按约定拼出最后一条消息。
        """
        for i in range(120):
            window.push(_make_tick(100.0, 1.0, False, ts_ms=base_ts_ms + i * 1000))
        last_baseline_ts = base_ts_ms + 119 * 1000
        for i in range(burst_seconds):
            window.push(_make_tick(100.0 + (i + 1) * 0.5, 6.0, False, ts_ms=last_baseline_ts + (i + 1) * 1000))
        return last_baseline_ts + (burst_seconds + 1) * 1000

    def test_discard_when_futures_resonance_without_spot_confirmation(self):
        """合约触网但现货未确认 -> 判定为洗盘噪声，不落库不广播"""
        monitor, mock_sink = self._build_monitor_with_mock_sink()
        final_ts = self._pump_to_resonance(monitor.futures_windows["btcusdt"], BASE_TS_MS)

        asyncio.run(
            monitor._on_futures_message(_agg_trade_json("BTCUSDT", 108.0, 6.0, False, ts_ms=final_ts))
        )

        mock_sink.persist_and_broadcast.assert_not_called()
        assert "btcusdt" not in monitor.active_leaders

    def test_confirmed_signal_persists_and_updates_active_leaders(self):
        """合约触网 + 现货确认 -> 落库广播，并更新活跃领导者集合"""
        monitor, mock_sink = self._build_monitor_with_mock_sink()
        final_ts = self._pump_to_resonance(monitor.futures_windows["btcusdt"], BASE_TS_MS)

        # 现货侧先出现同向大单（用真实"当前时间"，spot cache 的过期淘汰
        # 用的是墙钟时间，跟 futures 侧的历史模拟时间戳是两套独立时间轴）
        large_qty = (SPOT_LARGE_ORDER_NOTIONAL_USDT / 100.0) + 1
        monitor.spot_cache.push("btcusdt", _make_tick(100.0, large_qty, is_buyer_maker=False))

        asyncio.run(
            monitor._on_futures_message(_agg_trade_json("BTCUSDT", 108.0, 6.0, False, ts_ms=final_ts))
        )

        mock_sink.persist_and_broadcast.assert_called_once_with("BTCUSDT", "DISCOVERY")
        assert "btcusdt" in monitor.active_leaders

    def test_duplicate_discovery_not_retriggered_once_already_leader(self):
        """已经是活跃领导者时，重复的 DISCOVERY 方向共振不应再次触发"""
        monitor, mock_sink = self._build_monitor_with_mock_sink()
        monitor.active_leaders.add("btcusdt")
        final_ts = self._pump_to_resonance(monitor.futures_windows["btcusdt"], BASE_TS_MS)
        large_qty = (SPOT_LARGE_ORDER_NOTIONAL_USDT / 100.0) + 1
        monitor.spot_cache.push("btcusdt", _make_tick(100.0, large_qty, is_buyer_maker=False))

        asyncio.run(
            monitor._on_futures_message(_agg_trade_json("BTCUSDT", 108.0, 6.0, False, ts_ms=final_ts))
        )

        mock_sink.persist_and_broadcast.assert_not_called()


class TestContractOnlyAsymmetricVerification:
    """
    合约独有、没有现货可交叉验证的资产（如 1000PEPEUSDT/GOATUSDT）走
    "二次合约资金强度补偿校验"：量能门槛 x1.3、要求三档窗口全部一致，
    而不是直接跳过现货确认防线。数值已用脚本预先验证过。
    """

    def _build_monitor_with_contract_only_symbol(self) -> tuple[MarketMonitor, MagicMock]:
        mock_sink = MagicMock()
        mock_sink.persist_and_broadcast.return_value = "fake-uuid"
        monitor = MarketMonitor(
            symbols=("btcusdt",), sink=mock_sink, contract_only_symbols=frozenset({"btcusdt"})
        )
        return monitor, mock_sink

    @staticmethod
    def _pump_to_strict_resonance(window: TickWindow, base_ts_ms: int, baseline_count: int = 239) -> int:
        """
        239 秒基线 + 59 秒放量（每秒 1 笔、qty=8），剩 1 秒留给测试自己
        通过 _on_futures_message 送入最后一条消息。这个构造经过脚本验证：
        在 1.3 倍加严 + 全部三档窗口都要一致 的门槛下，短/中/长三档窗口
        全部触发（普通门槛下的构造做不到这一点，长窗口会被稀释）。
        """
        for i in range(baseline_count):
            window.push(_make_tick(100.0, 1.0, False, ts_ms=base_ts_ms + i * 1000))
        last_baseline_ts = base_ts_ms + (baseline_count - 1) * 1000
        for i in range(59):
            window.push(_make_tick(100.0 + (i + 1) * 0.3, 8.0, False, ts_ms=last_baseline_ts + (i + 1) * 1000))
        return last_baseline_ts + 60 * 1000

    def test_contract_only_symbol_triggers_without_spot_confirmation(self):
        """情况B：三档窗口全部一致的强共振 -> 即使完全没有现货数据也应该触发"""
        monitor, mock_sink = self._build_monitor_with_contract_only_symbol()
        final_ts = self._pump_to_strict_resonance(monitor.futures_windows["btcusdt"], BASE_TS_MS)
        # 故意不喂任何 spot_cache 数据，验证情况B确实不依赖现货确认

        asyncio.run(
            monitor._on_futures_message(_agg_trade_json("BTCUSDT", 118.0, 8.0, False, ts_ms=final_ts))
        )

        mock_sink.persist_and_broadcast.assert_called_once_with("BTCUSDT", "DISCOVERY")
        assert "btcusdt" in monitor.active_leaders

    def test_contract_only_discovery_rejects_regular_strength_resonance(self):
        """
        建仓（DISCOVERY）分支：用只能满足常规门槛（2/3 多数）、满足不了
        建仓加严门槛（3/3 全部一致 + 更高量能倍数）的共振强度喂给合约
        独有资产 -> 不应该触发。证明建仓门槛确实比情况A更严格，不是
        形同虚设。
        """
        monitor, mock_sink = self._build_monitor_with_contract_only_symbol()
        # 复用 TestMarketMonitorAsymmetricFilter 里验证过的"常规强度"构造：
        # 120 秒基线 + 16 秒放量，只够让 5s/20s 窗口触发，60s 窗口稀释不触发
        window = monitor.futures_windows["btcusdt"]
        for i in range(120):
            window.push(_make_tick(100.0, 1.0, False, ts_ms=BASE_TS_MS + i * 1000))
        last_baseline_ts = BASE_TS_MS + 119 * 1000
        for i in range(15):
            window.push(_make_tick(100.0 + (i + 1) * 0.5, 6.0, False, ts_ms=last_baseline_ts + (i + 1) * 1000))
        final_ts = last_baseline_ts + 16 * 1000

        asyncio.run(
            monitor._on_futures_message(_agg_trade_json("BTCUSDT", 108.0, 6.0, False, ts_ms=final_ts))
        )

        mock_sink.persist_and_broadcast.assert_not_called()
        assert "btcusdt" not in monitor.active_leaders

    def test_contract_only_exit_triggers_on_regular_strength_resonance(self):
        """
        离场（EXIT）分支：同一个"常规强度"构造（上面那个测试证明了它满足
        不了建仓的加严门槛），喂给一个**已经持仓**的合约独有资产时，应该
        能触发离场——证明离场门槛确实比建仓门槛松，不会因为门槛统一而
        卡在暴跌时退不出来。方向反过来（价格下跌 + 卖方主动），因为
        EXIT 对应的是卖方共振。
        """
        monitor, mock_sink = self._build_monitor_with_contract_only_symbol()
        monitor.active_leaders.add("btcusdt")  # 已经持仓
        window = monitor.futures_windows["btcusdt"]
        for i in range(120):
            window.push(_make_tick(100.0, 1.0, False, ts_ms=BASE_TS_MS + i * 1000))
        last_baseline_ts = BASE_TS_MS + 119 * 1000
        # 价格持续下跌 + 放量（is_buyer_maker=True 表示 taker 是卖方）
        for i in range(15):
            window.push(_make_tick(100.0 - (i + 1) * 0.5, 6.0, True, ts_ms=last_baseline_ts + (i + 1) * 1000))
        final_ts = last_baseline_ts + 16 * 1000

        asyncio.run(
            monitor._on_futures_message(_agg_trade_json("BTCUSDT", 92.0, 6.0, True, ts_ms=final_ts))
        )

        mock_sink.persist_and_broadcast.assert_called_once_with("BTCUSDT", "EXIT")
        assert "btcusdt" not in monitor.active_leaders

    def test_non_contract_only_symbol_still_requires_spot_confirmation(self):
        """对照组：同样的强共振喂给"有现货"的资产（不在 contract_only_symbols
        里），因为没有现货大单确认，仍然应该被丢弃——证明两条分支互不干扰"""
        mock_sink = MagicMock()
        mock_sink.persist_and_broadcast.return_value = "fake-uuid"
        monitor = MarketMonitor(symbols=("btcusdt",), sink=mock_sink, contract_only_symbols=frozenset())
        final_ts = self._pump_to_strict_resonance(monitor.futures_windows["btcusdt"], BASE_TS_MS)

        asyncio.run(
            monitor._on_futures_message(_agg_trade_json("BTCUSDT", 118.0, 8.0, False, ts_ms=final_ts))
        )

        mock_sink.persist_and_broadcast.assert_not_called()  # 没有现货确认，情况A分支拒绝


class TestSignalSinkLiveModeGuard:
    """ALPHA_RUN_MODE 生产安全锁：用 Mock 替换 _pool/_redis，验证 LIVE 模式
    内/外是否真的触发了底层调用，全程不需要真实 MySQL/Redis。"""

    def test_persist_and_broadcast_noop_when_not_live_mode(self, monkeypatch):
        monkeypatch.delenv("ALPHA_RUN_MODE", raising=False)
        sink = SignalSink()
        sink._pool = MagicMock()
        sink._redis = MagicMock()

        result = sink.persist_and_broadcast("BTCUSDT", "DISCOVERY")

        sink._pool.connection.assert_not_called()
        sink._redis.xadd.assert_not_called()
        assert isinstance(result, str) and result  # 仍然返回一个 uuid，只是没有真正落库/广播

    def test_persist_and_broadcast_blocked_when_run_mode_is_wrong_value(self, monkeypatch):
        monkeypatch.setenv("ALPHA_RUN_MODE", "BACKTEST")
        sink = SignalSink()
        sink._pool = MagicMock()
        sink._redis = MagicMock()

        sink.persist_and_broadcast("BTCUSDT", "DISCOVERY")

        sink._pool.connection.assert_not_called()
        sink._redis.xadd.assert_not_called()

    def test_persist_and_broadcast_writes_when_live_mode(self, monkeypatch):
        monkeypatch.setenv("ALPHA_RUN_MODE", "LIVE")
        sink = SignalSink()
        sink._pool = MagicMock()
        sink._redis = MagicMock()

        result = sink.persist_and_broadcast("BTCUSDT", "DISCOVERY")

        sink._pool.connection.assert_called_once()
        sink._redis.xadd.assert_called_once()
        assert isinstance(result, str) and result


def _agg_trade_json(symbol: str, price: float, qty: float, is_buyer_maker: bool, ts_ms: int | None = None) -> str:
    ts_ms = ts_ms if ts_ms is not None else int(time.time() * 1000)
    return json.dumps(
        {
            "stream": f"{symbol.lower()}@aggTrade",
            "data": {
                "e": "aggTrade",
                "E": ts_ms,
                "s": symbol,
                "p": str(price),
                "q": str(qty),
                "m": is_buyer_maker,
                "T": ts_ms,
            },
        }
    )
