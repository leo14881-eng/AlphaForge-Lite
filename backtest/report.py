"""
回测复盘审计报表工具

BacktestReporter 接收某一次回测运行的 run_id，从 SQLite 黑匣子记录
（StateTransitionLog）重建完整的状态迁移历史，输出三块复盘看板：

    1. 生命周期分布：各阶段平均停留时长、状态迁移路径分布；
    2. 非共识归因：进入 DISCOVERY 阶段时，组件 A（delta2_rs）与
       组件 B（volume_delta）各自的加权贡献占比；
    3. 核心审判 · Lead Time 审计：资产进入 SEED / DISCOVERY 的时间点，
       平均 / 中位数早于该资产随后生命周期内价格最高点多少个时间步。
       这是用数据直接回答"我们是否真正做到了早于市场发现领导者"的
       审计指标。

Lead Time 审计需要访问回测所用的原始价格序列，本工具通过
BacktestRun.param_snapshot 中记录的 data_source，用 DataLoader
重新加载同一份数据，不依赖 BacktestRunner 进程内的任何状态。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.data_loader import DataLoader
from database.models import BacktestRun, StateTransitionLog
from database.session import init_db
from state_machine.constants import STAGE_ORDER, LifecycleStage


class BacktestReporter:
    """针对单次回测运行（run_id）的复盘报表生成器"""

    def __init__(self, run_id: str):
        init_db()
        self.run: BacktestRun = self._resolve_run(run_id)
        self.logs_df: pd.DataFrame = self._load_logs()

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------

    def _resolve_run(self, run_id: str) -> BacktestRun:
        run = BacktestRun.get_or_none(BacktestRun.run_id == str(run_id))
        if run is None:
            raise ValueError(f"未找到 run_id={run_id!r} 对应的回测运行记录")
        return run

    def _load_logs(self) -> pd.DataFrame:
        query = (
            StateTransitionLog.select()
            .where(StateTransitionLog.backtest_run == self.run)
            .order_by(StateTransitionLog.asset, StateTransitionLog.event_ts)
        )
        rows = []
        for log in query:
            row = {
                "asset": log.asset.symbol,
                "event_ts": log.event_ts,
                "from_stage": log.from_stage,
                "to_stage": log.to_stage,
                "cs_score": log.cs_score,
                "trigger_reason": log.trigger_reason,
            }
            row.update(log.get_component_breakdown())
            rows.append(row)
        df = pd.DataFrame(rows)
        if not df.empty:
            df["event_ts"] = pd.to_datetime(df["event_ts"])
        return df

    def _load_price_series(self) -> pd.DataFrame:
        data_source = self.run.get_param_snapshot().get("data_source")
        if not data_source:
            raise ValueError("该回测运行未记录 data_source，无法回溯价格序列做 Lead Time 审计")
        loader = DataLoader()
        path = Path(data_source)
        if path.is_absolute() or path.exists():
            return loader.load_path(path)
        return loader.load(data_source)

    # ------------------------------------------------------------------
    # 看板一：生命周期分布
    # ------------------------------------------------------------------

    def stage_duration_report(self) -> pd.DataFrame:
        """各阶段平均停留时长（同一资产序列内，用下一条记录的时间戳做差）"""
        if self.logs_df.empty:
            return pd.DataFrame(columns=["stage", "avg_duration", "sample_count"])
        df = self.logs_df.sort_values(["asset", "event_ts"]).copy()
        df["next_ts"] = df.groupby("asset")["event_ts"].shift(-1)
        df["duration"] = df["next_ts"] - df["event_ts"]
        durations = df.dropna(subset=["duration"])
        summary = (
            durations.groupby("to_stage")["duration"]
            .agg(avg_duration="mean", sample_count="count")
            .reindex([s.value for s in STAGE_ORDER])
            .rename_axis("stage")
            .reset_index()
        )
        return summary

    def transition_path_distribution(self) -> pd.DataFrame:
        """状态迁移路径分布（from_stage -> to_stage 的出现次数，按次数降序）"""
        if self.logs_df.empty:
            return pd.DataFrame(columns=["from_stage", "to_stage", "count"])
        df = self.logs_df.copy()
        df["from_stage"] = df["from_stage"].fillna("(初始观察)")
        return (
            df.groupby(["from_stage", "to_stage"])
            .size()
            .rename("count")
            .reset_index()
            .sort_values("count", ascending=False)
            .reset_index(drop=True)
        )

    # ------------------------------------------------------------------
    # 看板二：非共识归因
    # ------------------------------------------------------------------

    def non_consensus_attribution(self) -> pd.DataFrame:
        """
        进入 DISCOVERY 阶段时，组件 A（delta2_rs）与组件 B（volume_delta）
        的平均得分与加权贡献占比——验证晋级信号是否符合"非共识建仓"
        （价格尚未明显异动、资金已开始持续温和聚集）的特征。
        """
        entries = self.logs_df[self.logs_df["to_stage"] == LifecycleStage.DISCOVERY.value]
        if entries.empty or "delta2_rs" not in entries.columns:
            return pd.DataFrame()

        detector_params = self.run.get_param_snapshot().get("detector_params", {})
        weight_a = detector_params.get("weight_delta2_rs", 0.5)
        weight_b = detector_params.get("weight_volume_delta", 0.5)

        avg_a = entries["delta2_rs"].mean()
        avg_b = entries["volume_delta"].mean()
        weighted_a = avg_a * weight_a
        weighted_b = avg_b * weight_b
        total = weighted_a + weighted_b

        return pd.DataFrame(
            {
                "component": ["delta2_rs（组件 A）", "volume_delta（组件 B）"],
                "avg_score": [avg_a, avg_b],
                "weight": [weight_a, weight_b],
                "weighted_contribution": [weighted_a, weighted_b],
                "contribution_share": [
                    weighted_a / total if total else 0.0,
                    weighted_b / total if total else 0.0,
                ],
            }
        )

    # ------------------------------------------------------------------
    # 看板三（核心审判）：Lead Time 审计
    # ------------------------------------------------------------------

    def lead_time_audit(self) -> pd.DataFrame:
        """
        核心审计指标：资产进入 SEED / DISCOVERY 的时间点，相对该资产随后
        生命周期内价格最高点，提前了多少个时间步（bar）。

        每个"周期"的搜索范围为 [首次进入该阶段的时间点, 该资产下一次进入
        EXIT 的时间点]（若尚未 EXIT，则搜索到数据末尾）。lead_time_bars
        为正表示状态机确实早于价格高点发现该资产；为负则说明判定滞后于
        价格高点，是需要重点复盘的反例。
        """
        if self.logs_df.empty:
            return pd.DataFrame()
        price_df = self._load_price_series()
        frames = []
        for stage_value in (LifecycleStage.SEED.value, LifecycleStage.DISCOVERY.value):
            stage_df = self._lead_time_for_stage(stage_value, price_df)
            if not stage_df.empty:
                stage_df.insert(1, "stage", stage_value)
                frames.append(stage_df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def _lead_time_for_stage(self, stage_value: str, price_df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for symbol, group in self.logs_df.groupby("asset"):
            symbol_price = (
                price_df[price_df["symbol"] == symbol].sort_values("timestamp").reset_index(drop=True)
            )
            if symbol_price.empty:
                continue

            entries = group[group["to_stage"] == stage_value].sort_values("event_ts")
            if entries.empty:
                continue
            entry_ts = entries["event_ts"].iloc[0]

            exits_after = group[
                (group["to_stage"] == LifecycleStage.EXIT.value) & (group["event_ts"] > entry_ts)
            ]
            cycle_end_ts = (
                exits_after["event_ts"].iloc[0] if not exits_after.empty else symbol_price["timestamp"].iloc[-1]
            )

            window = symbol_price[
                (symbol_price["timestamp"] >= entry_ts) & (symbol_price["timestamp"] <= cycle_end_ts)
            ]
            entry_matches = symbol_price.index[symbol_price["timestamp"] <= entry_ts]
            if window.empty or len(entry_matches) == 0:
                continue

            peak_idx = window["close"].idxmax()
            entry_idx = entry_matches[-1]
            rows.append(
                {
                    "asset": symbol,
                    "entry_ts": entry_ts,
                    "peak_ts": symbol_price.loc[peak_idx, "timestamp"],
                    "lead_time_bars": int(peak_idx - entry_idx),
                }
            )
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # 汇总打印
    # ------------------------------------------------------------------

    def print_report(self) -> None:
        """一次性打印完整复盘报告，供命令行直接查看"""
        print("===== AlphaForge-Lite 回测复盘报告 =====")
        print(
            f"run_id={self.run.run_id}  strategy={self.run.strategy_name} "
            f"({self.run.strategy_version})  status={self.run.status}"
        )
        print(f"数据区间: {self.run.data_start_ts} ~ {self.run.data_end_ts}")

        print("\n【生命周期分布】各阶段平均停留时长")
        print(self.stage_duration_report().to_string(index=False))

        print("\n【生命周期分布】状态迁移路径分布")
        print(self.transition_path_distribution().to_string(index=False))

        print("\n【非共识归因】进入 DISCOVERY 阶段时组件 A/B 的加权贡献占比")
        attribution = self.non_consensus_attribution()
        if attribution.empty:
            print("（本次运行没有资产进入 DISCOVERY 阶段，无法归因）")
        else:
            print(attribution.to_string(index=False))

        print("\n【核心审判】Lead Time 审计——早于价格高点多少个时间步")
        lead_time_df = self.lead_time_audit()
        if lead_time_df.empty:
            print("（本次运行没有可用于 Lead Time 审计的样本）")
            return

        print(lead_time_df.to_string(index=False))
        summary = lead_time_df.groupby("stage")["lead_time_bars"].agg(["mean", "median", "count"])
        print("\n汇总统计：")
        print(summary.to_string())

        print("\n结论：")
        for stage_value in (LifecycleStage.SEED.value, LifecycleStage.DISCOVERY.value):
            if stage_value not in summary.index:
                print(f"  - {stage_value}: 样本不足，无法判定")
                continue
            median = summary.loc[stage_value, "median"]
            if median > 0:
                print(
                    f"  - {stage_value}: 中位数 Lead Time = {median:.1f} 个时间步（> 0），"
                    "验证通过——策略确实早于价格高点发现资产"
                )
            else:
                print(
                    f"  - {stage_value}: 中位数 Lead Time = {median:.1f} 个时间步（≤ 0），"
                    "验证未通过——判定滞后于价格高点，需要复盘"
                )
