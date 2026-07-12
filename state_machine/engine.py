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
       hysteresis_window（默认 2）个时间步的 CS 得分都达到门槛，避免
       单点噪声导致状态瞬时切换。

    3. 波动率自适应：调用方可在 current_metrics 中提供 market_atr_ratio
       （市场整体波动率相对其历史均值的比例，1.0 = 正常波动）。波动率
       越高，晋升门槛按比例放大（更难触发，抑制噪声行情下的假信号）；
       波动率越低，门槛相应收窄。

    4. 防洗盘量价背离逃顶（v0.9 新增，v0.95-Beta 加固多窗口投票）：本引擎
       内部根据 close / delta2_rs / crowding_penalty 三个原始信号，自行
       计算 price_volume_divergent（量价背离标记），必须依次通过三层
       过滤网才会判定为真：
           - 过滤网一（绝对价格与相对强度双审计 + 多窗口共振投票）：对
             divergence_windows（默认 2/3/4 三档）分别独立判定"最近 N 个
             时间步内 close 创出局部新高，但 delta2_rs 必须在同一窗口内
             从非负值持续、单调断崖式下穿零轴"，只有多数窗口同时判定为真
             才算通过——单一固定窗口容易过拟合到某一段历史数据的节奏，
             多窗口投票要求信号在不同时间尺度上都站得住脚。只有"价格创
             新高但相对强度大趋势真的崩了"才算数；相对强度没崩，一律
             判定为"庄家假砸盘洗盘"，不触发，系统保持静默持股。
           - 过滤网三（状态机平滑迟滞）：过滤网一的原始信号需连续
             divergence_confirm_streak（默认 2）个时间步为真，避免单点
             噪声（实现上先做这一步，紧跟在过滤网一之后）。
           - 过滤网二（拥挤度共振）：最终判定时还必须与 crowding_penalty
             的连续告警状态共振（复用既有的 `_crowding_streak` 机制）——
             如果换手率并未连续触发高危拥挤报警，说明是"高位缩量洗盘"，
             直接拒绝触发背离。**工程说明**：把"连续 N 期"的迟滞要求放在
             过滤网一而非"一二组合结果"上，是刻意的实现选择——crowding
             共振信号本身已经是多期累积的结果，如果再要求组合结果也连续
             达标，会与下面第 5 点的"纯拥挤度持续触发"机制互相抢跑（拥挤
             度刚满足连续告警时，第 5 点机制会抢先把资产降级到
             DISTRIBUTION，导致量价背离永远等不到自己的确认窗口走完）。
             调整后两套机制会在同一时间步同时满足条件，量价背离因为判定
             优先级更高而胜出，得到更果断的直接 EXIT。详见
             `_compute_price_volume_divergent` 的实现注释。
       一旦确认，且资产正处于 DISCOVERY 或 LEADERSHIP（持仓）阶段，
       该信号拥有全局最高优先级，允许打破常规迟滞，直接前瞻性迁移至
       EXIT——目的是在崩溃前夜以最快速度强制清仓，而不是被动等 CS 得分
       跌穿阈值（那时往往已经跌了一大截）。

    5. 纯拥挤度持续触发（v0.6 起既有机制，独立于第 4 点）：即便没有
       触发量价背离，crowding_penalty 连续触发同样会驱动 LEADERSHIP
       提前打入 DISTRIBUTION、DISTRIBUTION 打入 EXIT，作为量价背离
       之外的第二道防线。

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
            出厂默认值 2 是用 run_tuning.py 在真实历史数据上做网格扫描
            后的实测最优结果，详见 project_manifest.md v0.7/v0.8 快照。
        vol_scale_min / vol_scale_max: 波动率自适应缩放系数的裁剪区间，
            防止极端波动率把门槛缩放到不合理的范围。
        crowding_alert_threshold: crowding_penalty 低于该值视为"本期拥挤"。
        crowding_alert_streak: 连续多少个时间步"拥挤"才触发前瞻逃顶
            （同时也是量价背离过滤网二的共振判定窗口）。
        divergence_windows: 量价背离过滤网一的**多窗口共振投票**回看
            窗口集合（v0.95-Beta 参数加固第一项，消除单一窗口的过拟合
            风险）。每个窗口各自独立判定"最近 N 个时间步内创新高 /
            相对强度断崖下穿零轴"，只有多数窗口同时判定为真，过滤网一
            才算通过。出厂默认 `(2, 3, 4)`——Reviewer 要求至少包含
            window=2 与 window=4 两档；这里额外补了 window=3，使投票
            总数为奇数，保证"多数"语义明确（不会出现 1:1 平票无法判定
            的情况）。
        divergence_confirm_streak: 量价背离过滤网三——通过多窗口投票的
            过滤网一信号需要连续为真的时间步数，才会被采纳为最终信号。
    """

    seed_enter: float = 0.35
    discovery_enter: float = 0.5
    confirmation_enter: float = 0.65
    leadership_enter: float = 0.8
    distribution_drawdown: float = 0.15
    exit_floor: float = 0.3
    downgrade_ratio: float = 0.5
    hysteresis_window: int = 2
    vol_scale_min: float = 0.7
    vol_scale_max: float = 1.5
    crowding_alert_threshold: float = 0.5
    crowding_alert_streak: int = 2
    divergence_windows: tuple[int, ...] = (2, 3, 4)
    divergence_confirm_streak: int = 2

    _score_history: dict[str, deque] = field(default_factory=dict, repr=False)
    _crowding_flags: dict[str, deque] = field(default_factory=dict, repr=False)
    _close_history: dict[str, deque] = field(default_factory=dict, repr=False)
    _delta2_rs_history: dict[str, deque] = field(default_factory=dict, repr=False)
    _divergence_flags: dict[str, deque] = field(default_factory=dict, repr=False)
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
            "divergence_windows": list(self.divergence_windows),
            "divergence_confirm_streak": self.divergence_confirm_streak,
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
            - close: 当期收盘价，用于内部计算量价背离过滤网一
            - delta2_rs: 当期相对强度二阶加速度，同上
            - price_volume_divergent: 可选的显式覆盖值。生产链路
              （BacktestRunner）不应传入此字段，让引擎用 close/delta2_rs/
              crowding_penalty 自行计算真实的三层过滤结果；仅在单测中
              需要脱离真实序列直接注入信号时才使用。

        本次调用如果触发了迁移，完整上下文会写入 self.last_transition
        （包含 from_stage / to_stage / reason），供调用方读取后落库；
        未触发迁移时 self.last_transition 会被清空为 None。
        """
        current_stage = self._resolve_current_stage(asset_id, db_session)
        self._push_score_history(asset_id, float(current_metrics.get("cs_score", 0.0)))
        self._push_crowding_flag(
            asset_id, float(current_metrics.get("crowding_penalty", 1.0)) < self.crowding_alert_threshold
        )

        divergent = self._resolve_price_volume_divergent(asset_id, current_metrics)

        self._last_reason = ""
        target_stage = self._decide_target_stage(asset_id, current_stage, current_metrics, divergent)

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
            self._close_history.pop(asset_id, None)
            self._delta2_rs_history.pop(asset_id, None)
            self._divergence_flags.pop(asset_id, None)
        else:
            self._current_stage_cache[asset_id] = target_stage
            if target_stage == Stage.LEADERSHIP:
                # 刚进入 LEADERSHIP 的这一步不会走 _decide_target_stage 里
                # "已在 LEADERSHIP" 的峰值维护分支，必须在此显式记录入场分
                # 作为峰值基准，否则下一步的回撤判断会失去正确参照。
                self._peak_score[asset_id] = float(current_metrics.get("cs_score", 0.0))

        return target_stage

    # ------------------------------------------------------------------
    # 量价背离三层过滤网
    # ------------------------------------------------------------------

    def _resolve_price_volume_divergent(self, asset_id: str, metrics: dict) -> bool:
        """
        计算/解析本次调用的 price_volume_divergent。

        显式传入的 price_volume_divergent（非 None）会作为直接覆盖值使用，
        跳过内部三层过滤计算——这是专门为单测场景保留的旁路，生产链路
        （BacktestRunner）不会传入该字段，因此总是走真实的
        _compute_price_volume_divergent 三层过滤逻辑。
        """
        override = metrics.get("price_volume_divergent")
        if override is not None:
            return bool(override)

        close = metrics.get("close")
        delta2_rs = metrics.get("delta2_rs")
        if close is None or delta2_rs is None:
            return False
        return self._compute_price_volume_divergent(asset_id, float(close), float(delta2_rs))

    def _compute_price_volume_divergent(self, asset_id: str, close: float, delta2_rs: float) -> bool:
        """
        三层过滤网的实际计算顺序有两处刻意的工程调整，在此说明原因：

        （1）过滤网一采用**多窗口共振投票**（v0.95-Beta 参数加固第一项）：
        不再用单一的 divergence_window 判定"最近 N 期创新高 + 相对强度
        断崖下穿零轴"，而是对 divergence_windows（默认 2/3/4 三档）分别
        独立判定，取多数窗口的投票结果——只有当多数窗口都判定为真，
        过滤网一才算通过。这是为了消除"单一窗口大小"这个超参数本身带来
        的过拟合风险：如果只用一个固定窗口，窗口选大选小都可能只是
        恰好拟合了某一段历史数据的节奏，多窗口投票要求信号在不同时间
        尺度上都站得住脚，才会被采纳。

        （2）过滤网三（"连续 divergence_confirm_streak 期为真"）套用在
        **过滤网一投票结果本身**的持续性上，而不是套在"过滤网一 AND
        过滤网二"的组合结果上；过滤网二（拥挤度共振）在最终判定时直接
        读取拥挤度自身已有的 `_crowding_streak` 连续告警状态做同一时刻
        的门控。原因：过滤网二复用的 crowding_alert_streak 本身就是
        "纯拥挤度持续触发"（既有机制）判定 LEADERSHIP -> DISTRIBUTION
        的同一个信号。如果再要求"过滤网一 AND 过滤网二"的组合结果本身
        也必须连续 divergence_confirm_streak 期为真，会出现两套机制
        互相抢跑的问题：拥挤度刚满足连续告警的那一刻，"纯拥挤度持续
        触发"机制会抢先把资产打到 DISTRIBUTION，量价背离的组合信号
        永远等不到自己的确认窗口走完，变成事实上无法触发的死代码。
        调整后，过滤网一的多窗口投票信号独立积累自己的持续性，一旦
        拥挤度也同时进入告警状态，两套机制会在同一个时间步同时满足
        触发条件——由于量价背离在 `_decide_target_stage` 中的判定
        优先级更高，会先一步抢占，得到更果断的直接 EXIT，而不是被
        "纯拥挤度"机制抢先降级到 DISTRIBUTION。
        """
        max_window = max(self.divergence_windows)
        close_history = self._close_history.setdefault(asset_id, deque(maxlen=max_window))
        delta2_history = self._delta2_rs_history.setdefault(asset_id, deque(maxlen=max_window))
        close_history.append(close)
        delta2_history.append(delta2_rs)

        votes = [
            self._filter1_vote_for_window(close_history, delta2_history, window)
            for window in self.divergence_windows
        ]
        majority_needed = len(self.divergence_windows) // 2 + 1
        filter1_raw = sum(votes) >= majority_needed

        # 过滤网三 · 状态机平滑迟滞：过滤网一（多窗口投票结果）需连续
        # divergence_confirm_streak 个时间步为真，避免单点噪声。
        filter1_flags = self._divergence_flags.setdefault(
            asset_id, deque(maxlen=self.divergence_confirm_streak)
        )
        filter1_flags.append(filter1_raw)
        filter1_confirmed = len(filter1_flags) == self.divergence_confirm_streak and all(filter1_flags)

        # 过滤网二 · 拥挤度共振：如果换手率没有连续触发高危报警，说明是
        # "高位缩量洗盘"，直接拒绝触发背离。
        filter2_pass = self._crowding_streak(asset_id) >= self.crowding_alert_streak

        return filter1_confirmed and filter2_pass

    @staticmethod
    def _filter1_vote_for_window(close_history: deque, delta2_history: deque, window: int) -> bool:
        """
        过滤网一在单个窗口尺度上的独立投票：最近 window 个时间步内，
        close 创出局部新高，且 delta2_rs 必须从非负值持续单调断崖式
        下穿零轴——相对强度大趋势没崩，判定为庄家假砸盘洗盘，本窗口
        投反对票。数据不足 window 长度时直接投反对票（数据不够，不能
        判定为真）。
        """
        if len(close_history) < window or len(delta2_history) < window:
            return False
        close_slice = list(close_history)[-window:]
        delta2_slice = list(delta2_history)[-window:]
        price_new_high = close_slice[-1] >= max(close_slice)
        monotonic_breakdown = all(
            delta2_slice[i] > delta2_slice[i + 1] for i in range(len(delta2_slice) - 1)
        )
        crossed_zero_downward = delta2_slice[0] >= 0 and delta2_slice[-1] < 0
        return price_new_high and monotonic_breakdown and crossed_zero_downward

    # ------------------------------------------------------------------
    # 状态判定逻辑
    # ------------------------------------------------------------------

    def _decide_target_stage(
        self, asset_id: str, current: Stage | None, metrics: dict, divergent: bool
    ) -> Stage | None:
        cs_score = float(metrics.get("cs_score", 0.0))
        atr_ratio = float(metrics.get("market_atr_ratio", 1.0))

        # ---- 最高优先级：量价背离逃顶（三层过滤已在 divergent 中确认） ----
        # 只在持仓阶段（DISCOVERY / LEADERSHIP）生效，允许打破常规迟滞，
        # 直接前瞻性强制清仓至 EXIT，而不是像纯拥挤度信号那样先退到
        # DISTRIBUTION 观察——量价背离是三层过滤后才确认的更强、更紧急
        # 的信号，值得更果断的动作。
        if divergent and current in (Stage.DISCOVERY, Stage.LEADERSHIP):
            self._last_reason = (
                f"量价背离三层过滤全部确认（连续 {self.divergence_confirm_streak} 期）："
                "价格创新高但相对强度断崖下穿零轴，且与拥挤度连续告警共振，"
                "判定庄家真实出逃，前瞻性强制清仓退出"
            )
            return Stage.EXIT

        # ---- 次优先级：纯拥挤度持续触发（v0.6 起既有机制） ----
        crowding_persistent = self._crowding_streak(asset_id) >= self.crowding_alert_streak
        if current in (Stage.LEADERSHIP, Stage.DISTRIBUTION) and crowding_persistent:
            if current == Stage.LEADERSHIP:
                self._last_reason = (
                    f"拥挤度惩罚连续 {self.crowding_alert_streak} 期低于阈值 "
                    f"{self.crowding_alert_threshold}，触发前瞻逃顶机制，提前判定优势衰减"
                )
                return Stage.DISTRIBUTION
            self._last_reason = "拥挤度持续处于高危状态，DISTRIBUTION 阶段提前判定优势彻底消失"
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
