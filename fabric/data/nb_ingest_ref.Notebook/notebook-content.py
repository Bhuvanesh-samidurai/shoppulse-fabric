# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   }
# META }

# CELL ********************

# nb_ingest_ref (owner: DE1, issue #25)
# Loads reference parquet files from lh_bronze Files/landing/ref/ into bronze.ref_* (full refresh).
# - Target schema comes from the existing table (created by nb_ddl_20_bronze from the contract):
#   columns are selected and cast to it, nothing is added or evolved.
# - Rows with a NULL in a NOT NULL contract column go to bronze._quarantine_<table> with a reason.
# - Every table writes one row to ops.run_log (lh_silver).
# - Idempotent: the same input file produces the same table content on every run.
import datetime as dt
import uuid

import notebookutils
from pyspark.sql import functions as F

RUN_ID = "ref-" + dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
WS_ID = notebookutils.runtime.context["currentWorkspaceId"]
ENV = notebookutils.variableLibrary.get("$(/**/vl_shoppulse/env_name)")
TABLES = ["ref_geography_de", "ref_category_translation", "ref_holiday_de"]


def lakehouse_root(name):
    lh = notebookutils.lakehouse.get(name)
    return f"abfss://{WS_ID}@onelake.dfs.fabric.microsoft.com/{lh['id']}"


BRONZE = lakehouse_root("lh_bronze")
RUN_LOG = lakehouse_root("lh_silver") + "/Tables/ops/run_log"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

def log_run(table, started, status, rows_in, rows_out, rows_q, error=None):
    row = [(RUN_ID, f"bronze.{table}", "nb_ingest_ref", ENV, started, dt.datetime.now(dt.UTC).replace(tzinfo=None),
            status, rows_in, rows_out, rows_q, None, error)]
    schema = spark.read.format("delta").load(RUN_LOG).schema
    spark.createDataFrame(row, schema).write.format("delta").mode("append").save(RUN_LOG)


def load(table):
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    src = f"{BRONZE}/Files/landing/ref/{table}.parquet"
    target = f"{BRONZE}/Tables/dbo/{table}"
    try:
        target_schema = spark.read.format("delta").load(target).schema
        raw = spark.read.parquet(src)
        df = (raw.withColumn("_ingested_at", F.current_timestamp())
                 .withColumn("_source", F.lit(f"landing/ref/{table}.parquet"))
                 .withColumn("_batch_id", F.lit(RUN_ID)))
        df = df.select([F.col(f.name).cast(f.dataType).alias(f.name) for f in target_schema])
        required = [f.name for f in target_schema if not f.nullable]
        bad_cond = " OR ".join(f"`{c}` IS NULL" for c in required) or "false"
        bad = df.filter(bad_cond)
        good = df.filter(f"NOT ({bad_cond})")
        n_in, n_bad = df.count(), bad.count()
        if n_bad:
            (bad.withColumn("_reason", F.lit(f"NULL in NOT NULL column ({', '.join(required)})"))
                .withColumn("_run_id", F.lit(RUN_ID))
                .write.format("delta").mode("append").save(f"{BRONZE}/Tables/dbo/_quarantine_{table}"))
        good.write.format("delta").mode("overwrite").option("overwriteSchema", "false").save(target)
        log_run(table, started, "succeeded", n_in, n_in - n_bad, n_bad)
        return {"table": table, "rows_in": n_in, "rows_out": n_in - n_bad, "quarantined": n_bad}
    except Exception as e:
        log_run(table, started, "failed", None, None, None, str(e)[:2000])
        raise

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

results = [load(t) for t in TABLES]
for r in results:
    print(r)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
