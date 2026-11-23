-- ShopPulse write-back tables (same Fabric SQL Database). Owner: DE2. Contract version 1.0.0.
-- Written only by the User Data Function behind the Fabric App (EXECUTE-only, SPEC §4a).
-- System-versioned temporal tables: every change is kept, so "who changed what, when" is queryable:
--   SELECT * FROM wb.reorder_policy FOR SYSTEM_TIME ALL WHERE material_id = '...';
-- SPEC names wb_reorder_policy / wb_forecast_override / wb_delivery_flag = wb.reorder_policy / wb.forecast_override / wb.delivery_flag.
-- Idempotent: safe to run repeatedly.

IF SCHEMA_ID(N'wb') IS NULL EXEC (N'CREATE SCHEMA wb');
GO

IF OBJECT_ID(N'wb.reorder_policy', N'U') IS NULL
CREATE TABLE wb.reorder_policy (
    warehouse_code  CHAR(3)        NOT NULL,
    material_id     VARCHAR(32)    NOT NULL,
    reorder_point   INT            NOT NULL,
    safety_stock    INT            NOT NULL,
    max_stock       INT            NOT NULL,
    note            NVARCHAR(400)  NULL,
    changed_by      NVARCHAR(128)  NOT NULL,   -- UPN from the app user, not a service principal
    valid_from      DATETIME2(3) GENERATED ALWAYS AS ROW START NOT NULL,
    valid_to        DATETIME2(3) GENERATED ALWAYS AS ROW END   NOT NULL,
    PERIOD FOR SYSTEM_TIME (valid_from, valid_to),
    CONSTRAINT pk_reorder_policy PRIMARY KEY (warehouse_code, material_id),
    CONSTRAINT ck_rp_warehouse CHECK (warehouse_code IN ('HAM', 'DOR', 'MUC')),
    CONSTRAINT ck_rp_levels CHECK (safety_stock >= 0 AND reorder_point >= safety_stock AND max_stock >= reorder_point)
) WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = wb.reorder_policy_history));
GO

IF OBJECT_ID(N'wb.forecast_override', N'U') IS NULL
CREATE TABLE wb.forecast_override (
    category_code   NVARCHAR(60)   NOT NULL,   -- English category (Silver product.category_en)
    warehouse_code  CHAR(3)        NOT NULL,
    week_start      DATE           NOT NULL,   -- ISO week Monday
    override_qty    INT            NOT NULL,
    reason          NVARCHAR(400)  NOT NULL,
    changed_by      NVARCHAR(128)  NOT NULL,
    valid_from      DATETIME2(3) GENERATED ALWAYS AS ROW START NOT NULL,
    valid_to        DATETIME2(3) GENERATED ALWAYS AS ROW END   NOT NULL,
    PERIOD FOR SYSTEM_TIME (valid_from, valid_to),
    CONSTRAINT pk_forecast_override PRIMARY KEY (category_code, warehouse_code, week_start),
    CONSTRAINT ck_fo_qty CHECK (override_qty >= 0),
    CONSTRAINT ck_fo_monday CHECK (DATEDIFF(DAY, '19000101', week_start) % 7 = 0)  -- 1900-01-01 was a Monday; language/DATEFIRST independent
) WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = wb.forecast_override_history));
GO

IF OBJECT_ID(N'wb.delivery_flag', N'U') IS NULL
CREATE TABLE wb.delivery_flag (
    order_id    VARCHAR(32)    NOT NULL,
    flag_type   VARCHAR(30)    NOT NULL,   -- INVESTIGATE, CUSTOMER_CONTACTED, CARRIER_CLAIM, RESOLVED
    comment     NVARCHAR(400)  NULL,
    changed_by  NVARCHAR(128)  NOT NULL,
    valid_from  DATETIME2(3) GENERATED ALWAYS AS ROW START NOT NULL,
    valid_to    DATETIME2(3) GENERATED ALWAYS AS ROW END   NOT NULL,
    PERIOD FOR SYSTEM_TIME (valid_from, valid_to),
    CONSTRAINT pk_delivery_flag PRIMARY KEY (order_id),
    CONSTRAINT ck_df_type CHECK (flag_type IN ('INVESTIGATE', 'CUSTOMER_CONTACTED', 'CARRIER_CLAIM', 'RESOLVED'))
) WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = wb.delivery_flag_history));
GO
