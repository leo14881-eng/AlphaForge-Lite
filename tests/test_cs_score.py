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


def test_symbol_column_stays_correctly_aligned_with_shuffled_unequal_groups():
    """
    全局扫描修复的回归测试：symbol 列以前是在 calculate_cs() 里按"整体
    输出顺序"位置对齐拼回去的隐含假设，现在改成在 _compute_for_symbol()
    内部每个分组自己把 symbol 写回去，天然跟随每一行。这里故意构造
    "非字典序 symbol + 不等长分组 + 输入行随机打乱"的输入，验证每一行
    的 symbol 标签、timestamp、close 三者的对应关系没有错位——如果又
    出现按位置对齐的 bug，这个测试会先于生产环境暴露出来。
    """
    rng = np.random.default_rng(2026)
    frames = []
    # 故意用非字典序的 symbol 名（ZZZ 排在 AAA 前面）+ 不等长分组
    for symbol, n in (("ZZZ", 40), ("AAA", 25), ("MMM", 55)):
        timestamps = pd.date_range("2026-01-01", periods=n, freq="D")
        close = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, size=n))
        frames.append(
            pd.DataFrame(
                {
                    "timestamp": timestamps,
                    "symbol": symbol,
                    "close": close,
                    "volume": rng.normal(1000, 30, size=n),
                    "turnover_rate": rng.uniform(0.01, 0.03, size=n),
                    "funding_rate": rng.normal(0.0001, 0.00005, size=n),
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    # 关键：把输入行的物理顺序打乱，不再是"按 symbol 分组连续排列"，
    # 逼真模拟真实世界里数据源不保证预先排好序的情况
    df = df.sample(frac=1.0, random_state=7).reset_index(drop=True)

    detector = CCSDetector(rs_slope_window=10, zscore_window=20, volume_window=10, crowding_window=20)
    result = detector.calculate_cs(df)

    # 用 (symbol, timestamp) 做外键，核对 close 值在结果表里跟原始输入
    # 完全一致——如果 symbol 标签跟其它列错位，这里的 close 值会对不上
    original_lookup = df.set_index(["symbol", "timestamp"])["close"]
    result_lookup = result.set_index(["symbol", "timestamp"])["close"]
    pd.testing.assert_series_equal(
        result_lookup.sort_index(), original_lookup.sort_index(), check_names=False
    )
    # 三个 symbol 各自的行数应该跟输入时一致，不多不少、没有跨组串位
    assert result[result["symbol"] == "ZZZ"].shape[0] == 40
    assert result[result["symbol"] == "AAA"].shape[0] == 25
    assert result[result["symbol"] == "MMM"].shape[0] == 55
