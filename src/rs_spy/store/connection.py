"""psycopg3 connection factory for the runs-store."""
import psycopg
from psycopg.rows import dict_row

from rs_spy.config import get_settings


def connect_pg(database_url: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    """Open a psycopg3 connection to the runs-store.

    Reads Settings.database_url when `database_url` is None. Rows come back as
    dicts (dict_row) so the repository's SELECTs return dict[str, Any].
    """
    url = database_url or get_settings().database_url
    return psycopg.connect(url, autocommit=autocommit, row_factory=dict_row)
