"""
参数网格扫描脚本（第四阶段：真实数据参数校准）

在真实历史大宽表 data/raw/crypto_market_daily.csv 上，对 CCSDetector
的组件权重（w_a=weight_delta2_rs 斜率加速度 / w_b=weight_volume_delta
温和放量，两两配对且总和为 1.0）与 StateMachineEngine 的迟滞窗口
hysteresis_window 做网格扫描。

每组参数静默跑一次完整的 BacktestRunner（回测循环本身不打印逐笔迁移
日志），只通过对应 run_id 实例化 BacktestReporter，从中提取两个核心
评估指标——Lead Time 中位数、SEED 阶段被触发的总频次——最终打印一张
按 Lead Time 中位数降序排列的"AlphaForge-Lite 参数审计天梯榜"，直接
回答"哪组参数最安全、最提前、最稳定地发现领导者"。

用法：
    python run_tuning.py
    python run_tuning.py --data crypto_market_daily.csv
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
from config.asset_profiles import MAINSTREAM_SYMBOLS
from config.settings import RAW_DATA_DIR
from database.session import init_db
from detectors.cs_score import CCSDetector
from state_machine.constants import LifecycleStage
from state_machine.engine import StateMachineEngine

# 权重组合固定配对给出，且每对总和为 1.0，覆盖"偏重组件A(斜率加速度)"
# 到"偏重组件B(温和放量)"的四档对照，不做笛卡尔积（避免出现两个权重
# 都很大/都很小这类无意义组合）。
WEIGHT_PAIRS: tuple[tuple[float, float], ...] = ((0.8, 0.2), (0.6, 0.4), (0.4, 0.6), (0.2, 0.8))
HYSTERESIS_WINDOWS: tuple[int, ...] = (2, 3, 4)

DEFAULT_DATA_FILE = "crypto_market_daily.csv"

# v0.9 起 data/raw/crypto_market_daily.csv 已扩展为主流资产池 + 妖币池
# 共 26 个资产（见 data/download_data.py）。本脚本的历史校准结果
# （v0.7/v0.8 快照记录的天梯榜）都是基于原始 12 个主流资产跑出来的，
# 这里显式限定 symbols，保证同样的命令行调用能复现同样的数字，不会
# 因为数据文件里多出的妖币资产而悄悄改变结果。
#
# 【全局扫描修复】MAINSTREAM_SYMBOLS 改为从 config.asset_profiles 导入，
# 不再本地重复硬编码一份——此前这份清单在四处（本文件/
# run_regression_check.py/data/download_data.py/
# config/asset_profiles.py::ASSET_PROFILE_MAP）各自独立维护，没有任何
# 机制保证一致，config.asset_profiles 现在是唯一权威来源。


@dataclass
class TuningResult:
    """单个参数组合的审计结果，用于最终天梯榜排序"""

    w_a: float
    w_b: float
    hysteresis_window: int
    run_id: str
    lead_time_median: float | None
    seed_trigger_count: int


# ----------------------------------------------------------------------
# 单组参数执行（静默：只取数据，不打印逐笔迁移日志）
# ----------------------------------------------------------------------


def _run_one_combo(
    data: pd.DataFrame,
    data_arg: str,
    w_a: float,
    w_b: float,
    hysteresis_window: int,
    symbols: list[str] | None = None,
) -> TuningResult:
    """
    symbols 默认 None（不过滤，使用 data 里出现的全部资产）——生产入口
    main() 会显式传入 MAINSTREAM_SYMBOLS 以保证历史结果可复现；单测用
    自己的合成数据、合成 symbol 名调用本函数时不应该被硬编码的主流资产
    名单过滤掉，因此这里不写死默认值。
    """
    detector = CCSDetector(weight_delta2_rs=w_a, weight_volume_delta=w_b)
    engine = StateMachineEngine(hysteresis_window=hysteresis_window)
    config = BacktestConfig(
        strategy_name="param_tuning_sweep",
        strategy_version=f"wa{w_a}_wb{w_b}_h{hysteresis_window}",
        data_source=data_arg,
        symbols=symbols,
        detector=detector,
        engine=engine,
    )

    # BacktestRunner.run() 本身就不打印逐笔迁移日志（每次状态迁移只落库，
    # 不 print），这里也刻意不调用 BacktestReporter.print_report()，
    # 只取三份结构化 DataFrame/计数，保证调参循环终端输出保持精简。
    run = BacktestRunner(data=data, config=config).run()
    reporter = BacktestReporter(run_id=run.run_id)

    lead_time_df = reporter.lead_time_audit()
    lead_time_median = float(lead_time_df["lead_time_bars"].median()) if not lead_time_df.empty else None
    seed_trigger_count = int((reporter.logs_df["to_stage"] == LifecycleStage.SEED.value).sum())

    return TuningResult(
        w_a=w_a,
        w_b=w_b,
        hysteresis_window=hysteresis_window,
        run_id=run.run_id,
        lead_time_median=lead_time_median,
        seed_trigger_count=seed_trigger_count,
    )


# ----------------------------------------------------------------------
# 天梯榜打印
# ----------------------------------------------------------------------


def _print_leaderboard(results: list[TuningResult]) -> None:
    df = pd.DataFrame([asdict(r) for r in results])
    ranked = df.sort_values("lead_time_median", ascending=False, na_position="last").reset_index(drop=True)
    ranked.insert(0, "rank", ranked.index + 1)

    display = ranked.rename(
        columns={
            "w_a": "w_a(斜率加速度)",
            "w_b": "w_b(温和放量)",
            "hysteresis_window": "Hysteresis",
            "lead_time_median": "Lead Time Median(中位数天数)",
            "seed_trigger_count": "Trigger Count",
        }
    )[
        [
            "rank",
            "w_a(斜率加速度)",
            "w_b(温和放量)",
            "Hysteresis",
            "Lead Time Median(中位数天数)",
            "Trigger Count",
            "run_id",
        ]
    ]

    print("\n===== AlphaForge-Lite 参数审计天梯榜（按 Lead Time Median 降序） =====")
    print(display.to_string(index=False))

    best = ranked.iloc[0]
    if pd.notna(best["lead_time_median"]):
        print(
            "\n结论：最符合"
            "\"早于市场发现领导者且保持信号稳定\""
            "的参数组合是 "
            f"w_a={best['w_a']}, w_b={best['w_b']}, hysteresis_window={int(best['hysteresis_window'])} —— "
            f"Lead Time 中位数 = {best['lead_time_median']:.1f} 个时间步，"
            f"SEED 阶段共触发 {int(best['seed_trigger_count'])} 次"
            f"（触发次数越高说明信号越稳定、不是孤例，run_id={best['run_id']}）。"
        )
    else:
        print("\n结论：所有参数组合均未产生可用的 Lead Time 样本，建议检查数据质量或放宽状态机阈值。")


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------


def _resolve_data_path(data_arg: str) -> Path:
    candidate = Path(data_arg)
    return candidate if candidate.is_absolute() or candidate.exists() else RAW_DATA_DIR / data_arg


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AlphaForge-Lite 参数网格扫描：在真实数据上寻找 Lead Time 中位数最大化的最优参数组合"
    )
    parser.add_argument(
        "--data",
        default=DEFAULT_DATA_FILE,
        help=f"真实历史大宽表文件名（相对 data/raw/）或路径，默认 {DEFAULT_DATA_FILE}",
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

    combos = list(product(WEIGHT_PAIRS, HYSTERESIS_WINDOWS))
    print(
        f"[参数扫描层] 共 {len(combos)} 组参数组合"
        f"（{len(WEIGHT_PAIRS)} 组权重 × {len(HYSTERESIS_WINDOWS)} 组迟滞窗口），开始逐组静默回测……"
    )

    results: list[TuningResult] = []
    for i, ((w_a, w_b), hysteresis_window) in enumerate(combos, start=1):
        print(f"[参数扫描层] ({i}/{len(combos)}) w_a={w_a}, w_b={w_b}, hysteresis_window={hysteresis_window} ...")
        try:
            result = _run_one_combo(data, args.data, w_a, w_b, hysteresis_window, symbols=list(MAINSTREAM_SYMBOLS))
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
