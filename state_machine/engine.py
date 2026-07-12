"""
状态机执行引擎（看门狗）

驱动单个资产在 LifecycleStage 六阶段之间轮转，是项目【最高目标与
三铁律】——"早于市场识别资金聚集、优势期参与、优势消失即退出"——
在状态判定层面的落地实现。

设计要点：

    1. 合法性校验：所有迁移在写回前都必须通过 constants.is_transition_allowed
       校验，非法迁移会以 assert 形式立即暴露（这是内部不变量，按设计
       _decide_target_stage 永远只会提出相邻的合法目标状态，assert 只是
       兜底防线，不做静默纠正）。

    2. 迟滞控制（Hysteresis）：进入 SEED / DISCOVERY 要求最近连续
       hysteresis_window（默认 3）个时间步的 CS 得分都达到门槛，避免
       单点噪声导致状态瞬时切换。

    3. 波动率自适应：调用方可在 current_metrics 中提供 market_atr_ratio
       （市场整体波动率相对其历史均值的比例，1.0 = 正常波动）。波动率
       越高，晋升门槛按比例放大（更难触发，抑制噪声行情下的假信号）；
       波动率越低，门槛相应收窄。

    4. 前瞻逃顶：一旦 crowding_penalty 连续触发（拥挤度长期处于高位）
       或调用方标记了量价背离（price_volume_divergent），无论 CS 得分
       是否还处于高位，状态机都会抢先把 LEADERSHIP 打入 DISTRIBUTION、
       或把 DISTRIBUTION 打入 EXIT——目的是在价格真正大跌之前退出。

依赖方向说明：本模块不导入 database.*，与持久层的交互通过 db_session
参数（只需实现 StageLookup 协议）解耦，保持 state_machine 作为全项目
状态语义唯一真源、不反向依赖上层模块的分层约束。真正的 peewee 查询
实现应由 backtest 层提供的适配器完成。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from state_machine.constants import LifecycleStage, is_transition_allowed

# 为贴合需求文档中的方法签名习惯，提供 Stage 别名；语义上与
# LifecycleStage 完全等价。
Stage = LifecycleStage


class StageLookup(Protocol):
    """
    db_session 需要满足的最小协议：给定 asset_id，返回该资产最近一次
    持久化的 to_stage（字符串，取值为 LifecycleStage.value），若无历史
    记录则返回 None。EXIT 是否收敛为"无历史"由本引擎自行处理，
    实现方只需如实返回最后一条记录即可。
    """

    def get_last_stage(self, asset_id: str) -> str | None: ...


@dataclass
class StageTransition:
    """一次状态迁移的完整上下文，供调用方（backtest.runner）落库使用"""

    from_stage: Stage | None
    to_stage: Stage
    reason: str


@dataclass
class StateMachineEngine:
    """
    自适应状态机执行引擎。

    Attributes:
        seed_enter / discovery_enter / confirmation_enter / leadership_enter:
            四个正向晋升阶段的基准 CS 得分门槛（未经波动率缩放前的值）。
        distribution_drawdown: LEADERSHIP 阶段相对其峰值 CS 得分的回撤
            比例，超过该比例判定优势开始衰减，迁移至 DISTRIBUTION。
        exit_floor: DISTRIBUTION 阶段 CS 得分跌破该值时判定优势彻底消失，
            迁移至 EXIT。
        downgrade_ratio: SEED 阶段判定信号证伪的比例阈值
            （score < seed_enter * downgrade_ratio 时直接 EXIT）。
        hysteresis_window: 进入 SEED / DISCOVERY 所需的连续达标时间步数。
        vol_scale_min / vol_scale_max: 波动率自适应缩放系数的裁剪区间，
            防止极端波动率把门槛缩放到不合理的范围。
        crowding_alert_threshold: crowding_penalty 低于该值视为"本期拥挤"。
        crowding_alert_streak: 连续多少个时间步"拥挤"才触发前瞻逃顶。
    """

    seed_enter: float = 0.35
    discovery_enter: float = 0.5
    confirmation_enter: float = 0.65
    leadership_enter: float = 0.8
    distribution_drawdown: float = 0.15
    exit_floor: float = 0.3
    downgrade_ratio: float = 0.5
    hysteresis_window: int = 3
    vol_scale_min: float = 0.7
    vol_scale_max: float = 1.5
    crowding_alert_threshold: float = 0.5
    crowding_alert_streak: int = 2

    _score_history: dict[str, deque] = field(default_factory=dict, repr=False)
    _crowding_flags: dict[str, deque] = field(default_factory=dict, repr=False)
    _current_stage_cache: dict[str, Stage | None] = field(default_factory=dict, repr=False)
    _peak_score: dict[str, float] = field(default_factory=dict, repr=False)
    last_transition: StageTransition | None = field(default=None, repr=False)
    _last_reason: str = field(default="", repr=False)

    def to_params(self) -> dict:
        """导出构造参数，供 BacktestRun.param_snapshot 落库以保证可复现"""
        return {
            "seed_enter": self.seed_enter,
            "discovery_enter": self.discovery_enter,
            "confirmation_enter": self.confirmation_enter,
            "leadership_enter": self.leadership_enter,
            "distribution_drawdown": self.distribution_drawdown,
            "exit_floor": self.exit_floor,
            "downgrade_ratio": self.downgrade_ratio,
            "hysteresis_window": self.hysteresis_window,
            "vol_scale_min": self.vol_scale_min,
            "vol_scale_max": self.vol_scale_max,
            "crowding_alert_threshold": self.crowding_alert_threshold,
            "crowding_alert_streak": self.crowding_alert_streak,
        }

    def update_asset_state(self, asset_id: str, current_metrics: dict, db_session: StageLookup | None) -> Stage | None:
        """
        驱动单个资产的状态轮转，返回本次判定后资产所处的最新阶段
        （若尚未纳入观察，返回 None）。

        current_metrics 期望字段（均来自 detectors.cs_score.CCSDetector
        的单行输出，外加调用方补充的市场层面指标）：
            - cs_score: 当期 CCS 总分
            - crowding_penalty: 拥挤度惩罚系数，(0, 1]
            - market_atr_ratio: 市场整体波动率相对比例，默认 1.0（不缩放）
            - price_volume_divergent: 量价背离标记，默认 False

        本次调用如果触发了迁移，完整上下文会写入 self.last_transition
        （包含 from_stage / to_stage / reason），供调用方读取后落库；
        未触发迁移时 self.last_transition 会被清空为 None。
        """
        current_stage = self._resolve_current_stage(asset_id, db_session)
        self._push_score_history(asset_id, float(current_metrics.get("cs_score", 0.0)))
        self._push_crowding_flag(
            asset_id, float(current_metrics.get("crowding_penalty", 1.0)) < self.crowding_alert_threshold
        )

        self._last_reason = ""
        target_stage = self._decide_target_stage(asset_id, current_stage, current_metrics)

        if target_stage is None or target_stage == current_stage:
            self.last_transition = None
            return current_stage

        if current_stage is not None:
            assert is_transition_allowed(current_stage, target_stage), (
                f"非法状态迁移: {current_stage} -> {target_stage}，"
                "_decide_target_stage 的实现与 ALLOWED_TRANSITIONS 不一致"
            )

        self.last_transition = StageTransition(
            from_stage=current_stage, to_stage=target_stage, reason=self._last_reason
        )

        if target_stage == Stage.EXIT:
            # EXIT 为终态，重置本地缓存与滚动状态，为下一轮观察周期让路，
            # 由上层（backtest）在需要时创建新的观察周期，与 constants.py
            # 中"EXIT 需由上层逻辑创建新的观察周期"的约定保持一致。
            self._current_stage_cache[asset_id] = None
            self._peak_score.pop(asset_id, None)
            self._score_history.pop(asset_id, None)
            self._crowding_flags.pop(asset_id, None)
        else:
            self._current_stage_cache[asset_id] = target_stage
            if target_stage == Stage.LEADERSHIP:
                # 刚进入 LEADERSHIP 的这一步不会走 _decide_target_stage 里
                # "已在 LEADERSHIP" 的峰值维护分支，必须在此显式记录入场分
                # 作为峰值基准，否则下一步的回撤判断会失去正确参照。
                self._peak_score[asset_id] = float(current_metrics.get("cs_score", 0.0))

        return target_stage

    # ------------------------------------------------------------------
    # 状态判定逻辑
    # ------------------------------------------------------------------

    def _decide_target_stage(self, asset_id: str, current: Stage | None, metrics: dict) -> Stage | None:
        cs_score = float(metrics.get("cs_score", 0.0))
        atr_ratio = float(metrics.get("market_atr_ratio", 1.0))
        divergent = bool(metrics.get("price_volume_divergent", False))

        # ---- 前瞻逃顶：最高优先级，不受得分门槛约束 ----
        crowding_persistent = self._crowding_streak(asset_id) >= self.crowding_alert_streak
        if current in (Stage.LEADERSHIP, Stage.DISTRIBUTION) and (crowding_persistent or divergent):
            if current == Stage.LEADERSHIP:
                self._last_reason = (
                    f"拥挤度惩罚连续 {self.crowding_alert_streak} 期低于阈值 {self.crowding_alert_threshold}"
                    if crowding_persistent
                    else "检测到量价背离信号"
                ) + "，触发前瞻逃顶机制，提前判定优势衰减"
                return Stage.DISTRIBUTION
            self._last_reason = "拥挤/背离信号在 DISTRIBUTION 阶段持续存在，提前判定优势彻底消失"
            return Stage.EXIT

        scale = min(max(atr_ratio, self.vol_scale_min), self.vol_scale_max)
        seed_th = self.seed_enter * scale
        discovery_th = self.discovery_enter * scale
        confirmation_th = self.confirmation_enter * scale
        leadership_th = self.leadership_enter * scale

        if current is None:
            if self._confirmed_above(asset_id, seed_th):
                self._last_reason = f"CS 得分连续 {self.hysteresis_window} 期高于自适应种子期阈值 {seed_th:.3f}"
                return Stage.SEED
            return None

        if current == Stage.SEED:
            if self._confirmed_above(asset_id, discovery_th):
                self._last_reason = f"CS 得分连续 {self.hysteresis_window} 期高于自适应发现期阈值 {discovery_th:.3f}"
                return Stage.DISCOVERY
            if cs_score < seed_th * self.downgrade_ratio:
                self._last_reason = "CS 得分回落至种子期阈值以下，判定信号证伪"
                return Stage.EXIT
            return None

        if current == Stage.DISCOVERY:
            if cs_score >= confirmation_th:
                self._last_reason = f"CS 得分 {cs_score:.3f} 达到自适应确认期阈值 {confirmation_th:.3f}"
                return Stage.CONFIRMATION
            if cs_score < seed_th:
                self._last_reason = "CS 得分跌破种子期阈值，动能不足，降级观察"
                return Stage.SEED
            return None

        if current == Stage.CONFIRMATION:
            if cs_score >= leadership_th:
                self._last_reason = f"CS 得分 {cs_score:.3f} 达到自适应主导期阈值 {leadership_th:.3f}"
                return Stage.LEADERSHIP
            if cs_score < discovery_th:
                self._last_reason = "CS 得分跌破发现期阈值，确认失败，降级观察"
                return Stage.DISCOVERY
            return None

        if current == Stage.LEADERSHIP:
            peak = max(self._peak_score.get(asset_id, cs_score), cs_score)
            self._peak_score[asset_id] = peak
            if cs_score <= peak * (1 - self.distribution_drawdown):
                self._last_reason = (
                    f"CS 得分较峰值 {peak:.3f} 回撤超过 {self.distribution_drawdown:.0%}，优势开始衰减"
                )
                return Stage.DISTRIBUTION
            return None

        if current == Stage.DISTRIBUTION:
            if cs_score >= leadership_th:
                self._last_reason = "CS 得分回升至自适应主导期阈值以上，优势恢复"
                return Stage.LEADERSHIP
            if cs_score <= self.exit_floor:
                self._last_reason = f"CS 得分跌破退出下限 {self.exit_floor}，优势确认消失"
                return Stage.EXIT
            return None

        return None

    # ------------------------------------------------------------------
    # 状态与历史缓存
    # ------------------------------------------------------------------

    def _resolve_current_stage(self, asset_id: str, db_session: StageLookup | None) -> Stage | None:
        if asset_id in self._current_stage_cache:
            return self._current_stage_cache[asset_id]
        if db_session is None:
            self._current_stage_cache[asset_id] = None
            return None
        last_value = db_session.get_last_stage(asset_id)
        if last_value is None:
            resolved = None
        else:
            stage = Stage(last_value)
            # 回溯到 EXIT 视为"当前无活跃周期"，等价于 None，允许开启新一轮观察
            resolved = None if stage == Stage.EXIT else stage
        self._current_stage_cache[asset_id] = resolved
        return resolved

    def _push_score_history(self, asset_id: str, score: float) -> None:
        history = self._score_history.setdefault(asset_id, deque(maxlen=self.hysteresis_window))
        history.append(score)

    def _confirmed_above(self, asset_id: str, threshold: float) -> bool:
        history = self._score_history.get(asset_id)
        if history is None or len(history) < self.hysteresis_window:
            return False
        return all(score >= threshold for score in history)

    def _push_crowding_flag(self, asset_id: str, is_crowded: bool) -> None:
        flags = self._crowding_flags.setdefault(asset_id, deque(maxlen=self.crowding_alert_streak))
        flags.append(is_crowded)

    def _crowding_streak(self, asset_id: str) -> int:
        flags = self._crowding_flags.get(asset_id)
        if not flags or len(flags) < self.crowding_alert_streak:
            return 0
        return len(flags) if all(flags) else 0
