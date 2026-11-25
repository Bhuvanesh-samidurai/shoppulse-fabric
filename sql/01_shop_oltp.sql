-- ShopPulse shop OLTP (Fabric SQL Database `sqldb_shop`). Owner: DE2. Contract version 1.0.0.
-- SAP-inspired model with readable names; the SAP equivalent is noted per table.
-- Data arrives already relocated to Germany by generator/ (PLZ, Bundesland, EUR).
-- Every table has a primary key: Fabric SQL DB mirroring to OneLake requires one.
-- Idempotent: safe to run repeatedly.

IF SCHEMA_ID(N'shop') IS NULL EXEC (N'CREATE SCHEMA shop AUTHORIZATION dbo');
GO

-- Customer master (SAP KNA1)
IF OBJECT_ID(N'shop.customer', N'U') IS NULL
CREATE TABLE shop.customer (
    customer_id         VARCHAR(32)   NOT NULL,  -- one per order in Olist
    customer_unique_id  VARCHAR(32)   NOT NULL,  -- the real person; PII, pseudonymised in Silver
    postal_code         CHAR(5)       NOT NULL,  -- German PLZ
    city                NVARCHAR(80)  NOT NULL,
    region_code         CHAR(2)       NOT NULL,  -- Bundesland ISO 3166-2:DE suffix, e.g. HH, BY
    created_at          DATETIME2(3)  NOT NULL CONSTRAINT df_customer_created DEFAULT SYSUTCDATETIME(),
    changed_at          DATETIME2(3)  NOT NULL CONSTRAINT df_customer_changed DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_customer PRIMARY KEY (customer_id),
    CONSTRAINT ck_customer_plz CHECK (postal_code LIKE '[0-9][0-9][0-9][0-9][0-9]')
);
GO

-- Material master (SAP MARA)
IF OBJECT_ID(N'shop.material', N'U') IS NULL
CREATE TABLE shop.material (
    material_id     VARCHAR(32)   NOT NULL,
    category_code   NVARCHAR(60)  NULL,          -- Olist category (Portuguese); translated in Silver
    weight_g        INT           NULL,
    length_cm       INT           NULL,
    height_cm       INT           NULL,
    width_cm        INT           NULL,
    photos_qty      INT           NULL,
    vat_rate        DECIMAL(4,2)  NOT NULL CONSTRAINT df_material_vat DEFAULT 0.19,  -- 0.19 standard, 0.07 reduced
    changed_at      DATETIME2(3)  NOT NULL CONSTRAINT df_material_changed DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_material PRIMARY KEY (material_id),
    CONSTRAINT ck_material_vat CHECK (vat_rate IN (0.19, 0.07))
);
GO

-- Vendor master = marketplace seller (SAP LFA1)
IF OBJECT_ID(N'shop.vendor', N'U') IS NULL
CREATE TABLE shop.vendor (
    vendor_id    VARCHAR(32)   NOT NULL,
    postal_code  CHAR(5)       NOT NULL,
    city         NVARCHAR(80)  NOT NULL,
    region_code  CHAR(2)       NOT NULL,
    changed_at   DATETIME2(3)  NOT NULL CONSTRAINT df_vendor_changed DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_vendor PRIMARY KEY (vendor_id)
);
GO

-- Sales order header (SAP VBAK)
IF OBJECT_ID(N'shop.sales_order_header', N'U') IS NULL
CREATE TABLE shop.sales_order_header (
    order_id                 VARCHAR(32)   NOT NULL,
    customer_id              VARCHAR(32)   NOT NULL,
    order_status             VARCHAR(20)   NOT NULL,  -- created, approved, invoiced, processing, shipped, delivered, canceled, unavailable
    purchase_ts              DATETIME2(3)  NOT NULL,
    approved_ts              DATETIME2(3)  NULL,
    carrier_handover_ts      DATETIME2(3)  NULL,
    delivered_ts             DATETIME2(3)  NULL,
    estimated_delivery_date  DATE          NOT NULL,
    warehouse_code           CHAR(3)       NOT NULL,  -- HAM, DOR, MUC
    currency                 CHAR(3)       NOT NULL CONSTRAINT df_soh_currency DEFAULT 'EUR',
    changed_at               DATETIME2(3)  NOT NULL CONSTRAINT df_soh_changed DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_sales_order_header PRIMARY KEY (order_id),
    CONSTRAINT fk_soh_customer FOREIGN KEY (customer_id) REFERENCES shop.customer (customer_id),
    CONSTRAINT ck_soh_warehouse CHECK (warehouse_code IN ('HAM', 'DOR', 'MUC')),
    CONSTRAINT ck_soh_status CHECK (order_status IN ('created', 'approved', 'invoiced', 'processing', 'shipped', 'delivered', 'canceled', 'unavailable'))
);
GO

-- Sales order item (SAP VBAP)
IF OBJECT_ID(N'shop.sales_order_item', N'U') IS NULL
CREATE TABLE shop.sales_order_item (
    order_id          VARCHAR(32)    NOT NULL,
    item_no           INT            NOT NULL,
    material_id       VARCHAR(32)    NOT NULL,
    vendor_id         VARCHAR(32)    NOT NULL,
    shipping_limit_ts DATETIME2(3)   NOT NULL,
    net_price         DECIMAL(12,2)  NOT NULL,   -- EUR, excl. VAT
    freight           DECIMAL(12,2)  NOT NULL,   -- EUR
    vat_rate          DECIMAL(4,2)   NOT NULL,
    changed_at        DATETIME2(3)   NOT NULL CONSTRAINT df_soi_changed DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_sales_order_item PRIMARY KEY (order_id, item_no),
    CONSTRAINT fk_soi_header FOREIGN KEY (order_id) REFERENCES shop.sales_order_header (order_id),
    CONSTRAINT fk_soi_material FOREIGN KEY (material_id) REFERENCES shop.material (material_id),
    CONSTRAINT fk_soi_vendor FOREIGN KEY (vendor_id) REFERENCES shop.vendor (vendor_id),
    CONSTRAINT ck_soi_amounts CHECK (net_price >= 0 AND freight >= 0)
);
GO

-- Payment (SAP BSEG-like customer payment line)
IF OBJECT_ID(N'shop.payment', N'U') IS NULL
CREATE TABLE shop.payment (
    order_id      VARCHAR(32)    NOT NULL,
    payment_seq   INT            NOT NULL,
    payment_type  VARCHAR(20)    NOT NULL,   -- credit_card, paypal, sepa_debit, voucher, klarna_invoice
    installments  INT            NOT NULL,
    amount        DECIMAL(12,2)  NOT NULL,   -- EUR, gross
    changed_at    DATETIME2(3)   NOT NULL CONSTRAINT df_pay_changed DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_payment PRIMARY KEY (order_id, payment_seq),
    CONSTRAINT fk_pay_header FOREIGN KEY (order_id) REFERENCES shop.sales_order_header (order_id)
);
GO

-- Customer review. Olist review_id is not unique on its own, so the key includes order_id.
IF OBJECT_ID(N'shop.review', N'U') IS NULL
CREATE TABLE shop.review (
    review_id    VARCHAR(32)     NOT NULL,
    order_id     VARCHAR(32)     NOT NULL,
    score        TINYINT         NOT NULL,
    title        NVARCHAR(200)   NULL,
    message      NVARCHAR(4000)  NULL,       -- free text, may contain PII; never copied to Gold
    created_ts   DATETIME2(3)    NOT NULL,
    answered_ts  DATETIME2(3)    NULL,
    changed_at   DATETIME2(3)    NOT NULL CONSTRAINT df_rev_changed DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_review PRIMARY KEY (review_id, order_id),
    CONSTRAINT fk_rev_header FOREIGN KEY (order_id) REFERENCES shop.sales_order_header (order_id),
    CONSTRAINT ck_rev_score CHECK (score BETWEEN 1 AND 5)
);
GO
