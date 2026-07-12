"""
数据库表结构定义模块

基于轻量级 ORM peewee（底层即标准库 sqlite3）描述本地 SQLite 库中的
三张核心表：

    1. Asset             —— 标的资产主表
    2. BacktestRun        —— 回测运行元数据表
    3. StateTransitionLog —— 状态机执行日志表（黑匣子）

设计与项目最高目标的对应关系：
    项目要做的是"尽早识别资金正在持续集中的资产，在领导优势形成阶段
    参与，在优势消失阶段退出"。因此 StateTransitionLog 不仅记录阶段
    迁移本身，还必须像黑匣子一样完整保留：
        - cs_score：当期资金集中度综合评分（Concentration Score），
          用于判断"资金是否正在持续集中"；
        - component_breakdown：CS 得分的组件拆解（量价、持仓集中度、
          资金流等子指标各自的贡献），保证每一次状态判定都可追溯、
          可复盘，而不是一个不可解释的黑箱数字。

选型说明：peewee 相比 SQLAlchemy 更轻量，Model 定义即 schema 定义，
适合本项目"本地沙盒验证"的定位；后续如需切换到更重的 ORM，
只需替换本文件与 database/session.py，不影响上层调用方。
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

import peewee as pw

from state_machine.constants import LifecycleStage

# 供 database.session 使用的共享数据库句柄，实际连接的文件路径在
# database/session.py 中通过 db.init(...) 延迟绑定，避免模块导入时
# 就固定死连接目标，便于测试环境替换为内存库。
db = pw.SqliteDatabase(None)

# 阶段枚举转换为 peewee CharField 的 choices 参数，既能约束取值范围，
# 又保留纯文本存储，便于直接用 SQL 工具查看，不依赖 ORM 反序列化。
_STAGE_CHOICES = [(stage.value, stage.value) for stage in LifecycleStage]


class BaseModel(pw.Model):
    """所有 ORM 模型的公共基类，统一绑定数据库句柄"""

    class Meta:
        database = db


class Asset(BaseModel):
    """
    标的资产主表。

    每一个被探测器纳入观察范围的加密资产（交易对）对应一行记录，
    是 StateTransitionLog 的外键指向目标。
    """

    # 交易对代码，如 BTCUSDT / ETHUSDT，作为业务唯一标识
    symbol = pw.CharField(max_length=32, unique=True, index=True)

    # 便于人工阅读的展示名称，如 "Bitcoin / USDT"，允许为空
    display_name = pw.CharField(max_length=128, null=True)

    # 该资产首次被纳入观察 / 出现在历史大宽表中的时间戳（业务时间）
    first_seen_at = pw.DateTimeField()

    # 是否仍在当前观察名单内；被剔除的资产不删除记录，仅置为 False 以保留历史
    is_active = pw.BooleanField(default=True)

    # 记录创建时间（写入库的时间，非业务时间）
    created_at = pw.DateTimeField(default=dt.datetime.utcnow)

    class Meta:
        table_name = "assets"

    def __str__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<Asset {self.symbol} active={self.is_active}>"


class BacktestRun(BaseModel):
    """
    回测运行元数据表。

    记录每一次沙盒回测的策略版本、参数快照与数据区间，
    用于保证"资金是否正在持续集中"的判定结果可复现、可对比。
    """

    # 全局唯一的运行编号（UUID），对外暴露给日志/报表引用
    run_id = pw.CharField(max_length=32, unique=True, default=lambda: uuid.uuid4().hex)

    # 策略名称，如 "non_consensus_accumulation"
    strategy_name = pw.CharField(max_length=64)

    # 策略版本号，便于同一策略多个迭代版本的结果对比
    strategy_version = pw.CharField(max_length=32)

    # 本次运行使用的参数快照（CS 权重、阈值等），序列化为 JSON 字符串保存
    param_snapshot = pw.TextField(null=True)

    # 本次回测所使用历史数据的起止时间戳（对应大宽表的时间范围）
    data_start_ts = pw.DateTimeField()
    data_end_ts = pw.DateTimeField()

    # 运行状态：PENDING / RUNNING / SUCCESS / FAILED
    status = pw.CharField(max_length=16, default="PENDING")

    # 实际开始 / 结束执行时间，用于统计耗时与失败排查
    started_at = pw.DateTimeField(null=True)
    finished_at = pw.DateTimeField(null=True)

    # 备注信息，如失败原因、人工复盘结论等
    notes = pw.TextField(null=True)

    # 记录创建时间
    created_at = pw.DateTimeField(default=dt.datetime.utcnow)

    class Meta:
        table_name = "backtest_runs"

    def set_param_snapshot(self, params: dict) -> None:
        """将参数字典序列化后写入 param_snapshot，供落库前调用"""
        self.param_snapshot = json.dumps(params, ensure_ascii=False)

    def get_param_snapshot(self) -> dict:
        """反序列化 param_snapshot，供复盘 / 报表读取"""
        return json.loads(self.param_snapshot) if self.param_snapshot else {}

    def __str__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<BacktestRun {self.run_id} strategy={self.strategy_name} status={self.status}>"


class StateTransitionLog(BaseModel):
    """
    状态机执行日志表 —— 黑匣子。

    每一行代表某个资产在某个历史时间戳上的一次状态判定：
    从哪个阶段迁移到哪个阶段、当期 CS 得分是多少、该得分由哪些
    子指标组件构成。这张表是"资金是否正在持续集中"这一核心问题
    的唯一真相来源，探测器的每一次判定都必须完整落库，不允许
    只保留结论而丢弃过程数据。
    """

    # 关联的标的资产
    asset = pw.ForeignKeyField(Asset, backref="transitions", on_delete="CASCADE")

    # 关联的回测运行；允许为空以支持独立于回测流程的人工标注 / 实时探测场景
    backtest_run = pw.ForeignKeyField(
        BacktestRun, backref="transitions", null=True, on_delete="SET NULL"
    )

    # 本次判定对应的历史业务时间戳（即大宽表中的时间列），而非写入时间
    event_ts = pw.DateTimeField(index=True)

    # 迁移前阶段；资产在观察窗口内的首条记录允许为空
    from_stage = pw.CharField(max_length=16, choices=_STAGE_CHOICES, null=True)

    # 迁移后阶段（必填）
    to_stage = pw.CharField(max_length=16, choices=_STAGE_CHOICES)

    # CS 得分（Concentration Score）：当期"资金集中度"综合评分，
    # 数值越高代表资金持续集中、形成领导优势的可能性越大，
    # 具体计算公式由 detectors 模块实现，本表只负责如实记录结果。
    cs_score = pw.FloatField(null=True)

    # CS 得分的组件拆解，JSON 结构如：
    #   {"volume_anomaly": 0.31, "holder_concentration": 0.42, "capital_inflow": 0.27}
    # 保证每一次状态判定都可以拆解还原到具体子指标，避免黑箱不可解释。
    component_breakdown = pw.TextField(null=True)

    # 触发本次迁移的规则 / 信号说明，便于人工复盘时快速定位原因
    trigger_reason = pw.TextField(null=True)

    # 触发判定时的原始指标快照（成交量、持仓分布等未加工数据），
    # 序列化为 JSON 字符串保存，是黑匣子记录的最底层依据。
    metrics_snapshot = pw.TextField(null=True)

    # 记录写入时间（审计用，非业务时间）
    created_at = pw.DateTimeField(default=dt.datetime.utcnow)

    class Meta:
        table_name = "state_transition_logs"
        indexes = ((("asset", "event_ts"), False),)

    def set_component_breakdown(self, components: dict) -> None:
        """写入 CS 得分的组件拆解，键为子指标名，值为该子指标贡献分"""
        self.component_breakdown = json.dumps(components, ensure_ascii=False)

    def get_component_breakdown(self) -> dict:
        return json.loads(self.component_breakdown) if self.component_breakdown else {}

    def set_metrics_snapshot(self, metrics: dict) -> None:
        """写入触发本次判定时的原始指标快照"""
        self.metrics_snapshot = json.dumps(metrics, ensure_ascii=False)

    def get_metrics_snapshot(self) -> dict:
        return json.loads(self.metrics_snapshot) if self.metrics_snapshot else {}

    def __str__(self) -> str:  # pragma: no cover - 调试辅助
        return (
            f"<StateTransitionLog asset_id={self.asset_id} "
            f"{self.from_stage} -> {self.to_stage} cs={self.cs_score} @ {self.event_ts}>"
        )


# 建表 / 迁移工具（如 database.session.init_db）遍历的模型清单，
# 新增表时只需在此追加，避免各处硬编码模型列表导致遗漏。
MODELS: tuple[type[BaseModel], ...] = (Asset, BacktestRun, StateTransitionLog)
