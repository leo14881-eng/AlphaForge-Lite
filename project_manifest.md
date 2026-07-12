# AlphaForge-Lite 项目清单（v0.5 快照 · 闭环版）

> 项目代号：AlphaForge-Lite
> 定位：加密资产"非共识资本聚集探测器"量化验证沙盒
> 快照日期：2026-07-13
> 当前阶段：端到端回测主流程与 Lead Time 审计报表已交付，沙盒闭环打通，单测 18 项全部通过
> 远程仓库：https://github.com/leo14881-eng/AlphaForge-Lite（`main` 分支）

---

## 〇、最高目标与三铁律（项目最高优先级原则，任何设计决策不得违背）

> 市场总会有资金去打造新的领导者，我们要做的不是预测，而是尽早识别资金
> 正在持续集中的资产，在领导优势形成阶段参与，在优势消失阶段退出，没有
> 高质量机会时保持现金。我们的目的是早于市场发现领导者，而不是等这个币
> 涨了很多才认为它是领导者。

本轮交付的意义在于**让这个目标第一次变成可被数据验证的闭环**：
`BacktestReporter.lead_time_audit()` 直接计算"状态机判定进入 SEED /
DISCOVERY 的时间点"与"该资产随后生命周期内价格最高点"之间相差多少个
时间步。中位数为正，才是"早于市场发现领导者"这句话真正成立的证据，
而不是策略设计者的自我宣称——这是本项目区别于"事后诸葛亮式回测"的
核心校验点。

---

## 一、项目当前状态

- [x] 项目目录结构、状态机常量、peewee 数据库模型、独立 git 仓库、README
- [x] `CCSDetector`（`detectors/cs_score.py`）：delta2_rs / volume_delta /
      crowding_penalty 三组件与 CS 总分
- [x] `StateMachineEngine`（`state_machine/engine.py`）：迟滞控制、波动率
      自适应门槛、前瞻逃顶机制
- [x] **本轮新增**：`backtest/data_loader.py` —— `DataLoader`，加载
      `data/raw/` 下的 CSV / Parquet 大宽表，做 schema 校验（复用
      `detectors.cs_score.REQUIRED_COLUMNS`，避免两处定义漂移）、
      时间戳解析、数值列类型强制、按 `(symbol, timestamp)` 排序去重
- [x] **本轮新增**：`backtest/runner.py` —— `BacktestRunner` +
      `BacktestConfig` + `PeeweeStageLookup`：
      - `PeeweeStageLookup` 是 `state_machine.engine.StageLookup` 协议的
        peewee 实现，是 `state_machine` 与 `database` 之间的胶水代码
      - `run()`：创建 `BacktestRun` Master 记录 -> 向量化一次性调用
        `CCSDetector.calculate_cs()` 算完全表 -> 按时间步顺序推进，
        触发迁移即在单个 `atomic()` 事务内批量写入 `StateTransitionLog`
      - 新增 `market_atr_ratio`（市场整体波动率相对比例）计算，真正把
        `StateMachineEngine` 的波动率自适应能力用起来，而不是让它闲置
- [x] **本轮新增**：`backtest/report.py` —— `BacktestReporter`：
      生命周期分布（阶段停留时长 + 迁移路径分布）、非共识归因
      （DISCOVERY 组件贡献占比）、**核心审判 · Lead Time 审计**
      （中位数 Lead Time 是否为正，直接打印验证结论）
- [x] **本轮新增**：`tests/test_backtest_pipeline.py` 端到端集成测试
      （合成"温和放量+持续上涨+后期拥挤反转"数据，验证全链路可跑通）
- [x] 单元测试总计 **18 项全部通过**，并已实机运行一次完整回测 + 报表
      打印做人工核验（详见下方"实机验证记录"）

尚未实现（后续可选增强，非本轮必需）：

- [ ] `price_volume_divergent`（量价背离标记）目前没有具体计算公式，
      `StateMachineEngine` 的前瞻逃顶目前只依赖 `crowding_penalty`
      连续触发这一条通道生效
- [ ] 命令行入口（`main.py` 目前仍只负责建库，尚未接入
      "指定数据文件 -> 跑回测 -> 打印报告"的一键式 CLI）
- [ ] 真实历史数据的接入与参数调优（当前所有阈值均为出厂默认值，
      未针对真实加密资产数据做校准）

---

## 二、架构说明

```
AlphaForge-Lite/
├── config/
│   ├── __init__.py
│   └── settings.py            # 全局路径配置
├── state_machine/
│   ├── __init__.py
│   ├── constants.py           # 6 阶段枚举 + 合法迁移规则
│   └── engine.py               # StateMachineEngine + StageLookup 协议
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
│   ├── __init__.py
│   ├── data_loader.py          # DataLoader：大宽表加载 + schema 校验
│   ├── runner.py               # BacktestRunner / BacktestConfig / PeeweeStageLookup
│   └── report.py               # BacktestReporter：三块复盘看板 + Lead Time 审计
├── logs/                       # 运行日志目录
├── tests/
│   ├── __init__.py
│   ├── test_state_machine.py         # 状态机常量与迁移规则单测
│   ├── test_state_machine_engine.py  # StateMachineEngine 单测
│   ├── test_models.py                # 数据库模型单测（内存 SQLite）
│   ├── test_cs_score.py              # CCSDetector 单测
│   └── test_backtest_pipeline.py     # 端到端集成测试（本轮新增）
├── main.py                     # 入口脚本，当前仅负责初始化数据库
├── requirements.txt
├── .gitignore
├── README.md
└── project_manifest.md         # 本文件
```

### 分层职责与依赖方向（本轮闭环，依赖方向与 v0.4 保持一致）

| 模块 | 职责 | 依赖方向 |
|---|---|---|
| `config` | 提供路径等基础配置 | 被所有模块依赖 |
| `state_machine` | 阶段枚举、迁移规则、状态机执行引擎 | 不依赖 `database`（通过 `StageLookup` 协议解耦） |
| `database` | peewee 表结构与连接管理 | 依赖 `state_machine` |
| `detectors` | 计算 CCS 得分与组件拆解 | 纯 pandas/numpy，不依赖其他业务模块 |
| `backtest` | 数据加载 + 回测主流程 + 复盘报表，**本轮打通的总装层** | 依赖 `detectors`、`state_machine`、`database`；`runner.py::PeeweeStageLookup` 是 `state_machine` 与 `database` 之间约定的唯一胶水代码 |

### DataLoader（`backtest/data_loader.py`）设计要点

- 支持 `.csv` / `.parquet`，必需列复用 `detectors.cs_score.REQUIRED_COLUMNS`
  （`timestamp/symbol/close/volume/turnover_rate`），`funding_rate` 可选。
- 时间戳统一解析为 `datetime64`，价格/成交量/换手率强制转数值类型，
  非法数据直接抛 `ValueError`/类型错误（不做静默兜底）。
- 按 `(symbol, timestamp)` 排序去重（重复时间戳保留最后一条），返回长表
  格式 DataFrame，直接对接 `CCSDetector.calculate_cs()`。

### BacktestRunner（`backtest/runner.py`）设计要点

- **两段式执行**：先向量化算完 CCS 全表（一次性 pandas 计算），再对
  排序后的 `(timestamp, symbol)` 做纯 Python 顺序遍历驱动状态机——
  重计算部分向量化、状态推进部分保持时间步顺序语义，两者不冲突。
- **market_atr_ratio**：因数据 schema 只有收盘价、没有高低价，用
  "日收益率绝对值的滚动均值"替代标准 ATR 作为波动率代理，再对全市场
  取横截面等权均值、除以其自身更长窗口的历史均值，得到相对比例
  （1.0 = 与近期常态持平），喂给 `StateMachineEngine` 的波动率自适应门槛。
- **事务策略**：整个时间步循环包在单个 `db.atomic()` 内批量提交，
  减少磁盘同步次数；`BacktestRun` 的 Master 记录创建与失败状态回写都在
  事务外单独完成，保证即使循环中途异常回滚，也不会丢失这次运行"失败"
  的审计记录。
- **可复现性**：`param_snapshot` 落库 `data_source`（供 `report.py`
  回溯价格序列）、`symbols`、`detector_params`、`engine_params`、
  `atr_window`，一次运行的全部配置均可从数据库单独还原。

### BacktestReporter（`backtest/report.py`）设计要点

- 只依赖 `run_id` 构造（内部通过 `param_snapshot.data_source` 用
  `DataLoader` 重新加载价格序列），不依赖 `BacktestRunner` 进程内的
  任何状态，可在独立进程/独立会话中复盘任意历史运行。
- **生命周期分布**：同一资产序列内用"下一条记录的时间戳"做差得到停留
  时长；迁移路径分布按 `(from_stage, to_stage)` 计数降序排列。
- **非共识归因**：读取该次运行落库的 `detector_params` 中的真实权重
  （而非写死默认值），计算组件 A/B 在 DISCOVERY 判定中的加权贡献占比，
  保证归因反映"这次运行实际用的参数"而非出厂默认。
- **Lead Time 审计（核心审判）**：对每个资产取其首次进入 SEED /
  DISCOVERY 的时间点，搜索窗口为 `[进入时间点, 该资产下一次 EXIT 的时间
  点]`（若未 EXIT 则到数据末尾），在窗口内找价格最高点，计算两者相差
  的时间步数（bar）。同时输出均值与**中位数**，并给出"中位数 > 0 即验证
  通过"的显式结论行，让 Reviewer 不需要自己算，直接看结论。

### 实机验证记录

本轮除 18 项单元测试外，额外用独立脚本对一份 150 根日线的合成数据
（PUMP 资产：60 根平静 + 50 根温和放量上涨 + 后段换手率/资金费率骤增
模拟拥挤反转）跑了一次真实的 `DataLoader -> BacktestRunner ->
BacktestReporter` 全链路，人工核验打印输出：SEED 阶段被触发 5 次、
Lead Time 审计正确识别出一次"提前 2 个时间步发现价格高点"的样本
（`lead_time_bars=2` > 0），验证报表的时间对齐与文案渲染均符合预期。
（该次验证使用临时数据库文件，未写入本仓库跟踪的数据/数据库文件。）

---

## 三、当前开发进度与下一步行动

**已完成（截至本次会话，2026-07-13）：** 目录脚手架、状态机常量、
peewee 数据库模型、独立 git 仓库并推送至 GitHub、README、
`CCSDetector`、`StateMachineEngine`、`DataLoader`、`BacktestRunner`、
`BacktestReporter`，端到端沙盒回测闭环已打通，单元测试 18 项全部通过，
并完成一次实机运行核验。

**下一次对话可以从这里开始（均为可选增强，非阻塞项）：**

1. **补充命令行入口**：在 `main.py` 或新增 `cli.py` 中封装
   "指定数据文件 -> 跑一次 `BacktestRunner` -> 自动打印 `BacktestReporter`
   报告"的一键流程，减少每次手工拼接 `DataLoader`/`BacktestConfig` 的样板代码。

2. **实现量价背离信号**（`price_volume_divergent`）：目前
   `StateMachineEngine` 的前瞻逃顶只有"拥挤度连续触发"一条通道生效，
   补上具体的量价背离计算公式（如价格新高但 delta2_rs / volume_delta
   同步走弱）可以让逃顶机制更完整。

3. **接入真实历史数据并做参数校准**：当前所有阈值（`CCSDetector` 权重、
   `StateMachineEngine` 六个门槛与迟滞/波动率参数）均为出厂默认值，
   下一步应该用真实加密资产历史数据跑 `BacktestReporter` 的 Lead Time
   审计，根据中位数结果反过来调优参数，形成"回测 -> 审计 -> 调参"的
   正向迭代循环——这也是本项目沙盒闭环真正投入使用的第一步。

> 本清单将随每个迭代版本更新，作为 Chief Reviewer 审查项目进展的固定参照物。
