# AlphaForge-Lite 项目清单（v0.2 快照）

> 项目代号：AlphaForge-Lite
> 定位：加密资产"非共识资本聚集探测器"量化验证沙盒
> 快照日期：2026-07-12
> 当前阶段：脚手架搭建完成，探测器/回测逻辑尚未实现

---

## 〇、最高目标（项目最高优先级原则，任何设计决策不得违背）

> 市场总会有资金去打造新的领导者，我们要做的不是预测，而是尽早识别资金
> 正在持续集中的资产，在领导优势形成阶段参与，在优势消失阶段退出，没有
> 高质量机会时保持现金。我们的目的是早于市场发现领导者，而不是等这个币
> 涨了很多才认为它是领导者。

本版本脚手架的每一处设计都直接服务于这一目标：

- **6 阶段生命周期**（`state_machine/constants.py`）把"尽早识别 -> 验证
  -> 参与优势期 -> 优势衰减预警 -> 退出保持现金"显式拆解为可判定的状态，
  避免用"已经涨了很多"这种滞后信号做判断。
- **CS 得分 + 组件拆解**（`database/models.py::StateTransitionLog`）把
  "资金是否正在持续集中"这一核心问题量化为可追溯、可复盘的黑匣子记录，
  而不是一个不可解释的黑箱结论。

---

## 一、项目当前状态

第一版核心脚手架已交付，具备以下能力：

- [x] 项目目录结构落地（配置、状态机、数据库、数据、探测器、回测引擎分层清晰）
- [x] 生命周期状态机的 6 个阶段枚举定义完成（`state_machine/constants.py`），
      阶段语义与最高目标一一对应，并附带迁移合法性校验规则
      （`ALLOWED_TRANSITIONS` / `is_transition_allowed`）
- [x] SQLite 数据库表结构设计完成（`database/models.py`），基于轻量级
      **peewee** ORM（底层为标准库 sqlite3），覆盖资产主表、回测运行
      元数据表、状态迁移日志表三张核心表
- [x] `StateTransitionLog` 按"黑匣子"标准设计：不仅记录阶段迁移，
      还落库 `cs_score`（资金集中度综合评分）与 `component_breakdown`
      （得分的组件拆解 JSON），保证每一次判定都可解释、可复盘
- [x] 数据库连接管理与建表初始化逻辑完成（`database/session.py`）
- [x] 基础单元测试覆盖状态机规则与数据库模型的核心行为（`tests/`）

尚未实现（占位阶段）：

- [ ] `detectors/`：CS 得分的具体计算逻辑（量价异常、持仓集中度、
      资金流等子指标的加权公式），是"尽早识别资金集中"的核心算法
- [ ] `backtest/`：基于本地 CSV/Parquet 大宽表驱动状态机的回测引擎主流程
- [ ] `state_machine/`：状态机执行引擎本身（当前只有常量与规则定义，
      尚无"读取行情 -> 计算 CS 得分 -> 驱动状态迁移 -> 落库"的执行器）
- [ ] 数据接入层：CSV/Parquet 大宽表的加载、校验、清洗逻辑

---

## 二、架构说明

```
AlphaForge-Lite/
├── config/
│   ├── __init__.py
│   └── settings.py            # 全局路径配置
├── state_machine/
│   ├── __init__.py
│   └── constants.py           # 6 阶段枚举 + 合法迁移规则（语义对齐最高目标）
├── database/
│   ├── __init__.py
│   ├── models.py               # peewee 表结构：Asset / BacktestRun / StateTransitionLog
│   └── session.py              # 数据库延迟绑定、建表初始化、事务上下文
├── data/
│   ├── raw/                    # 原始 CSV/Parquet 历史大宽表（不纳入版本管理）
│   └── processed/              # 清洗/加工后的中间数据（不纳入版本管理）
├── detectors/
│   └── __init__.py             # 探测器算法包（占位，CS 得分计算逻辑落地于此）
├── backtest/
│   └── __init__.py             # 回测引擎包（占位）
├── logs/                       # 运行日志目录
├── tests/
│   ├── __init__.py
│   ├── test_state_machine.py   # 状态机规则单测
│   └── test_models.py          # 数据库模型单测（内存 SQLite）
├── main.py                     # 入口脚本，当前仅负责初始化数据库
├── requirements.txt
├── .gitignore
└── project_manifest.md         # 本文件
```

### 分层职责

| 模块 | 职责 | 依赖方向 |
|---|---|---|
| `config` | 提供路径等基础配置 | 被所有模块依赖，不依赖任何业务模块 |
| `state_machine` | 定义阶段枚举与迁移规则，是全项目状态语义的唯一真源 | 被 `database`、`detectors`、`backtest` 依赖 |
| `database` | peewee 表结构与连接管理，负责状态机执行日志（黑匣子）与回测元数据的持久化 | 依赖 `state_machine`、`config` |
| `detectors` | （待实现）计算 CS 得分与组件拆解，产出阶段迁移建议 | 依赖 `state_machine` |
| `backtest` | （待实现）驱动"数据加载 -> CS 计算 -> 状态迁移 -> 落库"全流程 | 依赖 `detectors`、`state_machine`、`database` |

### 技术选型说明

数据库层选用 **peewee** 而非 SQLAlchemy：本项目定位是本地沙盒验证工具，
peewee 的 Model 定义即 schema 定义，足够表达三张表的结构与外键关系，
且依赖更轻、心智负担更小。底层数据库句柄在 `database/models.py` 中以
延迟绑定方式创建（`SqliteDatabase(None)`），实际文件路径由
`database/session.py::init_db()` 在应用启动时绑定，测试代码可借助
`bind_ctx` 临时切换到内存库，不污染真实数据文件。

### 数据库表关系

```
Asset (1) ──< StateTransitionLog >── (1) BacktestRun
```

`StateTransitionLog.backtest_run` 允许为空，因此状态迁移日志既可以在
独立的人工标注 / 实时探测场景下写入，也可以绑定到某一次具体的回测
运行以保证结果可复现。每条记录包含：

| 字段 | 含义 |
|---|---|
| `from_stage` / `to_stage` | 本次判定的阶段迁移（引用 `LifecycleStage`） |
| `cs_score` | 当期资金集中度综合评分（Concentration Score） |
| `component_breakdown` | CS 得分的组件拆解（JSON，各子指标贡献分） |
| `trigger_reason` | 触发本次迁移的规则 / 信号说明 |
| `metrics_snapshot` | 触发判定时的原始指标快照（JSON，黑匣子最底层依据） |

---

## 三、下一步行动计划

1. **实现 CS 得分计算逻辑**（`detectors/cs_score.py`，待创建）
   - 输入：某资产在某时间戳的原始指标（量价、持仓分布、资金流）
   - 输出：`cs_score` 总分 + `component_breakdown` 各子指标贡献分
   - 这是"尽早识别资金正在持续集中"的核心算法，需重点设计

2. **实现状态机执行引擎**（`state_machine/engine.py`，待创建）
   - 输入：CS 得分 + 组件拆解
   - 输出：是否触发阶段迁移、迁移到的目标阶段
   - 内部调用 `is_transition_allowed` 做合法性前置校验；
     LEADERSHIP -> DISTRIBUTION 的判定规则需重点关注"优势衰减"信号，
     避免"币涨了很多才承认是领导者"的滞后判断

3. **实现数据接入层**（`backtest/data_loader.py`，待创建）
   - 支持从 `data/raw/` 加载 CSV / Parquet 历史大宽表
   - 统一时间戳字段、资产标识字段的 schema 校验

4. **打通端到端回测流程**（`backtest/runner.py`，待创建）
   - 串联数据加载 -> CS 计算 -> 状态机引擎 -> 数据库落库
   - 每次运行自动创建一条 `BacktestRun` 记录并绑定全部迁移日志

5. **补充报表/复盘工具**（暂定 `backtest/report.py`）
   - 基于 `StateTransitionLog` 生成阶段停留时长、迁移路径分布、
     CS 得分组件贡献的统计视图，验证策略是否真的做到了"早于市场发现"

> 本清单将随每个迭代版本更新，作为 Chief Reviewer 审查项目进展的固定参照物。
