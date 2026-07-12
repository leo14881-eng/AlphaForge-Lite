"""
data/download_data.py 的单元测试。

只测试不依赖网络的纯函数（换手率代理指标的计算逻辑）；实际的
交易所拉取逻辑需要网络访问 Binance 公开接口，不适合放进常规单测里，
已通过手工联网验证（见 project_manifest.md 的"实机验证记录"）。
"""
import numpy as np
import pandas as pd
import pytest

from data.download_data import _estimate_turnover_rate, _TURNOVER_RATE_RANGE


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
