# AlphaForge-Lite 项目清单（v0.9 快照 · 完备闭环版）

> 项目代号：AlphaForge-Lite
> 定位：加密资产"非共识资本聚集探测器"量化验证沙盒
> 快照日期：2026-07-13
> 当前阶段：防洗盘量价背离三层过滤网 + 状态机逃顶通道完整打通，15 资产妖币池极限压力测试已交付，单测 35 项全部通过，沙盒功能性封版
> 远程仓库：https://github.com/leo14881-eng/AlphaForge-Lite（`main` 分支）

**封版声明**：本版本标志着 AlphaForge-Lite 沙盒"功能性闭环"的完成——
从数据接入、CS 得分探测、状态机判定、参数寻优、参数固化，到最后一块
拼图"防洗盘量价背离逃顶"，整条链路已经全部真实跑通并有测试覆盖。
"功能性封版"不等于"策略可实盘"，两者的区别见下方"诚实声明"章节，
后续迭代的重心应该从"加新功能"转向"验证有效性"。

---

## 〇、最高目标与三铁律（项目最高优先级原则，任何设计决策不得违背）

> 市场总会有资金去打造新的领导者，我们要做的不是预测，而是尽早识别资金
> 正在持续集中的资产，在领导优势形成阶段参与，在优势消失阶段退出，没有
> 高质量机会时保持现金。我们的目的是早于市场发现领导者，而不是等这个币
> 涨了很多才认为它是领导者。

本轮交付回答了最高目标里最容易被回避的一半——"优势消失阶段退出"：
`state_machine/engine.py` 新增的量价背离三层过滤网，让系统第一次有能力
区分"庄家真出逃"与"高位缩量假摔洗盘"，并用 15 个真实的高爆发/剧烈洗盘/
归零资产（含 LUNA 死亡螺旋、真实中文 symbol"币安人生"）验证了这套机制。

---

## 〇.五、诚实声明（请务必先读这一节）

Reviewer 在本轮会话中问过"这个项目能实现目标吗"，答案需要拆成两半看：

**工程目标已经做到**：CS 得分计算、状态机判定、回测执行、复盘审计、参数
寻优、真实数据接入、防洗盘逃顶，整条链路都是真实闭环运行的，不是伪代码
或纸面设计。

**"策略有效性"这半个目标还没有被证明**，以下是本轮暴露出来的具体证据，
不回避、不美化：

1. **参数在不同数据窗口下不稳定**：v0.7/v0.8 用 2023-2026（3 年）的
   12 资产数据跑出的最优参数是 `w_a=0.8, w_b=0.2, hysteresis_window=2`；
   本轮把同一份数据的时间窗口扩展到 2017-2026（9 年）后，重新跑
   `run_tuning.py`，最优组合变成了 `w_a=0.8, w_b=0.2, hysteresis_window=4`
   （Lead Time 中位数从旧窗口的某个值变成新窗口下的 6.0 天，具体见下方
   "参数网格扫描"小节的完整天梯榜）——**同一个"最优参数"在换了一个时间
   窗口后就不再最优**，这是过拟合/参数不稳定的直接证据，不是巧合。
2. **不同目标对应不同的"最优参数"**：`run_tuning.py`（优化"尽早发现"）
   和 `run_meme_stress_test.py`（优化"崩溃前逃顶挽救利润"）在同一份数据
   上跑出的最优权重方向几乎相反——前者偏爱 `w_a=0.8`（重相对强度信号），
   后者在崩溃资产样本上偏爱 `w_a=0.05`（重温和放量信号）。这说明"早于
   市场发现"和"安全逃顶"可能需要两套不同参数，不存在一组参数两个目标
   都最优，本项目目前没有为这个矛盾提供解法。
3. **调参和验证仍用的是同一份数据**：`run_tuning.py`／
   `run_meme_stress_test.py` 找到"最优参数"，`run_regression_check.py`
   又在同一批数据上验证效果好，没有做样本外（out-of-sample）验证，不能
   排除"调出来正好在这份数据上好看"。
4. **没有交易成本、执行现实性建模**：Lead Time / 覆盖完整度都是基于收盘价
   的理论指标，没有考虑滑点、手续费，更没有考虑"币安人生"这类新币真实
   下单能否成交在理想价位。
5. **LUNA 数据存在真实的"接续断档"**：2022-05-13 前后，Binance 在同一个
   `LUNA/USDT` symbol 下，从原始 Terra LUNA（崩盘归零）接续到了
   2022-05-28 上线的 Terra 2.0 新代币——2022-05-13 之后的"价格回升"不是
   原币复活，是两个不同资产的价格拼接。本项目未对此做特殊清洗，如实
   使用原始数据，仅在此处与代码注释中说明。

**结论**：这是一个设计良好、逻辑自洽、可持续迭代的研究沙盒，还不是可以
直接指导实盘的策略。下一步的关键不是继续堆功能，而是做样本外验证。

---

## 一、项目当前状态

- [x] 项目目录结构、状态机常量、peewee 数据库模型、独立 git 仓库、README
- [x] `CCSDetector`、`StateMachineEngine`、`DataLoader`、`BacktestRunner`、
      `BacktestReporter`，端到端沙盒回测闭环
- [x] `main.py` 一键 CLI、`api/app.py` 常驻 HTTP 服务、`run_tuning.py`
      参数网格扫描、`run_regression_check.py` 无损对比回归检查、
      `data/download_data.py` 真实数据下载工具（详见 v0.6-v0.8 快照）
- [x] **本轮新增：`state_machine/engine.py` 防洗盘量价背离三层过滤网**
      （`price_volume_divergent`，由引擎内部根据 `close`/`delta2_rs`/
      `crowding_penalty` 自行计算，不再由调用方外部传入布尔标记）：
      - **过滤网一（绝对价格与相对强度双审计）**：最近
        `divergence_window`（默认 3）个时间步内，`close` 创出局部新高，
        但 `delta2_rs` 必须从非负值持续单调断崖式下穿零轴——相对强度
        大趋势没崩，判定为"庄家假砸盘洗盘"，不触发，保持静默持股。
      - **过滤网二（拥挤度共振）**：最终判定时还需与 `crowding_penalty`
        的连续告警状态共振（复用既有 `_crowding_streak` 机制）——换手率
        没有连续触发高危报警，判定为"高位缩量洗盘"，直接拒绝触发。
      - **过滤网三（状态机平滑迟滞）**：过滤网一的原始信号需连续
        `divergence_confirm_streak`（默认 2）个时间步为真，避免单点噪声。
      - **工程说明（重要）**：过滤网三的"连续 N 期"要求刻意只套在过滤网
        一的信号上，而不是套在"一二组合结果"上——如果套在组合结果上，
        会和既有的"纯拥挤度持续触发"机制互相抢跑（拥挤度刚满足连续告警
        的那一刻，纯拥挤度机制会抢先把资产降级到 DISTRIBUTION，量价背离
        永远等不到自己的确认窗口走完，变成死代码）。调整后两套机制在
        同一时间步同时满足条件，量价背离因判定优先级更高而胜出，得到
        更果断的直接 EXIT，而不是被抢先降级。详见
        `_compute_price_volume_divergent` 的完整实现注释。
      - 一旦确认，且资产处于 **DISCOVERY 或 LEADERSHIP**（持仓阶段），
        允许打破常规迟滞，直接前瞻性强制迁移至 **EXIT**（不再像纯拥挤度
        机制那样先退到 DISTRIBUTION）。
      - `backtest/runner.py` 已同步更新：把 `close`/`delta2_rs` 传入
        `StateMachineEngine.update_asset_state()` 的 `current_metrics`，
        生产链路完全走引擎内部真实计算，不使用外部覆盖旁路
        （旁路仅保留给单测场景）。
- [x] **本轮新增：`run_meme_stress_test.py`** —— 15 资产妖币/神币池
      极限压力测试：
      - **EPIC_POOL**（15 个）：GALA, AXS, WIF, FLOKI, LUNA, BONK, PEPE,
        SOL, TIA, SUI, **币安人生**, ACT, GOAT, PNUT, MOODENG。
      - **中文 symbol 健壮性**：`币安人生` 是 Binance 上真实存在的现货
        交易对（base symbol 本身就是中文，已用 ccxt 实测确认
        `active=True`），`DataLoader` 读 CSV 时显式声明
        `encoding="utf-8-sig"`，避免 Windows 默认代码页猜错导致中文
        symbol 静默损坏或匹配失败。
      - **异常隔离**：`_resolve_epic_symbols_present()` 对资产池逐一核对
        是否存在于数据集中，缺失的资产打印警告后跳过，不中断整体扫描
        （覆盖 ACT/GOAT/PNUT/MOODENG/币安人生这类近期上线、早期历史缺失
        的资产）。
      - **极限光谱网格**：权重固定六档配对
        `(0.95,0.05)/(0.9,0.1)/(0.8,0.2)/(0.5,0.5)/(0.2,0.8)/(0.05,0.95)`
        × `hysteresis_window` 三档 `(1,2,3)`，共 18 组组合，全部静默执行。
      - **反归零专属看板**：横截面统计"妖币池 Lead Time 中位数"与自定义
        指标"主升浪核心段覆盖完整度"（= (退出价-入场价)/(区间峰值价-
        入场价)，1.0 = 精准逃在峰值附近，0 = 涨幅基本还光，负数 = 亏得
        比入场价还惨）；核心审判排序键是"经历过 ≥50% 峰值回撤的崩溃资产
        样本"上的覆盖完整度中位数，打平时用样本数量做次级排序（样本越多
        越可信，不是巧合命中一两个资产）。
      - **真实数据覆盖情况**（如实打印，不做美化）：EPIC_POOL 15 个资产
        没有一个早于 2020 年就已上市（LUNA/SOL/AXS 最早追溯到 2020 年
        下半年，多数 meme 2023-2024 年才上市，"币安人生"仅 2026-01 以来
        6 个月数据），因此无法覆盖 2017 牛市/2018 冰封；但天然覆盖了
        2021 年"5·19"闪崩与 2022 年 LUNA 死亡螺旋这两个最具代表性的
        极端事件。**这个缺口来自真实市场历史事实，不是脚本的 bug**——
        数据集里的 BTC/ETH/BNB/ADA/TRX 等主流资产池成员确实早在
        2017-2018 年就已上市，只是 EPIC_POOL 这 15 个被点名的资产恰好
        都是"年轻资产"。
- [x] **本轮：`data/download_data.py` 扩展为主流资产池 + 妖币资产池**：
      - `MAINSTREAM_SYMBOLS`（12，原有）+ `EPIC_POOL_SYMBOLS`（15，新增，
        与 SOL 去重后合计 14 个新增）= 默认下载 26 个交易对。
      - GOAT / MOODENG 在 Binance 现货没有挂牌，只有 USDT 本位永续合约
        （`GOAT/USDT:USDT`），`_resolve_tradable_symbol()` 实现现货优先、
        期货兜底的符号解析，两者都不存在才跳过（异常隔离）。
      - `DEFAULT_START` 从 `2023-01-01` 改为 `2017-01-01`（诚实地"尝试"
        拉取完整周期，实际截断由各资产真实上市日期决定，ccxt 自动处理，
        脚本运行结束会打印每个资产的真实数据起止时间）。
      - 写 CSV 时显式声明 `encoding="utf-8-sig"`，配合 `DataLoader` 的
        读取端声明，端到端保证中文 symbol 不会被损坏。
      - **本轮已实际执行下载**：`data/raw/crypto_market_daily.csv` 现为
        26 个资产、48419 行，BTC/ETH 覆盖至 2017-08-17，LUNA 覆盖
        2020-08-21 起（含 2022 年死亡螺旋），币安人生覆盖 2026-01-07 起。
- [x] **本轮：`run_tuning.py` / `run_regression_check.py` 一致性修复**：
      - 显式新增模块级常量 `MAINSTREAM_SYMBOLS`（12 个，与
        `data/download_data.py` 的定义一致），在生产入口 `main()` 里
        显式传给 `_run_one_combo`/`_run_experiment` 的新增 `symbols`
        参数，避免 `crypto_market_daily.csv` 扩容到 26 资产后，这两个
        脚本的历史校准结果被意外混入妖币池数据而悄悄失真。
      - `symbols` 参数默认 `None`（不过滤），修复了一处真实 bug：
        本轮最初实现直接把 `MAINSTREAM_SYMBOLS` 硬编码进
        `_run_one_combo`/`_run_experiment` 函数体内部，导致这两个函数
        被单测用合成数据（symbol 名不在主流名单里）调用时，数据被过滤
        成空表，落库时 `NaT` 时间戳绑定失败，5 项单测报错——已改为
        参数化，生产入口显式传参、测试保持不传参两种用法互不干扰。
      - 用新的 26 资产数据集（时间窗口从 3 年扩展到 9 年）重新跑了两个
        脚本，**新数字与 v0.7/v0.8 快照记录的数字不同**（这是预期变化，
        不是回归——数据集本身变了，见下方"参数网格扫描"小节的完整对比）。
- [x] **`tests/test_divergence.py`**（3 项，高压力仿真，真实喂入
      close/delta2_rs/crowding_penalty 序列走三层过滤网真实计算路径，
      不用显式覆盖值走捷径）：
      - `test_divergence_fake_washout_holds_running_position`："庄家恶性
        假砸盘洗盘"（价格仍创新高但动能已经在走弱）：过滤网一原始信号
        为真，但换手率全程不拥挤（过滤网二不通过），系统必须死守
        LEADERSHIP 不被骗出场。
      - `test_divergence_real_breakdown_forces_exit_before_crash`：模拟
        类 LUNA/"币安人生"式真实崩溃前夜（价格新高+相对强度加速度崩溃+
        高换手拥挤），三层过滤全部确认后，打破常规迟滞，前瞻性强行切入
        EXIT。
      - `test_divergence_violent_down_spike_shakeout_holds_position`
        （本轮补充）："庄家暴力砸盘式恶性洗盘"（价格直接暴砸+相对强度
        未崩+低换手缩量）——与第一项测试是两种不同的洗盘手法：这里价格
        根本不是"创新高"，过滤网一的前提条件从源头就不成立，系统同样
        必须稳稳持仓。
      - 同时更新 `tests/test_state_machine_engine.py` 里旧的
        `test_price_volume_divergence_forces_exit_from_distribution`
        为 `test_price_volume_divergence_override_forces_exit_from_leadership`
        ——量价背离强制退出的适用范围从"LEADERSHIP/DISTRIBUTION"收窄为
        "DISCOVERY/LEADERSHIP"（持仓阶段字面定义），DISTRIBUTION 阶段的
        退出仍由纯拥挤度机制或常规阈值负责。
- [x] 单元测试总计 **35 项全部通过**（较 v0.8 的 32 项：净减 1 项旧测试
      改名复用、新增 3 项高压力仿真测试，覆盖两种不同的洗盘手法 + 一种
      真实出逃场景）

尚未实现（后续可选增强，非阻塞项）：

- [ ] 出厂默认参数尚未针对本轮 9 年窗口重新校准（当前仍是 v0.8 固化的
      `w_a=0.8, w_b=0.2, hysteresis_window=2`，本轮新窗口下网格扫描找到
      的最优其实是 `hysteresis_window=4`，尚未决定是否需要再次回填——
      详见"诚实声明"第 1 点，这里涉及取舍判断，留给 Reviewer 决策而非
      由本次会话单方面再次改动出厂默认值）
- [ ] 样本外（out-of-sample）验证框架：滚动窗口训练/验证分割，是本项目
      从"设计自洽"迈向"可信"最关键的下一步
- [ ] `POST /runs` 是同步阻塞调用，数据量变大后如需异步化需要评估任务队列

---

## 二、架构说明

```
AlphaForge-Lite/
├── config/
├── state_machine/
│   ├── constants.py
│   └── engine.py               # +v0.9: 防洗盘量价背离三层过滤网
├── database/
├── data/
│   ├── raw/                    # crypto_market_daily.csv 现为 26 资产/48419 行
│   ├── processed/
│   └── download_data.py        # +v0.9: 妖币池、中文symbol、期货兜底、2017起始
├── detectors/
│   └── cs_score.py
├── backtest/
│   ├── data_loader.py          # +v0.9: 显式 utf-8-sig 编码
│   ├── runner.py               # +v0.9: 传入 close/delta2_rs 给状态机
│   └── report.py
├── api/
│   └── app.py
├── logs/
├── tests/
│   ├── test_state_machine.py
│   ├── test_state_machine_engine.py   # 更新：divergence override 测试改名
│   ├── test_divergence.py             # +v0.9: 高压力仿真测试（新文件）
│   ├── test_models.py
│   ├── test_cs_score.py
│   ├── test_backtest_pipeline.py
│   ├── test_api.py
│   ├── test_run_tuning.py             # 更新：symbols 参数化
│   ├── test_regression_check.py       # 更新：symbols 参数化
│   └── test_download_data.py
├── main.py
├── run_api.py
├── run_tuning.py                 # +v0.9: MAINSTREAM_SYMBOLS 显式限定
├── run_regression_check.py        # +v0.9: MAINSTREAM_SYMBOLS 显式限定
├── run_meme_stress_test.py         # +v0.9: 新文件，妖币极限压力测试
├── requirements.txt
├── .gitignore
├── README.md
└── project_manifest.md
```

### 分层职责与依赖方向（本轮无变化）

| 模块 | 职责 | 依赖方向 |
|---|---|---|
| `config` | 提供路径等基础配置 | 被所有模块依赖 |
| `state_machine` | 阶段枚举、迁移规则、状态机执行引擎（含量价背离三层过滤网） | 不依赖 `database`（`StageLookup` 协议解耦） |
| `database` | peewee 表结构与连接管理 | 依赖 `state_machine` |
| `detectors` | 计算 CCS 得分与组件拆解 | 纯 pandas/numpy |
| `backtest` | 数据加载 + 回测主流程 + 复盘报表 | 依赖 `detectors`、`state_machine`、`database` |
| `api` | 常驻 HTTP 服务 | 依赖 `backtest` |
| `main.py` / `run_tuning.py` / `run_regression_check.py` / `run_meme_stress_test.py` / `data/download_data.py` | 五个独立命令行入口 | 均只依赖既有模块，互相之间不依赖 |

### 参数网格扫描：新旧数据窗口对比（诚实记录，不掩盖不稳定性）

**v0.7/v0.8 快照**（2023-2026，3 年，12 主流资产）：最优组合
`w_a=0.8, w_b=0.2, hysteresis_window=2`，Lead Time 中位数 6.0 天（网格
扫描）；回归检查 3.0 天 -> 6.0 天（+100%）。

**v0.9 本轮**（2017-2026，9 年，同 12 主流资产，`symbols` 显式限定后
重新跑）：

| rank | w_a | w_b | Hysteresis | Lead Time Median | Trigger Count |
|---|---|---|---|---|---|
| 1 | 0.8 | 0.2 | 4 | **6.0** | 1734 |
| 2 | 0.6 | 0.4 | 3 | 5.0 | 1881 |
| 3 | 0.8 | 0.2 | 2 | 4.5 | 2651 |
| 4 | 0.8 | 0.2 | 3 | 3.5 | 2082 |
| ... | | | | | （完整 12 组见 `run_tuning.py` 实际输出） |

回归检查（新窗口）：旧启发式 `0.5/0.5/3` = 3.5 天，新固化默认
`0.8/0.2/2` = 4.5 天（+28.6%，仍然通过，但幅度与 v0.8 记录的 +100% 不同）。

**结论**：`w_a=0.8, w_b=0.2` 这个权重方向在两个窗口下都表现不错，说明
组件 A（相对强度加速度）权重更高这个大方向可能有一定稳健性；但
`hysteresis_window` 的最优值从 2 变成了 4，说明这个参数对数据窗口更敏感，
出厂默认值该不该跟着新窗口重新固化，是个需要 Reviewer 权衡的决策
（见"尚未实现"第一条），本轮不擅自替 Reviewer 做这个决定。

### 妖币压力测试完整天梯榜（18 组合，`data/raw/crypto_market_daily.csv`）

| rank | w_a | w_b | Hysteresis | 妖币LeadTime中位数 | 全体覆盖完整度中位数 | 崩溃资产覆盖完整度中位数 | 崩溃资产数 |
|---|---|---|---|---|---|---|---|
| 1 | 0.05 | 0.95 | 2 | 1.0 | 1.000 | **1.000** | 8 |
| 2 | 0.50 | 0.50 | 2 | 0.0 | 0.838 | 1.000 | 5 |
| 3 | 0.80 | 0.20 | 2 | 1.0 | 1.000 | 0.995 | 10 |
| 4 | 0.20 | 0.80 | 2 | 1.0 | 0.967 | 0.967 | 8 |
| 7 | 0.90 | 0.10 | 2 | 2.0 | 1.000 | 0.893 | 10 |
| ... | | | | | | | （完整 18 组见 `run_meme_stress_test.py` 实际输出） |

**核心审判结论**：崩溃资产覆盖完整度中位数最高、且样本数够多（8 个真实
经历过 ≥50% 回撤的崩溃资产）的组合是 `w_a=0.05, w_b=0.95,
hysteresis_window=2`——覆盖完整度中位数 1.00，意味着这批崩溃资产平均
恰好在峰值附近离场。**关键发现**：这个"逃顶最优"权重（重仓组件 B 温和
放量）与"尽早发现最优"权重（`run_tuning.py` 得出的 `w_a=0.8`，重仓组件
A 相对强度）方向几乎相反——本项目目前没有一组参数能同时把"早发现"和
"安全逃顶"都做到最优，详见"诚实声明"第 2 点。

### 数据完整性说明：LUNA 的"接续断档"

`data/raw/crypto_market_daily.csv` 里 `LUNAUSDT` 的价格序列在
2022-05-13 前后存在真实断档：2022-05-01 收盘 $82.23，5 天内跌至
2022-05-12 收盘 $0.00032（Terra 原始 LUNA 死亡螺旋，真实历史事件），但
2022-05-13 之后价格"回升"到个位数并延续至今——这不是原币复活，是
Binance 在同一 symbol 下接续了 2022-05-28 上线的 Terra 2.0 新代币。
本项目未做拆分清洗，如实使用 Binance 原始数据。

---

## 三、当前开发进度与下一步行动

**已完成（截至本次会话，2026-07-13）：** v0.1-v0.8 的全部内容（脚手架、
CCS 探测算法、状态机核心、端到端回测闭环、一键 CLI、常驻 API、参数网格
扫描与回归检查、真实数据下载、参数固化），本轮新增：防洗盘量价背离三层
过滤网、15 资产妖币池极限压力测试工具、主流+妖币合并数据集（26 资产，
2017-2026）、`run_tuning.py`/`run_regression_check.py` 的 symbols 一致性
修复；补充第三项高压力仿真测试（暴力砸盘式洗盘）。单元测试 35 项全部
通过。**特别交付了一份诚实声明**，明确指出当前
"效果良好"的结论存在参数不稳定、多目标冲突、样本外验证缺失等尚未解决的
方法论问题。

**下一次对话可以从这里开始：**

1. **决策：是否用 9 年窗口的新校准结果替换出厂默认值**（`hysteresis_window`
   从 2 改成 4？还是保留 2 作为"更保守/更少过拟合"的选择？）——这是一个
   需要 Reviewer 判断的取舍，不是纯技术问题。

2. **样本外验证框架**：滚动窗口切分（如用 2017-2023 数据调参，2024-2026
   数据验证），这是让"策略有效"这个结论真正立得住的关键一步。

3. **量价背离参数的独立校准**：`divergence_window`/`divergence_confirm_streak`
   目前仍是本轮设定的默认值（3/2），尚未像 CS 权重那样做过网格扫描校准。

4. **"早发现" vs "安全逃顶"的参数冲突**：是否需要拆成两套独立参数
   （比如允许 CS 权重和量价背离阈值分开配置，而不是共用同一套
   `CCSDetector` 实例），需要先想清楚业务上是否允许"一套参数负责发现，
   另一套负责逃顶"这种分工。

> 本清单将随每个迭代版本更新，作为 Chief Reviewer 审查项目进展的固定参照物。
