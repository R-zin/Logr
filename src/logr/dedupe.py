"""Cross-source dedupe: the same hackathon listed on Devfolio and Luma shows once.

Two listings from *different* sources are the same event when their titles
match after normalization and their dates line up. Matching is deliberately
strict — a missed merge just shows a duplicate row, a wrong merge hides a
hackathon.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from logr.models import DATE_FIELDS, Hackathon, Source

# Generic words that say nothing about *which* hackathon it is.
_STOP = {
    "the", "a", "an", "of", "and", "for", "by", "x", "with", "in", "at", "on", "to",
    "hackathon", "hack", "hacks", "buildathon", "edition", "season", "official",
    "presents", "powered", "online", "offline", "virtual", "global", "national",
    "that", "this", "your", "you", "is", "are", "free",
}
_YEAR = re.compile(r"^(19|20)\d{2}$|^\d{2}$|^'\d{2}$")

# Which listing represents a merged group: most precise dates first.
SOURCE_RANK = {Source.DEVFOLIO: 0, Source.UNSTOP: 1, Source.DEVPOST: 2, Source.HACK2SKILL: 3, Source.LUMA: 4}


_VERSION = re.compile(r"(\d)\.(\d)")


def normalize(title: str) -> tuple[str, ...]:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    t = _VERSION.sub(r"\1_\2", t)  # "3.0" stays one token ("3_0"), not "3" + "0"
    t = re.sub(r"[^a-z0-9_']+", " ", t)
    return tuple(w for w in t.split() if w not in _STOP and not _YEAR.match(w))


def _distinctive(tokens: set[str]) -> set[str]:
    """Words that identify an event — not version numbers or tiny words."""
    return {w for w in tokens if len(w) >= 3 and not re.fullmatch(r"[\d_']+", w)}


def titles_match(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    sa, sb = set(a), set(b)
    small, big = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    # "Agents that act. Systems that answer." vs the same + " | Free Agentic-AI Hackathon"
    if small <= big and len(_distinctive(small)) >= 3:
        return True
    jaccard = len(sa & sb) / len(sa | sb)
    if jaccard < 0.5:
        return False
    return jaccard >= 0.8 or SequenceMatcher(None, " ".join(a), " ".join(b)).ratio() >= 0.85


def _dates(h: Hackathon) -> dict[str, datetime]:
    return {f: getattr(h, f) for f in DATE_FIELDS if getattr(h, f) is not None}


def _instants(h: Hackathon) -> list[datetime]:
    pts = list(_dates(h).values())
    for r in h.rounds:
        pts += [d for d in (r.starts_at, r.ends_at) if d]
    return pts


def dates_match(a: Hackathon, b: Hackathon) -> bool:
    """Only called once titles already match, so one shared moment is strong
    evidence. Platforms disagree on what fields *mean* (Unstop's ``end_date`` is
    often the registration close; Devfolio's ``ends_at`` is the end of the hack),
    so we look for any coinciding timestamp rather than field-by-field equality."""
    da, db = _dates(a), _dates(b)
    if not da or not db:
        return False
    if "starts_at" in da and "starts_at" in db:
        # both say when it starts: decisive either way (catches next year's edition)
        return abs(da["starts_at"] - db["starts_at"]) <= timedelta(days=2)
    ia, ib = _instants(a), _instants(b)
    if any(abs(x - y) <= timedelta(days=1) for x in ia for y in ib):
        return True
    if set(da) & set(db):
        return False  # they publish the same kind of date and none line up
    # Disjoint fields (e.g. Hack2skill registration vs Luma start): timelines
    # must overlap or sit within a week of each other.
    lo_a, hi_a, lo_b, hi_b = min(ia), max(ia), min(ib), max(ib)
    return lo_a <= hi_b + timedelta(days=7) and lo_b <= hi_a + timedelta(days=7)


def same_event(a: Hackathon, b: Hackathon, ta: tuple[str, ...], tb: tuple[str, ...]) -> bool:
    return a.source != b.source and titles_match(ta, tb) and dates_match(a, b)


@dataclass
class Listing:
    """A hackathon as shown to the user: the best record plus its other listings."""

    primary: Hackathon
    others: list[Hackathon] = field(default_factory=list)

    @property
    def all(self) -> list[Hackathon]:
        return [self.primary, *self.others]

    @property
    def sources(self) -> list[Source]:
        return [h.source for h in self.all]


def _precision(h: Hackathon) -> tuple[int, int, int]:
    exact = sum(1 for f in DATE_FIELDS if getattr(h, f) and not h.is_approx(f))
    return (-exact - len(h.rounds), SOURCE_RANK.get(h.source, 9), len(h.uid))


def group(items: list[Hackathon]) -> list[Listing]:
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    toks = [normalize(h.title) for h in items]
    # Block on tokens so we only compare listings that share a word.
    by_token: dict[str, list[int]] = {}
    for i, t in enumerate(toks):
        for w in set(t):
            by_token.setdefault(w, []).append(i)
    checked: set[tuple[int, int]] = set()
    for idxs in by_token.values():
        if len(idxs) > 200:  # a word so common it can't discriminate
            continue
        for x, i in enumerate(idxs):
            for j in idxs[x + 1:]:
                if (i, j) in checked:
                    continue
                checked.add((i, j))
                if find(i) != find(j) and same_event(items[i], items[j], toks[i], toks[j]):
                    parent[find(j)] = find(i)

    clusters: dict[int, list[Hackathon]] = {}
    for i, h in enumerate(items):
        clusters.setdefault(find(i), []).append(h)
    out = []
    for members in clusters.values():
        # never fold two listings from the same source together
        members.sort(key=_precision)
        primary, rest, used = members[0], [], {members[0].source}
        for m in members[1:]:
            if m.source in used:
                out.append(Listing(m))
            else:
                used.add(m.source)
                rest.append(m)
        out.append(Listing(primary, rest))
    return out
