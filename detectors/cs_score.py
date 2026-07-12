"""
CCS（Capital Convergence Score，资金聚集得分）探测模块

这是项目【最高目标与三铁律】中"放弃预测、早于市场识别资金聚集"的
核心量化实现。CCSDetector 基于三个可解释的向量化子指标计算总分：

    组件 A · delta2_rs（二阶相对强度加速度）：
        以等权合成基准衡量个股相对强度 RS，取其 30 期滚动斜率的
        变化率（斜率的一阶差分，即离散意义上的二阶导数），捕捉
        "价格尚未明显异动、但相对强度已经开始非线性加速"的早期信号。

    组件 B · volume_delta（隐蔽放量指标）：
        并非成交量越大得分越高——用对数空间的高斯钟形函数，让得分
        在"温和放大"（默认约 2 倍均量）附近达到峰值，对"暴风骤雨式"
        的极端放量反而收窄评分，贴合"隐蔽建仓"而非"新闻驱动脉冲"的
        行为特征。

    组件 C · crowding_penalty（拥挤度惩罚项）：
        提取资金费率 / 换手率相对自身历史的极端程度，超过 1σ 的部分
        按指数曲线压制总分——这不是一个可加分量，而是作用在 A、B
        加权和之上的乘法压制系数，用于过滤"散户狂欢"的动量末端。

    总分公式：
        raw_convergence = w_a * delta2_rs + w_b * volume_delta
        cs_score = raw_convergence * crowding_penalty

三个子指标的值均落在 component_breakdown 中，保证黑匣子记录完整、
可审查、可复盘。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# 必需的原始行情列。funding_rate（资金费率）为可选列——现货类资产
# 通常没有资金费率，缺失时 crowding_penalty 仅基于换手率极端值计算。
REQUIRED_COLUMNS: tuple[str, ...] = ("timestamp", "symbol", "close", "volume", "turnover_rate")
OPTIONAL_FUNDING_RATE_COLUMN = "funding_rate"

COMPONENT_DELTA2_RS = "delta2_rs"
COMPONENT_VOLUME_DELTA = "volume_delta"
COMPONENT_CROWDING_PENALTY = "crowding_penalty"


@dataclass
class CCSDetector:
    """
    Capital Convergence Score 探测器。

    Attributes:
        rs_slope_window: 计算相对强度滚动斜率所用的窗口（默认 30，对应
            需求中"30 天相对强度"）。
        zscore_window: 对 delta2_rs 原始值做滚动 z-score 归一化的窗口。
        volume_window: 成交量基准均值的滚动窗口。
        volume_target_ratio: volume_delta 高斯钟形函数的峰值中心，即
            "最理想的温和放量倍数"，默认 2.0（当前成交量约为近期均量
            的 2 倍时得分最高）。
        volume_sigma: 高斯钟形函数在对数空间的展开宽度，越小则评分
            对偏离 target_ratio 越敏感。
        crowding_window: 计算资金费率 / 换手率历史均值与标准差的窗口。
        crowding_lambda: 拥挤度指数压制系数，excess_z 每超出 1 个单位，
            crowding_penalty 按 exp(-lambda) 衰减。
        weight_delta2_rs / weight_volume_delta: A、B 两个可加分量的权重，
            应满足 weight_delta2_rs + weight_volume_delta == 1。出厂默认值
            0.8 / 0.2 并非启发式拍脑袋，而是用 run_tuning.py 在真实历史
            数据（data/raw/crypto_market_daily.csv，3 年 / 12 资产）上做
            过完整网格扫描后的实测最优结果：weight_delta2_rs 权重越高，
            Lead Time 中位数越大——"相对强度斜率加速度"这个先行信号确实
            比"温和放量"更早于市场反应，直接对应最高目标"早于市场发现
            领导者"。详见 project_manifest.md v0.7/v0.8 快照的天梯榜记录。
    """

    rs_slope_window: int = 30
    zscore_window: int = 60
    volume_window: int = 20
    volume_target_ratio: float = 2.0
    volume_sigma: float = 0.6
    crowding_window: int = 60
    crowding_lambda: float = 1.5
    weight_delta2_rs: float = 0.8
    weight_volume_delta: float = 0.2

    @property
    def component_names(self) -> list[str]:
        return [COMPONENT_DELTA2_RS, COMPONENT_VOLUME_DELTA, COMPONENT_CROWDING_PENALTY]

    def to_params(self) -> dict:
        """导出构造参数，供 BacktestRun.param_snapshot 落库以保证可复现"""
        return {
            "rs_slope_window": self.rs_slope_window,
            "zscore_window": self.zscore_window,
            "volume_window": self.volume_window,
            "volume_target_ratio": self.volume_target_ratio,
            "volume_sigma": self.volume_sigma,
            "crowding_window": self.crowding_window,
            "crowding_lambda": self.crowding_lambda,
            "weight_delta2_rs": self.weight_delta2_rs,
            "weight_volume_delta": self.weight_volume_delta,
        }

    def calculate_cs(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        输入长表 df（列至少含 REQUIRED_COLUMNS，可选 funding_rate），
        按 symbol 分组向量化计算 CCS 总分与三个子指标，返回在原表基础上
        追加 delta2_rs / volume_delta / crowding_penalty / cs_score 四列
        的新 DataFrame（不修改入参）。

        相对强度基准说明：由于本地沙盒不额外提供大盘指数数据，基准采用
        同一张大宽表内所有 symbol 在每个时间戳上的等权平均收益率合成，
        属于自包含的简化处理，后续接入真实指数数据后可替换。
        """
        missing = set(REQUIRED_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"输入大宽表缺少必需列: {sorted(missing)}")

        working = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True).copy()
        working["_ret"] = working.groupby("symbol")["close"].pct_change()

        bench = (
            working.drop_duplicates("timestamp")[["timestamp"]]
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        bench_ret = working.groupby("timestamp")["_ret"].mean()
        bench["_bench_index"] = (1 + bench_ret.reindex(bench["timestamp"]).fillna(0).to_numpy()).cumprod()
        working = working.merge(bench, on="timestamp", how="left")

        working["_price_index"] = working.groupby("symbol")["close"].transform(lambda s: s / s.iloc[0])
        working["rs"] = working["_price_index"] / working["_bench_index"]

        result = working.groupby("symbol", group_keys=False).apply(
            self._compute_for_symbol, include_groups=False
        )
        result["symbol"] = working.sort_values(["symbol", "timestamp"])["symbol"].to_numpy()
        return result.drop(columns=["_ret", "_bench_index", "_price_index"]).reset_index(drop=True)

    def _compute_for_symbol(self, group: pd.DataFrame) -> pd.DataFrame:
        group = group.copy()

        # ------- 组件 A：delta2_rs（二阶相对强度加速度） -------
        rs_slope = self._rolling_ols_slope(group["rs"], self.rs_slope_window)
        delta2_rs_raw = rs_slope.diff()
        z_mean = delta2_rs_raw.rolling(self.zscore_window, min_periods=max(2, self.zscore_window // 2)).mean()
        z_std = delta2_rs_raw.rolling(self.zscore_window, min_periods=max(2, self.zscore_window // 2)).std()
        delta2_rs_z = (delta2_rs_raw - z_mean) / z_std.replace(0, np.nan)
        group[COMPONENT_DELTA2_RS] = self._sigmoid(delta2_rs_z).fillna(0.0)

        # ------- 组件 B：volume_delta（隐蔽放量指标，对数高斯钟形函数） -------
        vol_mean = group["volume"].rolling(self.volume_window, min_periods=max(2, self.volume_window // 2)).mean()
        vol_ratio = group["volume"] / vol_mean.replace(0, np.nan)
        log_ratio = np.log(vol_ratio.clip(lower=1e-6))
        log_target = np.log(self.volume_target_ratio)
        bump = np.exp(-((log_ratio - log_target) ** 2) / (2 * self.volume_sigma**2))
        # 未放量（vol_ratio <= 1）不给分，避免"缩量也能得分"的反直觉结果
        group[COMPONENT_VOLUME_DELTA] = bump.where(vol_ratio > 1, 0.0).fillna(0.0)

        # ------- 组件 C：crowding_penalty（拥挤度惩罚，超 1σ 部分指数压制） -------
        turnover_z = self._rolling_zscore(group["turnover_rate"], self.crowding_window)
        if OPTIONAL_FUNDING_RATE_COLUMN in group.columns:
            funding_z = self._rolling_zscore(group[OPTIONAL_FUNDING_RATE_COLUMN], self.crowding_window)
            extreme_z = pd.concat([turnover_z.abs(), funding_z.abs()], axis=1).max(axis=1)
        else:
            extreme_z = turnover_z.abs()
        excess = (extreme_z - 1.0).clip(lower=0.0).fillna(0.0)
        group[COMPONENT_CROWDING_PENALTY] = np.exp(-self.crowding_lambda * excess)

        # ------- 总分：A、B 加权和 × 拥挤度惩罚系数 -------
        raw_convergence = (
            self.weight_delta2_rs * group[COMPONENT_DELTA2_RS]
            + self.weight_volume_delta * group[COMPONENT_VOLUME_DELTA]
        )
        group["cs_score"] = raw_convergence * group[COMPONENT_CROWDING_PENALTY]
        return group

    @staticmethod
    def _rolling_ols_slope(y: pd.Series, window: int) -> pd.Series:
        """
        滚动窗口一元线性回归斜率的闭式解（全向量化，不逐窗口调用 numpy.polyfit）。

        对等间隔样本 x = 0..window-1，标准 OLS 斜率为：
            slope = (w * sum(xy) - sum(x) * sum(y)) / (w * sum(x^2) - sum(x)^2)
        利用全局位置索引 i 与窗口起点 start = i - w + 1 的关系：
            sum(x*y in window) = sum(i*y in window) - start * sum(y in window)
        因此只需对 y 与 (i*y) 分别做滚动求和，即可换算出滚动斜率，
        整个过程全部由 pandas rolling().sum() 完成，O(n) 复杂度。
        """
        values = y.to_numpy(dtype=float)
        n = len(values)
        idx = np.arange(n, dtype=float)
        iy_series = pd.Series(idx * values, index=y.index)
        y_series = pd.Series(values, index=y.index)

        sum_y = y_series.rolling(window).sum()
        sum_iy = iy_series.rolling(window).sum()

        w = float(window)
        sum_x = w * (w - 1) / 2
        sum_x2 = w * (w - 1) * (2 * w - 1) / 6
        denom = w * sum_x2 - sum_x**2

        start_idx = pd.Series(idx - w + 1, index=y.index)
        sum_xy = sum_iy - start_idx * sum_y
        return (w * sum_xy - sum_x * sum_y) / denom

    @staticmethod
    def _rolling_zscore(s: pd.Series, window: int) -> pd.Series:
        mean = s.rolling(window, min_periods=max(2, window // 2)).mean()
        std = s.rolling(window, min_periods=max(2, window // 2)).std()
        return (s - mean) / std.replace(0, np.nan)

    @staticmethod
    def _sigmoid(z: pd.Series) -> pd.Series:
        return 1.0 / (1.0 + np.exp(-z.clip(-10, 10)))
