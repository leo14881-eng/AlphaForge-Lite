"""
状态机常量定义模块

定义"非共识资本聚集探测器"对标的资产建模所依赖的生命周期阶段，
以及阶段之间允许的迁移路径。

本模块的阶段划分直接服务于项目最高目标：
    "市场总会有资金去打造新的领导者，我们要做的不是预测，而是尽早
    识别资金正在持续集中的资产，在领导优势形成阶段参与，在优势消失
    阶段退出，没有高质量机会时保持现金。"
即：SEED/DISCOVERY 对应"尽早识别"，CONFIRMATION 对应"验证是否值得
参与"，LEADERSHIP 对应"优势期参与"，DISTRIBUTION/EXIT 对应"优势
衰减时的退出与观望"。

所有上层模块（探测器、回测引擎、数据库落库逻辑）均应从本模块导入
阶段枚举，禁止在业务代码中使用裸字符串表示阶段，避免拼写错误导致
状态机失控。
"""
from __future__ import annotations

from enum import Enum


class LifecycleStage(str, Enum):
    """
    标的资产生命周期阶段枚举。

    继承 str 是为了让枚举值可以直接参与字符串比较 / 序列化到 SQLite，
    不需要额外的编解码逻辑。
    """

    # 种子期：资产处于极低关注度，尚未形成合力，仅出现零星的非共识资金介入迹象；
    # 对应"早于市场发现苗头"——此时不参与，仅纳入观察名单
    SEED = "SEED"

    # 发现期：聪明钱信号增多，成交量与持仓集中度开始出现温和但持续的抬升，
    # 资金集中趋势初步显现，仍需进一步验证
    DISCOVERY = "DISCOVERY"

    # 确认期：多维度指标（量价、持仓分布、资金流）共振，资金持续集中的趋势
    # 得到初步验证——这是决定"是否值得参与"的关键判定点
    CONFIRMATION = "CONFIRMATION"

    # 主导期：资产进入板块或市场焦点，资金呈现持续净流入，领导优势已经形成；
    # 对应目标中"在领导优势形成阶段参与"，是本策略的核心持仓阶段
    LEADERSHIP = "LEADERSHIP"

    # 派发期：早期资金开始分批兑现，聪明钱与散户行为出现明显分歧，
    # 领导优势开始衰减——这是预警信号，而非退出终点本身
    DISTRIBUTION = "DISTRIBUTION"

    # 退出期：核心信号强度衰减至阈值以下，领导优势确认消失，状态机判定
    # 本轮生命周期结束；对应目标中"在优势消失阶段退出，保持现金"
    EXIT = "EXIT"


# 阶段的标准顺序，用于判断"是否为正向推进"以及在报表中排序展示
STAGE_ORDER: tuple[LifecycleStage, ...] = (
    LifecycleStage.SEED,
    LifecycleStage.DISCOVERY,
    LifecycleStage.CONFIRMATION,
    LifecycleStage.LEADERSHIP,
    LifecycleStage.DISTRIBUTION,
    LifecycleStage.EXIT,
)

# 允许的阶段迁移表：key 为当前阶段，value 为可迁移到的下一阶段集合。
# 设计原则：
#   1. 支持正向顺序推进（如 SEED -> DISCOVERY）；
#   2. 支持信号衰减导致的"降级"或"提前退出"（如 CONFIRMATION -> SEED 重新观察，
#      或 LEADERSHIP -> EXIT 信号骤然消失）；
#   3. EXIT 为终态，不再向外迁移，需由上层逻辑创建新的观察周期。
ALLOWED_TRANSITIONS: dict[LifecycleStage, frozenset[LifecycleStage]] = {
    LifecycleStage.SEED: frozenset({LifecycleStage.DISCOVERY, LifecycleStage.EXIT}),
    LifecycleStage.DISCOVERY: frozenset(
        {LifecycleStage.CONFIRMATION, LifecycleStage.SEED, LifecycleStage.EXIT}
    ),
    LifecycleStage.CONFIRMATION: frozenset(
        {LifecycleStage.LEADERSHIP, LifecycleStage.DISCOVERY, LifecycleStage.EXIT}
    ),
    LifecycleStage.LEADERSHIP: frozenset(
        {LifecycleStage.DISTRIBUTION, LifecycleStage.EXIT}
    ),
    LifecycleStage.DISTRIBUTION: frozenset(
        {LifecycleStage.EXIT, LifecycleStage.LEADERSHIP}
    ),
    LifecycleStage.EXIT: frozenset(),
}


def is_transition_allowed(from_stage: LifecycleStage, to_stage: LifecycleStage) -> bool:
    """判断某次阶段迁移是否合法，供状态机执行引擎在落库前做前置校验"""
    return to_stage in ALLOWED_TRANSITIONS.get(from_stage, frozenset())
