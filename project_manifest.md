# AlphaForge-Lite 项目清单（v0.95-Beta 快照 · 参数加固移交版）

> 项目代号：AlphaForge-Lite
> 定位：加密资产"非共识资本聚集探测器"量化验证沙盒
> 快照日期：2026-07-13
> 当前阶段：多窗口共振投票 + CORE/MEME 资产画像分类权重两项防御性加固已完成，新增 Java 执行端信号弹射通道 + live_monitor 实时监控子系统（纸上模拟/研究用途），单测 60 项全部通过
> 远程仓库：https://github.com/leo14881-eng/AlphaForge-Lite（`main` 分支）

**封版声明**：本版本标志着 AlphaForge-Lite 沙盒"功能性闭环"的完成——
从数据接入、CS 得分探测、状态机判定、参数寻优、参数固化、防洗盘量价
背离逃顶，到最后两项参数加固（多窗口共振投票、CORE/MEME 分类权重），
整条链路已经全部真实跑通并有测试覆盖。**"功能性封版"不等于"策略可
实盘"**，两者的区别见下方"诚实声明"章节，移交 Java 执行端后的下一步
重心应该从"加新功能"转向"验证有效性"（样本外验证），而不是继续
堆参数加固——加固能消除的是工程实现层面的脆弱性，消除不了方法论层面
"策略是否真的有效"这个尚未回答的问题。

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
   都最优。**v0.95-Beta 用 CORE/MEME 资产画像分类权重做了一个结构性
   缓解**（主流资产用 0.8/0.2，妖币资产用 0.05/0.95，见"参数加固"章节），
   但这只是把"按资产类别分组"这个维度纳入了参数选择，**没有解决"同一个
   资产在不同生命周期阶段可能需要不同权重"这个更细粒度的矛盾**（比如
   一个妖币资产在"尽早发现"阶段可能更适合 CORE 权重、在"逼近崩溃"阶段
   才切到 MEME 权重）——这仍然是一个尚未解决的方法论问题，只是把矛盾
   从"全局单一参数"降级成了"资产类别粒度的参数"，没有彻底消除。
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
- [x] **本轮新增：多窗口共振投票**（`state_machine/engine.py`，参数加固
      第一项）：`hysteresis_window` 单值设计改为 `divergence_windows`
      多窗口集合（出厂默认 `(2, 3, 4)`，Reviewer 要求至少覆盖 window=2
      与 window=4，额外补了 window=3 保证投票总数为奇数、多数语义
      明确），`_filter1_vote_for_window()` 对每个窗口独立投票，
      `_compute_price_volume_divergent()` 只有多数窗口同时判定为真，
      过滤网一才算通过——用多时间尺度共振消除单一窗口选择本身带来的
      过拟合风险。
- [x] **本轮新增：CORE/MEME 资产画像分类权重**（`config/asset_profiles.py`
      + `detectors/cs_score.py` + `backtest/runner.py`，参数加固第二项）：
      - 26 个资产显式分类为 `AssetClass.CORE`（12 个主流资产）/
        `AssetClass.MEME`（14 个妖币/神币，SOL 虽在 EPIC_POOL 下载清单
        里但按真实资产属性判定为 CORE）
      - `CCSDetector` 新增 `asset_weight_overrides: dict[str, tuple[float,
        float]] | None` 字段，`_compute_for_symbol` 按 symbol 查表决定
        实际使用的权重对，查不到则退回实例默认权重
      - `BacktestConfig` 的默认 `detector` 工厂函数
        `_default_detector()` 自动挂载
        `build_asset_weight_overrides()`（CORE: 0.8/0.2，MEME:
        0.05/0.95），`main.py`/`api/app.py` 走的生产默认路径不再是
        "一组参数通吃"；`run_tuning.py`/`run_regression_check.py`/
        `run_meme_stress_test.py` 这三个参数寻优/压力测试工具继续显式
        构造不带 overrides 的 CCSDetector，不受这个默认行为影响（它们
        的研究目的就是测试统一权重的整体表现）。
      - **过程中发现并修复一处真实 bug**：`_compute_for_symbol` 最初在
        `group = group.copy()` **之后**才读取 `group.name`，而 `.name`
        是 pandas 在 `groupby(...).apply()` 内部动态挂在原始分组对象
        实例上的属性，`.copy()` 产生的新对象不会带着这个属性——导致
        `AttributeError: 'DataFrame' object has no attribute 'name'`。
        用小样本手工验证时因为走了 pandas 内部的另一条代码路径而没有
        暴露，真实数据规模下才复现，已修复为在 `.copy()` 之前提前读取。
      - 用真实 26 资产数据（含中文 symbol"币安人生"）跑通 `main.py`
        默认路径验证：全部资产正常处理，无崩溃。
- [x] **本轮新增单测**：`tests/test_asset_profiles.py`（4 项，验证
      画像字典与权重覆盖表一致、CORE/MEME 权重方向相反、
      `BacktestConfig()` 默认路径确实挂载了覆盖表、同一份量价数据在
      不同 override 权重下算出不同 cs_score）+
      `tests/test_state_machine_engine.py` 新增 3 项（`_filter1_vote_
      for_window` 基础语义、多数票压过少数异议、少数票不构成多数则
      拒绝触发）。**单元测试总计 42 项全部通过**。

尚未实现（后续可选增强，非阻塞项）：

- [ ] **订单簿挂单失衡、微观价格行为分析未实现，且当前数据架构不支持**：
      本轮审计中 Reviewer 一度以为量价背离三层过滤网包含"订单簿挂单
      失衡""微观价格行为"，已明确纠正——项目数据源是 `ccxt` 拉取的
      Binance **日线** K 线（`fetch_ohlcv`），没有订单簿深度数据，也
      没有分钟/tick 级数据，这两项在当前数据架构下无法实现，需要接入
      订单簿或逐笔成交接口才能做，属于数据源级别的扩展，不是当前代码
      的疏漏。

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
扫描与回归检查、真实数据下载、参数固化），v0.9 新增：防洗盘量价背离三层
过滤网、15 资产妖币池极限压力测试工具、主流+妖币合并数据集（26 资产，
2017-2026），本轮（v0.95-Beta）新增：**多窗口共振投票**（消除单一窗口
过拟合风险）、**CORE/MEME 资产画像分类权重**（拒绝一组参数通吃，
`main.py`/`api/app.py` 默认生产路径自动按资产类别挂载权重），过程中
发现并修复一处 `group.name` 在 `.copy()` 之后失效的真实 bug。**单元
测试总计 42 项全部通过**。**持续维护一份诚实声明**，明确指出当前
"效果良好"的结论仍存在参数不稳定、多目标冲突（CORE/MEME 分类只是结构性
缓解，未彻底解决）、样本外验证缺失等尚未解决的方法论问题。

**下一次对话可以从这里开始：**

1. **决策：是否用 9 年窗口的新校准结果替换出厂默认值**（`hysteresis_window`
   从 2 改成 4？还是保留 2 作为"更保守/更少过拟合"的选择？）——这是一个
   需要 Reviewer 判断的取舍，不是纯技术问题。

2. **样本外验证框架**：滚动窗口切分（如用 2017-2023 数据调参，2024-2026
   数据验证），这是让"策略有效"这个结论真正立得住的关键一步。

3. **量价背离参数的独立校准**：`divergence_windows`/`divergence_confirm_streak`
   目前仍是本轮设定的默认值（`(2,3,4)`/2），尚未像 CS 权重那样做过网格
   扫描校准；`divergence_windows` 本身要不要也按 CORE/MEME 分类挂载
   不同窗口集合，也是一个待评估的问题。

4. **"早发现" vs "安全逃顶"的参数冲突**：v0.95-Beta 的 CORE/MEME 分类
   权重只是结构性缓解（按资产类别分组），没有解决"同一资产在不同生命
   周期阶段可能需要不同权重"这个更细粒度的矛盾，见"诚实声明"第 2 点。

5. **订单簿/微观结构分析**：如果 Java 执行端或后续迭代确实需要这个能力，
   需要先接入 Binance 订单簿深度或逐笔成交接口，是数据源级别的扩展，
   当前日线 K 线的数据架构不支持。

---

## 四、Java 执行端信号对接（本轮新增，v0.95-Beta 增补）

- [x] **新增 `integration/signal_launcher.py`**：Python 策略侧 -> Java
      执行引擎（`:8088`）的 HTTP 信号弹射模块，补上此前"Python 侧完全
      没有对外输出通道"的断层。
      - `SignalLauncher` 类：基于 `requests.Session` 连接池复用，
        `get_instance()` 提供线程安全的进程内单例（双重检查锁）。
      - `launch_signal(asset, signal_type, confirmed_windows=None,
        total_windows=None)` 模块级便捷函数，Payload 严格对齐 Java 侧
        DTO：`asset`、`signalType`（只允许 `DISCOVERY`/`EXIT`，非法值
        直接拒绝、不发起网络请求）、`confirmedWindows`、`totalWindows`。
      - 健壮性：`timeout=2.0` 秒硬性红线 + 超时/连接失败各重试 1 次
        （共 2 次尝试，最坏情况 ~4 秒后返回 `False`）；4xx/5xx 响应与
        未预期异常不重试、直接失败退出；**所有异常路径都只记日志、
        返回 `False`，绝不向调用方抛出**——已用真实网络请求（打一个
        没有服务监听的端口）验证：超时后老实返回 `False`，没有卡死、
        没有崩溃，耗时约 4.03 秒符合预期。
      - 单测 `tests/test_signal_launcher.py`（9 项）全程 mock 网络层，
        覆盖成功/非法信号类型/HTTP 错误状态码/超时重试/连接失败重试/
        重试后成功/未知异常不重试/单例复用等场景。
- [x] **单元测试总计 51 项全部通过**（42 + 本轮新增 9 项）。

**⚠️ 集成时的关键安全提醒（写入代码注释与本文件两处，务必不要忽略）**：

`launch_signal()` **绝不能直接接入 `backtest/runner.py::BacktestRunner`
的批量回测循环**。原因：`BacktestRunner` 是 `run_tuning.py` /
`run_meme_stress_test.py` / `run_regression_check.py` / `main.py --data`
等所有批量脚本共用的核心执行路径，一次调用会在几秒内逐行重放 2017-2026
年的历史数据、产生成千上万条状态迁移。如果把信号弹射直接挂在这个循环
里，**每一次历史回测/参数网格扫描都会把海量"历史重放信号"当成真实
信号打给 Java 执行引擎**——如果 Java 侧真的据此下单，后果是灾难性的。

`integration/signal_launcher.py` 本身只是一个可靠的"信号搬运工"，
**当前没有被接入代码库任何现有的调用路径**（不在 `backtest/runner.py`
里，也不在 `main.py`/`api/app.py` 里）。AlphaForge-Lite 的 Python 侧
截至本快照仍然是纯"本地静态历史数据回测"工具，**没有真实的实时策略
主循环**——这是一个需要先补上的架构缺口，而不是"随便找个地方插一行
调用"就能解决的事：真正的实时信号触发点应该是一个尚未实现的"live 主
循环"（订阅实时行情 -> 逐 tick 跑 `StateMachineEngine.update_asset_state`
-> 状态变为 `DISCOVERY`/`EXIT` 时调用 `launch_signal()`），而不是现有
的批量回测入口。是否要现在就实现这个 live 主循环，建议 Reviewer 先
确认再排期，避免和"沙盒回测 / 参数寻优"这两条已经封版的核心链路搅在
一起。

---

## 五、live_monitor 实时监控子系统（本轮新增，纸上模拟/研究用途）

**范围声明（务必先读）**：本轮用户提出的原始需求包含"Java 消费端实现
多 IP 代理池平滑下单，防止交易所 Rate Limit 封锁服务器 IP"——这一项
本质是绕过交易所反滥用/限流机制的规避手段，大概率违反 Binance 用户
协议，**已明确拒绝实现**，不在本子系统范围内。用户确认本子系统定位为
"纸上模拟/研究监控"，不接任何真实交易所下单接口，因此也没有实现
"Java 单线程顺序消费 + 真实下单 + 熔断死闸"这部分——这部分需要真实
下单场景才有意义，当前不适用。

- [x] **新增独立子系统 `live_monitor/`**：与 `backtest/` 物理隔离
      （不同数据库：`live_monitor` 用 MySQL/Redis，`backtest` 用
      peewee/SQLite；不共享调用路径），对应"项目里有没有定时/实时找
      领导者的逻辑、有没有把领导者产生/退出写库"这两个问题给出的答案
      ——此前完全空缺，本轮补上。
      - `live_monitor/schema.sql`：`strategy_signals`（热表，近 7 天）
        + `strategy_signals_archive`（冷表，同结构，存历史全量），
        `signal_uuid` 唯一键防重。
      - `live_monitor/market_monitor.py`：asyncio 常驻服务。
        - **高频合约主线**：`websockets` 订阅 Binance U 本位永续合约
          `trade` 原始逐笔成交流（**不是 aggTrade**——实测排障发现合约
          `aggTrade`/`markPrice@1s` 在当前网络环境被选择性限流，连续
          4 轮独立真实连接实验（combined URL、单流 `/ws/`、裸 `/ws` +
          显式 `SUBSCRIBE` 指令，BTC/ETH 双币种）稳定复现 0 条消息，而
          合约 `trade`、合约 `bookTicker`、现货 `aggTrade` 全部正常，
          已改用 `trade`，两种事件共享 `p/q/m/T` 字段，兼容）；
          `TickWindow`（`deque(maxlen=60)`）滚动维护短(5)/中(20)/长(60)
          tick 三档窗口，独立投票（价格方向 + 成交量超过自身基线均值
          1.8 倍才计入共振），多数（>=2/3）同向才算"合约触网"——是
          `state_machine.engine` 多窗口共振投票思路的实时/tick级简化版，
          不是同一套代码（CS 得分依赖日线 OHLCV 批量向量化计算，无法
          直接下沉到逐笔流处理）。
        - **低频现货防御**：`SpotLargeOrderCache` 独立维护现货
          `aggTrade` 流（实测正常，维持不变）的大单缓存（名义金额 >= 5
          万 USDT 才计入，只保留最近 10 秒），合约触网后回溯校验现货
          是否有同向真实大单，未确认则丢弃（不落库不广播），判定为
          "合约瞬时洗盘噪声"。
        - **动态波段 ID**：`signal_uuid = str(uuid.uuid4())`，不用"分钟
          时间戳拼接"，物理杜绝剧烈波段一分钟内二次变盘导致的漏单/误判重复。
        - **落库与广播**：`SignalSink` 用 `PooledDB` 连接池执行
          `INSERT IGNORE` 写 MySQL，`redis.xadd()` 写 Redis Stream
          （`stream:strategy:signals`），同时维护
          `leaders:active:{date}` / `leaders:exited:{date}` 两个 Redis
          Set 供大屏预聚合读取（`SCARD`/`SMEMBERS`，不用 MySQL 现算
          `COUNT`/`GROUP BY`）。
      - `live_monitor/archive_cold_data.py`：冷热分离归档脚本，"先复制
        到冷表、确认成功、再从热表删除"，避免归档失败丢数据；建议
        cron/任务计划每日执行。
      - `live_monitor/api.py`（FastAPI）：三个只读接口——
        `GET /api/v1/market/ticker`（代理 Binance 24hr 公开行情）、
        `GET /api/v1/signals/daily`（读 Redis 预聚合集合）、
        `GET /api/v1/signals/history`（`UNION ALL` 热表+冷表分页查询，
        保证冷热分离后历史全量数据依然可查）。
      - `live_monitor/static/dashboard.html`：暗黑系单文件大屏，含
        全市场行情看板（价格变动绿/红闪烁特效）、今日焦点矩阵（活跃
        领导者绿卡 / 今日退出者红卡）、历史信号流水分页表格，纯
        `fetch()` 轮询，无需构建工具链。
- [x] **单元测试** `tests/test_live_monitor.py`（9 项）：覆盖
      `TickWindow` 多窗口投票（含"长窗口被稀释、不足以单独触发"的边界
      场景）、`SpotLargeOrderCache` 大单确认与过期淘汰、
      `MarketMonitor` 的完整非对称过滤链路（合约触网无现货确认时丢弃、
      双重确认后落库广播、已是活跃领导者时不重复触发）——`SignalSink`
      用 `Mock` 替换，不需要真实 MySQL/Redis 即可测。
- [x] **单元测试总计 60 项全部通过**（51 + 本轮新增 9 项）。

**真实环境联调记录（订正此前说法）**：此前认为"沙盒环境无法长时间
维持到 Binance 的 WebSocket 连接"，实测证明是错的——现货流连续 33 秒
收到 95 条真实成交、合约 `bookTicker` 连续 25 秒收到 1 万+条，均无
断线。真实（而非猜测）的限制是上面提到的合约 `aggTrade`/`markPrice`
选择性限流，已修复。用生产代码 `MarketMonitor.run()` 本身（非探测
脚本）实测 15 秒：合约(trade) 收到 258 条、现货(aggTrade) 收到 55 条，
双路数据流确认打通。

**本机基础设施接入状态**：
  - MySQL：已接入真实本机实例（`localhost:3306`，`root/123456`，库名
    `alphaforge_lite`，与 Java 侧 `application.yml` 保持一致），实测
    连通，`alphaforge_lite` 库已存在。
  - **Redis：已升级完成并全链路真实验证**。本机原版本 `3.0.504`
    （2016 年 Windows 移植版，不支持 Stream）已卸载停用（`Set-Service
    Redis -StartupType Disabled`），换装 **Memurai Developer 4.1.2**
    （`winget install Memurai.MemuraiDeveloper`，Windows 原生服务，
    `redis_version` 报告 `7.2.5` 兼容），默认即为开机自启（`Automatic`）。
    首次 `winget install` 因端口 6379 仍被旧服务占用而以 MSI 1603 失败
    （日志明确写着 `port is in use by another application`），停掉旧
    服务后重装成功。
    - 已用真实 `redis-py` 客户端完整跑通消费组可靠性语义：
      `XADD` → `XGROUP CREATE` → `XREADGROUP`（模拟 Java 消费）→
      未 `XACK` 时 `XPENDING` 能看到挂起消息 → `XCLAIM`（模拟另一个
      消费者在原消费者崩溃后抢回消息，即断线重连补偿）→ `XACK` 确认 →
      `XPENDING` 归零。全部符合用户原规格"Java 消费端必须基于 Redis
      Stream 消费组可靠消费，完成下单后发送 ACK"的要求。
  - **MySQL：发现 `alphaforge_lite` 并非空库**，已包含 `users` /
    `positions` / `order_history` / `user_virtual_account` /
    `user_api_keys` / `user_strategies` 等真实 Java 应用表，确认是与
    Java 共用的正式库（不是测试库）。`live_monitor/schema.sql` 用的是
    `CREATE TABLE IF NOT EXISTS`，已安全执行，只新增
    `strategy_signals`/`strategy_signals_archive` 两张表，未触碰任何
    已有表。
  - **数据流向澄清（此前引发过一次误解，明确记录）**：`strategy_signals`
    /`strategy_signals_archive` 两张 MySQL 表**只做审计/大屏历史留痕，
    不是 Java 下单的读取源**；Java 下单必须消费 `stream:strategy:signals`
    这个 Redis Stream（消费组可靠消费 + ACK），这是双方已确认维持的
    原规格。写 MySQL 和写 Redis Stream 是两条独立旁路，互不阻塞——已用
    真实 `SignalSink.persist_and_broadcast()` 生产代码（非 mock）对着
    真实 MySQL + 真实 Memurai Redis 验证：DISCOVERY/EXIT 两条信号均
    正确落库、正确广播到 Stream、`leaders:active:{date}`/
    `leaders:exited:{date}` 两个 Redis Set 正确增删（先 DISCOVERY 后
    EXIT，活跃集合正确清空、退出集合正确出现该资产）。测试数据已清理，
    未在共用库里留下垃圾行。

**当前状态**：live_monitor 子系统的基础设施接入（MySQL 建表、Redis 升级
+ Stream 消费组）、合约数据源排障修复（aggTrade -> trade）均已完成真实
验证，60 项单测全部通过。尚未做的是长时间（数小时级）稳定性挂机观察，
以及真实 Java 消费端接入后的联调（Java 侧代码不在本仓库范围内）。

> 本清单将随每个迭代版本更新，作为 Chief Reviewer 审查项目进展的固定参照物。
