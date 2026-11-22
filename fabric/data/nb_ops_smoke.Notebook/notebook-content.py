# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   }
# META }

# CELL ********************

# CI smoke test (owner: de-manager). Reads the active Variable Library value set
# so a run proves both the deploy and the per-environment settings.
import notebookutils

env_name = notebookutils.variableLibrary.get("$(/**/vl_shoppulse/env_name)")
print(f"shoppulse smoke test OK - env={env_name} - spark={spark.version}")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
