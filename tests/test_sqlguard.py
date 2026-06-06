import pytest
from agents.sqlguard import validate_select_only


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select count() from v_orders",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "  \n SELECT a, b FROM v_orders WHERE a > 1  ",
    "SELECT * FROM v_orders -- a trailing comment\n",
])
def test_accepts_single_select(sql):
    ok, reason = validate_select_only(sql)
    assert ok is True, reason


@pytest.mark.parametrize("sql", [
    "",
    "   ",
    "INSERT INTO v_orders VALUES (1)",
    "DROP TABLE v_orders",
    "ALTER TABLE v_orders DELETE WHERE 1=1",
    "SYSTEM RELOAD CONFIG",
    "SELECT 1; DROP TABLE v_orders",
    "SELECT 1; SELECT 2",
    "TRUNCATE TABLE v_orders",
    "GRANT SELECT ON *.* TO x",
    "select 1 into outfile 'x'",
])
def test_rejects_non_select_or_multi(sql):
    ok, reason = validate_select_only(sql)
    assert ok is False
    assert reason
