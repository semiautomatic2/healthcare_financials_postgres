-- schema.sql - database structure for healthcare company financial statements
-- All amounts are stored in USD millions.

DROP TABLE IF EXISTS financial_reports CASCADE;
DROP TABLE IF EXISTS companies CASCADE;
DROP TABLE IF EXISTS sectors CASCADE;

CREATE TABLE sectors (
    sector_id SMALLSERIAL PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE
);

CREATE TABLE companies (
    company_id SERIAL PRIMARY KEY,
    ticker     VARCHAR(10) NOT NULL UNIQUE,
    name       TEXT        NOT NULL,
    cik        INTEGER     NOT NULL UNIQUE,   -- company identifier in SEC EDGAR
    sector_id  SMALLINT    NOT NULL REFERENCES sectors(sector_id)
);

CREATE TABLE financial_reports (
    report_id         SERIAL PRIMARY KEY,
    company_id        INTEGER      NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    fiscal_year       SMALLINT     NOT NULL CHECK (fiscal_year BETWEEN 2000 AND 2100),
    period_end        DATE         NOT NULL,
    revenue           NUMERIC(14,2) NOT NULL CHECK (revenue >= 0),
    net_income        NUMERIC(14,2) NOT NULL,
    total_assets      NUMERIC(14,2) NOT NULL CHECK (total_assets > 0),
    total_liabilities NUMERIC(14,2) NOT NULL CHECK (total_liabilities >= 0),
    equity            NUMERIC(14,2) NOT NULL,          -- can be negative
    rd_expense        NUMERIC(14,2) CHECK (rd_expense >= 0),  -- NULL when the company does not report R&D
    UNIQUE (company_id, fiscal_year)
);

CREATE INDEX idx_reports_year ON financial_reports(fiscal_year);
