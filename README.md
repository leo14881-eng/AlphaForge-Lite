# AlphaForge-Lite

加密资产"非共识资本聚集探测器"量化验证沙盒。

## 最高目标

> 市场总会有资金去打造新的领导者，我们要做的不是预测，而是尽早识别资金
> 正在持续集中的资产，在领导优势形成阶段参与，在优势消失阶段退出，没有
> 高质量机会时保持现金。我们的目的是早于市场发现领导者，而不是等这个币
> 涨了很多才认为它是领导者。

项目的全部设计决策都围绕这一目标展开，详见 [`project_manifest.md`](./project_manifest.md)。

**诚实声明**：目前的 Lead Time / 回归检查等"效果良好"的结论，都只基于本地
一份静态历史数据的单次回测，没有做样本外（out-of-sample）验证、没有建模
真实交易成本，也没有和简单基线策略比较过——这是一个设计自洽的研究沙盒，
不是可以直接实盘的投资建议。详见 `project_manifest.md` 的诚实声明章节。

## 项目定位

- 基于本地静态 CSV / Parquet 历史时序大宽表做沙盒回测，不接入实时数据源
- 用 6 阶段状态机（`SEED -> DISCOVERY -> CONFIRMATION -> LEADERSHIP -> DISTRIBUTION -> EXIT`）
  刻画标的资产的"资金集中"生命周期
- 每一次状态判定都以"黑匣子"方式落库：不仅记录阶段迁移，还记录 CS 得分
  （资金集中度综合评分）与其组件拆解，保证可解释、可复盘
- Lead Time 审计直接回答"我们是否真正做到了早于市场发现领导者"，用数据
  而非自我宣称验证策略有效性
- 防洗盘量价背离三层过滤网：在崩溃/剧烈洗盘前夜，区分"庄家真出逃"与
  "高位缩量假摔洗盘"，只在真出逃时前瞻性强制清仓

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
├── config/          # 全局路径配置 + asset_profiles.py（CORE/MEME 资产画像分类权重）
├── state_machine/   # 生命周期阶段枚举、迁移规则、自适应状态机执行引擎（看门狗，含防洗盘量价背离三层过滤网）
├── database/        # peewee 表结构与连接管理
├── data/            # 原始/加工后的历史行情大宽表（不纳入版本管理）+ download_data.py 数据下载工具
├── detectors/       # CCS（Capital Convergence Score）探测算法
├── backtest/        # 数据加载（DataLoader）+ 回测主流程（BacktestRunner）+ 复盘报表（BacktestReporter）
├── api/             # 可选的常驻 HTTP API 服务层（FastAPI）
├── logs/            # 运行日志
├── tests/           # 单元测试
├── main.py                  # 一键 CLI 入口（数据库初始化 -> 数据接入 -> 回测 -> 复盘报表）
├── run_api.py                # 启动常驻 HTTP API 服务
├── run_tuning.py              # 参数网格扫描，寻找 Lead Time 中位数最大化的参数组合
├── run_regression_check.py     # 无损对比回归检查：验证参数回填没有引入逻辑回归
└── run_meme_stress_test.py      # 妖币/神币极限压力测试：15 资产池反生存者偏差审计
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
# 默认拉取主流资产池 + 妖币/神币资产池共 26 个交易对
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

具体天梯榜数字会随数据集时间范围变化而变化（详见 `project_manifest.md`
中关于"参数在不同数据窗口下不稳定"的诚实说明），当前出厂默认值
`w_a=0.8, w_b=0.2, hysteresis_window=2` 是某一轮真实数据校准的结果，
不代表在所有市场周期下都是全局最优。

### 回归检查：验证参数回填没有引入逻辑回归

```bash
python run_regression_check.py --data crypto_market_daily.csv
```

并排对比"旧启发式默认（0.5/0.5/3）"与"新固化默认（不传参数，直接读
源码默认值）"的 Lead Time 中位数与触发频次。

### 妖币/神币极限压力测试：反生存者偏差审计

```bash
python run_meme_stress_test.py --data crypto_market_daily.csv
```

对 15 个"史诗级"高爆发/剧烈洗盘/归零资产（含真实中文 symbol"币安人生"）
做 6×3=18 组极限参数网格扫描，横截面统计 Lead Time 与"主升浪核心段覆盖
完整度"，核心审判：哪组参数最能在崩溃前夜以最快前瞻天数强制清仓、
挽救利润。运行时会如实打印每个资产的真实数据覆盖区间（这批资产大多
2023-2024 年才上市，无法覆盖 2017-2019 年的早期周期）。

## 当前状态

脚手架、数据库模型、CCS 探测算法、自适应状态机执行引擎（含防洗盘量价背离
三层过滤网 + 多窗口共振投票）、数据加载层、端到端回测主流程、复盘审计报表、
一键 CLI、常驻 HTTP API 服务、参数网格扫描工具、真实历史数据下载工具
（主流 + 妖币资产池共 26 个交易对）、无损对比回归检查工具、妖币极限压力
测试工具、CORE/MEME 资产画像分类权重均已完成，且已用真实数据完成参数
校准并回填为出厂默认值，沙盒闭环已打通，单元测试 42 项全部通过。具体
设计、诚实的局限性说明与下一步计划见 [`project_manifest.md`](./project_manifest.md)
的相应章节。
