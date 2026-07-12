"""
参数网格扫描脚本

在真实历史大宽表上，对 CCSDetector 的组件权重
（weight_delta2_rs / weight_volume_delta）与 StateMachineEngine 的
迟滞窗口（hysteresis_window）做网格扫描：每组参数各跑一次独立的
BacktestRunner，只从 BacktestReporter 抓取结构化审计结果（不打印
逐笔迁移日志），最终打印一张按 Lead Time 中位数降序排列的
"最优参数审计天梯榜"，直接回答"哪组参数最安全、最提前发现领导者"。

用法：
    python run_tuning.py --data your_real_wide_table.csv
    python run_tuning.py --data xxx.csv --weight-pairs "0.7,0.3;0.5,0.5" --hysteresis-windows "2,3"
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

# 默认扫描网格：权重按"配对"给出而非笛卡尔积，避免出现两个权重都很大/
# 都很小这类不构成有效对照的组合。
DEFAULT_WEIGHT_PAIRS: tuple[tuple[float, float], ...] = ((0.8, 0.2), (0.6, 0.4), (0.4, 0.6))
DEFAULT_HYSTERESIS_WINDOWS: tuple[int, ...] = (2, 3, 4)


@dataclass
class TuningResult:
    """单个参数组合的审计结果，用于最终天梯榜排序"""

    weight_delta2_rs: float
    weight_volume_delta: float
    hysteresis_window: int
    run_id: str
    status: str
    overall_median_lead_time: float | None
    seed_median_lead_time: float | None
    discovery_median_lead_time: float | None
    discovery_trigger_count: int
    sample_count: int


# ----------------------------------------------------------------------
# 参数解析
# ----------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AlphaForge-Lite 参数网格扫描：寻找 Lead Time 中位数最大化的最优参数组合"
    )
    parser.add_argument("--data", required=True, help="真实历史大宽表文件名（相对 data/raw/）或路径")
    parser.add_argument(
        "--weight-pairs",
        default=None,
        help='自定义权重组合，格式 "0.8,0.2;0.6,0.4;0.4,0.6"；不指定则使用默认三组',
    )
    parser.add_argument(
        "--hysteresis-windows",
        default=None,
        help='自定义迟滞窗口列表，逗号分隔，如 "2,3,4"；不指定则使用默认 2~4',
    )
    return parser.parse_args()


def _parse_weight_pairs(raw: str | None) -> tuple[tuple[float, float], ...]:
    if not raw:
        return DEFAULT_WEIGHT_PAIRS
    pairs = []
    for chunk in raw.split(";"):
        a_str, b_str = chunk.split(",")
        w_a, w_b = float(a_str), float(b_str)
        if abs((w_a + w_b) - 1.0) > 1e-6:
            print(
                f"[参数扫描层] 警告：权重组合 ({w_a}, {w_b}) 之和不为 1，CCSDetector 仍会按原样计算",
                file=sys.stderr,
            )
        pairs.append((w_a, w_b))
    return tuple(pairs)


def _parse_hysteresis_windows(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return DEFAULT_HYSTERESIS_WINDOWS
    return tuple(int(x) for x in raw.split(","))


def _resolve_data_path(data_arg: str) -> Path:
    candidate = Path(data_arg)
    return candidate if candidate.is_absolute() or candidate.exists() else RAW_DATA_DIR / data_arg


# ----------------------------------------------------------------------
# 单组参数执行
# ----------------------------------------------------------------------


def _run_one_combo(
    data: pd.DataFrame, data_arg: str, w_a: float, w_b: float, hysteresis_window: int
) -> TuningResult:
    detector = CCSDetector(weight_delta2_rs=w_a, weight_volume_delta=w_b)
    engine = StateMachineEngine(hysteresis_window=hysteresis_window)
    config = BacktestConfig(
        strategy_name="param_tuning_sweep",
        strategy_version=f"wa{w_a}_wb{w_b}_h{hysteresis_window}",
        data_source=data_arg,
        detector=detector,
        engine=engine,
    )

    run = BacktestRunner(data=data, config=config).run()
    reporter = BacktestReporter(run_id=run.run_id)

    lead_time_df = reporter.lead_time_audit()
    overall_median = float(lead_time_df["lead_time_bars"].median()) if not lead_time_df.empty else None

    seed_series = lead_time_df.loc[lead_time_df.get("stage") == LifecycleStage.SEED.value, "lead_time_bars"] \
        if not lead_time_df.empty else pd.Series(dtype=float)
    discovery_series = lead_time_df.loc[lead_time_df.get("stage") == LifecycleStage.DISCOVERY.value, "lead_time_bars"] \
        if not lead_time_df.empty else pd.Series(dtype=float)

    discovery_trigger_count = int((reporter.logs_df["to_stage"] == LifecycleStage.DISCOVERY.value).sum())

    return TuningResult(
        weight_delta2_rs=w_a,
        weight_volume_delta=w_b,
        hysteresis_window=hysteresis_window,
        run_id=run.run_id,
        status=run.status,
        overall_median_lead_time=overall_median,
        seed_median_lead_time=float(seed_series.median()) if not seed_series.empty else None,
        discovery_median_lead_time=float(discovery_series.median()) if not discovery_series.empty else None,
        discovery_trigger_count=discovery_trigger_count,
        sample_count=len(lead_time_df),
    )


# ----------------------------------------------------------------------
# 天梯榜打印
# ----------------------------------------------------------------------


def _print_leaderboard(results: list[TuningResult]) -> None:
    df = pd.DataFrame([asdict(r) for r in results])
    ranked = df.sort_values(
        "overall_median_lead_time", ascending=False, na_position="last"
    ).reset_index(drop=True)
    ranked.insert(0, "rank", ranked.index + 1)

    print("\n===== 最优参数审计天梯榜（按整体 Lead Time 中位数降序） =====")
    display_cols = [
        "rank",
        "weight_delta2_rs",
        "weight_volume_delta",
        "hysteresis_window",
        "overall_median_lead_time",
        "seed_median_lead_time",
        "discovery_median_lead_time",
        "discovery_trigger_count",
        "sample_count",
        "status",
        "run_id",
    ]
    print(ranked[display_cols].to_string(index=False))

    best = ranked.iloc[0]
    if pd.notna(best["overall_median_lead_time"]):
        print(
            "\n结论：最优参数组合为 "
            f"weight_delta2_rs={best['weight_delta2_rs']}, "
            f"weight_volume_delta={best['weight_volume_delta']}, "
            f"hysteresis_window={int(best['hysteresis_window'])} —— "
            f"整体 Lead Time 中位数 = {best['overall_median_lead_time']:.1f} 个时间步，"
            f"DISCOVERY 触发 {int(best['discovery_trigger_count'])} 次"
            f"（run_id={best['run_id']}，可用 BacktestReporter(run_id=...).print_report() 复查细节）。"
        )
    else:
        print("\n结论：所有参数组合均未产生可用的 Lead Time 样本，建议检查数据质量或放宽状态机阈值。")


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------


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

    weight_pairs = _parse_weight_pairs(args.weight_pairs)
    hysteresis_windows = _parse_hysteresis_windows(args.hysteresis_windows)
    combos = list(product(weight_pairs, hysteresis_windows))
    print(f"[参数扫描层] 共 {len(combos)} 组参数组合，开始逐组回测……")

    results: list[TuningResult] = []
    for i, ((w_a, w_b), hysteresis_window) in enumerate(combos, start=1):
        print(
            f"[参数扫描层] ({i}/{len(combos)}) "
            f"weight_delta2_rs={w_a}, weight_volume_delta={w_b}, hysteresis_window={hysteresis_window} ..."
        )
        try:
            result = _run_one_combo(data, args.data, w_a, w_b, hysteresis_window)
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
