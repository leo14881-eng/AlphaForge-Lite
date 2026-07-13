"""config/asset_profiles.py 与其在 CCSDetector / BacktestConfig 中的接线的单测"""
import numpy as np
import pandas as pd

from backtest.runner import BacktestConfig
from config.asset_profiles import (
    ASSET_CLASS_PROFILES,
    ASSET_PROFILE_MAP,
    AssetClass,
    MAINSTREAM_SYMBOLS,
    MAINSTREAM_SYMBOLS_CCXT,
    build_asset_weight_overrides,
)
from detectors.cs_score import CCSDetector


def test_mainstream_symbols_consistent_across_all_consumers():
    """
    全局扫描修复的回归测试：MAINSTREAM_SYMBOLS 此前在
    data/download_data.py / run_tuning.py / run_regression_check.py /
    config/asset_profiles.py::ASSET_PROFILE_MAP 四处各自独立硬编码，
    没有测试守护一致性。现在 config.asset_profiles 是唯一权威来源，
    其余三处改成直接 import——这里验证：
        1. run_tuning.py / run_regression_check.py 确实 import 的是
           同一个对象（而不是自己又重新定义了一份"恰好长得一样"的元组）
        2. data/download_data.py 的 ccxt 格式清单（MAINSTREAM_SYMBOLS_CCXT）
           跟规范格式清单一一对应、转换正确
        3. ASSET_PROFILE_MAP 里被标记为 CORE 的 symbol 集合，跟
           MAINSTREAM_SYMBOLS 完全一致
    """
    import run_regression_check
    import run_tuning
    from data.download_data import MAINSTREAM_SYMBOLS as download_data_symbols

    assert run_tuning.MAINSTREAM_SYMBOLS is MAINSTREAM_SYMBOLS
    assert run_regression_check.MAINSTREAM_SYMBOLS is MAINSTREAM_SYMBOLS
    assert download_data_symbols is MAINSTREAM_SYMBOLS_CCXT

    assert len(MAINSTREAM_SYMBOLS_CCXT) == len(MAINSTREAM_SYMBOLS)
    for plain, ccxt_format in zip(MAINSTREAM_SYMBOLS, MAINSTREAM_SYMBOLS_CCXT):
        assert ccxt_format.replace("/", "") == plain

    core_symbols = {s for s, cls in ASSET_PROFILE_MAP.items() if cls == AssetClass.CORE}
    assert core_symbols == set(MAINSTREAM_SYMBOLS)


def test_build_asset_weight_overrides_matches_profile_map():
    overrides = build_asset_weight_overrides()
    assert len(overrides) == len(ASSET_PROFILE_MAP)
    core_profile = ASSET_CLASS_PROFILES[AssetClass.CORE]
    meme_profile = ASSET_CLASS_PROFILES[AssetClass.MEME]

    assert overrides["BTCUSDT"] == (core_profile.weight_delta2_rs, core_profile.weight_volume_delta)
    assert overrides["LUNAUSDT"] == (meme_profile.weight_delta2_rs, meme_profile.weight_volume_delta)


def test_core_and_meme_profiles_have_opposite_weight_emphasis():
    core = ASSET_CLASS_PROFILES[AssetClass.CORE]
    meme = ASSET_CLASS_PROFILES[AssetClass.MEME]
    assert core.weight_delta2_rs > core.weight_volume_delta
    assert meme.weight_delta2_rs < meme.weight_volume_delta


def test_backtest_config_default_detector_has_overrides_wired_in():
    config = BacktestConfig()
    assert config.detector.asset_weight_overrides is not None
    assert config.detector.asset_weight_overrides["BTCUSDT"] == (0.8, 0.2)
    assert config.detector.asset_weight_overrides["LUNAUSDT"] == (0.05, 0.95)


def _make_wide_table(symbols: tuple[str, ...], n: int = 80, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2026-01-01", periods=n, freq="D")
    frames = []
    for symbol in symbols:
        frames.append(
            pd.DataFrame(
                {
                    "timestamp": ts,
                    "symbol": symbol,
                    "close": 100 * np.cumprod(1 + rng.normal(0.002, 0.01, n)),
                    "volume": rng.uniform(1000, 1500, n),
                    "turnover_rate": rng.uniform(0.01, 0.03, n),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_asset_weight_overrides_produce_different_cs_score_per_symbol():
    """
    同样的量价形态，一个 symbol 落在 override 表里、一个不在，
    应该用不同的权重算出不同的 cs_score——证明覆盖机制真的在生效，
    不是挂了个空字典摆设。
    """
    df = _make_wide_table(("BTCUSDT", "NOTINMAP"))
    detector = CCSDetector(
        rs_slope_window=10,
        zscore_window=20,
        volume_window=10,
        crowding_window=20,
        weight_delta2_rs=0.5,
        weight_volume_delta=0.5,
        asset_weight_overrides={"BTCUSDT": (0.9, 0.1)},
    )
    result = detector.calculate_cs(df)

    btc = result[result["symbol"] == "BTCUSDT"].tail(20)
    other = result[result["symbol"] == "NOTINMAP"].tail(20)

    # BTCUSDT 用 override 权重 (0.9, 0.1)，NOTINMAP 用实例默认权重 (0.5, 0.5)
    expected_btc = 0.9 * btc["delta2_rs"] + 0.1 * btc["volume_delta"]
    expected_other = 0.5 * other["delta2_rs"] + 0.5 * other["volume_delta"]

    assert np.allclose((btc["cs_score"] / btc["crowding_penalty"]).to_numpy(), expected_btc.to_numpy(), atol=1e-9)
    assert np.allclose(
        (other["cs_score"] / other["crowding_penalty"]).to_numpy(), expected_other.to_numpy(), atol=1e-9
    )
