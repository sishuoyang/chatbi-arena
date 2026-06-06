from dataclasses import dataclass
import clickhouse_connect
from arena.config import ClickHouseCfg


def make_admin_client(cfg: ClickHouseCfg, database: str | None = None):
    """Full-privilege client for setup + writing results. Never used to run agent SQL.

    Pass database='default' during bootstrap before the arena database exists.
    """
    return clickhouse_connect.get_client(
        host=cfg.host, port=cfg.port, secure=cfg.secure,
        username=cfg.admin_user, password=cfg.admin_password,
        database=database or cfg.database,
    )


@dataclass
class QueryResult:
    rows: list[tuple]
    cols: list[str]


class ROClickHouseClient:
    """Read-only client used to execute agent-generated SQL. Enforces server-side limits."""

    def __init__(self, cfg: ClickHouseCfg):
        self._cfg = cfg
        lim = cfg.query_limits
        self._settings = {
            "readonly": 1,
            "max_execution_time": lim.max_execution_time,
            "max_result_rows": lim.max_result_rows,
            "max_memory_usage": lim.max_memory_usage,
            "max_rows_to_read": lim.max_rows_to_read,
            "result_overflow_mode": "throw",
        }
        self._client = clickhouse_connect.get_client(
            host=cfg.host, port=cfg.port, secure=cfg.secure,
            username=cfg.ro_user, password=cfg.ro_password,
            database=cfg.database,
        )

    def query(self, sql: str) -> QueryResult:
        res = self._client.query(sql, settings=self._settings)
        rows = [tuple(r) for r in res.result_rows]
        return QueryResult(rows=rows, cols=list(res.column_names))
