"""
AlphaForge-Lite HTTP API 服务层

把 backtest.runner.BacktestRunner / backtest.report.BacktestReporter
包装成常驻的 HTTP 接口，进程启动后监听端口、持续接收请求，供习惯
"服务常驻、调接口"工作方式的调用方使用（用 `python run_api.py` 或
`uvicorn api.app:app` 启动，不会像 main.py 那样跑完就退出）。

v1 定位说明：本服务是同步阻塞式的薄封装——POST /runs 会在请求内
同步跑完整个回测。本项目面向本地静态大宽表的小规模沙盒场景，单次
回测通常在秒级到分钟级，暂不引入任务队列；数据规模变大后如需
异步化，可在此基础上接入 Celery/RQ，不影响其它模块。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from backtest.data_loader import DataLoader
from backtest.report import BacktestReporter
from backtest.runner import BacktestConfig, BacktestRunner
from database.models import BacktestRun
from database.session import init_db


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(
    title="AlphaForge-Lite API",
    description="非共识资本聚集探测器 —— 回测执行与复盘审计的 HTTP 接口",
    version="0.6.0",
    lifespan=_lifespan,
)


class RunRequest(BaseModel):
    """POST /runs 的请求体：指定数据源与回测配置"""

    data_source: str  # 文件名，只能解析到 data/raw/ 目录内，不接受绝对路径或越界的相对路径
    strategy_name: str = "non_consensus_accumulation"
    strategy_version: str = "v1"
    symbols: list[str] | None = None  # 不指定则使用数据中出现的全部资产


def _load_data(data_source: str):
    """
    把 HTTP 请求里的 data_source 严格约束在 RAW_DATA_DIR 目录内，不能读取
    任意路径的文件。

    【安全修复记录】此前实现是"只要 path.exists() 就放行走 load_path()
    （不受目录限制的任意路径加载）"——只要调用方传入的相对路径恰好在
    进程工作目录下存在（如 "../../../etc/passwd" 之类的路径穿越写法），
    校验形同虚设，能绕过目录限制读取任意可解析的表格文件。旧版
    DataLoader.load_path() 本身是给"受信任的本地脚本/CLI 直接指定任意
    路径"用的，不应该被 HTTP 端点直接暴露——HTTP 请求方是不受信任的
    输入来源，跟本地脚本调用是两种不同的信任边界。现在统一解析到
    RAW_DATA_DIR 内部，用 Path.resolve() + relative_to() 严格校验解析后
    的绝对路径确实落在目录内（同时防住相对路径的 ".." 穿越和直接传入
    绝对路径这两种绕过方式），而不是像原来那样看"传入的字符串长什么
    样"来判断。
    """
    loader = DataLoader()
    raw_dir = Path(loader.raw_dir).resolve()
    candidate = (raw_dir / data_source).resolve()
    try:
        candidate.relative_to(raw_dir)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"data_source 必须是 {raw_dir} 目录内的文件，不允许越界访问其它路径",
        )
    try:
        return loader.load_path(candidate)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/runs")
def create_run(payload: RunRequest) -> dict:
    """同步跑一次完整回测，返回本次运行的 run_id 与最终状态"""
    data = _load_data(payload.data_source)
    config = BacktestConfig(
        strategy_name=payload.strategy_name,
        strategy_version=payload.strategy_version,
        data_source=payload.data_source,
        symbols=payload.symbols,
    )
    try:
        run = BacktestRunner(data=data, config=config).run()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"回测执行失败: {exc}") from exc

    return {"run_id": run.run_id, "status": run.status, "notes": run.notes}


@app.get("/runs")
def list_runs(limit: int = 20) -> list[dict]:
    """列出最近的回测运行（按创建时间倒序）"""
    query = BacktestRun.select().order_by(BacktestRun.created_at.desc()).limit(limit)
    return [
        {
            "run_id": r.run_id,
            "strategy_name": r.strategy_name,
            "strategy_version": r.strategy_version,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in query
    ]


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """查询某次回测运行的元数据"""
    run = BacktestRun.get_or_none(BacktestRun.run_id == run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"未找到 run_id={run_id}")
    return {
        "run_id": run.run_id,
        "strategy_name": run.strategy_name,
        "strategy_version": run.strategy_version,
        "status": run.status,
        "data_start_ts": run.data_start_ts.isoformat(),
        "data_end_ts": run.data_end_ts.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "notes": run.notes,
    }


@app.get("/runs/{run_id}/report")
def get_report(run_id: str) -> dict:
    """获取某次回测运行的完整复盘报告（生命周期分布/非共识归因/Lead Time 审计）"""
    try:
        reporter = BacktestReporter(run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return reporter.to_dict()
