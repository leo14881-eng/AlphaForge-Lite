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

## 技术栈

- Python 3.10+
- [peewee](http://docs.peewee-orm.com/)（轻量级 ORM，底层 SQLite）
- pandas / pyarrow（CSV / Parquet 大宽表读取）
- pytest（单元测试）

## 目录结构

```
AlphaForge-Lite/
├── config/          # 全局路径配置
├── state_machine/   # 生命周期阶段枚举、迁移规则、自适应状态机执行引擎（看门狗）
├── database/        # peewee 表结构与连接管理
├── data/            # 原始 / 加工后的历史行情大宽表（不纳入版本管理）
├── detectors/       # CCS（Capital Convergence Score）探测算法
├── backtest/        # 回测引擎（数据加载、runner、report，待实现）
├── logs/            # 运行日志
├── tests/           # 单元测试
└── main.py          # 入口脚本
```

完整的分层职责说明、数据库表设计与开发进度，见 [`project_manifest.md`](./project_manifest.md)。

## 快速开始

```bash
# 安装依赖
python -m pip install -r requirements.txt

# 运行单元测试
python -m pytest tests/ -v

# 初始化本地 SQLite 数据库
python main.py
```

## 当前状态

脚手架、数据库模型、CCS（Capital Convergence Score）探测算法、自适应状态机执行引擎
均已完成并有单元测试覆盖。数据加载层与端到端回测流程（backtest/runner.py、
backtest/report.py）尚未实现，具体计划见 [`project_manifest.md`](./project_manifest.md)
的"当前开发进度与下一步行动"章节。
