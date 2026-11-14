"""The one canonical degree list and normaliser (FR-5, AD-21).

* A degree id is a stable lower-case slug that is never reused or removed.
* ``profile`` (Preferences) and ``listings`` (the eligibility evaluator, Epic 2) both call
  this module; no other module keeps a degree list or alias table.
* :func:`normalize` matches case-insensitively and ignores punctuation and spacing, so
  "B.E.", "BE", "b. e" and "B.Tech" all give ``btech``. A trailing qualifier such as
  "in Computer Science", "(CSE)", ", ECE" or an unspaced "-CSE" is ignored.

Pure Python, data in the module, no I/O.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Degree:
    id: str
    label: str
    aliases: tuple[str, ...] = ()


def _d(degree_id: str, label: str, *aliases: str) -> Degree:
    return Degree(degree_id, label, aliases)


# fmt: off
_DEGREES: tuple[Degree, ...] = (
    _d("btech", "B.Tech / B.E.", "b.tech", "be", "b.e", "bachelor of technology",
       "bachelor of engineering", "bachelors of technology", "bachelors of engineering",
       "bachelor's of technology", "bachelor's of engineering", "b.engg", "beng", "b.eng"),
    _d("mtech", "M.Tech / M.E.", "m.tech", "me", "m.e", "master of technology",
       "master of engineering", "masters of technology", "masters of engineering",
       "master's of technology", "master's of engineering", "m.engg", "meng", "m.eng"),
    _d("bsc", "B.Sc", "b.sc", "bs", "b.s", "bachelor of science", "bachelors of science",
       "bachelor's of science"),
    _d("msc", "M.Sc", "m.sc", "ms", "m.s", "master of science", "masters of science",
       "master's of science"),
    _d("bca", "BCA", "bachelor of computer applications", "bachelor of computer application",
       "bachelors of computer applications"),
    _d("mca", "MCA", "master of computer applications", "master of computer application",
       "masters of computer applications"),
    _d("bba", "BBA", "bachelor of business administration",
       "bachelors of business administration", "bbm", "bachelor of business management"),
    _d("mba", "MBA", "master of business administration", "masters of business administration"),
    _d("pgdm", "PGDM / PGDBA / PG Diploma", "pgdba", "pg diploma", "post graduate diploma",
       "postgraduate diploma", "post graduate diploma in management",
       "post graduate diploma in business administration", "pgd"),
    _d("bcom", "B.Com", "b.com", "bachelor of commerce", "bachelors of commerce"),
    _d("mcom", "M.Com", "m.com", "master of commerce", "masters of commerce"),
    _d("ba", "B.A.", "b.a", "bachelor of arts", "bachelors of arts"),
    _d("ma", "M.A.", "m.a", "master of arts", "masters of arts"),
    _d("barch", "B.Arch", "b.arch", "bachelor of architecture", "bachelors of architecture"),
    _d("bdes", "B.Des", "b.des", "bachelor of design", "bachelors of design"),
    _d("bpharm", "B.Pharm", "b.pharm", "b.pharma", "bachelor of pharmacy",
       "bachelors of pharmacy"),
    _d("mpharm", "M.Pharm", "m.pharm", "m.pharma", "master of pharmacy", "masters of pharmacy"),
    _d("mbbs", "MBBS", "m.b.b.s", "bachelor of medicine and bachelor of surgery",
       "bachelor of medicine, bachelor of surgery"),
    _d("bds", "BDS", "b.d.s", "bachelor of dental surgery"),
    _d("llb", "LLB", "ll.b", "bachelor of laws", "bachelor of law", "ba llb", "ba ll.b",
       "bba llb", "bba ll.b", "b.a. ll.b"),
    _d("llm", "LLM", "ll.m", "master of laws", "master of law"),
    _d("integrated", "Integrated / Dual Degree", "integrated degree", "dual degree",
       "integrated m.tech", "integrated mtech", "integrated m.sc", "integrated msc",
       "integrated b.tech m.tech", "integrated btech mtech", "b.tech m.tech dual degree",
       "btech mtech dual degree", "bs-ms", "bs ms", "b.s.-m.s", "integrated mba",
       "integrated ma", "five year integrated", "5 year integrated", "dual degree b.tech m.tech"),
    _d("phd", "Ph.D.", "ph.d", "doctor of philosophy", "doctorate", "dphil"),
    _d("diploma", "Diploma", "polytechnic diploma", "polytechnic", "diploma in engineering"),
    _d("other", "Other"),
)
# fmt: on

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
# A qualifier after the degree name: "B.Tech in CSE", "B.E. (ECE)", "MBA, Finance",
# "M.Sc - Physics".
_QUALIFIER = re.compile(r"\s+(?:in|with)\s+|[(,;/]|\s[-–—]\s")


def _key(term: str) -> str:
    """Case-folded, letters and digits only."""
    return _NON_ALNUM.sub("", term.casefold())


def _build() -> tuple[dict[str, Degree], dict[str, str]]:
    by_id: dict[str, Degree] = {}
    alias_to_id: dict[str, str] = {}
    for degree in _DEGREES:
        if degree.id in by_id:
            raise ValueError(f"duplicate degree id {degree.id!r}")
        if not re.fullmatch(r"[a-z]+", degree.id):
            raise ValueError(f"degree id {degree.id!r} is not a lower-case slug")
        by_id[degree.id] = degree
        for term in (degree.id, degree.label, *degree.aliases):
            key = _key(term)
            owner = alias_to_id.setdefault(key, degree.id)
            if owner != degree.id:
                raise ValueError(f"alias {term!r} belongs to {owner!r} and {degree.id!r}")
    return by_id, alias_to_id


_BY_ID, _ALIAS_TO_ID = _build()

DEGREE_IDS: tuple[str, ...] = tuple(d.id for d in _DEGREES)


def all_degrees() -> tuple[Degree, ...]:
    """Every canonical degree, in display order (``other`` last)."""
    return _DEGREES


def is_degree_id(degree_id: str) -> bool:
    return degree_id in _BY_ID


def label(degree_id: str) -> str:
    """The display label of ``degree_id``; the id itself when it is not a canonical id."""
    degree = _BY_ID.get(degree_id)
    return degree.label if degree is not None else degree_id


def normalize(text: str) -> str | None:
    """The canonical degree id ``text`` names, or None when it names none.

    The whole text is tried first, then the part before a qualifier ("B.Tech in CSE").
    """
    found = _ALIAS_TO_ID.get(_key(text))
    if found is not None:
        return found
    head = _QUALIFIER.split(text.casefold(), maxsplit=1)[0]
    key = _key(head)
    found = _ALIAS_TO_ID.get(key) if key else None
    if found is not None:
        return found
    # An unspaced dash qualifier ("B.Tech-CSE", "M.Sc-Physics"): split at each dash, left to
    # right, and accept the first left part that is a known alias (so "BS-MS" stays whole).
    for match in re.finditer(r"[-–—]", head):
        left = _key(head[: match.start()])
        if left and left in _ALIAS_TO_ID:
            return _ALIAS_TO_ID[left]
    return None
