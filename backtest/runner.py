"""
回测主流程引擎

BacktestRunner 把 detectors.cs_score.CCSDetector 与
state_machine.engine.StateMachineEngine 串联成端到端的沙盒回测流程：

    1. 一次性向量化计算全表 CCS 得分（利用 Pandas 优势，避免逐行重复计算）；
    2. 按时间步顺序推进（逐资产、逐时间戳喂给状态机看门狗）；
    3. 触发状态迁移时，把完整黑匣子上下文写入 StateTransitionLog，
       并绑定本次运行的 run_id，保证结果可复现、可复盘。

PeeweeStageLookup 是 state_machine.engine.StageLookup 协议的 peewee
实现，是本模块承担的"胶水代码"——state_machine 本身不感知持久层，
只有 backtest 层知道如何用 peewee 查询资产的历史阶段。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.asset_profiles import build_asset_weight_overrides
from database.models import Asset, BacktestRun, StateTransitionLog
from database.session import get_db, init_db
from detectors.cs_score import CCSDetector
from state_machine.engine import StageLookup, StateMachineEngine


def _default_detector() -> CCSDetector:
    """
    生产默认路径（main.py / api/app.py 走的 BacktestConfig() 默认构造）
    自动挂载 CORE/MEME 资产分类权重（v0.95-Beta 参数加固第二项），
    拒绝用一组权重通吃全部资产。

    run_tuning.py / run_regression_check.py / run_meme_stress_test.py
    这三个参数寻优/压力测试工具会显式构造自己的 CCSDetector（不传
    asset_weight_overrides），不受这个默认行为影响——它们的研究目的
    就是测试"同一组权重在一批资产上的整体表现"。
    """
    return CCSDetector(asset_weight_overrides=build_asset_weight_overrides())


class PeeweeStageLookup:
    """StageLookup 协议的 peewee 实现：按 symbol 查询资产最近一次 to_stage"""

    def get_last_stage(self, asset_id: str) -> str | None:
        asset = Asset.get_or_none(Asset.symbol == asset_id)
        if asset is None:
            return None
        last_log = (
            StateTransitionLog.select()
            .where(StateTransitionLog.asset == asset)
            .order_by(StateTransitionLog.event_ts.desc())
            .first()
        )
        return last_log.to_stage if last_log else None


@dataclass
class BacktestConfig:
    """
    一次回测运行的配置。

    Attributes:
        strategy_name / strategy_version: 落库到 BacktestRun，用于区分
            不同策略与迭代版本。
        data_source: 数据来源标识（文件名或路径），落库进
            param_snapshot，供 backtest.report.BacktestReporter 复盘时
            重新加载同一份价格序列（用于 Lead Time 审计）。
        symbols: 限定参与回测的资产范围；None 表示使用数据中出现的
            全部 symbol。
        detector / engine: 可注入自定义参数的探测器与状态机实例。
            detector 默认通过 `_default_detector()` 自动挂载 CORE/MEME
            资产分类权重（见 config/asset_profiles.py）；如需统一权重
            扫描（参数寻优场景），显式传入不带 asset_weight_overrides
            的 CCSDetector 即可覆盖这个默认行为。engine 默认使用出厂
            参数。
        atr_window: 用于估算"市场整体波动率相对比例"（market_atr_ratio）
            的滚动窗口。
    """

    strategy_name: str = "non_consensus_accumulation"
    strategy_version: str = "v1"
    data_source: str = ""
    symbols: list[str] | None = None
    detector: CCSDetector = field(default_factory=_default_detector)
    engine: StateMachineEngine = field(default_factory=StateMachineEngine)
    atr_window: int = 14


class BacktestRunner:
    """
    回测主流程引擎。

    初始化时传入已由 DataLoader 加载校验好的大宽表 DataFrame 与配置，
    调用 run() 驱动一次完整的沙盒回测，返回本次运行对应的
    database.models.BacktestRun 记录。
    """

    def __init__(self, data: pd.DataFrame, config: BacktestConfig | None = None):
        init_db()
        self.config = config or BacktestConfig()
        self.data = (
            data[data["symbol"].isin(self.config.symbols)].reset_index(drop=True)
            if self.config.symbols
            else data
        )
        # 【全局扫描修复】config.symbols 过滤后数据为空时，早期显式拒绝，
        # 不要让空表一路传到 run() 里——self.data["timestamp"].min() 在空
        # 表上会返回 NaT，NaT.to_pydatetime() 本身不报错，但 NaT 会被传给
        # BacktestRun.create(data_start_ts=...) 落库，属于一个几乎无意义
        # 的错误配置本该在配置校验阶段就被拒绝，而不是让脏数据一路走到
        # 落库层才可能出问题。
        if self.data.empty:
            available = sorted(data["symbol"].unique()) if not data.empty else []
            raise ValueError(
                f"按 symbols={self.config.symbols} 过滤后数据集为空，无法执行回测。"
                f"数据集中实际存在的 symbol 有：{available}"
            )
        self.symbols: list[str] = sorted(self.data["symbol"].unique())

    def run(self) -> BacktestRun:
        run_row = BacktestRun.create(
            strategy_name=self.config.strategy_name,
            strategy_version=self.config.strategy_version,
            data_start_ts=self.data["timestamp"].min().to_pydatetime(),
            data_end_ts=self.data["timestamp"].max().to_pydatetime(),
            status="RUNNING",
            started_at=dt.datetime.utcnow(),
        )
        run_row.set_param_snapshot(
            {
                "data_source": self.config.data_source,
                "symbols": self.symbols,
                "detector_params": self.config.detector.to_params(),
                "engine_params": self.config.engine.to_params(),
                "atr_window": self.config.atr_window,
            }
        )
        run_row.save()

        try:
            written = self._execute(run_row)
            run_row.status = "SUCCESS"
            run_row.notes = f"共写入 {written} 条状态迁移日志"
        except Exception as exc:
            run_row.status = "FAILED"
            run_row.notes = str(exc)
            raise
        finally:
            run_row.finished_at = dt.datetime.utcnow()
            run_row.save()

        return run_row

    def _execute(self, run_row: BacktestRun) -> int:
        # ---- 第一步：向量化一次性算完全表 CCS 得分（组件 A/B/C + 总分） ----
        scored = self.config.detector.calculate_cs(self.data)
        scored["market_atr_ratio"] = self._compute_market_atr_ratio(scored)

        assets = {symbol: self._get_or_create_asset(symbol, scored) for symbol in self.symbols}
        stage_lookup: StageLookup = PeeweeStageLookup()
        component_cols = self.config.detector.component_names
        metric_cols = [c for c in ("close", "volume", "turnover_rate", "funding_rate") if c in scored.columns]

        ordered = scored.sort_values(["timestamp", "symbol"])
        written = 0

        # ---- 第二步：按时间步顺序推进，触发迁移即落库 ----
        # 整个循环包裹在单个 atomic() 事务内做批量提交，相比逐条 autocommit
        # 大幅减少磁盘同步次数；若循环中途异常，事务整体回滚，不会残留
        # 半成品日志，run_row 的失败状态在事务外单独落库以保留审计线索。
        with get_db():
            for row in ordered.itertuples(index=False):
                metrics = {
                    "cs_score": row.cs_score,
                    "crowding_penalty": row.crowding_penalty,
                    "market_atr_ratio": row.market_atr_ratio,
                    # close / delta2_rs 供 StateMachineEngine 内部计算
                    # 量价背离三层过滤（v0.9 新增），不再由调用方直接
                    # 传入 price_volume_divergent 布尔值。
                    "close": row.close,
                    "delta2_rs": row.delta2_rs,
                }
                self.config.engine.update_asset_state(row.symbol, metrics, stage_lookup)
                transition = self.config.engine.last_transition
                if transition is None:
                    continue

                log = StateTransitionLog.create(
                    asset=assets[row.symbol],
                    backtest_run=run_row,
                    event_ts=row.timestamp.to_pydatetime(),
                    from_stage=transition.from_stage.value if transition.from_stage else None,
                    to_stage=transition.to_stage.value,
                    cs_score=row.cs_score,
                    trigger_reason=transition.reason,
                )
                log.set_component_breakdown({name: getattr(row, name) for name in component_cols})
                metrics_snapshot = {col: getattr(row, col) for col in metric_cols}
                metrics_snapshot["market_atr_ratio"] = row.market_atr_ratio
                log.set_metrics_snapshot(metrics_snapshot)
                log.save()
                written += 1

        return written

    def _get_or_create_asset(self, symbol: str, scored: pd.DataFrame) -> Asset:
        first_seen = scored.loc[scored["symbol"] == symbol, "timestamp"].min().to_pydatetime()
        asset, _ = Asset.get_or_create(symbol=symbol, defaults={"first_seen_at": first_seen})
        return asset

    def _compute_market_atr_ratio(self, scored: pd.DataFrame) -> pd.Series:
        """
        市场整体波动率相对比例（供 StateMachineEngine 的波动率自适应门槛使用）。

        本地数据 schema 只有收盘价，没有最高/最低价，无法计算标准 ATR，
        因此用"日收益率绝对值的滚动均值"作为简化的单资产波动率代理，
        再对全市场等权取横截面均值得到"市场当期波动率"，最后除以其自身
        更长窗口（atr_window * 4）的滚动均值，得到相对比例
        （1.0 = 与近期常态持平）。
        """
        w = self.config.atr_window
        working = scored[["timestamp", "symbol", "close"]].copy()
        working["_daily_vol"] = working.groupby("symbol")["close"].transform(
            lambda s: s.pct_change().abs()
        )
        working["_rolling_vol"] = working.groupby("symbol")["_daily_vol"].transform(
            lambda s: s.rolling(w, min_periods=max(2, w // 2)).mean()
        )
        market_vol_by_ts = working.groupby("timestamp")["_rolling_vol"].mean()
        baseline_window = max(w * 4, w + 1)
        market_baseline = market_vol_by_ts.rolling(
            baseline_window, min_periods=max(2, baseline_window // 2)
        ).mean()
        ratio_by_ts = (market_vol_by_ts / market_baseline.replace(0, np.nan)).fillna(1.0)
        return working["timestamp"].map(ratio_by_ts).to_numpy()
