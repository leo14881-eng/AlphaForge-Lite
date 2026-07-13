"""main.py 的路径解析辅助函数单测（全局扫描修复的回归测试）"""
from pathlib import Path

from config.settings import DB_PATH
from main import _resolve_db_path


def test_bare_filename_resolves_under_database_dir():
    result = _resolve_db_path("foo.db")
    assert result == DB_PATH.parent / "foo.db"


def test_explicit_relative_path_used_as_is():
    result = _resolve_db_path("sub/foo.db")
    assert result == Path("sub/foo.db")


def test_dotdot_is_not_misclassified_as_bare_filename():
    """
    全局扫描修复：Path("..").parent 跟 Path("foo.db").parent 一样都等于
    Path(".")，旧实现会把 ".." 误判成"裸文件名"，拼出
    DB_PATH.parent / ".." 逃出预期的 database 目录。
    """
    result = _resolve_db_path("..")
    assert result == Path("..")
    assert result != DB_PATH.parent / ".."


def test_dot_is_not_misclassified_as_bare_filename():
    result = _resolve_db_path(".")
    assert result == Path(".")


def test_deeper_traversal_path_already_falls_through_correctly():
    """不是裸文件名的多层路径穿越写法，parent 本来就不是 "."，一直都能正确穿透，不需要额外处理"""
    result = _resolve_db_path("../../etc/passwd")
    assert result == Path("../../etc/passwd")
