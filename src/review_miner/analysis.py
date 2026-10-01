"""Cheap, deterministic stats. The LLM does the real interpretation;
these numbers keep it honest and save it from reading every review."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from .models import Review

_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

STOPWORDS = set("""
the and for that this with you are was but not have has had its it's just all can get got
from they them their there then than too very really more much some any one app game
would could should will been being into out about when what which who how why also only
even still like use used using make made does did doing dont don't cant can't wont won't
im i'm ive i've your yours our ours his her she him its it's were what's that's there's
after before again over under because while where every other same such own now new
please fix update version play playing played time times thing things lot way want need
bir ve bu da de ile için çok ama gibi daha en ne var yok olan olarak ben sen biz siz
onlar şey kadar sonra önce her hiç mi mı mu mü diye ya ki uygulama uygulaması oyun oyunu
bile artık tek neden nasıl şu o hep
dont didnt doesnt isnt wasnt arent cant wont couldnt shouldnt wouldnt youre thats theres
know think maybe felt feel look looks around people something anything everything
good great nice fun best better love loved bad worst game games app apps really
one two first last many much lot lots bit got get gets going go goes went
never ever always since back end actually especially absolutely literally basically
tried try trying makes making means feels years year days day off anymore already
said says thing someone anyone keep kept give gave take took put come came
rağmen niye ayrıca asla tekrar sadece hala hâlâ bunu şimdi lütfen gerçekten zaten
uygulamayı uygulamada uygulamanın uygulamaya aldım yine hiçbir diğer bazı olduğu
yani böyle öyle şöyle olmuş oldu olsun olur zaman yeni değil bana beni benim bizim sizin
kötü berbat rezalet iyi güzel harika süper tane gün günü yıl yıldır yıllardır şekilde
lazım gerek bence cidden resmen falan filan bide birde teşekkürler
""".split())
_APOSTROPHE = re.compile(r"(?<=\w)['’](?=\w)")


_CLAUSE = re.compile(r"[.!?;,:\n()]+|\s[-–—]\s")


def _grams(text: str) -> set[str]:
    """Words + two-word phrases. Phrases only from words that are truly adjacent
    in the same clause, so 'too expensive and it keeps' never yields 'expensive keeps'."""
    out: set[str] = set()
    for clause in _CLAUSE.split(_APOSTROPHE.sub("", text)):
        raw = [m.group(0).casefold() for m in _WORD.finditer(clause)]
        out.update(w for w in raw if w not in STOPWORDS)
        out.update(f"{a} {b}" for a, b in zip(raw, raw[1:]) if a not in STOPWORDS and b not in STOPWORDS)
    return out


def _doc_freq(reviews: list[Review], ignore: set[str]) -> Counter[str]:
    df: Counter[str] = Counter()
    for r in reviews:
        df.update(g for g in _grams(r.title) | _grams(r.text) if not (set(g.split()) & ignore))
    return df


def top_terms(
    reviews: list[Review], n: int = 15, baseline: list[Review] | None = None, ignore: set[str] | None = None
) -> list[dict[str, Any]]:
    """Terms that characterize these (negative) reviews.

    With a baseline of positive reviews, rank by how much MORE often a term shows up in
    complaints than in praise (so 'story' or 'learn', which everyone mentions, drops out).
    Without one, fall back to plain frequency. Each term is counted once per review.
    """
    ignore = ignore or set()
    df = _doc_freq(reviews, ignore)
    total = max(len(reviews), 1)
    base = baseline if baseline and len(baseline) >= 10 else None
    if base:
        bdf, btotal = _doc_freq(base, ignore), len(base)
        scored = []
        for term, c in df.items():
            if c < 2:
                continue
            lift = ((c + 0.5) / (total + 1)) / ((bdf.get(term, 0) + 0.5) / (btotal + 1))
            if lift >= 1.5:
                scored.append((c * math.log(lift), term, c, lift))
        scored.sort(reverse=True)
        items = [(t, c, l) for _, t, c, l in scored]
    else:
        items = [(t, c, None) for t, c in df.most_common(n * 4) if c >= 2]

    # Prefer phrases: drop a word when a phrase containing it is almost as frequent.
    phrases = {t: c for t, c, _ in items if " " in t}
    result = []
    for term, count, lift in items:
        if " " not in term and any(term in p.split() and pc >= 0.6 * count for p, pc in phrases.items()):
            continue
        row: dict[str, Any] = {"term": term, "reviews": count, "share": f"{100 * count / total:.0f}%"}
        if lift is not None:
            row["x_vs_positive"] = round(min(lift, 99), 1)
        result.append(row)
        if len(result) >= n:
            break
    return result


def summarize(
    reviews: list[Review], baseline: list[Review] | None = None, app_name: str = ""
) -> dict[str, Any]:
    """baseline: extra positive reviews to contrast against (used when `reviews` has few)."""
    n = len(reviews)
    if n == 0:
        return {"reviews_analyzed": 0}
    negatives = [r for r in reviews if r.is_negative]
    out: dict[str, Any] = {
        "reviews_analyzed": n,
        "negative_share": f"{100 * len(negatives) / n:.0f}%",
        "date_range": _date_range(reviews),
    }
    stars = [r.rating for r in reviews if r.rating is not None]
    if stars:
        dist = Counter(stars)
        out["avg_rating_recent"] = round(sum(stars) / len(stars), 2)
        out["star_distribution"] = {f"{s}★": dist.get(s, 0) for s in (5, 4, 3, 2, 1)}
        out["rating_by_version"] = _by_version(reviews)
    else:
        hours = [r.playtime_hours for r in negatives if r.playtime_hours]
        if hours:
            hours.sort()
            out["median_playtime_of_negative_reviewers_h"] = hours[len(hours) // 2]
    positives = [r for r in reviews if not r.is_negative] + [r for r in (baseline or []) if not r.is_negative]
    ignore = {w.casefold() for w in re.findall(r"[^\W\d_]{3,}", app_name)}
    out["top_complaint_terms"] = top_terms(negatives, baseline=positives, ignore=ignore) if negatives else []
    return out


def _date_range(reviews: list[Review]) -> str | None:
    dates = sorted(r.date for r in reviews if r.date)
    return f"{dates[0]} → {dates[-1]}" if dates else None


def _by_version(reviews: list[Review], limit: int = 5) -> list[dict[str, Any]]:
    """Average stars for the most recent versions. Spots 'the update broke it'."""
    groups: dict[str, list[int]] = defaultdict(list)
    latest: dict[str, str] = {}
    for r in reviews:
        if r.version and r.rating is not None:
            groups[r.version].append(r.rating)
            if r.date and r.date > latest.get(r.version, ""):
                latest[r.version] = r.date
    ordered = sorted(groups, key=lambda v: latest.get(v, ""), reverse=True)[:limit]
    return [
        {"version": v, "reviews": len(groups[v]), "avg": round(sum(groups[v]) / len(groups[v]), 2)}
        for v in ordered
    ]


def pick_samples(reviews: list[Review], k: int) -> list[Review]:
    """Most helpful first, then longest: the reviews most worth an LLM's attention."""
    return sorted(reviews, key=lambda r: (r.helpful_votes, len(r.text)), reverse=True)[:k]
