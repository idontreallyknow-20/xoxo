"""SEC EDGAR client: filings, XBRL facts, and 8-K earnings exhibits.

EDGAR is the only genuinely free, genuinely complete source in this project. No
key, no tier, no rate card. The price of admission is a real ``User-Agent`` with a
contact address and staying under the fair-access limit of ten requests a second.

What this module can get, all free:

``company_tickers``    ticker -> CIK, for every registrant
``submissions``        the filing index: form, filing date, report date, accession,
                       primary document, and the 8-K item codes
``companyfacts``       every XBRL fact the company has ever tagged, and crucially
                       the ``filed`` date on each one, which is what makes
                       point-in-time reconstruction possible at all
``companyconcept``     one tag's full history, when you do not want the whole blob
filing documents       the exhibits themselves, including Exhibit 99.1, which is
                       the earnings press release with the actual guidance language

What it cannot get: earnings call transcripts. Companies file the press release and
usually the slides, not the Q&A. See NOTES.md for what is actually available.

Nothing here was run against the live API. The container this was written in cannot
reach ``sec.gov`` at all (403 at the egress proxy). Every call is unit-tested against
recorded-shape fixtures and every entry point has a ``--dry-run``. Run
``python scripts/fetch_edgar.py AAPL`` once on a machine with egress before trusting it.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import paths
from .http import HttpTransport, Transport
from .store import Cache, FetchError

__all__ = [
    "EdgarClient",
    "Filing",
    "Fact",
    "default_user_agent",
    "cik_to_str",
    "SEC_RATE_PER_SECOND",
    "KEY_TAGS",
]

SEC_RATE_PER_SECOND = 8.0  # the published limit is 10/s; leave headroom

BASE_WWW = "https://www.sec.gov"
BASE_DATA = "https://data.sec.gov"

# The tags that carry the numbers this project cares about. Companies do not all use
# the same tag for the same line, so each metric is a preference-ordered list and the
# first one with data wins.
KEY_TAGS: Dict[str, List[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfServices"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "basic_shares": ["WeightedAverageNumberOfSharesOutstandingBasic"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "short_term_debt": ["LongTermDebtCurrent", "ShortTermBorrowings"],
    "interest_expense": ["InterestExpense", "InterestExpenseDebt"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"],
    "rd": ["ResearchAndDevelopmentExpense"],
    "sbc": ["ShareBasedCompensation"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
}


def default_user_agent() -> str:
    """SEC fair access requires a real contact. Read it from the environment.

    Set ``SEC_USER_AGENT`` to something like ``"Desk Research you@example.com"``.
    The placeholder below will work but is impolite and the SEC may throttle it.
    """
    return os.environ.get("SEC_USER_AGENT") or "Desk personal research (set SEC_USER_AGENT to your email)"


def cik_to_str(cik: int | str) -> str:
    """EDGAR wants the CIK zero-padded to ten digits in a path, bare in Archives URLs."""
    digits = re.sub(r"\D", "", str(cik))
    if not digits:
        raise ValueError(f"not a CIK: {cik!r}")
    return digits.zfill(10)


@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    filing_date: str
    report_date: Optional[str]
    primary_document: Optional[str]
    primary_doc_description: Optional[str]
    items: List[str]
    size: Optional[int]
    cik: str

    @property
    def accession_nodash(self) -> str:
        return self.accession.replace("-", "")

    @property
    def directory_url(self) -> str:
        return f"{BASE_WWW}/Archives/edgar/data/{int(self.cik)}/{self.accession_nodash}"

    @property
    def index_json_url(self) -> str:
        return f"{self.directory_url}/index.json"

    @property
    def primary_url(self) -> Optional[str]:
        return f"{self.directory_url}/{self.primary_document}" if self.primary_document else None

    @property
    def is_earnings_8k(self) -> bool:
        """Item 2.02 is "Results of Operations and Financial Condition" — the earnings 8-K."""
        return self.form.startswith("8-K") and any(i.startswith("2.02") for i in self.items)


@dataclass(frozen=True)
class Fact:
    """One XBRL observation, with the date it was actually filed.

    ``filed`` is the whole point. It is what lets a backtest ask "what did the market
    know on this date" instead of quietly using a restated figure from two years later.
    """

    tag: str
    unit: str
    value: float
    start: Optional[str]
    end: Optional[str]
    filed: str
    accession: Optional[str]
    form: Optional[str]
    fiscal_year: Optional[int]
    fiscal_period: Optional[str]
    frame: Optional[str]

    @property
    def is_annual(self) -> bool:
        return self.fiscal_period == "FY"


class EdgarClient:
    def __init__(
        self,
        transport: Optional[Transport] = None,
        *,
        cache: Optional[Cache] = None,
        user_agent: Optional[str] = None,
        ttl: float = 86_400.0,
    ):
        self.user_agent = user_agent or default_user_agent()
        self.transport = transport or HttpTransport(self.user_agent, rate_per_second=SEC_RATE_PER_SECOND)
        self.cache = cache if cache is not None else Cache(paths.EDGAR_CACHE, default_ttl=ttl)
        self.ttl = ttl

    # -- plumbing ---------------------------------------------------------

    def _json(self, url: str, *, key: str, ttl: Optional[float] = None) -> Any:
        def fetch() -> Any:
            raw = self.transport.get(url, headers={"User-Agent": self.user_agent})
            try:
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise FetchError(f"not JSON from {url}: {e}", url=url) from e

        return self.cache.get_or_fetch(key, fetch, ttl=self.ttl if ttl is None else ttl, meta={"url": url})

    def _text(self, url: str, *, key: str, ttl: Optional[float] = None) -> str:
        def fetch() -> str:
            return self.transport.get(url, headers={"User-Agent": self.user_agent}).decode("utf-8", errors="replace")

        return self.cache.get_or_fetch(key, fetch, ttl=self.ttl if ttl is None else ttl, meta={"url": url})

    # -- ticker -> CIK ----------------------------------------------------

    def company_tickers(self) -> Dict[str, Dict[str, Any]]:
        """``{"AAPL": {"cik": "0000320193", "title": "Apple Inc."}}`` for every registrant."""
        url = f"{BASE_WWW}/files/company_tickers.json"
        raw = self._json(url, key="company_tickers", ttl=7 * 86_400.0)
        out: Dict[str, Dict[str, Any]] = {}
        rows = raw.values() if isinstance(raw, dict) else raw
        for row in rows:
            t = str(row.get("ticker", "")).upper()
            if t:
                out[t] = {"cik": cik_to_str(row.get("cik_str") or row.get("cik")), "title": row.get("title")}
        return out

    def cik_for(self, ticker: str) -> Optional[str]:
        """CIK for a ticker. Handles the class-share dot/dash mismatch (BRK.B vs BRK-B)."""
        t = ticker.upper().strip()
        table = self.company_tickers()
        for candidate in (t, t.replace(".", "-"), t.replace("-", "."), t.split(".")[0]):
            if candidate in table:
                return table[candidate]["cik"]
        return None

    # -- filings ----------------------------------------------------------

    def submissions(self, cik: str) -> Dict[str, Any]:
        c = cik_to_str(cik)
        return self._json(f"{BASE_DATA}/submissions/CIK{c}.json", key=f"submissions_{c}")

    def filings(
        self,
        cik: str,
        *,
        forms: Optional[Iterable[str]] = None,
        limit: int = 40,
        include_older: bool = False,
    ) -> List[Filing]:
        """Recent filings, newest first.

        ``submissions`` carries the last ~1,000 filings inline under ``filings.recent``
        and paginates the rest into ``filings.files``. Most uses only want recent, so
        older pages are opt-in.
        """
        c = cik_to_str(cik)
        sub = self.submissions(c)
        pages = [sub.get("filings", {}).get("recent", {})]
        if include_older:
            for f in sub.get("filings", {}).get("files", []) or []:
                name = f.get("name")
                if name:
                    pages.append(self._json(f"{BASE_DATA}/submissions/{name}", key=f"submissions_{c}_{name}"))
        wanted = {w.upper() for w in forms} if forms else None
        out: List[Filing] = []
        for page in pages:
            n = len(page.get("accessionNumber", []) or [])
            for i in range(n):
                form = str(page["form"][i])
                if wanted and form.upper() not in wanted and form.upper().split("/")[0] not in wanted:
                    continue
                items_raw = (page.get("items") or [""] * n)[i] or ""
                out.append(
                    Filing(
                        accession=page["accessionNumber"][i],
                        form=form,
                        filing_date=page["filingDate"][i],
                        report_date=(page.get("reportDate") or [None] * n)[i] or None,
                        primary_document=(page.get("primaryDocument") or [None] * n)[i] or None,
                        primary_doc_description=(page.get("primaryDocDescription") or [None] * n)[i] or None,
                        items=[s.strip() for s in str(items_raw).split(",") if s.strip()],
                        size=(page.get("size") or [None] * n)[i],
                        cik=c,
                    )
                )
        out.sort(key=lambda f: f.filing_date, reverse=True)
        return out[:limit]

    def filing_documents(self, filing: Filing) -> List[Dict[str, Any]]:
        """Every file in a filing's directory, from its ``index.json``."""
        blob = self._json(filing.index_json_url, key=f"index_{filing.accession_nodash}", ttl=30 * 86_400.0)
        items = (blob.get("directory") or {}).get("item", []) or []
        return [
            {
                "name": it.get("name"),
                "type": it.get("type"),
                "size": it.get("size"),
                "url": f"{filing.directory_url}/{it.get('name')}",
            }
            for it in items
        ]

    def earnings_exhibits(self, filing: Filing) -> Dict[str, Optional[str]]:
        """Locate Exhibit 99.1 (press release) and 99.2 (slides) inside an 8-K.

        The exhibit type in ``index.json`` is the authority; filename patterns are a
        fallback because plenty of filers name the file ``ex991.htm`` and leave the
        type blank.
        """
        docs = self.filing_documents(filing)
        found: Dict[str, Optional[str]] = {"ex99_1": None, "ex99_2": None}
        for d in docs:
            typ = str(d.get("type") or "").upper().replace(" ", "")
            name = str(d.get("name") or "").lower()
            if found["ex99_1"] is None and (typ.startswith("EX-99.1") or re.search(r"ex[-_]?99[._]?1", name)):
                found["ex99_1"] = d["url"]
            if found["ex99_2"] is None and (typ.startswith("EX-99.2") or re.search(r"ex[-_]?99[._]?2", name)):
                found["ex99_2"] = d["url"]
        return found

    def document_text(self, url: str) -> str:
        """Fetch a filing document and strip it to readable text."""
        from .htmltext import to_text

        key = f"doc_{re.sub(r'[^A-Za-z0-9]+', '-', url)[-90:]}"
        raw = self._text(url, key=key, ttl=365 * 86_400.0)
        return to_text(raw) if "<" in raw[:2000] else raw

    # -- XBRL -------------------------------------------------------------

    def companyfacts(self, cik: str) -> Dict[str, Any]:
        c = cik_to_str(cik)
        return self._json(f"{BASE_DATA}/api/xbrl/companyfacts/CIK{c}.json", key=f"companyfacts_{c}")

    def companyconcept(self, cik: str, tag: str, taxonomy: str = "us-gaap") -> Dict[str, Any]:
        c = cik_to_str(cik)
        return self._json(
            f"{BASE_DATA}/api/xbrl/companyconcept/CIK{c}/{taxonomy}/{tag}.json",
            key=f"concept_{c}_{taxonomy}_{tag}",
        )

    def facts_for(self, companyfacts: Dict[str, Any], tag: str, taxonomy: str = "us-gaap") -> List[Fact]:
        node = ((companyfacts.get("facts") or {}).get(taxonomy) or {}).get(tag)
        if not node:
            return []
        out: List[Fact] = []
        for unit, rows in (node.get("units") or {}).items():
            for r in rows:
                val = r.get("val")
                filed = r.get("filed")
                if val is None or filed is None:
                    continue
                out.append(
                    Fact(
                        tag=tag,
                        unit=unit,
                        value=float(val),
                        start=r.get("start"),
                        end=r.get("end"),
                        filed=filed,
                        accession=r.get("accn"),
                        form=r.get("form"),
                        fiscal_year=r.get("fy"),
                        fiscal_period=r.get("fp"),
                        frame=r.get("frame"),
                    )
                )
        out.sort(key=lambda f: (f.end or "", f.filed))
        return out

    def metric(self, companyfacts: Dict[str, Any], metric: str) -> List[Fact]:
        """First tag in the preference list that actually has data. Companies differ."""
        for tag in KEY_TAGS.get(metric, []):
            facts = self.facts_for(companyfacts, tag)
            if facts:
                return facts
        return []

    def as_known_on(self, facts: List[Fact], on_date: str, *, period_end: Optional[str] = None) -> Optional[Fact]:
        """The value that was public on ``on_date``, ignoring later restatements.

        This is the whole reason to use EDGAR rather than a convenience API. A screen
        built on restated numbers looks far cleverer in a backtest than it was.
        """
        eligible = [f for f in facts if f.filed <= on_date and (period_end is None or f.end == period_end)]
        if not eligible:
            return None
        return max(eligible, key=lambda f: (f.end or "", f.filed))
