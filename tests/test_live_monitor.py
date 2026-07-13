"""
live_monitor 子系统的单元测试。

只测试不依赖真实 MySQL/Redis/WebSocket 连接的纯逻辑部分：
    - TickWindow 的多窗口共振投票
    - SpotLargeOrderCache 的现货大单确认
    - MarketMonitor 的非对称过滤决策链路（SignalSink 用 Mock 替换，
      不发起真实网络/数据库调用）

真实的 WebSocket 长连接、MySQL 写入、Redis 广播需要在真实环境里跑
一段时间验证，本仓库的沙盒环境无法长时间维持外部连接，不在这里测。
"""
import time
from unittest.mock import MagicMock

from live_monitor.market_monitor import (
    MarketMonitor,
    SpotLargeOrderCache,
    Tick,
    TickWindow,
    SPOT_LARGE_ORDER_NOTIONAL_USDT,
)


def _make_tick(price: float, qty: float, is_buyer_maker: bool, ts_ms: int | None = None) -> Tick:
    return Tick(price=price, qty=qty, is_buyer_maker=is_buyer_maker, ts_ms=ts_ms or int(time.time() * 1000))


class TestTickWindow:
    def test_vote_requires_full_window(self):
        window = TickWindow(maxlen=60)
        window.push(_make_tick(100.0, 1.0, False))
        assert window.vote(5) == 0  # 数据不足 5 条，直接投反对票

    def test_resonance_detects_buy_side_with_volume_amplification(self):
        window = TickWindow(maxlen=60)
        # 先填满一整个窗口的"平静"基线成交（小单量、价格不动）
        for _ in range(60):
            window.push(_make_tick(100.0, 1.0, False))
        # 随后连续 25 条价格上涨 + 成交量显著放大（10 倍单量），
        # 短窗口(5)/中窗口(20)的"最近N条"会被这批放量完全覆盖，足以
        # 超过基线均值 1.8 倍的放大门槛；长窗口(60)因为"最近60条"里
        # 仍混了 35 条基线成交被稀释，达不到放大门槛——这正是"多数
        # （2/3）而非全体窗口都要同意"设计的意义所在。
        for i in range(25):
            window.push(_make_tick(100.0 + i, 10.0, False))

        assert window.vote(5) == 1
        assert window.vote(20) == 1
        assert window.vote(60) == 0  # 长窗口被稀释，不投票
        assert window.resonance() == 1  # 2/3 窗口同意，构成多数

    def test_resonance_returns_zero_without_volume_amplification(self):
        window = TickWindow(maxlen=60)
        # 价格持续上涨但成交量始终维持基线水平（没有放量）
        for i in range(60):
            window.push(_make_tick(100.0 + i * 0.1, 1.0, False))
        assert window.resonance() == 0  # 没有量能放大，任何窗口都不投票


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
    def _pump_to_resonance(window: TickWindow, elevated_ticks: int = 24) -> None:
        """
        把窗口灌到"短+中窗口共振买方"的状态：先填满 60 条基线成交，
        再推 elevated_ticks 条放量上涨成交（剩 1 条留给测试自己通过
        _on_futures_message 送入，模拟"最后一条到达时触发信号"）。
        """
        for _ in range(60):
            window.push(_make_tick(100.0, 1.0, False))
        for i in range(elevated_ticks):
            window.push(_make_tick(100.0 + i, 10.0, False))

    def test_discard_when_futures_resonance_without_spot_confirmation(self):
        """合约触网但现货未确认 -> 判定为洗盘噪声，不落库不广播"""
        monitor, mock_sink = self._build_monitor_with_mock_sink()
        self._pump_to_resonance(monitor.futures_windows["btcusdt"])

        monitor._on_futures_message(_agg_trade_json("BTCUSDT", 130.0, 10.0, False))

        mock_sink.persist_and_broadcast.assert_not_called()
        assert "btcusdt" not in monitor.active_leaders

    def test_confirmed_signal_persists_and_updates_active_leaders(self):
        """合约触网 + 现货确认 -> 落库广播，并更新活跃领导者集合"""
        monitor, mock_sink = self._build_monitor_with_mock_sink()
        self._pump_to_resonance(monitor.futures_windows["btcusdt"])

        # 现货侧先出现同向大单
        large_qty = (SPOT_LARGE_ORDER_NOTIONAL_USDT / 100.0) + 1
        monitor.spot_cache.push("btcusdt", _make_tick(100.0, large_qty, is_buyer_maker=False))

        monitor._on_futures_message(_agg_trade_json("BTCUSDT", 130.0, 10.0, False))

        mock_sink.persist_and_broadcast.assert_called_once_with("BTCUSDT", "DISCOVERY")
        assert "btcusdt" in monitor.active_leaders

    def test_duplicate_discovery_not_retriggered_once_already_leader(self):
        """已经是活跃领导者时，重复的 DISCOVERY 方向共振不应再次触发"""
        monitor, mock_sink = self._build_monitor_with_mock_sink()
        monitor.active_leaders.add("btcusdt")
        self._pump_to_resonance(monitor.futures_windows["btcusdt"])
        large_qty = (SPOT_LARGE_ORDER_NOTIONAL_USDT / 100.0) + 1
        monitor.spot_cache.push("btcusdt", _make_tick(100.0, large_qty, is_buyer_maker=False))

        monitor._on_futures_message(_agg_trade_json("BTCUSDT", 130.0, 10.0, False))

        mock_sink.persist_and_broadcast.assert_not_called()


def _agg_trade_json(symbol: str, price: float, qty: float, is_buyer_maker: bool) -> str:
    import json

    return json.dumps(
        {
            "stream": f"{symbol.lower()}@aggTrade",
            "data": {
                "e": "aggTrade",
                "E": int(time.time() * 1000),
                "s": symbol,
                "p": str(price),
                "q": str(qty),
                "m": is_buyer_maker,
                "T": int(time.time() * 1000),
            },
        }
    )
