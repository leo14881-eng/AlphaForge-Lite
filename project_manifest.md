# AlphaForge-Lite 项目清单（v0.7 快照 · 真实数据校准版）

> 项目代号：AlphaForge-Lite
> 定位：加密资产"非共识资本聚集探测器"量化验证沙盒
> 快照日期：2026-07-13
> 当前阶段：已用真实 3 年 / 12 资产数据完成一轮参数网格扫描，产出实测最优参数组合，单测 28 项全部通过
> 远程仓库：https://github.com/leo14881-eng/AlphaForge-Lite（`main` 分支）

---

## 〇、最高目标与三铁律（项目最高优先级原则，任何设计决策不得违背）

> 市场总会有资金去打造新的领导者，我们要做的不是预测，而是尽早识别资金
> 正在持续集中的资产，在领导优势形成阶段参与，在优势消失阶段退出，没有
> 高质量机会时保持现金。我们的目的是早于市场发现领导者，而不是等这个币
> 涨了很多才认为它是领导者。

本轮交付把"验证是否早于市场发现领导者"从单次回测的人工判断，升级为
可自动化重复、可参数寻优的流程：`run_tuning.py` 对 CCS 权重与状态机
迟滞窗口做网格扫描，用每组参数产出的 Lead Time 中位数直接排序，回答
"哪组参数最安全、最提前发现领导者"，而不是靠感觉调参。

---

## 一、项目当前状态

- [x] 项目目录结构、状态机常量、peewee 数据库模型、独立 git 仓库、README
- [x] `CCSDetector`（`detectors/cs_score.py`）、`StateMachineEngine`
      （`state_machine/engine.py`）、`DataLoader`（`backtest/data_loader.py`）、
      `BacktestRunner` / `BacktestReporter`（`backtest/runner.py` /
      `backtest/report.py`），端到端沙盒回测闭环
- [x] **本轮新增**：`main.py` 全量重写为工业级一键 CLI 入口：
      - `argparse` 定义 `--data`（大宽表文件）、`--init-db`（清空重建库，
        显式 opt-in 的破坏性操作）、`--db-path`（自定义数据库文件，默认
        `alphaforge_lite.db`，裸文件名落在既有 `database/` 目录下）
      - 数据库层 -> 数据接入层 -> 回测执行层 -> 报表渲染层，每层独立
        `try/except`，失败时打印带 `[XX层]` 标签的清晰错误并以非零状态码
        退出，不静默崩溃
      - 不带 `--data` 时只做数据库初始化后优雅退出（这是设计如此，不是
        bug——本工具是批处理脚本，不是常驻服务）
- [x] **本轮新增**：`database/session.py::init_db()` 支持传入
      `db_path` 覆盖默认路径（仅在数据库句柄首次绑定时生效），配合
      `main.py --db-path` 使用
- [x] **本轮新增**：`api/app.py` —— 可选的常驻 HTTP API 服务层
      （FastAPI + uvicorn，用 `run_api.py` 启动）：
      - `POST /runs`：同步跑一次完整回测，返回 `run_id`
      - `GET /runs`：列出最近的回测运行
      - `GET /runs/{run_id}`：查询某次运行的元数据
      - `GET /runs/{run_id}/report`：获取完整复盘报告（JSON）
      - `backtest/report.py::BacktestReporter.to_dict()` +
        模块级 `_df_to_records()` 辅助函数，把各看板 DataFrame 转成
        JSON 安全结构，供 API 层直接返回
      - v1 为同步阻塞式薄封装，不引入任务队列（数据规模变大后可选接入
        Celery/RQ，不影响其它模块）
- [x] `run_tuning.py` —— 参数网格扫描脚本：
      - 对 `CCSDetector` 的 `weight_delta2_rs`(w_a，斜率加速度) /
        `weight_volume_delta`(w_b，温和放量) 做固定四档配对扫描
        （`0.8/0.2, 0.6/0.4, 0.4/0.6, 0.2/0.8`，每对总和恒为 1.0）、
        `StateMachineEngine` 的 `hysteresis_window` 扫描 `2/3/4`，
        共 4×3=12 组组合
      - 每组参数静默跑一次 `BacktestRunner`（回测循环本身不打印逐笔
        迁移日志，调参脚本也不调用 `print_report()`），只抓取两个核心
        指标：`Lead Time Median`（`lead_time_audit()` 全量中位数）与
        `Trigger Count`（SEED 阶段在 `logs_df` 里的总触发频次，全量
        计数而非"每资产仅首次"采样）
      - 打印列为 `w_a(斜率加速度) | w_b(温和放量) | Hysteresis |
        Lead Time Median(中位数天数) | Trigger Count`，按 Lead Time
        Median 降序排列，并给出显式结论行
      - 单组合执行失败会被跳过并记录警告，不会中断整个扫描
- [x] **本轮：真实数据网格扫描实测结果**——用
      `data/raw/crypto_market_daily.csv`（3 年、12 资产、15468 行）跑完
      全部 12 组参数组合，完整天梯榜：

      | rank | w_a | w_b | Hysteresis | Lead Time Median | Trigger Count |
      |---|---|---|---|---|---|
      | 1 | 0.8 | 0.2 | 2 | **6.0** | 1248 |
      | 2 | 0.8 | 0.2 | 4 | 6.0 | 871 |
      | 3 | 0.2 | 0.8 | 2 | 5.5 | 1510 |
      | 4 | 0.8 | 0.2 | 3 | 5.0 | 1019 |
      | 5 | 0.6 | 0.4 | 2 | 4.5 | 1263 |
      | 6 | 0.6 | 0.4 | 3 | 4.5 | 936 |
      | 7 | 0.6 | 0.4 | 4 | 4.5 | 703 |
      | 8 | 0.4 | 0.6 | 2 | 4.0 | 1349 |
      | 9 | 0.4 | 0.6 | 3 | 4.0 | 832 |
      | 10 | 0.2 | 0.8 | 3 | 4.0 | 874 |
      | 11 | 0.2 | 0.8 | 4 | 3.5 | 478 |
      | 12 | 0.4 | 0.6 | 4 | 2.5 | 503 |

      **实测最优组合：`w_a=0.8, w_b=0.2, hysteresis_window=2`**
      （Lead Time 中位数 6.0 个时间步，SEED 触发 1248 次，兼顾"提前量"
      与"信号稳定性"）。结果规律清晰：组件 A（delta2_rs，相对强度
      斜率加速度）权重越高，Lead Time 中位数总体越大——说明"价格尚未
      明显异动、相对强度已开始非线性加速"这个先行信号，比"温和放量"
      更早于市场反应，这与项目最高目标"早于市场发现"直接吻合。
- [x] `data/download_data.py` —— 真实历史数据下载工具：
      - 用 `ccxt` 从 Binance 公开现货 K 线接口（无需 API Key）批量拉取
        12 个主流交易对（BTC/ETH/SOL/BNB/LINK/ADA/XRP/DOGE/AVAX/DOT/
        LTC/TRX）近 3 年日线数据，支持自定义交易对/时间范围
      - 内置分页拉取（应对 Binance 单次请求根数上限）与逐交易对异常
        隔离（单个交易对失败不影响其它交易对）
      - 用 `log1p(volume)` 做 min-max 归一化，映射到 `[0.5%, 5%]` 区间
        作为 `turnover_rate` 代理指标（真实流通量数据不易稳定获取，
        已在代码注释与本文件中明确标注这是代理指标，不等价于真实换手率）
      - 产出严格对齐 `detectors.cs_score.REQUIRED_COLUMNS`，保存为
        `data/raw/crypto_market_daily.csv`，可直接被 `DataLoader` /
        `main.py --data` 使用；**本轮已实际下载完成**：15468 行、
        12 个资产、2023-01-01 ~ 2026-07-12
- [x] `AlphaForge-Lite/.venv` 独立虚拟环境（此前 IntelliJ IDEA 项目
      未配置 Python SDK，误用了同目录下另一个项目 `ShortGPT` 的重量级
      venv，已排查并建立专属环境，详见下方"环境排障记录"）
- [x] 单元测试总计 **28 项全部通过**（`run_tuning.py` 因本轮接口调整
      同步更新了对应单测，净减 1 项属正常的接口变更），并用真实 3 年
      12 资产 Binance 数据完整跑通过一次端到端回测与一次全量参数网格
      扫描做实机验证

尚未实现（后续可选增强，非阻塞项）：

- [ ] **实测最优参数尚未回填为出厂默认值**：`run_tuning.py` 已经
      跑出 `w_a=0.8, w_b=0.2, hysteresis_window=2` 是当前数据集上的
      最优组合，但 `CCSDetector`/`StateMachineEngine` 的构造函数默认值
      仍是本轮之前设定的启发式估计（`weight_delta2_rs=0.5,
      weight_volume_delta=0.5`、`hysteresis_window=3`），尚未被这次
      实测结果替换——这是最直接的下一步
- [ ] `price_volume_divergent`（量价背离标记）仍未实现具体计算公式，
      `StateMachineEngine` 前瞻逃顶目前只有拥挤度这一条通道生效
- [ ] `POST /runs` 是同步阻塞调用，数据量变大后如需异步化需要评估
      任务队列方案

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
│   └── session.py              # 数据库延迟绑定、init_db(db_path=...)、事务上下文
├── data/
│   ├── raw/                    # 原始 CSV/Parquet 历史大宽表（不纳入版本管理）
│   ├── processed/              # 清洗/加工后的中间数据（不纳入版本管理）
│   └── download_data.py        # 真实历史数据下载工具（ccxt + Binance，本轮新增）
├── detectors/
│   ├── __init__.py
│   └── cs_score.py             # CCSDetector：delta2_rs / volume_delta / crowding_penalty
├── backtest/
│   ├── __init__.py
│   ├── data_loader.py          # DataLoader：大宽表加载 + schema 校验
│   ├── runner.py               # BacktestRunner / BacktestConfig / PeeweeStageLookup
│   └── report.py               # BacktestReporter：三块复盘看板 + to_dict()（本轮新增）
├── api/
│   ├── __init__.py
│   └── app.py                   # FastAPI 常驻 HTTP 服务（本轮新增）
├── logs/                       # 运行日志目录
├── tests/
│   ├── __init__.py
│   ├── test_state_machine.py
│   ├── test_state_machine_engine.py
│   ├── test_models.py
│   ├── test_cs_score.py
│   ├── test_backtest_pipeline.py
│   ├── test_api.py                   # API 集成测试（本轮新增）
│   ├── test_run_tuning.py            # 参数扫描脚本单测（本轮新增）
│   └── test_download_data.py         # 换手率代理指标单测（本轮新增）
├── main.py                     # 一键 CLI 入口（本轮全量重写）
├── run_api.py                   # 启动常驻 HTTP API 服务（本轮新增）
├── run_tuning.py                 # 参数网格扫描脚本（本轮新增）
├── requirements.txt
├── .gitignore
├── README.md
└── project_manifest.md         # 本文件
```

### 分层职责与依赖方向（本轮扩展）

| 模块 | 职责 | 依赖方向 |
|---|---|---|
| `config` | 提供路径等基础配置 | 被所有模块依赖 |
| `state_machine` | 阶段枚举、迁移规则、状态机执行引擎 | 不依赖 `database`（`StageLookup` 协议解耦） |
| `database` | peewee 表结构与连接管理 | 依赖 `state_machine` |
| `detectors` | 计算 CCS 得分与组件拆解 | 纯 pandas/numpy |
| `backtest` | 数据加载 + 回测主流程 + 复盘报表 | 依赖 `detectors`、`state_machine`、`database` |
| `api` | 常驻 HTTP 服务，把 `backtest` 的能力暴露成接口 | 依赖 `backtest`；不被其它模块依赖 |
| `main.py` / `run_tuning.py` / `data/download_data.py` | 三个独立的命令行入口，分别对应"一键批处理"、"参数寻优"、"真实数据采集" | 均只依赖既有模块，互相之间不依赖 |

### main.py（一键 CLI）设计要点

- `--data` 未指定：只做数据库初始化，打印确认信息后 `return`——这是
  预期行为，`main.py` 定位始终是批处理脚本而非常驻服务，运行完就退出
  代表"成功"（`exit code 0`），不是异常。
- `--init-db`：显式 opt-in 的破坏性操作，会删除旧的 `.db`/`-wal`/
  `-shm`/`-journal` 文件后重新建表；不带这个参数时绝不会触碰已有数据。
- `--db-path` 默认值 `alphaforge_lite.db`，与 `config.settings.DB_PATH`
  的库级默认值 `alphaforge.db` 是两个不同的物理文件——`main.py` CLI
  的默认值遵循本次需求文档给定的值，二者不冲突，只是刻意区分"直接
  import 模块用的库级默认库"与"CLI 一键跑批用的库"。
- 数据接入层会先做一次显式的文件存在性检查（配合 `_resolve_data_path`
  按"裸文件名 -> `data/raw/` 目录"解析），再交给 `DataLoader` 做
  schema 校验，两层检查合起来才能覆盖"文件不存在"和"文件存在但格式不对"
  两类错误，且都有 `[数据接入层]` 前缀的清晰提示。

### api/app.py（常驻 HTTP 服务）设计要点

- 用 `python run_api.py` 或 `uvicorn api.app:app` 启动，进程常驻监听
  端口，不会像 `main.py` 那样跑完退出——这是专门为"习惯服务常驻、
  调接口"工作方式的场景补的入口，`main.py`（批处理）与 `api/`（常驻
  服务）是两种互补的使用方式，不是谁取代谁。
- 用 FastAPI 的 `lifespan` 上下文管理器在服务启动时调用 `init_db()`
  （新版写法，替代已废弃的 `@app.on_event("startup")`）。
- `POST /runs` 在请求内同步跑完整回测——面向本地小规模沙盒场景，
  暂不引入任务队列；`BacktestReporter.to_dict()` 把三块看板 DataFrame
  转成 JSON 安全结构（`Timestamp`/`Timedelta` 转字符串、`NaN` 转
  `None`），供 `GET /runs/{run_id}/report` 直接返回。

### run_tuning.py（参数网格扫描）设计要点

- 权重组合固定为四档配对（`(0.8,0.2)/(0.6,0.4)/(0.4,0.6)/(0.2,0.8)`），
  每对总和恒为 1.0，覆盖"偏重组件A"到"偏重组件B"的完整光谱，不做笛卡
  尔积，避免出现两个权重都很大/都很小这类无意义组合。
- 每组参数独立调用 `CCSDetector(weight_delta2_rs=..., weight_volume_delta=...)`
  与 `StateMachineEngine(hysteresis_window=...)` 注入 `BacktestConfig`，
  互不干扰；数据只加载一次（在循环外），避免重复 I/O。
- **静默执行**：`BacktestRunner.run()` 本身不打印逐笔迁移日志（只落库），
  调参循环也刻意不调用 `BacktestReporter.print_report()`，只提取两个
  结构化指标，保证终端输出不被 12 组合 × 每组合上千条状态迁移的海量
  日志淹没。
- 只抓取两个核心指标（不再是 v0.6 版本的多指标面板）：`Lead Time
  Median`（`lead_time_audit()` 的全量中位数，SEED+DISCOVERY 合并计算，
  主排序键）与 `Trigger Count`（SEED 阶段在 `logs_df` 里的总触发频次，
  全量计数而非 `lead_time_audit()` 的"每资产仅首次"采样——用于让
  Reviewer 判断"信号是否稳定"，而不只是"有没有巧合命中一次"）。

### data/download_data.py（真实数据下载）设计要点

- 独立于核心回测链路：`DataLoader`/`BacktestRunner` 等模块不依赖
  `ccxt`，只依赖本脚本产出的 CSV 文件，卸载 `ccxt` 不影响回测功能。
- 脚本位于 `data/` 子目录但需要 `import config`/`detectors`，运行时
  显式把项目根目录插入 `sys.path`，避免"直接跑子目录脚本导致根包
  导入失败"这个常见坑。
- `turnover_rate` 是**代理指标**（`log1p(volume)` 归一化映射到
  `[0.5%, 5%]`），不是真实换手率——已在代码注释、单测、本文件三处
  一致声明，避免未来被误当作真实数据使用。

### 环境排障记录（本轮会话中发生，记录在案）

用户反馈 IntelliJ IDEA 调试 `main.py` 时报 `torch` 相关的
`ImportError`/`KeyboardInterrupt`。排查发现 `AlphaForge-Lite` 项目在
IDE 里被识别成 `JAVA_MODULE`（`.idea/misc.xml` 显示 `project-jdk-type=
"JavaSDK"`），没有配置专属 Python 解释器，实际调试时误用了同目录下
另一个项目 `ShortGPT` 的 `.venv`（装有 CUDA 版 torch，DLL 加载慢导致
表面上像是"卡死"）。已为本项目单独创建 `.venv` 并安装
`requirements.txt` 全部依赖（含本轮新增的 `fastapi`/`uvicorn`/`httpx`/
`ccxt`），已被 `.gitignore` 排除，不影响仓库。用户在此过程中还提出了
"main.py 一启动就退出"的疑问，本质是把批处理脚本预期成了 Java 式常驻
服务——这正是本轮新增 `api/` HTTP 服务层的直接动机。

---

## 三、当前开发进度与下一步行动

**已完成（截至本次会话，2026-07-13）：** 目录脚手架、状态机常量、
peewee 数据库模型、独立 git 仓库、README、`CCSDetector`、
`StateMachineEngine`、`DataLoader`、`BacktestRunner`、`BacktestReporter`
（端到端沙盒回测闭环）、一键 CLI（`main.py`）、常驻 HTTP API 服务
（`api/`、`run_api.py`）、真实历史数据下载工具（`data/download_data.py`，
已实际下载 3 年 12 资产真实数据）、参数网格扫描工具（`run_tuning.py`，
**已用真实数据完整跑过一轮 12 组合网格扫描，产出实测最优参数
`w_a=0.8, w_b=0.2, hysteresis_window=2`**）、专属 `.venv` 环境。单元
测试 28 项全部通过。

**下一次对话可以从这里开始：**

1. **把实测最优参数回填为出厂默认值**：修改
   `detectors/cs_score.py::CCSDetector` 的 `weight_delta2_rs`/
   `weight_volume_delta` 默认值为 `0.8`/`0.2`，`state_machine/engine.py
   ::StateMachineEngine` 的 `hysteresis_window` 默认值为 `2`，让
   "不带任何自定义参数直接跑" 也能复现本轮校准出的最优效果；建议保留
   一份对比说明（改前/改后各跑一次 Lead Time 审计），证明回填没有
   引入回归。

2. **实现量价背离信号**（`price_volume_divergent`）：让
   `StateMachineEngine` 的前瞻逃顶机制不只依赖拥挤度这一条通道。

3. **`POST /runs` 异步化评估**：如果后续对接的数据量明显变大、单次
   回测耗时变长，评估是否需要引入任务队列（Celery/RQ），避免长时间
   阻塞 HTTP 请求。

> 本清单将随每个迭代版本更新，作为 Chief Reviewer 审查项目进展的固定参照物。
