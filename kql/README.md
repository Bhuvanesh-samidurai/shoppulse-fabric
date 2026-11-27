# kql/ (owner: DE1)

The Eventhouse schema lives with the item: `fabric/data/kqldb_shoppulse.KQLDatabase/DatabaseSchema.kql`. fabric-cicd applies it on every deploy.

| Table | Columns | Retention | Hot cache |
|---|---|---|---|
| `wms_event` | same as `contracts/bronze/wms_event.yml`, without the `_` metadata columns | 3 days | 1 day |
| `carrier_event` | same as `contracts/bronze/carrier_event.yml`, without the `_` metadata columns | 3 days | 1 day |

- **Streaming ingestion** is on, and the Eventstreams write here with `ProcessedIngestion` in about 30 seconds.
- **Allowed commands:** the schema script may only use the commands Fabric allows there: `.create-merge table`, `.alter table … policy (retention|caching|streamingingestion|…)`, and ingestion mappings. The simplified `softdelete = 3d` syntax is rejected; use the JSON policy form.
- **Querying:** the engine-level database name is the KQL database **item id**, not its display name.
