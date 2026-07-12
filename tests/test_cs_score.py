"""CCSDetector（Capital Convergence Score）的基础单元测试"""
import numpy as np
import pandas as pd
import pytest

from detectors.cs_score import CCSDetector


def _make_wide_table(n: int = 100, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2026-01-01", periods=n, freq="D")

    frames = []
    for symbol, drift in (("AAA", 0.004), ("BBB", 0.0)):
        noise = rng.normal(0, 0.01, size=n)
        returns = drift + noise
        close = 100 * np.cumprod(1 + returns)
        # 低噪声基线成交量（标准差约为均值的 2.5%），避免随机噪声本身
        # 就足以触发"温和放量"钟形函数，干扰"平飞 vs 放量"的对比测试
        volume = rng.normal(1200, 30, size=n)
        # AAA 在后半段模拟温和放量
        if symbol == "AAA":
            volume[n // 2 :] *= 2.1
        turnover_rate = rng.uniform(0.01, 0.03, size=n)
        funding_rate = rng.normal(0.0001, 0.00005, size=n)
        frames.append(
            pd.DataFrame(
                {
                    "timestamp": timestamps,
                    "symbol": symbol,
                    "close": close,
                    "volume": volume,
                    "turnover_rate": turnover_rate,
                    "funding_rate": funding_rate,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_calculate_cs_produces_all_components():
    df = _make_wide_table()
    detector = CCSDetector(rs_slope_window=10, zscore_window=20, volume_window=10, crowding_window=20)
    result = detector.calculate_cs(df)

    for col in ("delta2_rs", "volume_delta", "crowding_penalty", "cs_score"):
        assert col in result.columns

    tail = result.groupby("symbol").tail(20)
    assert tail[["delta2_rs", "volume_delta", "crowding_penalty", "cs_score"]].notna().all().all()


def test_volume_delta_bounded_and_penalizes_no_amplification():
    df = _make_wide_table()
    detector = CCSDetector(rs_slope_window=10, zscore_window=20, volume_window=10, crowding_window=20)
    result = detector.calculate_cs(df)

    assert (result["volume_delta"] >= 0).all()
    assert (result["volume_delta"] <= 1).all()

    # 放量指标衡量"相对自身近期滚动均值的异常放大"，只在放量刚发生、
    # 滚动均值尚未被新水平"追上"的过渡窗口内才会明显走高；用量窗口
    # volume_window=10，因此取放量发生点（第 50 根）后 15 根作为过渡窗口。
    flat_symbol = result[result["symbol"] == "BBB"].iloc[50:65]
    pumped_symbol = result[result["symbol"] == "AAA"].iloc[50:65]
    assert pumped_symbol["volume_delta"].mean() > flat_symbol["volume_delta"].mean()


def test_crowding_penalty_within_unit_interval():
    df = _make_wide_table()
    detector = CCSDetector(rs_slope_window=10, zscore_window=20, volume_window=10, crowding_window=20)
    result = detector.calculate_cs(df)

    assert (result["crowding_penalty"] > 0).all()
    assert (result["crowding_penalty"] <= 1).all()


def test_missing_required_column_raises():
    df = _make_wide_table().drop(columns=["turnover_rate"])
    detector = CCSDetector()
    with pytest.raises(ValueError):
        detector.calculate_cs(df)
