"""SqlSink: idempotent MERGE of relocated shop rows into sqldb_shop (owner: DE1, issue #26).

- Auth: Entra token of the running identity (agent service principal in its feature workspace).
- Per table: rows go to a #staging temp table (fast_executemany), then one MERGE on the primary key.
  A matched row is only updated when a value actually differs, so re-sending the same rows changes
  nothing in the database and creates no mirror churn in Bronze.
- Parent-first order keeps the foreign keys valid: customer, material, vendor, header, item, payment, review.
- A Fabric SQL database pauses with its capacity: connect() retries while it wakes up.
"""
from __future__ import annotations

import struct
import time
from decimal import Decimal

import pandas as pd

from generator.relocate import COLUMNS

ORDER = ["customer", "material", "vendor", "sales_order_header", "sales_order_item", "payment", "review"]
KEYS = {"customer": ["customer_id"], "material": ["material_id"], "vendor": ["vendor_id"], "sales_order_header": ["order_id"],
        "sales_order_item": ["order_id", "item_no"], "payment": ["order_id", "payment_seq"], "review": ["review_id", "order_id"]}
SQL_TYPES = {  # staging column types, mirroring sql/01_shop_oltp.sql
    "customer_id": "VARCHAR(32)", "customer_unique_id": "VARCHAR(32)", "postal_code": "CHAR(5)", "city": "NVARCHAR(80)",
    "region_code": "CHAR(2)", "created_at": "DATETIME2(3)", "changed_at": "DATETIME2(3)", "material_id": "VARCHAR(32)",
    "category_code": "NVARCHAR(60)", "weight_g": "INT", "length_cm": "INT", "height_cm": "INT", "width_cm": "INT", "photos_qty": "INT",
    "vat_rate": "DECIMAL(4,2)", "vendor_id": "VARCHAR(32)", "order_id": "VARCHAR(32)", "order_status": "VARCHAR(20)",
    "purchase_ts": "DATETIME2(3)", "approved_ts": "DATETIME2(3)", "carrier_handover_ts": "DATETIME2(3)", "delivered_ts": "DATETIME2(3)",
    "estimated_delivery_date": "DATE", "warehouse_code": "CHAR(3)", "currency": "CHAR(3)", "item_no": "INT",
    "shipping_limit_ts": "DATETIME2(3)", "net_price": "DECIMAL(12,2)", "freight": "DECIMAL(12,2)", "payment_seq": "INT",
    "payment_type": "VARCHAR(20)", "installments": "INT", "amount": "DECIMAL(12,2)", "review_id": "VARCHAR(32)", "score": "TINYINT",
    "title": "NVARCHAR(200)", "message": "NVARCHAR(4000)", "created_ts": "DATETIME2(3)", "answered_ts": "DATETIME2(3)",
}


def merge_sql(table: str) -> str:
    cols, keys = COLUMNS[table], KEYS[table]
    on = " AND ".join(f"t.[{k}] = s.[{k}]" for k in keys)
    upd = [c for c in cols if c not in keys]
    return (
        f"MERGE shop.[{table}] WITH (HOLDLOCK) AS t USING #staging AS s ON {on} "
        f"WHEN MATCHED AND EXISTS (SELECT {', '.join(f's.[{c}]' for c in upd)} EXCEPT SELECT {', '.join(f't.[{c}]' for c in upd)}) "
        f"THEN UPDATE SET {', '.join(f'[{c}] = s.[{c}]' for c in upd)} "
        f"WHEN NOT MATCHED BY TARGET THEN INSERT ({', '.join(f'[{c}]' for c in cols)}) VALUES ({', '.join(f's.[{c}]' for c in cols)}) "
        "OUTPUT $action;"
    )


def _convert(v, sql_type: str):
    if v is None:
        return None
    if sql_type.startswith("DECIMAL"):
        return Decimal(str(round(float(v), int(sql_type.split(",")[1].rstrip(")")))))
    if sql_type in ("INT", "TINYINT"):
        return int(v)
    if sql_type.startswith("DATETIME2"):
        return pd.Timestamp(v).to_pydatetime().replace(microsecond=pd.Timestamp(v).microsecond // 1000 * 1000)
    if sql_type == "DATE":
        return pd.Timestamp(v).date()
    return str(v)


def to_rows(df: pd.DataFrame, table: str) -> list[tuple]:
    """DataFrame -> tuples of Python values typed per SQL column (NaT/NA -> None)."""
    cols = COLUMNS[table]
    out = df[cols].astype(object).where(df[cols].notna(), None)
    types = [SQL_TYPES[c] for c in cols]
    return [tuple(_convert(v, t) for v, t in zip(rec, types, strict=True)) for rec in out.itertuples(index=False, name=None)]


def input_sizes(table: str) -> list[tuple]:
    """Explicit parameter types for fast_executemany, so a NULL in the first row can't make pyodbc guess
    VARCHAR for a numeric column ("Error converting data type varchar to numeric")."""
    import pyodbc

    sizes = []
    for c in COLUMNS[table]:
        t = SQL_TYPES[c]
        n = int(t[t.index("(") + 1:t.index(")")].split(",")[0]) if "(" in t else 0
        if t.startswith("NVARCHAR"):
            sizes.append((pyodbc.SQL_WVARCHAR, n, 0))
        elif t.startswith(("VARCHAR", "CHAR")):
            sizes.append((pyodbc.SQL_VARCHAR, n, 0))
        elif t.startswith("DECIMAL"):
            sizes.append((pyodbc.SQL_DECIMAL, n, int(t.split(",")[1].rstrip(")"))))
        elif t == "INT":
            sizes.append((pyodbc.SQL_INTEGER, 0, 0))
        elif t == "TINYINT":
            sizes.append((pyodbc.SQL_TINYINT, 0, 0))
        elif t.startswith("DATETIME2"):
            sizes.append((pyodbc.SQL_TYPE_TIMESTAMP, 23, 3))
        elif t == "DATE":
            sizes.append((pyodbc.SQL_TYPE_DATE, 0, 0))
    return sizes


class SqlSink:
    def __init__(self, server: str, database: str, credential, batch_size: int = 2000, max_wait_s: int = 240):
        self.server, self.database, self.credential = server, database, credential
        self.batch_size, self.max_wait_s = batch_size, max_wait_s
        self.conn = None

    def connect(self):
        import pyodbc

        waited, delay = 0, 10
        while True:
            token = self.credential.get_token("https://database.windows.net/.default").token.encode("utf-16-le")
            try:
                self.conn = pyodbc.connect(
                    f"Driver={{ODBC Driver 18 for SQL Server}};Server={self.server};Database={self.database};"
                    "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60",
                    attrs_before={1256: struct.pack(f"<I{len(token)}s", len(token), token)}, autocommit=False)
                return self
            except pyodbc.Error as e:
                if waited >= self.max_wait_s:
                    raise RuntimeError(f"sqldb_shop not reachable after {waited}s (capacity paused?): {e}") from e
                print(f"sqldb_shop not ready, retrying in {delay}s")
                time.sleep(delay)
                waited, delay = waited + delay, min(delay * 2, 60)

    def write(self, table: str, df: pd.DataFrame) -> dict[str, int]:
        """MERGE one table's rows; returns counts of INSERT / UPDATE actions (0/0 when nothing changed)."""
        counts = {"INSERT": 0, "UPDATE": 0}
        if df.empty:
            return counts
        cols = COLUMNS[table]
        cur = self.conn.cursor()
        cur.fast_executemany = True
        rows = to_rows(df, table)
        for i in range(0, len(rows), self.batch_size):
            cur.execute("IF OBJECT_ID('tempdb..#staging') IS NOT NULL DROP TABLE #staging")
            cur.execute(f"CREATE TABLE #staging ({', '.join(f'[{c}] {SQL_TYPES[c]}' for c in cols)})")
            cur.setinputsizes(input_sizes(table))
            cur.executemany(f"INSERT INTO #staging VALUES ({', '.join('?' * len(cols))})", rows[i:i + self.batch_size])
            cur.execute(merge_sql(table))
            for (action,) in cur.fetchall():
                counts[action] = counts.get(action, 0) + 1
            self.conn.commit()
        return counts

    def write_all(self, tables: dict[str, pd.DataFrame]) -> dict[str, dict[str, int]]:
        return {t: self.write(t, tables[t]) for t in ORDER if t in tables}

    def close(self):
        if self.conn:
            self.conn.close()
