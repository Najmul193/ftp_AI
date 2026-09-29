"""Pseudonymisation: real identifiers become opaque tokens before text leaves.

Not encryption. A model cannot reason over ciphertext, but it reasons about
`BR_K7Q` as well as about "Gulshan": it is the same entity in every sentence.
The mapping stays here, on the server, and the answer is re-hydrated on return.

Tokens are random per vault, never derived from the identifier, so the same
branch is `BR_K7Q` in one conversation and `BR_2MX` in the next. A provider
that logs every request still cannot link them, and nothing about a token
(its number, its order) says which branch it stands for.

PURE: no I/O. Persist a vault with `to_dict` / `from_dict`.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Iterable

#: The entity kinds that are ever tokenised. Accounts and users are listed so
#: the vault can recognise them in text a person typed, but the field policy
#: never lets them leave even as tokens (see `policy.HARD_DROP`).
KINDS = ("BR", "PRD", "DIST", "DIV", "ACCT", "USER")

#: Crockford-style alphabet: no I/L/O/U, so a token is never misread as a word
#: or a number when a model copies it.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TOKEN_RE = re.compile(r"\b(" + "|".join(KINDS) + r")_([0-9A-HJKMNP-TV-Z]{3})\b")


def is_short_code(s: str) -> bool:
    """An upper-case letter code of up to four letters, like "NET" or "SYL"."""
    return s.isalpha() and s.isupper() and len(s) <= 4


@dataclass(frozen=True, slots=True)
class Entity:
    kind: str
    #: The identifier as stored (branch code, product code, ...).
    key: str
    #: What a person reads when the token is re-hydrated.
    display: str
    #: Other spellings a user might type ("Gulshan", "142", "Gulshan Branch").
    aliases: tuple[str, ...] = ()


@dataclass
class Vault:
    """Token <-> entity map for one conversation or one generation run."""

    _by_token: dict[str, Entity] = field(default_factory=dict)
    _by_key: dict[tuple[str, str], str] = field(default_factory=dict)

    # --- tokenising ------------------------------------------------------ #

    def token(self, kind: str, key: str, display: str | None = None,
              aliases: Iterable[str] = ()) -> str:
        """The token for an entity, minted on first sight."""
        if kind not in KINDS:
            raise ValueError(f"unknown entity kind {kind!r}")
        k = (kind, str(key))
        if k in self._by_key:
            return self._by_key[k]
        while True:
            tok = f"{kind}_{''.join(secrets.choice(_ALPHABET) for _ in range(3))}"
            if tok not in self._by_token:
                break
        self._by_token[tok] = Entity(kind, str(key), display or str(key),
                                     tuple(a for a in aliases if a))
        self._by_key[k] = tok
        return tok

    def register(self, entities: Iterable[Entity]) -> None:
        """Pre-mint tokens for everything a user might mention by name."""
        for e in entities:
            self.token(e.kind, e.key, e.display, e.aliases)

    def mask_text(self, text: str) -> str:
        """Replace every known spelling of a registered entity with its token.

        Used on text a person typed ("why did Gulshan drop?"). Longest spelling
        first, so "Gulshan Avenue" is not half-replaced by "Gulshan". Matching
        is on word boundaries and case-insensitive; a purely numeric alias
        (a branch code) is only replaced where it reads as a code -- after
        "branch", "br", "code" or "#" -- never inside an amount.
        """
        spellings: list[tuple[str, str]] = []
        for tok, e in self._by_token.items():
            for s in {e.display, e.key, *e.aliases}:
                if s and s.strip():
                    spellings.append((s.strip(), tok))
        spellings.sort(key=lambda p: len(p[0]), reverse=True)

        out = text
        # A name with its code beside it ("Dhaka Main 105", "Dhaka Main (105)")
        # is that one branch -- names repeat, codes do not. Settled first, so
        # the bare-name pass below cannot give it to a namesake.
        for tok, e in self._by_token.items():
            if not e.key.isdigit():
                continue
            for n in e.aliases:
                if n and len(n) >= 3 and not n.isdigit():
                    pat = re.compile(r"(?i)(?<![\w])" + re.escape(n) + r"\s*[,(-]?\s*"
                                     + re.escape(e.key) + r"\)?(?![\w])")
                    out = pat.sub(tok, out)
        for s, tok in spellings:
            if s.isdigit():
                pat = re.compile(
                    r"(?i)\b(branch|br|code|sol|product|prd)\s*(?:no\.?|#|:|-)?\s*"
                    + re.escape(s) + r"\b")
                def as_code(m: re.Match[str], t: str = tok) -> str:
                    return f"{m.group(1)} {t}"
                out = pat.sub(as_code, out)
            elif is_short_code(s):
                # "NET" is Netrokona's code and also a word: capitals only.
                pat = re.compile(r"(?<![\w])" + re.escape(s) + r"(?![\w])")
                out = pat.sub(tok, out)
            elif len(s) >= 3:
                pat = re.compile(r"(?i)(?<![\w])" + re.escape(s) + r"(?![\w])")
                out = pat.sub(tok, out)
        return out

    # --- re-hydrating ---------------------------------------------------- #

    def rehydrate(self, text: str) -> str:
        """Tokens back to the names a person reads.

        A token the vault never issued (a model inventing `BR_ZZZ`) is left as
        it is: shown masked, which is the safe way to be wrong.
        """
        def swap(m: re.Match[str]) -> str:
            e = self._by_token.get(m.group(0))
            return e.display if e else m.group(0)
        return _TOKEN_RE.sub(swap, text)

    def entities(self) -> list[Entity]:
        return list(self._by_token.values())

    def entity(self, token: str) -> Entity | None:
        return self._by_token.get(token)

    def tokens_in(self, text: str) -> set[str]:
        return {m.group(0) for m in _TOKEN_RE.finditer(text)}

    # --- DLP support ----------------------------------------------------- #

    def raw_spellings(self, kinds: Iterable[str] | None = None) -> set[str]:
        """Every real-world spelling this vault knows, for the DLP scan."""
        want = set(kinds) if kinds is not None else set(KINDS)
        out: set[str] = set()
        for e in self._by_token.values():
            if e.kind in want:
                out.update(s for s in (e.display, e.key, *e.aliases) if s)
        return out

    def __len__(self) -> int:
        return len(self._by_token)

    # --- persistence ----------------------------------------------------- #

    def to_dict(self) -> dict:
        return {tok: [e.kind, e.key, e.display, list(e.aliases)]
                for tok, e in self._by_token.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "Vault":
        v = cls()
        for tok, (kind, key, display, aliases) in d.items():
            e = Entity(kind, key, display, tuple(aliases))
            v._by_token[tok] = e
            v._by_key[(kind, key)] = tok
        return v
