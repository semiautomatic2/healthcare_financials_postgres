-- queries.sql - analytical queries. Amounts in USD millions.

-- ============ 0. View with key metrics ============
CREATE OR REPLACE VIEW v_company_kpi AS
SELECT c.ticker,
       c.name                                              AS company,
       s.name                                              AS sector,
       r.fiscal_year,
       r.revenue, r.net_income, r.total_assets,
       r.total_liabilities, r.equity, r.rd_expense,
       ROUND(100 * r.net_income / NULLIF(r.revenue, 0), 1)       AS net_margin_pct,
       ROUND(100 * r.net_income / NULLIF(r.total_assets, 0), 1)  AS roa_pct,
       CASE WHEN r.equity > 0 THEN ROUND(100 * r.net_income / r.equity, 1) END      AS roe_pct,
       CASE WHEN r.equity > 0 THEN ROUND(r.total_liabilities / r.equity, 2) END     AS debt_to_equity,
       ROUND(100 * r.rd_expense / NULLIF(r.revenue, 0), 1)       AS rd_pct
FROM financial_reports r
JOIN companies c USING (company_id)
JOIN sectors   s USING (sector_id);

-- ============ 1. Year-over-year revenue growth (LAG) ============
SELECT ticker, fiscal_year, revenue,
       ROUND(100 * (revenue - LAG(revenue) OVER w) / NULLIF(LAG(revenue) OVER w, 0), 1) AS growth_pct
FROM v_company_kpi
WINDOW w AS (PARTITION BY ticker ORDER BY fiscal_year)
ORDER BY ticker, fiscal_year;

-- ============ 2. Compound annual revenue growth (CAGR) over the whole period ============
WITH b AS (
    SELECT company_id, MIN(fiscal_year) AS y0, MAX(fiscal_year) AS y1
    FROM financial_reports GROUP BY company_id
)
SELECT c.ticker, b.y0, b.y1,
       ROUND(100 * (POWER(r1.revenue / r0.revenue, 1.0 / (b.y1 - b.y0)) - 1), 1) AS revenue_cagr_pct
FROM b
JOIN companies c USING (company_id)
JOIN financial_reports r0 ON r0.company_id = b.company_id AND r0.fiscal_year = b.y0
JOIN financial_reports r1 ON r1.company_id = b.company_id AND r1.fiscal_year = b.y1
WHERE b.y1 > b.y0
ORDER BY revenue_cagr_pct DESC;

-- ============ 3. Net income ranking within each sector (RANK) ============
-- use the latest year for which ALL companies have data
WITH common_year AS (
    SELECT MAX(fiscal_year) AS y
    FROM (SELECT fiscal_year FROM financial_reports
          GROUP BY fiscal_year
          HAVING COUNT(*) = (SELECT COUNT(*) FROM companies)) t
)
SELECT k.sector,
       RANK() OVER (PARTITION BY k.sector ORDER BY k.net_income DESC) AS rank_in_sector,
       k.ticker, k.fiscal_year, k.net_income, k.net_margin_pct
FROM v_company_kpi k, common_year
WHERE k.fiscal_year = common_year.y
ORDER BY k.sector, rank_in_sector;

-- ============ 4. Net margin vs. sector median ============
WITH common_year AS (
    SELECT MAX(fiscal_year) AS y
    FROM (SELECT fiscal_year FROM financial_reports
          GROUP BY fiscal_year
          HAVING COUNT(*) = (SELECT COUNT(*) FROM companies)) t
),
med AS (
    SELECT k.sector,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY k.net_margin_pct) AS median_margin
    FROM v_company_kpi k, common_year
    WHERE k.fiscal_year = common_year.y
    GROUP BY k.sector
)
SELECT k.ticker, k.sector, k.net_margin_pct,
       ROUND(m.median_margin::numeric, 1)                 AS sector_median,
       ROUND(k.net_margin_pct - m.median_margin::numeric, 1) AS diff_pp
FROM v_company_kpi k
JOIN med m USING (sector), common_year
WHERE k.fiscal_year = common_year.y
ORDER BY k.sector, diff_pp DESC;

-- ============ 5. Cumulative net income (running total) ============
SELECT ticker, fiscal_year, net_income,
       SUM(net_income) OVER (PARTITION BY ticker ORDER BY fiscal_year) AS cumulative_net_income
FROM v_company_kpi
ORDER BY ticker, fiscal_year;

-- ============ 6. R&D intensity (only companies that report R&D) ============
SELECT ticker, ROUND(AVG(rd_pct), 1) AS avg_rd_pct_of_revenue
FROM v_company_kpi
WHERE rd_pct IS NOT NULL
GROUP BY ticker
ORDER BY avg_rd_pct_of_revenue DESC;

-- ============ 7. Leverage: liabilities more than 3x equity ============
SELECT ticker, fiscal_year, debt_to_equity
FROM v_company_kpi
WHERE debt_to_equity > 3
ORDER BY debt_to_equity DESC;

-- ============ 8. Data quality checks ============
-- 8.1 Balance sheet: assets ~ liabilities + equity (10% tolerance because of minority interests)
SELECT c.ticker, r.fiscal_year,
       ROUND(100 * ABS(r.total_assets - r.total_liabilities - r.equity) / r.total_assets, 1) AS gap_pct
FROM financial_reports r JOIN companies c USING (company_id)
WHERE ABS(r.total_assets - r.total_liabilities - r.equity) > 0.10 * r.total_assets;

-- 8.2 Years loaded per company
SELECT c.ticker, COUNT(*) AS years_loaded, MIN(r.fiscal_year) AS first_year, MAX(r.fiscal_year) AS last_year
FROM companies c LEFT JOIN financial_reports r USING (company_id)
GROUP BY c.ticker ORDER BY years_loaded, c.ticker;

-- 8.3 Companies without R&D data
SELECT c.ticker, COUNT(*) FILTER (WHERE r.rd_expense IS NULL) AS years_without_rd
FROM companies c JOIN financial_reports r USING (company_id)
GROUP BY c.ticker HAVING COUNT(*) FILTER (WHERE r.rd_expense IS NULL) > 0;

-- ============ 9. Execution plan ============
EXPLAIN ANALYZE
SELECT c.ticker, r.fiscal_year, r.revenue
FROM financial_reports r JOIN companies c USING (company_id)
WHERE c.ticker = 'PFE' AND r.fiscal_year >= 2021;
