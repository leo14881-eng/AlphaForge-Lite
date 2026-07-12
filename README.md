# AlphaForge-Lite

加密资产"非共识资本聚集探测器"量化验证沙盒。

## 最高目标

> 市场总会有资金去打造新的领导者，我们要做的不是预测，而是尽早识别资金
> 正在持续集中的资产，在领导优势形成阶段参与，在优势消失阶段退出，没有
> 高质量机会时保持现金。我们的目的是早于市场发现领导者，而不是等这个币
> 涨了很多才认为它是领导者。

项目的全部设计决策都围绕这一目标展开，详见 [`project_manifest.md`](./project_manifest.md)。

## 项目定位

- 基于本地静态 CSV / Parquet 历史时序大宽表做沙盒回测，不接入实时数据源
- 用 6 阶段状态机（`SEED -> DISCOVERY -> CONFIRMATION -> LEADERSHIP -> DISTRIBUTION -> EXIT`）
  刻画标的资产的"资金集中"生命周期
- 每一次状态判定都以"黑匣子"方式落库：不仅记录阶段迁移，还记录 CS 得分
  （资金集中度综合评分）与其组件拆解，保证可解释、可复盘
- Lead Time 审计直接回答"我们是否真正做到了早于市场发现领导者"，用数据
  而非自我宣称验证策略有效性

## 技术栈

- Python 3.10+
- [peewee](http://docs.peewee-orm.com/)（轻量级 ORM，底层 SQLite）
- pandas / numpy（CCS 得分向量化计算）
- [FastAPI](https://fastapi.tiangolo.com/) + uvicorn（可选的常驻 HTTP API 服务层）
- [ccxt](https://github.com/ccxt/ccxt)（可选，仅 `data/download_data.py` 抓取真实历史数据时需要）
- pytest（单元测试）

## 目录结构

```
AlphaForge-Lite/
├── config/          # 全局路径配置
├── state_machine/   # 生命周期阶段枚举、迁移规则、自适应状态机执行引擎（看门狗）
├── database/        # peewee 表结构与连接管理
├── data/            # 原始/加工后的历史行情大宽表（不纳入版本管理）+ download_data.py 数据下载工具
├── detectors/       # CCS（Capital Convergence Score）探测算法
├── backtest/        # 数据加载（DataLoader）+ 回测主流程（BacktestRunner）+ 复盘报表（BacktestReporter）
├── api/             # 可选的常驻 HTTP API 服务层（FastAPI）
├── logs/            # 运行日志
├── tests/           # 单元测试
├── main.py          # 一键 CLI 入口（数据库初始化 -> 数据接入 -> 回测 -> 复盘报表）
├── run_api.py        # 启动常驻 HTTP API 服务
├── run_tuning.py      # 参数网格扫描，寻找 Lead Time 中位数最大化的参数组合
└── run_regression_check.py  # 无损对比回归检查：验证参数回填没有引入逻辑回归
```

完整的分层职责说明、数据库表设计与开发进度，见 [`project_manifest.md`](./project_manifest.md)。

## 快速开始

```bash
# 安装依赖
python -m pip install -r requirements.txt

# 运行单元测试
python -m pytest tests/ -v

# 仅初始化本地 SQLite 数据库（不带 --data 时安全退出）
python main.py
```

### 方式一：一键命令行（跑完即退出，适合批处理/CI）

```bash
# （可选）抓取真实历史数据，需要先 pip install ccxt
python data/download_data.py

# 跑一次完整回测并自动打印复盘报告
python main.py --data crypto_market_daily.csv

# 清空并重新初始化数据库
python main.py --init-db
```

### 方式二：常驻 HTTP API 服务（进程一直监听端口，适合交互式调用）

```bash
python run_api.py
# 访问 http://127.0.0.1:8000/docs 查看交互式接口文档
```

```bash
curl -X POST http://127.0.0.1:8000/runs -H "Content-Type: application/json" \
     -d '{"data_source": "crypto_market_daily.csv"}'
curl http://127.0.0.1:8000/runs/{run_id}/report
```

### 方式三：交互式 Python 调用（Python Console / Jupyter，进程不退出，逐步查看中间结果）

```python
from backtest.data_loader import DataLoader
from backtest.runner import BacktestConfig, BacktestRunner
from backtest.report import BacktestReporter

data = DataLoader().load("your_wide_table.csv")  # 从 data/raw/ 加载
config = BacktestConfig(data_source="your_wide_table.csv")
run = BacktestRunner(data=data, config=config).run()
BacktestReporter(run_id=run.run_id).print_report()
```

### 参数调优：寻找 Lead Time 中位数最大化的参数组合

```bash
python run_tuning.py --data crypto_market_daily.csv
```

用真实 3 年 / 12 资产数据跑出的实测最优参数（`w_a=0.8, w_b=0.2,
hysteresis_window=2`）已固化为 `CCSDetector` / `StateMachineEngine`
的出厂默认值。

### 回归检查：验证参数回填没有引入逻辑回归

```bash
python run_regression_check.py --data crypto_market_daily.csv
```

并排对比"旧启发式默认（0.5/0.5/3）"与"新固化默认（不传参数，直接读
源码默认值）"的 Lead Time 中位数，真实数据上验证提升 3.0 -> 6.0 天。

## 当前状态

脚手架、数据库模型、CCS 探测算法、自适应状态机执行引擎、数据加载层、端到端回测
主流程、复盘审计报表（含 Lead Time 审计）、一键 CLI、常驻 HTTP API 服务、参数网格
扫描工具、真实历史数据下载工具、无损对比回归检查工具均已完成，且已用真实数据完成
一轮参数校准并回填为出厂默认值，沙盒闭环已打通，单元测试 32 项全部通过。
具体设计与下一步计划见 [`project_manifest.md`](./project_manifest.md) 的
"当前开发进度与下一步行动"章节。
