"""Helpers for strict first-party search requests."""

from dataclasses import dataclass
import re
from urllib.parse import urlparse


_OFFICIAL_REQUEST_RE = re.compile(
    r"\b(?:official|primary source|first[ -]party|central bank)\b", re.IGNORECASE
)
_AUTHORITY_PATTERNS = (
    ("South African Reserve Bank", re.compile(r"\b(?:SARB|South African Reserve Bank)\b", re.IGNORECASE), "resbank.co.za"),
    ("Federal Reserve data", re.compile(r"\b(?:Federal Reserve|FRED)\b", re.IGNORECASE), "fred.stlouisfed.org"),
)
_USD_ZAR_RE = re.compile(r"\bUSD\s*[/:-]\s*ZAR\b|\bUS\s+Dollar\b.{0,40}\bSouth African Rand\b", re.IGNORECASE)
_RATE_RE = re.compile(r"\b(?:exchange|currency)\s+rate\b|\brate\b", re.IGNORECASE)


@dataclass(frozen=True)
class OfficialSourcePlan:
    authority: str
    host: str

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset({self.host, f"www.{self.host}"})

    def search_query(self, query: str) -> str:
        return f"site:{self.host} {query}"


def official_source_plan(query: str) -> OfficialSourcePlan | None:
    """Resolve known official authorities for explicit first-party requests."""
    text = query or ""
    if not _OFFICIAL_REQUEST_RE.search(text):
        return None

    matches = []
    for authority, pattern, host in _AUTHORITY_PATTERNS:
        match = pattern.search(text)
        if match:
            matches.append((match.start(), authority, host))
    if _USD_ZAR_RE.search(text) and _RATE_RE.search(text):
        sarb_match = next((item for item in matches if item[2] == "resbank.co.za"), None)
        if sarb_match:
            return OfficialSourcePlan(
                authority="South African Reserve Bank",
                host="resbank.co.za",
            )
        fred_match = next((item for item in matches if item[2] == "fred.stlouisfed.org"), None)
        if fred_match:
            return OfficialSourcePlan(
                authority="Federal Reserve data",
                host="fred.stlouisfed.org",
            )
    if matches:
        _, authority, host = min(matches)
        return OfficialSourcePlan(authority=authority, host=host)

    if _USD_ZAR_RE.search(text) and _RATE_RE.search(text):
        return OfficialSourcePlan(
            authority="South African Reserve Bank",
            host="resbank.co.za",
        )
    return None


def has_fetched_official_content(text: str, plan: OfficialSourcePlan) -> bool:
    """Return whether fetched page content came from the planned host."""
    for match in re.finditer(
        r"^\[CONTENT(?:\s+\d+)?\]\s+From:\s*(\S+)", text or "", re.MULTILINE
    ):
        try:
            hostname = (urlparse(match.group(1)).hostname or "").lower()
        except ValueError:
            continue
        if hostname in plan.allowed_hosts:
            return True
    return False