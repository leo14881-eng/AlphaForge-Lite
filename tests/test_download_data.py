"""
data/download_data.py 的单元测试。

大部分测试只覆盖不依赖网络的纯函数（换手率代理指标的计算逻辑）；实际
对接 Binance 公开接口的联通性已通过手工联网验证（见 project_manifest.md
的"实机验证记录"）。

_fetch_symbol_ohlcv 的分页重试/部分数据保留逻辑（全局扫描修复）用
Mock exchange 对象验证，不需要真实网络，纯粹测试重试状态机本身的
正确性。
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from data.download_data import (
    _estimate_turnover_rate,
    _fetch_symbol_ohlcv,
    _KLINES_PER_REQUEST,
    _MAX_PAGE_RETRIES,
    _TURNOVER_RATE_RANGE,
)


def test_estimate_turnover_rate_within_target_range():
    volume = pd.Series(np.linspace(100, 100_000, 50))
    turnover = _estimate_turnover_rate(volume)

    low, high = _TURNOVER_RATE_RANGE
    assert (turnover >= low - 1e-9).all()
    assert (turnover <= high + 1e-9).all()


def test_estimate_turnover_rate_monotonic_with_volume():
    volume = pd.Series([100.0, 1_000.0, 10_000.0, 100_000.0])
    turnover = _estimate_turnover_rate(volume)
    assert turnover.is_monotonic_increasing


def test_estimate_turnover_rate_constant_volume_returns_midpoint():
    volume = pd.Series([500.0] * 10)
    turnover = _estimate_turnover_rate(volume)
    low, high = _TURNOVER_RATE_RANGE
    assert np.allclose(turnover.to_numpy(), (low + high) / 2)


def _make_kline(ts_ms: int) -> list:
    """构造一条最小可用的 ccxt OHLCV 行：[ts, open, high, low, close, volume]"""
    return [ts_ms, 100.0, 101.0, 99.0, 100.5, 1000.0]


def test_fetch_symbol_ohlcv_happy_path_pages_until_exhausted():
    """正常翻页：每页凑不满 _KLINES_PER_REQUEST 条就代表拿到最新数据，停止翻页"""
    exchange = MagicMock()
    page1 = [_make_kline(1000 + i * 86_400_000) for i in range(_KLINES_PER_REQUEST)]
    page2 = [_make_kline(1000 + (i + _KLINES_PER_REQUEST) * 86_400_000) for i in range(50)]
    exchange.fetch_ohlcv.side_effect = [page1, page2]

    rows = _fetch_symbol_ohlcv(exchange, "BTC/USDT", "1d", since_ms=0, until_ms=10**15)

    assert len(rows) == _KLINES_PER_REQUEST + 50
    assert exchange.fetch_ohlcv.call_count == 2


def test_fetch_symbol_ohlcv_retries_transient_failure_then_succeeds():
    """
    全局扫描修复的回归测试：第一页请求前两次失败（模拟网络抖动），
    第三次成功——不应该丢弃、不应该抛异常，最终应该拿到完整数据。
    """
    exchange = MagicMock()
    page1 = [_make_kline(1000 + i * 86_400_000) for i in range(30)]
    exchange.fetch_ohlcv.side_effect = [
        ConnectionError("网络抖动"),
        ConnectionError("网络抖动"),
        page1,
    ]

    with patch("data.download_data.time.sleep"):  # 不真的等待重试退避时间
        rows = _fetch_symbol_ohlcv(exchange, "BTC/USDT", "1d", since_ms=0, until_ms=10**15)

    assert len(rows) == 30
    assert exchange.fetch_ohlcv.call_count == 3


def test_fetch_symbol_ohlcv_preserves_partial_data_when_retries_exhausted():
    """
    全局扫描修复的核心回归测试：第一页成功拿到数据后，第二页连续
    _MAX_PAGE_RETRIES 次都失败——应该保留第一页已经拿到的数据、正常
    返回（不抛异常），而不是把第一页也一起丢掉。
    """
    exchange = MagicMock()
    page1 = [_make_kline(1000 + i * 86_400_000) for i in range(_KLINES_PER_REQUEST)]
    exchange.fetch_ohlcv.side_effect = [page1] + [ConnectionError("持续网络故障")] * _MAX_PAGE_RETRIES

    with patch("data.download_data.time.sleep"):
        rows = _fetch_symbol_ohlcv(exchange, "BTC/USDT", "1d", since_ms=0, until_ms=10**15)

    # 第一页的数据必须保留下来，不能因为第二页失败就整体丢弃
    assert len(rows) == _KLINES_PER_REQUEST
    assert exchange.fetch_ohlcv.call_count == 1 + _MAX_PAGE_RETRIES
