"""
全周期超大样本妖币压力测试脚本（第六阶段：反生存者偏差极限寻优）

诚实的范围说明（请先读这一段再看结果）：
    本脚本设计目标是覆盖 2017-2026 年完整周期，但经过 ccxt 实测确认，
    EPIC_POOL 里的 15 个资产没有任何一个在 Binance 上早于 2020 年就已
    上市——LUNA/SOL/AXS 最早也只追溯到 2020 年下半年，多数 meme 资产
    2023-2024 年才上市，"币安人生"更是 2026-01 才上市。因此本脚本实际
    能验证的是"这批资产各自从真实上市日起到 2026-07"这段区间，天然
    覆盖了 2021 年"5·19"闪崩、2022 年 LUNA 死亡螺旋这两个最具代表性的
    极端事件，但无法覆盖 2017 牛市/2018 冰封（这批资产当时都不存在）。
    这不是脚本的 bug，是真实市场的历史事实，脚本运行时会如实打印
    每个资产的实际数据覆盖区间，不做任何美化。

    另有一处数据完整性提示：LUNA/USDT 在 2022-05-13 前后的价格序列存在
    真实的"接续断档"——原始 Terra LUNA 崩盘归零后，Binance 在同一个
    symbol 下接续了 2022-05-28 上线的 Terra 2.0 新代币，即 2022-05-13
    之后的"价格回升"并非原币复活，而是两个不同资产的价格拼接。本脚本
    不对此做特殊处理（如实使用原始数据），仅在此说明，供复盘时留意。

用法：
    python run_meme_stress_test.py
    python run_meme_stress_test.py --data crypto_market_daily.csv
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path

import pandas as pd

from backtest.data_loader import DataLoader
from backtest.report import BacktestReporter
from backtest.runner import BacktestConfig, BacktestRunner
from config.settings import RAW_DATA_DIR
from database.session import init_db
from detectors.cs_score import CCSDetector
from state_machine.constants import LifecycleStage
from state_machine.engine import StateMachineEngine

DEFAULT_DATA_FILE = "crypto_market_daily.csv"

# 15 个"史诗级妖币/神币"资产池（原始名称 -> 落库后的 symbol 命名）。
# "币安人生"是 Binance 上真实存在的现货交易对（base 本身就是中文），
# 用原始 dict 做显式的多模式映射，确保中文 symbol 在过滤/匹配时不会
# 因为编码或空白差异被静默漏掉。
EPIC_POOL: dict[str, str] = {
    "GALA": "GALAUSDT",
    "AXS": "AXSUSDT",
    "WIF": "WIFUSDT",
    "FLOKI": "FLOKIUSDT",
    "LUNA": "LUNAUSDT",
    "BONK": "BONKUSDT",
    "PEPE": "PEPEUSDT",
    "SOL": "SOLUSDT",
    "TIA": "TIAUSDT",
    "SUI": "SUIUSDT",
    "币安人生": "币安人生USDT",
    "ACT": "ACTUSDT",
    "GOAT": "GOATUSDT",
    "PNUT": "PNUTUSDT",
    "MOODENG": "MOODENGUSDT",
}

# 极限光谱网格：6 档权重配对（含两组接近单组件独占的极端配置） x 3 档迟滞窗口
WEIGHT_PAIRS: tuple[tuple[float, float], ...] = (
    (0.95, 0.05),
    (0.9, 0.1),
    (0.8, 0.2),
    (0.5, 0.5),
    (0.2, 0.8),
    (0.05, 0.95),
)
HYSTERESIS_WINDOWS: tuple[int, ...] = (1, 2, 3)

# 判定"该资产从峰值显著下跌"的阈值，用于甄别"真正经历崩溃"的样本，
# 核心审判结论优先看这批样本的表现（逃顶质量），而不是全体资产的均值。
CRASH_DRAWDOWN_THRESHOLD = 0.5  # 峰值回撤超过 50% 视为"崩溃/洗盘出局"


@dataclass
class EpicComboResult:
    """单个参数组合在整个妖币资产池上的横截面统计结果"""

    w_a: float
    w_b: float
    hysteresis_window: int
    run_id: str
    epic_lead_time_median: float | None
    epic_lead_time_sample: int
    coverage_completeness_median: float | None
    coverage_completeness_sample: int
    crashed_asset_coverage_median: float | None
    crashed_asset_count: int


# ----------------------------------------------------------------------
# 数据接入：健壮解析 EPIC_POOL（含中文 symbol），异常隔离
# ----------------------------------------------------------------------


def _resolve_epic_symbols_present(data: pd.DataFrame) -> dict[str, str]:
    """
    在实际加载的大宽表里核对 EPIC_POOL 每个资产是否存在，返回
    {原始名称: 实际symbol} 的子集；缺失的资产打印警告但不中断整体流程
    （异常隔离——特别是给 ACT/GOAT/PNUT/MOODENG/币安人生 这类近期上线、
    早期历史缺失的资产用的容错路径）。
    """
    present_symbols = set(data["symbol"].astype(str).str.strip())
    resolved: dict[str, str] = {}
    for label, symbol in EPIC_POOL.items():
        if symbol in present_symbols:
            resolved[label] = symbol
        else:
            print(f"[异常隔离] 资产池成员 {label!r}（期望 symbol={symbol!r}）不在数据集中，跳过", file=sys.stderr)
    if not resolved:
        print("[异常隔离] 整个妖币资产池都未能在数据中匹配到，请先运行 data/download_data.py", file=sys.stderr)
    return resolved


def _print_coverage_summary(data: pd.DataFrame, epic_symbols: dict[str, str]) -> None:
    print("\n[数据覆盖] 妖币资产池实际数据区间（如实反映早期历史缺口）：")
    for label, symbol in epic_symbols.items():
        asset_rows = data[data["symbol"] == symbol]
        start, end = asset_rows["timestamp"].min(), asset_rows["timestamp"].max()
        print(f"  {label:10s} ({symbol:16s}): {start.date()} ~ {end.date()}，共 {len(asset_rows)} 根K线")


# ----------------------------------------------------------------------
# 单组参数执行 + 妖币池横截面统计
# ----------------------------------------------------------------------


def _compute_coverage_completeness(
    symbol: str, entry_ts, exit_ts_or_none, price_df: pd.DataFrame
) -> float | None:
    """
    主升浪核心段覆盖完整度 = (退出价 - 入场价) / (区间峰值价 - 入场价)。

    衡量"从进场到退出，实际吃到了理论最大涨幅的多少比例"——1.0 表示
    精准在峰值附近离场，越接近 0 甚至为负，说明离场太晚、把涨幅甚至
    本金都还了回去。搜索区间为 [入场时间, 退出时间]（若尚未退出则用
    数据末尾），峰值价与退出价都从原始价格序列里取，不依赖任何四舍
    五入的中间结果。
    """
    symbol_price = price_df[price_df["symbol"] == symbol].sort_values("timestamp")
    if symbol_price.empty:
        return None

    window_end = exit_ts_or_none if exit_ts_or_none is not None else symbol_price["timestamp"].max()
    window = symbol_price[(symbol_price["timestamp"] >= entry_ts) & (symbol_price["timestamp"] <= window_end)]
    if window.empty:
        return None

    entry_matches = symbol_price[symbol_price["timestamp"] <= entry_ts]
    if entry_matches.empty:
        return None
    entry_price = float(entry_matches.iloc[-1]["close"])
    peak_price = float(window["close"].max())

    exit_matches = symbol_price[symbol_price["timestamp"] <= window_end]
    exit_price = float(exit_matches.iloc[-1]["close"])

    if peak_price <= entry_price:
        return None  # 入场后从未创出新高，谈不上"覆盖了多少主升浪"
    return (exit_price - entry_price) / (peak_price - entry_price)


def _epic_pool_cross_section(
    reporter: BacktestReporter, epic_symbols: dict[str, str], price_df: pd.DataFrame
) -> tuple[list[float], list[float], list[tuple[str, float]]]:
    """
    对妖币池做横截面统计：返回
        (lead_time样本列表, 覆盖完整度样本列表, [(资产, 覆盖完整度) for 崩溃资产])
    """
    logs_df = reporter.logs_df
    lead_time_samples: list[float] = []
    coverage_samples: list[float] = []
    crashed_coverage: list[tuple[str, float]] = []

    for label, symbol in epic_symbols.items():
        asset_logs = logs_df[logs_df["asset"] == symbol].sort_values("event_ts")
        if asset_logs.empty:
            continue

        entries = asset_logs[asset_logs["to_stage"].isin([LifecycleStage.SEED.value, LifecycleStage.DISCOVERY.value])]
        if entries.empty:
            continue
        entry_row = entries.iloc[0]
        entry_ts = entry_row["event_ts"]

        symbol_price = price_df[price_df["symbol"] == symbol].sort_values("timestamp").reset_index(drop=True)
        if symbol_price.empty:
            continue
        entry_price_matches = symbol_price[symbol_price["timestamp"] <= entry_ts]
        if entry_price_matches.empty:
            continue
        entry_idx_matches = symbol_price.index[symbol_price["timestamp"] <= entry_ts]
        entry_idx = entry_idx_matches[-1]

        exits_after = asset_logs[
            (asset_logs["to_stage"] == LifecycleStage.EXIT.value) & (asset_logs["event_ts"] > entry_ts)
        ]
        cycle_end_ts = exits_after["event_ts"].iloc[0] if not exits_after.empty else symbol_price["timestamp"].iloc[-1]
        window = symbol_price[(symbol_price["timestamp"] >= entry_ts) & (symbol_price["timestamp"] <= cycle_end_ts)]
        if not window.empty:
            peak_idx = window["close"].idxmax()
            lead_time_samples.append(float(peak_idx - entry_idx))

        exit_ts = exits_after["event_ts"].iloc[0] if not exits_after.empty else None
        coverage = _compute_coverage_completeness(symbol, entry_ts, exit_ts, price_df)
        if coverage is not None:
            coverage_samples.append(coverage)

            full_series = price_df[price_df["symbol"] == symbol].sort_values("timestamp")
            peak_price = float(full_series["close"].max())
            last_price = float(full_series["close"].iloc[-1])
            if peak_price > 0 and (peak_price - last_price) / peak_price >= CRASH_DRAWDOWN_THRESHOLD:
                crashed_coverage.append((label, coverage))

    return lead_time_samples, coverage_samples, crashed_coverage


def _run_one_combo(
    data: pd.DataFrame,
    data_arg: str,
    epic_symbols: dict[str, str],
    price_df: pd.DataFrame,
    w_a: float,
    w_b: float,
    hysteresis_window: int,
) -> EpicComboResult:
    detector = CCSDetector(weight_delta2_rs=w_a, weight_volume_delta=w_b)
    engine = StateMachineEngine(hysteresis_window=hysteresis_window)
    config = BacktestConfig(
        strategy_name="meme_stress_test",
        strategy_version=f"wa{w_a}_wb{w_b}_h{hysteresis_window}",
        data_source=data_arg,
        detector=detector,
        engine=engine,
    )

    run = BacktestRunner(data=data, config=config).run()
    reporter = BacktestReporter(run_id=run.run_id)

    lead_time_samples, coverage_samples, crashed_coverage = _epic_pool_cross_section(
        reporter, epic_symbols, price_df
    )

    lead_time_median = float(pd.Series(lead_time_samples).median()) if lead_time_samples else None
    coverage_median = float(pd.Series(coverage_samples).median()) if coverage_samples else None
    crashed_median = (
        float(pd.Series([c for _, c in crashed_coverage]).median()) if crashed_coverage else None
    )

    return EpicComboResult(
        w_a=w_a,
        w_b=w_b,
        hysteresis_window=hysteresis_window,
        run_id=run.run_id,
        epic_lead_time_median=lead_time_median,
        epic_lead_time_sample=len(lead_time_samples),
        coverage_completeness_median=coverage_median,
        coverage_completeness_sample=len(coverage_samples),
        crashed_asset_coverage_median=crashed_median,
        crashed_asset_count=len(crashed_coverage),
    )


# ----------------------------------------------------------------------
# 天梯榜打印
# ----------------------------------------------------------------------


def _print_leaderboard(results: list[EpicComboResult]) -> None:
    df = pd.DataFrame([asdict(r) for r in results])
    # 核心审判的排序键是"崩溃资产的覆盖完整度中位数"——这直接回答
    # "哪组参数最能在崩溃前夜清仓、挽救利润"；中位数打平时，用崩溃资产
    # 样本数量做次级排序键（样本越多说明信号越稳定，不是巧合命中一两
    # 个资产），样本不足（未观测到任何崩溃资产）的组合排在最后。
    ranked = df.sort_values(
        ["crashed_asset_coverage_median", "crashed_asset_count"],
        ascending=[False, False],
        na_position="last",
    ).reset_index(drop=True)
    ranked.insert(0, "rank", ranked.index + 1)

    display = ranked.rename(
        columns={
            "w_a": "w_a(斜率加速度)",
            "w_b": "w_b(温和放量)",
            "hysteresis_window": "Hysteresis",
            "epic_lead_time_median": "妖币LeadTime中位数",
            "epic_lead_time_sample": "LeadTime样本数",
            "coverage_completeness_median": "全体覆盖完整度中位数",
            "coverage_completeness_sample": "覆盖度样本数",
            "crashed_asset_coverage_median": "崩溃资产覆盖完整度中位数",
            "crashed_asset_count": "崩溃资产数",
        }
    )

    print("\n===== 史诗级妖币参数天梯榜（按崩溃资产覆盖完整度中位数降序） =====")
    print(
        display[
            [
                "rank",
                "w_a(斜率加速度)",
                "w_b(温和放量)",
                "Hysteresis",
                "妖币LeadTime中位数",
                "LeadTime样本数",
                "全体覆盖完整度中位数",
                "覆盖度样本数",
                "崩溃资产覆盖完整度中位数",
                "崩溃资产数",
                "run_id",
            ]
        ].to_string(index=False)
    )

    best = ranked.iloc[0]
    print("\n【核心审判结论】")
    if pd.notna(best["crashed_asset_coverage_median"]):
        print(
            "  面对可能走向归零/剧烈洗盘的资产，最能'在崩溃前夜以最快前瞻天数强制清仓逃顶、"
            "挽救利润'的参数组合是："
            f" w_a={best['w_a']}, w_b={best['w_b']}, hysteresis_window={int(best['hysteresis_window'])}"
        )
        print(
            f"  该组合在 {int(best['crashed_asset_count'])} 个真实经历过 >= "
            f"{CRASH_DRAWDOWN_THRESHOLD:.0%} 峰值回撤的'崩溃资产'样本上，"
            f"覆盖完整度中位数为 {best['crashed_asset_coverage_median']:.2f}"
            "（1.0 = 精准逃在峰值附近，0 = 把涨幅基本还光，负数 = 亏得比入场价还惨）。"
        )
        if pd.notna(best["epic_lead_time_median"]):
            print(f"  同一组合在全体妖币池上的 Lead Time 中位数为 {best['epic_lead_time_median']:.1f} 个时间步。")
    else:
        print("  本次扫描没有任何参数组合在崩溃资产样本上产生可用的覆盖完整度数据，无法给出核心审判结论。")

    print(
        "\n  提醒：以上结论仅基于本地这一份静态历史数据的单次回测，"
        "样本量有限、时间窗口单一，不构成可直接实盘的投资建议，"
        "详见 project_manifest.md 中关于样本外验证缺失的说明。"
    )


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------


def _resolve_data_path(data_arg: str) -> Path:
    candidate = Path(data_arg)
    return candidate if candidate.is_absolute() or candidate.exists() else RAW_DATA_DIR / data_arg


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AlphaForge-Lite 妖币压力测试：15 资产池极限参数网格扫描 + 反归零逃顶审计"
    )
    parser.add_argument(
        "--data",
        default=DEFAULT_DATA_FILE,
        help=f"历史大宽表文件名（相对 data/raw/）或路径，默认 {DEFAULT_DATA_FILE}",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    init_db()

    resolved = _resolve_data_path(args.data)
    if not resolved.exists():
        print(f"[数据接入层] 文件不存在: {resolved}", file=sys.stderr)
        sys.exit(1)

    print(f"[数据接入层] 正在加载大宽表: {resolved}")
    try:
        data = DataLoader().load_path(resolved)
    except ValueError as exc:
        print(f"[数据接入层] Schema 校验失败: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"[数据接入层] 加载完成：{len(data)} 行，涉及 {data['symbol'].nunique()} 个资产")

    epic_symbols = _resolve_epic_symbols_present(data)
    if not epic_symbols:
        sys.exit(1)
    _print_coverage_summary(data, epic_symbols)

    combos = list(product(WEIGHT_PAIRS, HYSTERESIS_WINDOWS))
    print(
        f"\n[参数扫描层] 共 {len(combos)} 组极限参数组合"
        f"（{len(WEIGHT_PAIRS)} 组权重 × {len(HYSTERESIS_WINDOWS)} 组迟滞窗口），开始逐组静默回测……"
    )

    results: list[EpicComboResult] = []
    for i, ((w_a, w_b), hysteresis_window) in enumerate(combos, start=1):
        print(f"[参数扫描层] ({i}/{len(combos)}) w_a={w_a}, w_b={w_b}, hysteresis_window={hysteresis_window} ...")
        try:
            result = _run_one_combo(data, args.data, epic_symbols, data, w_a, w_b, hysteresis_window)
        except Exception as exc:
            print(f"[参数扫描层] 该组合执行失败，跳过: {exc}", file=sys.stderr)
            continue
        results.append(result)

    if not results:
        print("[参数扫描层] 没有任何参数组合成功完成，无法生成天梯榜。", file=sys.stderr)
        sys.exit(1)

    _print_leaderboard(results)


if __name__ == "__main__":
    main()
