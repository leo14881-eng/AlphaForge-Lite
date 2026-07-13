"""
api/app.py 的集成测试：用 FastAPI TestClient 验证服务常驻模式下
POST /runs -> GET /runs -> GET /runs/{run_id} -> GET /runs/{run_id}/report
这条链路可以正常工作，且服务在多次请求之间保持状态（不像 main.py
那样跑一次就退出）。
"""
import numpy as np
import pandas as pd
import peewee as pw
import pytest
from fastapi.testclient import TestClient

from config.settings import RAW_DATA_DIR
from database.models import MODELS
from database.models import db as peewee_db


def _make_synthetic_csv(path) -> None:
    rng = np.random.default_rng(11)
    n = 100
    ts = pd.date_range("2026-01-01", periods=n, freq="D")
    volume = rng.normal(1000, 30, n) * np.concatenate([np.ones(50), np.linspace(1.2, 2.2, 50)])
    close = 100 * np.cumprod(1 + rng.normal(0.003, 0.008, n))
    df = pd.DataFrame(
        {
            "timestamp": ts,
            "symbol": "APIDEMO",
            "close": close,
            "volume": volume,
            "turnover_rate": rng.uniform(0.01, 0.02, n),
            "funding_rate": rng.normal(0.0001, 0.00003, n),
        }
    )
    df.to_csv(path, index=False)


@pytest.fixture()
def api_db(tmp_path):
    """
    用临时文件数据库而非 ':memory:'——FastAPI TestClient 通过线程池执行
    路由处理函数，SQLite ':memory:' 库是"每连接独立"的，不同线程拿到的
    连接会看到互不相通的空库，导致 'no such table' 假性失败；
    文件数据库天然跨连接/线程共享，规避这个问题。
    """
    db_path = tmp_path / "api_test.db"
    peewee_db.init(str(db_path), pragmas={"foreign_keys": 1, "journal_mode": "wal"})
    peewee_db.connect()
    peewee_db.create_tables(MODELS)
    yield peewee_db
    peewee_db.drop_tables(MODELS)
    peewee_db.close()


@pytest.fixture()
def client(api_db):
    from api.app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def raw_dir_csv():
    """
    _load_data() 现在把 data_source 严格限定在 RAW_DATA_DIR 目录内（见
    api/app.py 的安全修复记录），测试数据必须真的写在这个目录下，不能
    再用 tmp_path 那种目录外的路径——用完即删，不污染真实的 data/raw/。
    """
    csv_path = RAW_DATA_DIR / "_test_api_wide_table.csv"
    _make_synthetic_csv(csv_path)
    yield csv_path.name
    csv_path.unlink(missing_ok=True)


def test_full_run_lifecycle_via_http(raw_dir_csv, client):
    create_resp = client.post("/runs", json={"data_source": raw_dir_csv})
    assert create_resp.status_code == 200
    body = create_resp.json()
    assert body["status"] == "SUCCESS"
    run_id = body["run_id"]

    list_resp = client.get("/runs")
    assert list_resp.status_code == 200
    assert any(r["run_id"] == run_id for r in list_resp.json())

    detail_resp = client.get(f"/runs/{run_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["status"] == "SUCCESS"

    report_resp = client.get(f"/runs/{run_id}/report")
    assert report_resp.status_code == 200
    report = report_resp.json()
    assert report["run_id"] == run_id
    assert "stage_duration" in report
    assert "lead_time_detail" in report


def test_get_unknown_run_returns_404(client):
    resp = client.get("/runs/does-not-exist")
    assert resp.status_code == 404


def test_create_run_with_missing_file_returns_400(client):
    resp = client.post("/runs", json={"data_source": "definitely_missing.csv"})
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "malicious_data_source",
    [
        "../../../../etc/passwd",  # 相对路径穿越
        "..\\..\\..\\Windows\\win.ini",  # Windows 风格路径穿越
    ],
)
def test_create_run_rejects_path_traversal(client, malicious_data_source, tmp_path):
    """
    安全回归测试：data_source 不能通过 ".." 穿越到 RAW_DATA_DIR 之外。
    用一个真实存在于目录外、但不满足业务 schema 的文件验证——如果穿越
    防护失效，会先在读文件这步失败（ValueError/FileNotFoundError 一样
    是 400），但我们要的是"根本不该尝试读目录外的文件"这个更早的拒绝，
    用越界后仍然可能命中的真实文件路径来确保测的是路径校验本身，不是
    凑巧文件不存在导致的 400。
    """
    resp = client.post("/runs", json={"data_source": malicious_data_source})
    assert resp.status_code == 400
    assert "不允许越界访问" in resp.json()["detail"]


def test_create_run_rejects_absolute_path_outside_raw_dir(client, tmp_path):
    """绝对路径同样必须落在 RAW_DATA_DIR 内才允许，不能靠传绝对路径绕过限制"""
    outside_csv = tmp_path / "outside_raw_dir.csv"
    _make_synthetic_csv(outside_csv)

    resp = client.post("/runs", json={"data_source": str(outside_csv)})
    assert resp.status_code == 400
    assert "不允许越界访问" in resp.json()["detail"]
