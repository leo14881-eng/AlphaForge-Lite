# AlphaForge-Lite 项目清单（v0.4 快照）

> 项目代号：AlphaForge-Lite
> 定位：加密资产"非共识资本聚集探测器"量化验证沙盒
> 快照日期：2026-07-13
> 当前阶段：CCS 探测算法 + 自适应状态机执行引擎已交付并通过单测，回测主流程尚未打通
> 远程仓库：https://github.com/leo14881-eng/AlphaForge-Lite（`main` 分支）

---

## 〇、最高目标与三铁律（项目最高优先级原则，任何设计决策不得违背）

> 市场总会有资金去打造新的领导者，我们要做的不是预测，而是尽早识别资金
> 正在持续集中的资产，在领导优势形成阶段参与，在优势消失阶段退出，没有
> 高质量机会时保持现金。我们的目的是早于市场发现领导者，而不是等这个币
> 涨了很多才认为它是领导者。

本版本新增的 CCS 探测算法与状态机执行引擎，逐条对应这一目标：

- **放弃预测，尽早识别**：`CCSDetector.calculate_cs` 的组件 A（delta2_rs）
  专门捕捉"价格尚未明显异动、相对强度已开始非线性加速"的早期信号，
  组件 B（volume_delta）用高斯钟形函数奖励"温和放量"而非追涨已经
  爆量的资产——两者都是刻意设计成比价格更早反应的先行指标。
- **领导优势形成阶段参与**：`StateMachineEngine` 的迟滞控制（连续 3 期
  达标才确认进入 SEED/DISCOVERY）避免噪声导致的假信号，波动率自适应
  门槛让"参与"标准在不同市场环境下保持一致的严格度。
- **优势消失阶段退出，无机会保持现金**：组件 C（crowding_penalty）与
  状态机的"前瞻逃顶机制"联动——一旦拥挤度持续处于高位或出现量价背离，
  状态机会抢在价格大跌前把 LEADERSHIP 打入 DISTRIBUTION、把
  DISTRIBUTION 打入 EXIT，而不是等回撤已经发生才反应。

---

## 一、项目当前状态

- [x] 项目目录结构落地（配置、状态机、数据库、数据、探测器、回测引擎分层清晰）
- [x] 生命周期状态机的 6 个阶段枚举定义完成（`state_machine/constants.py`），
      阶段语义与最高目标一一对应，并附带迁移合法性校验规则
      （`ALLOWED_TRANSITIONS` / `is_transition_allowed`）
- [x] SQLite 数据库表结构设计完成（`database/models.py`），基于轻量级
      **peewee** ORM（底层为标准库 sqlite3），覆盖资产主表、回测运行
      元数据表、状态迁移日志（黑匣子）表三张核心表——本轮**未修改**
      数据库表结构，CCS 得分与组件拆解复用已有的 `cs_score` /
      `component_breakdown` 字段落库
- [x] 数据库连接管理与建表初始化逻辑完成（`database/session.py`）
- [x] 已建立独立 git 仓库并推送至
      `https://github.com/leo14881-eng/AlphaForge-Lite`（`main` 分支）
- [x] `README.md` 作为仓库首页说明文档
- [x] **本轮新增**：`detectors/cs_score.py` —— `CCSDetector`
      （Capital Convergence Score 探测器），向量化实现 delta2_rs /
      volume_delta / crowding_penalty 三组件与 `calculate_cs()` 总分计算
- [x] **本轮新增**：`state_machine/engine.py` —— `StateMachineEngine`
      （自适应状态机执行引擎/看门狗），实现 `update_asset_state()`，
      内置迟滞控制、波动率自适应门槛、前瞻逃顶机制
- [x] **本轮新增**：`tests/test_cs_score.py`、`tests/test_state_machine_engine.py`，
      单元测试总计 **17 项全部通过**

尚未实现：

- [ ] `backtest/data_loader.py`：CSV / Parquet 大宽表的加载、schema 校验
- [ ] `backtest/runner.py`：串联"数据加载 -> CCS 计算 -> 状态机驱动 -> 落库"
      的端到端回测主流程（`BacktestRunner`）
- [ ] `backtest/report.py`：基于 `StateTransitionLog` 的复盘报表工具
      （`BacktestReporter`），包括阶段停留时长、迁移路径分布、
      DISCOVERY 组件贡献度归因、Lead Time 审计

---

## 二、架构说明

```
AlphaForge-Lite/
├── config/
│   ├── __init__.py
│   └── settings.py            # 全局路径配置
├── state_machine/
│   ├── __init__.py
│   ├── constants.py           # 6 阶段枚举 + 合法迁移规则（语义对齐最高目标）
│   └── engine.py               # StateMachineEngine：迟滞控制/波动率自适应/前瞻逃顶看门狗
├── database/
│   ├── __init__.py
│   ├── models.py               # peewee 表结构：Asset / BacktestRun / StateTransitionLog
│   └── session.py              # 数据库延迟绑定、建表初始化、事务上下文
├── data/
│   ├── raw/                    # 原始 CSV/Parquet 历史大宽表（不纳入版本管理）
│   └── processed/              # 清洗/加工后的中间数据（不纳入版本管理）
├── detectors/
│   ├── __init__.py
│   └── cs_score.py             # CCSDetector：delta2_rs / volume_delta / crowding_penalty
├── backtest/
│   └── __init__.py             # 回测引擎包（data_loader / runner / report 待实现）
├── logs/                       # 运行日志目录
├── tests/
│   ├── __init__.py
│   ├── test_state_machine.py         # 状态机常量与迁移规则单测
│   ├── test_state_machine_engine.py  # StateMachineEngine 单测（迟滞/波动率/前瞻逃顶）
│   ├── test_models.py                # 数据库模型单测（内存 SQLite）
│   └── test_cs_score.py              # CCSDetector 单测
├── main.py                     # 入口脚本，当前仅负责初始化数据库
├── requirements.txt
├── .gitignore
├── README.md
└── project_manifest.md         # 本文件
```

### 分层职责与依赖方向（本轮有修正）

| 模块 | 职责 | 依赖方向 |
|---|---|---|
| `config` | 提供路径等基础配置 | 被所有模块依赖，不依赖任何业务模块 |
| `state_machine` | 阶段枚举、迁移规则、状态机执行引擎，全项目状态语义的唯一真源 | **不依赖 `database`**——`engine.py` 通过 `StageLookup` 协议（鸭子类型）与持久层解耦，只声明"需要一个能查到资产最近阶段的对象"，具体 peewee 查询实现留给 `backtest` 层适配 |
| `database` | peewee 表结构与连接管理，负责状态机执行日志（黑匣子）与回测元数据的持久化 | 依赖 `state_machine`（引用 `LifecycleStage` 定义字段取值） |
| `detectors` | 计算 CCS 得分与组件拆解，纯 pandas/numpy 向量化计算 | 不依赖 `state_machine` / `database`，输出结构通过约定字段名与下游对接 |
| `backtest` | （待实现）驱动"数据加载 -> CCS 计算 -> 状态迁移 -> 落库"全流程，并提供 `StageLookup` 的 peewee 适配器 | 依赖 `detectors`、`state_machine`、`database` |

> 修正说明：v0.3 中 `state_machine 被 database 依赖` 的描述被误读为
> "state_machine 依赖 database"，本轮明确澄清并通过 `StageLookup`
> Protocol 在代码层面强制了这一约束，避免核心状态语义模块反向依赖
> 持久层实现细节。

### CCSDetector（`detectors/cs_score.py`）设计要点

- **输入**：长表 DataFrame，至少包含 `timestamp / symbol / close / volume /
  turnover_rate`，`funding_rate` 为可选列。
- **相对强度基准**：本地沙盒不额外提供大盘指数，用同一张大宽表内所有
  symbol 的等权平均收益率合成基准指数（自包含简化处理，后续可替换为
  真实指数）。
- **组件 A · delta2_rs**：30 期滚动相对强度斜率（闭式解，全向量化，
  非逐窗口调用 `numpy.polyfit`）的一阶差分，经滚动 z-score + sigmoid
  归一化到 `[0, 1]`。
- **组件 B · volume_delta**：对数空间的高斯钟形函数，峰值在
  "约 2 倍均量"附近，刻意让暴风骤雨式极端放量的得分低于温和放量，
  且未放量（比值 ≤ 1）不给分。
- **组件 C · crowding_penalty**：换手率 / 资金费率相对自身历史的滚动
  z-score，超过 1σ 的部分按 `exp(-λ·excess)` 指数压制；作用于总分而非
  作为可加分量。
- **总分**：`cs_score = (w_a·delta2_rs + w_b·volume_delta) × crowding_penalty`。
- 三个组件与总分均输出为 DataFrame 列，供 `backtest.runner` 直接提取
  写入 `StateTransitionLog.component_breakdown`。

### StateMachineEngine（`state_machine/engine.py`）设计要点

- **核心方法**：`update_asset_state(asset_id, current_metrics, db_session) -> Stage | None`，
  返回本次判定后的最新阶段；触发迁移时完整上下文（`from_stage` /
  `to_stage` / `reason`）记录在 `self.last_transition`，供调用方读取后落库。
- **合法性校验**：迁移前 `assert is_transition_allowed(...)`——按设计
  `_decide_target_stage` 只会提出相邻合法目标，assert 是兜底防线，
  不做静默纠正。
- **迟滞控制**：进入 SEED / DISCOVERY 需要最近连续 `hysteresis_window`
  （默认 3）期得分均达标，单点噪声不触发切换。
- **波动率自适应**：调用方可传入 `market_atr_ratio`（市场整体波动率
  相对比例），晋升门槛按 `clip(atr_ratio, 0.7, 1.5)` 缩放——波动越大，
  门槛越高，抑制噪声行情下的假信号。
- **前瞻逃顶**：`crowding_penalty` 连续 `crowding_alert_streak`
  （默认 2）期低于阈值，或调用方标记 `price_volume_divergent=True`，
  会无视得分直接把 LEADERSHIP 打入 DISTRIBUTION、DISTRIBUTION 打入
  EXIT，优先级高于常规得分门槛判断。
- **EXIT 语义**：EXIT 为终态，触发后本地缓存与滚动状态自动重置，
  为资产开启全新观察周期，无需上层显式干预。

### 数据库表关系（本轮未变更）

```
Asset (1) ──< StateTransitionLog >── (1) BacktestRun
```

| 字段 | 含义 |
|---|---|
| `from_stage` / `to_stage` | 本次判定的阶段迁移（引用 `LifecycleStage`） |
| `cs_score` | CCSDetector 输出的资金聚集综合评分 |
| `component_breakdown` | `{delta2_rs, volume_delta, crowding_penalty}` 组件拆解（JSON） |
| `trigger_reason` | `StateMachineEngine.last_transition.reason`，触发迁移的具体原因 |
| `metrics_snapshot` | 触发判定时的原始指标快照（JSON，黑匣子最底层依据） |

---

## 三、当前开发进度与下一步行动

**已完成（截至本次会话，2026-07-13）：** 目录脚手架、状态机常量、
peewee 数据库模型/连接管理、独立 git 仓库并推送至 GitHub、README.md、
`CCSDetector`（CS 得分探测算法）、`StateMachineEngine`（自适应状态机
执行引擎），单元测试 17 项全部通过。

**下一次对话应从这里开始：**

1. **实现数据接入层**（`backtest/data_loader.py`，待创建）
   - 支持从 `data/raw/` 加载 CSV / Parquet 历史大宽表
   - 统一时间戳字段、资产标识字段的 schema 校验

2. **打通端到端回测流程**（`backtest/runner.py`，待创建）
   - `BacktestRunner`：加载大宽表、创建 `BacktestRun` 记录、按时间步
     顺序推进，调用 `CCSDetector.calculate_cs` + `StateMachineEngine.
     update_asset_state`，触发迁移时落库 `StateTransitionLog`
   - 需要实现一个 `StageLookup` 的 peewee 适配器（查询某资产最近一次
     `to_stage`），作为 `state_machine.engine` 与 `database` 之间的
     胶水代码，维持前述依赖方向约束

3. **补充报表/复盘工具**（`backtest/report.py`，待创建）
   - `BacktestReporter`：阶段停留时长、状态迁移路径分布、DISCOVERY
     组件贡献度归因
   - Lead Time 审计：进入 SEED/DISCOVERY 的时间点平均早于价格绝对
     高点多少个时间步，验证策略是否真正做到了"早于市场发现"

> 本清单将随每个迭代版本更新，作为 Chief Reviewer 审查项目进展的固定参照物。
