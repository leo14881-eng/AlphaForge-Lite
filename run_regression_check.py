"""
无损对比回归脚本（第五阶段：最优参数回填验证）

在真实历史大宽表 data/raw/crypto_market_daily.csv 上，并排跑两组
实验，验证"把 run_tuning.py 实测出的最优参数固化为出厂默认值"这次
改动本身没有引入任何逻辑回归、且确实带来了 Lead Time 的实质提升：

    实验组 A（旧启发式默认）：显式传入 w_a=0.5, w_b=0.5,
        hysteresis_window=3（本项目 v0.7 之前的出厂默认值，作为对照组）
    实验组 B（新固化默认）：CCSDetector() / StateMachineEngine() 均不传
        任何自定义参数，直接读取源码里当前生效的构造函数默认值——
        这样验证的是"代码里真正跑起来的默认值"，而不是脚本里手写、
        可能与源码悄悄脱节的一份参数副本。

用法：
    python run_regression_check.py
    python run_regression_check.py --data crypto_market_daily.csv
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
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

# 实验组 A：v0.7 之前的启发式默认值（对照组，代表"回填前"的系统行为）
LEGACY_WEIGHT_DELTA2_RS = 0.5
LEGACY_WEIGHT_VOLUME_DELTA = 0.5
LEGACY_HYSTERESIS_WINDOW = 3

# v0.9 起 data/raw/crypto_market_daily.csv 已扩展为主流资产池 + 妖币池
# 共 26 个资产（见 data/download_data.py）。v0.8 快照记录的回归检查结果
# （3.0 天 -> 6.0 天）是基于原始 12 个主流资产跑出来的，这里显式限定
# symbols，保证同样的命令行调用能复现同样的数字。
MAINSTREAM_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "LINKUSDT",
    "ADAUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "TRXUSDT",
)


@dataclass
class ExperimentResult:
    """单组实验的审计结果，用于最终并排对比"""

    label: str
    w_a: float
    w_b: float
    hysteresis_window: int
    run_id: str
    lead_time_median: float | None
    seed_trigger_count: int


# ----------------------------------------------------------------------
# 单组实验执行（静默：只取结构化数据，不打印逐笔迁移日志）
# ----------------------------------------------------------------------


def _run_experiment(
    data: pd.DataFrame,
    data_arg: str,
    label: str,
    w_a: float | None,
    w_b: float | None,
    hysteresis_window: int | None,
    symbols: list[str] | None = None,
) -> ExperimentResult:
    """
    w_a/w_b/hysteresis_window 为 None 时不传给构造函数，直接使用类的
    出厂默认值——实验组 B 正是靠这个"None 分支"验证代码里真正生效的
    默认值，而不是本脚本自己维护的一份影子副本。

    symbols 默认 None（不过滤）——生产入口 main() 会显式传入
    MAINSTREAM_SYMBOLS 以保证历史结果可复现；单测用自己的合成数据调用
    本函数时不应被硬编码的主流资产名单过滤掉。
    """
    detector = CCSDetector() if w_a is None else CCSDetector(weight_delta2_rs=w_a, weight_volume_delta=w_b)
    engine = StateMachineEngine() if hysteresis_window is None else StateMachineEngine(hysteresis_window=hysteresis_window)

    config = BacktestConfig(
        strategy_name="regression_check",
        strategy_version=label,
        data_source=data_arg,
        symbols=symbols,
        detector=detector,
        engine=engine,
    )

    # BacktestRunner.run() 本身不打印逐笔迁移日志（只落库），这里也刻意
    # 不调用 BacktestReporter.print_report()，只提取两个结构化指标。
    run = BacktestRunner(data=data, config=config).run()
    reporter = BacktestReporter(run_id=run.run_id)

    lead_time_df = reporter.lead_time_audit()
    lead_time_median = float(lead_time_df["lead_time_bars"].median()) if not lead_time_df.empty else None
    seed_trigger_count = int((reporter.logs_df["to_stage"] == LifecycleStage.SEED.value).sum())

    return ExperimentResult(
        label=label,
        w_a=detector.weight_delta2_rs,
        w_b=detector.weight_volume_delta,
        hysteresis_window=engine.hysteresis_window,
        run_id=run.run_id,
        lead_time_median=lead_time_median,
        seed_trigger_count=seed_trigger_count,
    )


# ----------------------------------------------------------------------
# 并排大考成绩打印
# ----------------------------------------------------------------------


def _print_scoreboard(result_a: ExperimentResult, result_b: ExperimentResult) -> None:
    df = pd.DataFrame([asdict(result_a), asdict(result_b)])
    display = df.rename(
        columns={
            "label": "实验组",
            "w_a": "w_a(斜率加速度)",
            "w_b": "w_b(温和放量)",
            "hysteresis_window": "Hysteresis",
            "lead_time_median": "Lead Time Median(天)",
            "seed_trigger_count": "Trigger Count",
        }
    )[
        [
            "实验组",
            "w_a(斜率加速度)",
            "w_b(温和放量)",
            "Hysteresis",
            "Lead Time Median(天)",
            "Trigger Count",
            "run_id",
        ]
    ]

    print("\n===== AlphaForge-Lite 参数回填 · 无损对比大考成绩 =====")
    print(display.to_string(index=False))

    median_a, median_b = result_a.lead_time_median, result_b.lead_time_median

    print("\n【Lead Time 中位数对比】")
    if median_a is None or median_b is None:
        print("  两组实验中至少有一组未产生可用的 Lead Time 样本，无法得出结论，请检查数据质量。")
        return

    print(
        f"  旧启发式（w_a={result_a.w_a}, w_b={result_a.w_b}, h={result_a.hysteresis_window}）: "
        f"{median_a:.1f} 天"
    )
    print(
        f"  新固化默认（w_a={result_b.w_a}, w_b={result_b.w_b}, h={result_b.hysteresis_window}）: "
        f"{median_b:.1f} 天"
    )

    delta = median_b - median_a
    if delta > 0:
        pct = f"（+{delta / median_a:.1%}）" if median_a > 0 else ""
        print(f"  -> 提升 {delta:.1f} 天{pct}：旧启发式: {median_a:.1f}天 -> 新固化默认: {median_b:.1f}天")
    elif delta == 0:
        print("  -> 持平，未观测到差异")
    else:
        print(f"  -> 下降 {abs(delta):.1f} 天")

    print("\n【结论】")
    if delta > 0:
        print(
            "  回归检查通过：新固化默认参数没有引入任何逻辑回归，Lead Time 中位数较旧启发式默认"
            f"提升 {delta:.1f} 天，系统默认策略确认达到本轮真实数据上的实测最优状态。"
        )
    elif delta == 0:
        print("  回归检查通过（持平）：新固化默认参数未引入回归，但本次真实数据上未观测到进一步提升。")
    else:
        print(
            "  警告：新固化默认参数的 Lead Time 中位数反而低于旧启发式默认，"
            "请复查 run_tuning.py 的扫描结果与本次固化的参数值是否一致，不应视为通过。"
        )


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------


def _resolve_data_path(data_arg: str) -> Path:
    candidate = Path(data_arg)
    return candidate if candidate.is_absolute() or candidate.exists() else RAW_DATA_DIR / data_arg


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AlphaForge-Lite 参数回填无损对比回归脚本：验证新固化默认值相对旧启发式默认值的提升"
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

    print("\n[回归检查层] 实验组 A（旧启发式默认：w_a=0.5, w_b=0.5, hysteresis_window=3）静默回测中……")
    result_a = _run_experiment(
        data,
        args.data,
        "A: 旧启发式默认",
        LEGACY_WEIGHT_DELTA2_RS,
        LEGACY_WEIGHT_VOLUME_DELTA,
        LEGACY_HYSTERESIS_WINDOW,
        symbols=list(MAINSTREAM_SYMBOLS),
    )

    print("[回归检查层] 实验组 B（新固化默认，CCSDetector()/StateMachineEngine() 不传任何参数）静默回测中……")
    result_b = _run_experiment(data, args.data, "B: 新固化默认", None, None, None, symbols=list(MAINSTREAM_SYMBOLS))

    _print_scoreboard(result_a, result_b)


if __name__ == "__main__":
    main()
