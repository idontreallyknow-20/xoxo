"""SEC DERA Financial Statement Data Sets: as-reported fundamentals, point in time by construction.

WHY A SECOND SEC SOURCE
-----------------------
``edgar.py`` reads ``companyfacts``, one JSON per company holding every XBRL fact
it ever tagged, each with the date it was filed. That is the right shape for
looking at one company. It is the wrong shape for a backtest over 1,500 names,
which needs *every* company's numbers *as they stood on a date*, and would have to
pull 1,500 files of 15 to 25 MB to get them.

The Division of Economic and Risk Analysis publishes the same facts the other way
round: one zip per calendar quarter holding every number from every filing
accepted in that quarter, as it was filed. Four tab-separated tables:

``sub.txt``   one row per submission: accession, CIK, form, period, fiscal year
              and period, the date it was filed and the moment it was accepted
``num.txt``   one row per numeric fact: accession, tag, taxonomy version,
              co-registrant, period end date, how many quarters it covers, unit,
              value
``pre.txt``   which statement each fact was presented on, and in what order
``tag.txt``   the tag dictionary

A quarter's file is 50 to 100 MB and covers every registrant, so twelve files
give three years of point-in-time fundamentals for the whole market at zero
cost and with no per-company request. The lag is the catch: a set is cut a few
weeks after quarter end, and a filing accepted after the cut lands in the next
set. Useless for a current screen, fine for a backtest.

FOUR THINGS THE FORMAT MEANS, EACH OF WHICH PRODUCES A WRONG NUMBER IF IGNORED
------------------------------------------------------------------------------
*``qtrs`` is the period, ``ddate`` is where it ends.* A ``qtrs`` of 0 is an
instant (a balance-sheet line), 1 is a quarter, 4 is a fiscal year, and 2 or 3
is a year-to-date figure from a 10-Q. There is no start date; ``ddate`` and
``qtrs`` together are the period. The same tag with the same ``ddate`` and a
different ``qtrs`` is a different number, and both are usually present.

*``coreg`` non-blank is not the company.* Rows for a co-registrant or a
subsidiary sit beside the consolidated ones under the same accession. The loader
drops them unless asked not to.

*A fact is filed many times.* The FY2022 revenue appears in the FY2022 10-K,
again as a comparative in the FY2023 10-K, and again in the FY2024 one, possibly
restated. The date the market first saw a figure is the ``filed`` date of the
earliest submission carrying it, and the figure it believed on any later date is
the one in the latest submission filed by then. :func:`first_reported` and
:func:`as_known_on` are those two questions, and ``edgar.as_known_on`` is the
same primitive over the other shape.

*Q4 is never filed.* The 10-K reports the year, so a quarterly series has Q1,
Q2, Q3 and a hole. The fourth quarter is the year minus the nine-month figure
from the Q3 10-Q, and :func:`derive_fourth_quarters` does that subtraction with
the filing date of the *10-K*, because that is when the fourth quarter became
knowable. A derived fact says so.

WHETHER THIS HAS RUN FOR REAL
-----------------------------
No. The container this was written in cannot reach ``www.sec.gov``. Every column
below is taken from the data set's published readme, and the loader is tested on
a hand-built two-quarter fixture whose figures are Apple's FY2023 10-K and Q3
10-Q numbers as published. The zip layout, the encoding and the exact header
strings are the things most likely to differ on the first live file; the loader
reads by header name rather than position and raises on a header it does not
recognise, so a difference will be loud rather than silent.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union

from . import paths
from .edgar import IFRS_TAGS, KEY_TAGS, SEC_RATE_PER_SECOND, cik_to_str, default_user_agent
from .http import HttpTransport, Transport
from .store import FetchError

__all__ = [
    "DeraClient",
    "Submission",
    "Fact",
    "Quarter",
    "PlannedCall",
    "quarter_url",
    "quarter_label",
    "parse_quarter_label",
    "load_quarter",
    "load_quarters",
    "metric_facts",
    "first_reported",
    "as_known_on",
    "annual_series",
    "quarterly_series",
    "derive_fourth_quarters",
    "BASE_URL",
    "SUB_COLUMNS",
    "NUM_COLUMNS",
]

BASE_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets"

# The columns the loader relies on, by name. sub.txt has 36 columns and num.txt has
# nine in the plain data sets (the Notes variant adds dimension columns, handled
# below); only these are read, so an added column is harmless and a missing one
# is an error.
SUB_COLUMNS = ("adsh", "cik", "name", "sic", "form", "period", "fy", "fp", "filed", "accepted", "prevrpt")
NUM_COLUMNS = ("adsh", "tag", "version", "coreg", "ddate", "qtrs", "uom", "value")

# In the Financial Statement *and Notes* sets, num.txt carries a dimension hash and
# this value means "no dimensions": the consolidated, whole-company number.
NO_DIMENSIONS = "0x00000000"

_LABEL = re.compile(r"^(\d{4})[qQ]([1-4])$")


def quarter_label(year: int, q: int) -> str:
    if q not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1..4, not {q}")
    return f"{year}q{q}"


def parse_quarter_label(label: str) -> Tuple[int, int]:
    m = _LABEL.match(label.strip())
    if not m:
        raise ValueError(f"expected a label like 2024q1, not {label!r}")
    return int(m.group(1)), int(m.group(2))


def quarter_url(year: int, q: int) -> str:
    return f"{BASE_URL}/{quarter_label(year, q)}.zip"


def _yyyymmdd(v: str) -> Optional[date]:
    s = (v or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _float(v: str) -> Optional[float]:
    s = (v or "").strip()
    if not s:
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _int(v: str) -> Optional[int]:
    s = (v or "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


@dataclass(frozen=True)
class PlannedCall:
    label: str
    url: str
    path: Path


@dataclass(frozen=True)
class Submission:
    adsh: str
    cik: str
    name: str
    form: str
    period: Optional[date]
    fiscal_year: Optional[int]
    fiscal_period: Optional[str]
    filed: Optional[date]
    accepted: Optional[str]
    superseded: bool
    sic: Optional[str] = None

    @property
    def is_annual_report(self) -> bool:
        return self.form.upper().startswith(("10-K", "20-F", "40-F"))


@dataclass(frozen=True)
class Fact:
    """One number, with the submission it came from joined on.

    ``qtrs`` is the length of the period in quarters (0 for an instant) and
    ``ddate`` its end. ``filed`` is the point-in-time stamp: the market could not
    have known this value before that date.
    """

    cik: str
    tag: str
    version: str
    ddate: date
    qtrs: int
    unit: str
    value: float
    adsh: str
    filed: date
    form: str
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[str] = None
    coreg: str = ""
    derived: bool = False

    @property
    def taxonomy(self) -> str:
        return self.version.split("/")[0]

    @property
    def is_instant(self) -> bool:
        return self.qtrs == 0

    @property
    def covers_a_year(self) -> bool:
        return self.qtrs == 4

    @property
    def covers_a_quarter(self) -> bool:
        return self.qtrs == 1

    @property
    def period_key(self) -> Tuple[date, int]:
        return (self.ddate, self.qtrs)


@dataclass
class Quarter:
    """One data set (or several concatenated), filtered on load."""

    label: str
    submissions: Dict[str, Submission] = field(default_factory=dict)
    facts: List[Fact] = field(default_factory=list)
    rows_read: int = 0
    rows_kept: int = 0

    def for_cik(self, cik: Union[int, str]) -> List[Fact]:
        c = cik_to_str(cik)
        return [f for f in self.facts if f.cik == c]


# -- reading the tables ---------------------------------------------------------


Source = Union[Path, str, zipfile.ZipFile, bytes]


def _open_table(source: Source, name: str) -> io.TextIOBase:
    """A text handle on ``name`` inside a zip, a directory, or raw zip bytes."""
    if isinstance(source, bytes):
        source = zipfile.ZipFile(io.BytesIO(source))
    if isinstance(source, zipfile.ZipFile):
        members = {m.lower(): m for m in source.namelist()}
        if name not in members:
            raise FetchError(f"{name} is not in the archive; members are {sorted(members)}")
        return io.TextIOWrapper(source.open(members[name]), encoding="utf-8", errors="replace", newline="")
    p = Path(source)
    if p.is_dir():
        f = p / name
        if not f.exists():
            raise FetchError(f"{f} does not exist")
        return open(f, encoding="utf-8", errors="replace", newline="")
    if p.suffix.lower() == ".zip":
        return _open_table(zipfile.ZipFile(p), name)
    raise FetchError(f"{p} is neither a directory nor a zip")


def _rows(handle: io.TextIOBase, required: Sequence[str], name: str) -> Iterator[Dict[str, str]]:
    reader = csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
    try:
        header = next(reader)
    except StopIteration:
        raise FetchError(f"{name} is empty")
    header = [h.strip().lstrip("\ufeff") for h in header]
    missing = [c for c in required if c not in header]
    if missing:
        raise FetchError(f"{name} lacks columns {missing}; header was {header}. The format has changed.")
    idx = {h: i for i, h in enumerate(header)}
    n = len(header)
    for row in reader:
        if not row:
            continue
        if len(row) < n:
            row = row + [""] * (n - len(row))
        yield {h: row[i] for h, i in idx.items()}


def read_submissions(source: Source) -> Dict[str, Submission]:
    with _open_table(source, "sub.txt") as h:
        out: Dict[str, Submission] = {}
        for r in _rows(h, SUB_COLUMNS, "sub.txt"):
            adsh = r["adsh"].strip()
            if not adsh:
                continue
            out[adsh] = Submission(
                adsh=adsh,
                cik=cik_to_str(r["cik"].strip() or "0"),
                name=r["name"].strip(),
                form=r["form"].strip(),
                period=_yyyymmdd(r["period"]),
                fiscal_year=_int(r["fy"]),
                fiscal_period=(r["fp"].strip() or None),
                filed=_yyyymmdd(r["filed"]),
                accepted=(r["accepted"].strip() or None),
                superseded=r["prevrpt"].strip() == "1",
                sic=(r["sic"].strip() or None),
            )
    return out


def _cik_set(ciks: Optional[Iterable[Union[int, str]]]) -> Optional[Set[str]]:
    if ciks is None:
        return None
    return {cik_to_str(c) for c in ciks}


def load_quarter(
    source: Source,
    *,
    label: str = "",
    ciks: Optional[Iterable[Union[int, str]]] = None,
    tags: Optional[Iterable[str]] = None,
    forms: Optional[Iterable[str]] = None,
    include_coreg: bool = False,
) -> Quarter:
    """Read one data set, keeping only the companies, tags and forms asked for.

    The whole of ``num.txt`` is streamed once. Filtering while streaming is what
    keeps a 100 MB quarter from becoming a few gigabytes of dataclasses.
    """
    wanted_ciks = _cik_set(ciks)
    wanted_tags = set(tags) if tags is not None else None
    wanted_forms = {f.upper() for f in forms} if forms is not None else None

    subs = read_submissions(source)
    keep_subs = {
        adsh: s
        for adsh, s in subs.items()
        if (wanted_ciks is None or s.cik in wanted_ciks) and (wanted_forms is None or s.form.upper() in wanted_forms)
    }
    q = Quarter(label=label or (str(source) if not isinstance(source, (bytes, zipfile.ZipFile)) else ""), submissions=keep_subs)
    if not keep_subs:
        return q

    with _open_table(source, "num.txt") as h:
        for r in _rows(h, NUM_COLUMNS, "num.txt"):
            q.rows_read += 1
            adsh = r["adsh"].strip()
            s = keep_subs.get(adsh)
            if s is None:
                continue
            tag = r["tag"].strip()
            if wanted_tags is not None and tag not in wanted_tags:
                continue
            coreg = r["coreg"].strip()
            if coreg and not include_coreg:
                continue
            dimh = r.get("dimh")
            if dimh is not None and dimh.strip() not in ("", NO_DIMENSIONS):
                continue
            value = _float(r["value"])
            ddate = _yyyymmdd(r["ddate"])
            qtrs = _int(r["qtrs"])
            if value is None or ddate is None or qtrs is None or s.filed is None:
                continue
            q.facts.append(
                Fact(
                    cik=s.cik,
                    tag=tag,
                    version=r["version"].strip(),
                    ddate=ddate,
                    qtrs=qtrs,
                    unit=r["uom"].strip(),
                    value=value,
                    adsh=adsh,
                    filed=s.filed,
                    form=s.form,
                    fiscal_year=s.fiscal_year,
                    fiscal_period=s.fiscal_period,
                    coreg=coreg,
                )
            )
            q.rows_kept += 1
    q.facts.sort(key=lambda f: (f.cik, f.tag, f.ddate, f.qtrs, f.filed, f.adsh))
    return q


def load_quarters(sources: Sequence[Tuple[str, Source]], **kwargs: Any) -> Quarter:
    """Several data sets concatenated. Facts stay sorted; submissions merge by accession."""
    merged = Quarter(label="+".join(lbl for lbl, _ in sources))
    for label, src in sources:
        q = load_quarter(src, label=label, **kwargs)
        merged.submissions.update(q.submissions)
        merged.facts.extend(q.facts)
        merged.rows_read += q.rows_read
        merged.rows_kept += q.rows_kept
    merged.facts.sort(key=lambda f: (f.cik, f.tag, f.ddate, f.qtrs, f.filed, f.adsh))
    return merged


# -- the questions --------------------------------------------------------------


def metric_facts(facts: Iterable[Fact], cik: Union[int, str], metric: str) -> List[Fact]:
    """Facts for one metric, using the first tag in the preference list the company actually uses.

    Same preference lists as ``edgar.KEY_TAGS``; us-gaap first, then ifrs-full,
    so a 20-F filer is found rather than silently empty.
    """
    c = cik_to_str(cik)
    mine = [f for f in facts if f.cik == c]
    for taxonomy, table in (("us-gaap", KEY_TAGS), ("ifrs-full", IFRS_TAGS)):
        for tag in table.get(metric, []):
            hits = [f for f in mine if f.tag == tag and f.taxonomy == taxonomy]
            if hits:
                return hits
    return []


def first_reported(facts: Iterable[Fact], *, ddate: date, qtrs: int) -> Optional[Fact]:
    """The figure as the market first saw it: the earliest filing carrying this period."""
    hits = [f for f in facts if f.ddate == ddate and f.qtrs == qtrs]
    if not hits:
        return None
    return min(hits, key=lambda f: (f.filed, f.adsh))


def as_known_on(
    facts: Iterable[Fact],
    on_date: date,
    *,
    ddate: Optional[date] = None,
    qtrs: Optional[int] = None,
) -> Optional[Fact]:
    """The value that was public on ``on_date``, restatements after that date ignored.

    With ``ddate`` unset, the latest period the market knew about on that date;
    with it set, that period specifically. ``qtrs`` narrows to instants (0),
    quarters (1) or years (4).
    """
    eligible = [
        f for f in facts
        if f.filed <= on_date and (ddate is None or f.ddate == ddate) and (qtrs is None or f.qtrs == qtrs)
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda f: (f.ddate, f.filed, f.adsh))


def annual_series(facts: Iterable[Fact], *, on_date: Optional[date] = None) -> List[Fact]:
    """One fact per fiscal year, oldest first. Point in time if ``on_date`` is given, else as first reported."""
    years = sorted({f.ddate for f in facts if f.covers_a_year})
    out: List[Fact] = []
    for d in years:
        if on_date is None:
            f = first_reported(facts, ddate=d, qtrs=4)
        else:
            f = as_known_on(facts, on_date, ddate=d, qtrs=4)
        if f is not None:
            out.append(f)
    return out


def derive_fourth_quarters(facts: Sequence[Fact]) -> List[Fact]:
    """FY minus the nine-month figure, stamped with the 10-K's filing date.

    For every annual fact, the year-to-date fact (``qtrs == 3``) ending one quarter
    earlier is looked up as the market knew it when the 10-K was filed, and the
    difference is returned as a ``qtrs == 1`` fact ending on the fiscal year end
    with ``derived=True``. Nothing is returned for a year whose nine-month figure
    is absent; a hole is better than a guess.
    """
    out: List[Fact] = []
    by_key: Dict[Tuple[str, str], List[Fact]] = {}
    for f in facts:
        by_key.setdefault((f.cik, f.tag), []).append(f)
    for (cik, tag), group in by_key.items():
        already = {f.ddate for f in group if f.qtrs == 1}
        for fy in sorted({f.ddate for f in group if f.qtrs == 4}):
            if fy in already:
                continue
            # Every filing of this fiscal year, newest first: the derivation is
            # repeated per 10-K so a restated year gives a restated fourth quarter.
            annuals = sorted((f for f in group if f.ddate == fy and f.qtrs == 4), key=lambda f: f.filed)
            for a in annuals:
                nine = [
                    f for f in group
                    if f.qtrs == 3 and f.filed <= a.filed and 75 <= (fy - f.ddate).days <= 100
                ]
                if not nine:
                    continue
                ytd = max(nine, key=lambda f: (f.filed, f.adsh))
                out.append(
                    replace(
                        a,
                        qtrs=1,
                        value=a.value - ytd.value,
                        derived=True,
                        fiscal_period="Q4",
                    )
                )
    return out


def quarterly_series(facts: Sequence[Fact], *, on_date: Optional[date] = None) -> List[Fact]:
    """One fact per quarter, oldest first, with fourth quarters derived where they are missing."""
    with_q4 = list(facts) + derive_fourth_quarters(facts)
    ends = sorted({f.ddate for f in with_q4 if f.qtrs == 1})
    out: List[Fact] = []
    for d in ends:
        f = first_reported(with_q4, ddate=d, qtrs=1) if on_date is None else as_known_on(with_q4, on_date, ddate=d, qtrs=1)
        if f is not None:
            out.append(f)
    return out


# -- fetching --------------------------------------------------------------------


class DeraClient:
    """Download quarter zips into ``data/cache/dera/``. One request per quarter, never repeated."""

    def __init__(
        self,
        transport: Optional[Transport] = None,
        *,
        cache_dir: Optional[Path] = None,
        user_agent: Optional[str] = None,
    ):
        self.user_agent = user_agent or default_user_agent()
        self.transport = transport or HttpTransport(self.user_agent, rate_per_second=SEC_RATE_PER_SECOND)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else paths.DERA_CACHE

    def path_for(self, year: int, q: int) -> Path:
        return self.cache_dir / f"{quarter_label(year, q)}.zip"

    def plan(self, quarters: Iterable[Tuple[int, int]]) -> List[PlannedCall]:
        return [PlannedCall(quarter_label(y, q), quarter_url(y, q), self.path_for(y, q)) for y, q in quarters]

    def fetch(self, year: int, q: int, *, force: bool = False) -> Path:
        """The zip on disk, downloading it if absent. A truncated download is never kept."""
        p = self.path_for(year, q)
        if p.exists() and not force:
            return p
        url = quarter_url(year, q)
        raw = self.transport.get(url, headers={"User-Agent": self.user_agent, "Accept": "application/zip"}, timeout=600.0)
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = {n.lower() for n in zf.namelist()}
        except zipfile.BadZipFile as e:
            raise FetchError(f"{url} did not return a zip ({len(raw)} bytes)", url=url) from e
        if "num.txt" not in names or "sub.txt" not in names:
            raise FetchError(f"{url} is a zip without num.txt/sub.txt; members: {sorted(names)}", url=url)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".zip.part")
        tmp.write_bytes(raw)
        tmp.replace(p)
        return p

    def cached(self) -> List[Path]:
        return sorted(self.cache_dir.glob("*.zip")) if self.cache_dir.exists() else []
