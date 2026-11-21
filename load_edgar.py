#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
load_edgar.py - load annual financial statements (Form 10-K) from SEC EDGAR into PostgreSQL.

Source: XBRL "companyfacts" API  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
SEC requires a contact in the User-Agent header and at most 10 requests per second.

Examples:
    python load_edgar.py --email you@example.com --dsn "dbname=finance_db user=postgres"
    python load_edgar.py --email you@example.com --dry-run      # CSV only, no database
"""
import argparse
import csv
import sys
import time
from datetime import date

import requests

# (ticker, name, CIK, sector, options)
# options: year_from - first usable fiscal year; ignore_rd - drop R&D values that were mis-picked
COMPANIES = [
    ("PFE",  "Pfizer Inc.",              78003,   "Pharma & Biotech", {}),
    ("MRK",  "Merck & Co., Inc.",        310158,  "Pharma & Biotech", {}),
    ("LLY",  "Eli Lilly and Company",    59478,   "Pharma & Biotech", {}),
    ("ABBV", "AbbVie Inc.",              1551152, "Pharma & Biotech", {}),
    ("BMY",  "Bristol-Myers Squibb",     14272,   "Pharma & Biotech", {}),
    # J&J: pre-2021 revenue includes the consumer business that was later spun off (Kenvue),
    # so earlier years are not comparable; the auto-selected R&D tag is also unreliable.
    ("JNJ",  "Johnson & Johnson",        200406,  "Pharma & Biotech", {"year_from": 2021, "ignore_rd": True}),
    ("ABT",  "Abbott Laboratories",      1800,    "Medical Devices", {}),
    ("MDT",  "Medtronic plc",            1613103, "Medical Devices", {}),
    ("UNH",  "UnitedHealth Group",       731766,  "Health Services & Insurance", {}),
    ("CVS",  "CVS Health Corporation",   64803,   "Health Services & Insurance", {}),
]

# us-gaap tags in priority order (companies change tags from year to year)
TAGS = {
    "revenue": ["Revenues",
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "rd_expense": ["ResearchAndDevelopmentExpense",
                   "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
}
INSTANT = {"total_assets", "total_liabilities", "equity"}  # point-in-time items (no period)


def fetch_facts(cik, user_agent):
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def annual_series(facts, tags, instant):
    """Return {period_end_date: value} taken from annual 10-K filings."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out = {}
    for tag in tags:                                   # by tag priority
        items = gaap.get(tag, {}).get("units", {}).get("USD", [])
        best = {}
        for it in items:
            if it.get("form") not in ("10-K", "10-K/A"):
                continue
            if not instant:                            # flow items must cover about one year
                if "start" not in it:
                    continue
                days = (date.fromisoformat(it["end"]) - date.fromisoformat(it["start"])).days
                if not 350 <= days <= 380:
                    continue
            end = it["end"]
            if end not in best or it["filed"] > best[end]["filed"]:   # keep the latest filing
                best[end] = it
        for end, it in best.items():
            out.setdefault(end, it["val"])
    return out


def build_rows(facts, year_from=2019, year_to=None):
    s = {k: annual_series(facts, t, k in INSTANT) for k, t in TAGS.items()}
    rows = {}
    for end, revenue in s["revenue"].items():
        net_income = s["net_income"].get(end)
        assets = s["total_assets"].get(end)
        equity = s["equity"].get(end)
        if None in (net_income, assets, equity):
            continue
        liabilities = s["total_liabilities"].get(end, assets - equity)   # fallback
        fy = int(end[:4])
        if end[5:7] == "01" and int(end[8:10]) <= 10:   # 52/53-week year (e.g. J&J): a period ending on Jan 3 belongs to the previous year
            fy -= 1
        if fy < year_from or (year_to and fy > year_to):
            continue
        if fy in rows and rows[fy]["period_end"] > end:
            continue
        m = lambda v: None if v is None else round(v / 1e6, 2)           # in USD millions
        rows[fy] = {
            "fiscal_year": fy, "period_end": end,
            "revenue": m(revenue), "net_income": m(net_income),
            "total_assets": m(assets), "total_liabilities": m(liabilities),
            "equity": m(equity), "rd_expense": m(s["rd_expense"].get(end)),
        }
    return [rows[y] for y in sorted(rows)]


def load_to_db(dsn, data):
    import psycopg2
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for ticker, name, cik, sector, _ in COMPANIES:
            cur.execute("INSERT INTO sectors(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (sector,))
            cur.execute(
                """INSERT INTO companies(ticker, name, cik, sector_id)
                   VALUES (%s, %s, %s, (SELECT sector_id FROM sectors WHERE name = %s))
                   ON CONFLICT (ticker) DO UPDATE
                   SET name = EXCLUDED.name, cik = EXCLUDED.cik, sector_id = EXCLUDED.sector_id""",
                (ticker, name, cik, sector))
        for ticker, rows in data.items():
            cur.execute("SELECT company_id FROM companies WHERE ticker = %s", (ticker,))
            company_id = cur.fetchone()[0]
            for r in rows:
                cur.execute(
                    """INSERT INTO financial_reports
                         (company_id, fiscal_year, period_end, revenue, net_income,
                          total_assets, total_liabilities, equity, rd_expense)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (company_id, fiscal_year) DO UPDATE SET
                         period_end = EXCLUDED.period_end, revenue = EXCLUDED.revenue,
                         net_income = EXCLUDED.net_income, total_assets = EXCLUDED.total_assets,
                         total_liabilities = EXCLUDED.total_liabilities, equity = EXCLUDED.equity,
                         rd_expense = EXCLUDED.rd_expense""",
                    (company_id, r["fiscal_year"], r["period_end"], r["revenue"], r["net_income"],
                     r["total_assets"], r["total_liabilities"], r["equity"], r["rd_expense"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True, help="your e-mail for the User-Agent header (SEC requirement)")
    ap.add_argument("--dsn", default="dbname=finance_db", help="PostgreSQL connection string")
    ap.add_argument("--year-from", type=int, default=2019)
    ap.add_argument("--year-to", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="only write the CSV, do not touch the database")
    args = ap.parse_args()

    ua = f"Student project {args.email}"
    data = {}
    for ticker, name, cik, _, opts in COMPANIES:
        facts = fetch_facts(cik, ua)
        rows = build_rows(facts, max(args.year_from, opts.get("year_from", 0)), args.year_to)
        if opts.get("ignore_rd"):
            for r in rows:
                r["rd_expense"] = None
        data[ticker] = rows
        years = f"{rows[0]['fiscal_year']}-{rows[-1]['fiscal_year']}" if rows else "no data"
        print(f"{ticker:5} {facts.get('entityName', '?'):40} {len(rows)} years ({years})")
        time.sleep(0.2)                                  # SEC limit: <= 10 requests/second

    with open("financial_reports.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "fiscal_year", "period_end", "revenue", "net_income",
                    "total_assets", "total_liabilities", "equity", "rd_expense"])
        for ticker, rows in data.items():
            for r in rows:
                w.writerow([ticker, r["fiscal_year"], r["period_end"], r["revenue"], r["net_income"],
                            r["total_assets"], r["total_liabilities"], r["equity"], r["rd_expense"]])
    print("CSV saved: financial_reports.csv")

    if not args.dry_run:
        load_to_db(args.dsn, data)
        print("Data loaded into PostgreSQL")


if __name__ == "__main__":
    sys.exit(main())
