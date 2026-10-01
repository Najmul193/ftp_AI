"""Who is who in Bangladesh Bank's bank-wise tables.

The tables name banks by short codes ("THE CITY", "EBL", "NRBBL"); people
name them by what they are called ("City Bank", "Eastern", "DBBL"). This is
the directory between the two: each code with its full name (as Bangladesh
Bank's own bank list spells it, refreshed from that list on every collection),
its id in that list, its group, whether it is an Islamic bank, and the names
people use for it.

PURE: no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher


@dataclass(frozen=True)
class Bank:
    code: str            # as in the tables
    name: str            # full name, as Bangladesh Bank's list spells it
    bb_id: str           # its id in that list
    group: str           # SCB | DFI | PCB | FB
    islamic: bool = False
    aliases: tuple[str, ...] = ()


_B = Bank
DIRECTORY: tuple[Bank, ...] = (
    # State-owned commercial banks
    _B("SONALI", "Sonali Bank PLC", "1", "SCB"),
    _B("JANATA", "Janata Bank PLC", "2", "SCB"),
    _B("AGRANI", "Agrani Bank PLC", "3", "SCB"),
    _B("RUPALI", "Rupali Bank PLC", "4", "SCB"),
    _B("BASIC", "BASIC Bank PLC", "9", "SCB", aliases=("Bangladesh Small Industries and Commerce Bank",)),
    _B("BDBL", "Bangladesh Development Bank PLC", "50", "SCB"),
    # Specialised banks
    _B("BKB", "Bangladesh Krishi Bank", "5", "DFI", aliases=("Krishi Bank",)),
    _B("RAKUB", "Rajshahi Krishi Unnayan Bank", "7", "DFI"),
    _B("PKB", "Probashi Kollyan Bank", "63", "DFI", aliases=("Probashi Kallyan Bank",)),
    # Private commercial banks
    _B("AB-BANK", "AB Bank PLC", "12", "PCB", aliases=("AB",)),
    _B("AL-ARAFAH", "Al-Arafah Islami Bank PLC", "24", "PCB", True, ("Al Arafah", "AIBL")),
    _B("BANK ASIA", "Bank Asia PLC.", "35", "PCB"),
    _B("BCBL", "Bangladesh Commerce Bank Limited", "36", "PCB"),
    _B("Bengal", "Bengal Commercial Bank PLC.", "67", "PCB", aliases=("Bengal Bank",)),
    _B("BRAC", "BRAC Bank PLC", "39", "PCB"),
    _B("CBBL", "Community Bank Bangladesh PLC.", "62", "PCB", aliases=("Community Bank",)),
    _B("Citizens", "Citizens Bank PLC", "68", "PCB"),
    _B("DHAKA", "Dhaka Bank PLC", "23", "PCB"),
    _B("DUTCH-BANGLA", "Dutch-Bangla Bank PLC", "26", "PCB", aliases=("DBBL", "Dutch Bangla")),
    _B("EBL", "Eastern Bank PLC", "19", "PCB", aliases=("Eastern",)),
    _B("EXIM", "Export Import Bank of Bangladesh PLC", "29", "PCB", True, ("EXIM Bank",)),
    _B("FIRST SECU", "First Security Islami Bank PLC", "31", "PCB", True, ("FSIBL", "First Security")),
    _B("GIBL", "Global Islami Bank PLC", "56", "PCB", True, ("Global Islami",)),
    _B("ICB", "ICB Islamic Bank Ltd.", "18", "PCB", True, ("ICB Islamic",)),
    _B("IFIC", "IFIC Bank PLC", "13", "PCB"),
    _B("ISLAMI", "Islami Bank Bangladesh PLC", "14", "PCB", True, ("IBBL", "Islami Bank")),
    _B("JAMUNA", "Jamuna Bank PLC", "37", "PCB"),
    _B("MDBL", "Midland Bank Limited", "55", "PCB", aliases=("Midland",)),
    _B("MERCANTILE", "Mercantile Bank PLC", "27", "PCB", aliases=("MBL",)),
    _B("MGBL", "Meghna Bank PLC", "54", "PCB", aliases=("Meghna",)),
    _B("MMBL", "Modhumoti Bank PLC", "58", "PCB", aliases=("Modhumoti",)),
    _B("MUTUAL TRUST", "Mutual Trust Bank PLC", "34", "PCB", aliases=("MTB",)),
    _B("NBL", "National Bank PLC", "15", "PCB", aliases=("National Bank",)),
    _B("NCCBL", "National Credit & Commerce Bank PLC", "20", "PCB", aliases=("NCC Bank", "NCC")),
    _B("NRBBL", "NRB Bank PLC", "59", "PCB", aliases=("NRB Bank", "NRB")),
    _B("NRBCBL", "NRBC Bank PLC", "51", "PCB", aliases=("NRB Commercial Bank", "NRBC")),
    _B("ONE BANK", "One Bank PLC", "28", "PCB", aliases=("One",)),
    _B("Padma", "Padma Bank PLC", "57", "PCB"),
    _B("PREMIER", "The Premier Bank PLC", "30", "PCB", aliases=("Premier Bank",)),
    _B("PRIME", "Prime Bank PLC", "21", "PCB"),
    _B("PUBALI", "Pubali Bank PLC", "10", "PCB"),
    _B("SBACBL", "SBAC Bank PLC", "53", "PCB",
       aliases=("SBAC", "South Bangla Agriculture and Commerce Bank")),
    _B("SHAHJALAL", "Shahjalal Islami Bank PLC", "38", "PCB", True, ("SJIBL", "Shahjalal")),
    _B("SHIMANTO", "Shimanto Bank PLC", "60", "PCB"),
    _B("SIBL", "Social Islami Bank PLC", "25", "PCB", True, ("Social Islami",)),
    _B("SOUTHEAST", "Southeast Bank PLC", "22", "PCB"),
    _B("STANDARD", "Standard Islami Bank PLC", "32", "PCB", True, ("Standard Bank", "SIBPLC")),
    _B("THE CITY", "City Bank PLC", "16", "PCB", aliases=("City", "City Bank", "The City Bank")),
    _B("TRUST BANK", "Trust Bank PLC", "33", "PCB", aliases=("Trust",)),
    _B("UCBL", "United Commercial Bank PLC", "17", "PCB", aliases=("UCB", "United Commercial")),
    _B("UNBL", "Union Bank PLC", "52", "PCB", True, ("Union Bank",)),
    _B("UTTARA", "Uttara Bank PLC", "11", "PCB"),
    # Foreign banks
    _B("AL FALAH", "Bank Al-Falah Limited", "47", "FB", aliases=("Al-Falah", "Alfalah")),
    _B("CITI N.A.", "Citibank N.A", "45", "FB", aliases=("Citibank", "Citi")),
    _B("COMMERCIAL B.", "Commercial Bank of Ceylon Limited", "40", "FB", aliases=("Commercial Bank of Ceylon",)),
    _B("HABIB", "Habib Bank Ltd.", "42", "FB", aliases=("HBL",)),
    _B("HSBC", "The Hong Kong and Shanghai Banking Corporation. Ltd.", "48", "FB", aliases=("HSBC",)),
    _B("NBP", "National Bank of Pakistan", "44", "FB"),
    _B("SBI", "State Bank of India", "43", "FB"),
    _B("STAN.CHART", "Standard Chartered Bank", "41", "FB", aliases=("Standard Chartered", "SCB", "StanChart")),
    _B("WOORI", "Woori Bank", "46", "FB"),
)
BY_CODE: dict[str, Bank] = {b.code: b for b in DIRECTORY}

GROUP_LABELS = {"SCB": "State-owned", "DFI": "Specialised", "PCB": "Private", "FB": "Foreign"}


def with_names(names_by_id: dict[str, str] | None) -> dict[str, Bank]:
    """The directory with full names as Bangladesh Bank's list spells them
    today (a bank that renames keeps its code and id)."""
    if not names_by_id:
        return dict(BY_CODE)
    return {c: replace(b, name=names_by_id.get(b.bb_id, b.name)) for c, b in BY_CODE.items()}


def bank_of(code: str, directory: dict[str, Bank] | None = None) -> Bank:
    """A code's entry; a code the directory does not know is its own name."""
    d = directory or BY_CODE
    return d.get(code) or Bank(code, code.title(), "", "PCB")


def _norm(s: str) -> str:
    s = re.sub(r"\b(plc|ltd|limited|bank|the|of|n\.?a)\b\.?", " ", s.lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def resolve_bank(text: str, directory: dict[str, Bank] | None = None,
                 min_score: float = 0.75) -> Bank | None:
    """The bank meant by a name, alias or code ("city", "Eastern", "dbbl",
    "nrb bank"); None when nothing is close enough to be sure."""
    hits = search_banks(text, directory, limit=1)
    return hits[0][0] if hits and hits[0][1] >= min_score else None


def search_banks(text: str, directory: dict[str, Bank] | None = None,
                 limit: int = 8) -> list[tuple[Bank, float]]:
    """Banks matching `text`, best first, with a score in 0..1."""
    d = directory or BY_CODE
    q = _norm(text)
    raw = text.strip().lower()
    if not q and not raw:
        return []
    out = []
    for b in d.values():
        names = [b.code, b.name, *b.aliases]
        best = 0.0
        for n in names:
            if raw == n.lower() or (q and q == _norm(n)):
                best = 1.0
                break
            nn = _norm(n)
            if q and nn and (nn.startswith(q) or f" {q}" in f" {nn}"):
                best = max(best, 0.9 if len(q) >= 3 else 0.6)
            elif q and nn:
                best = max(best, SequenceMatcher(None, q, nn).ratio() * 0.85)
        if best > 0.45:
            out.append((b, round(best, 3)))
    out.sort(key=lambda x: (-x[1], x[0].name))
    return out[:limit]


def parse_bank_list(html: str) -> dict[str, str]:
    """{bb_id: full name} from the bank list on a bank-wise rate page."""
    from bs4 import BeautifulSoup

    sel = BeautifulSoup(html, "html.parser").find("select", {"name": "select_bank"})
    if sel is None:
        return {}
    return {o.get("value"): " ".join(o.get_text(" ", strip=True).split())
            for o in sel.find_all("option") if o.get("value")}
