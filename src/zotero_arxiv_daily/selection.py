# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
"""Related papers first, with a small reproducible topic-diverse exploration set."""
import hashlib
from datetime import datetime, timedelta, timezone


def publication_date(paper):
    try:
        value = paper.published.strip()
        # A month-only date uses its earliest day, never a fabricated recent ingestion date.
        if len(value) == 7:
            value += "-01"
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def recent_papers(papers, now, days=30):
    return [p for p in papers if (date := publication_date(p)) is not None and
            now - timedelta(days=days) <= date <= now]


def research_priority(paper):
    return (paper.relevance_level,)


def allocate_groups(eligible, limit, group_weights):
    total = sum(group_weights.values())
    if total <= 0 or any(v < 0 for v in group_weights.values()):
        raise ValueError("Recommendation group weights must be nonnegative with a positive sum")
    quotas = {g: int(limit * w / total) for g, w in group_weights.items()}
    for g in sorted(quotas, key=lambda g: limit * group_weights[g] / total - quotas[g], reverse=True)[:limit - sum(quotas.values())]:
        quotas[g] += 1
    selected = []
    for group, quota in quotas.items():
        selected.extend([p for p in eligible if (p.recommendation_group or "core") == group][:quota])
    selected_ids = {id(p) for p in selected}
    selected.extend([p for p in eligible if id(p) not in selected_ids][:max(0, limit - len(selected))])
    return selected


def shortlist_papers(papers, limit, group_weights, now):
    def order(p):
        date = publication_date(p)
        fresh = date is not None and (now - date).days < 7
        authority = {"Q1": 2, "Q2": 1, "Q3": 0}.get((p.metrics or {}).get("jcr"), 1)
        return *research_priority(p), fresh, authority, p.published
    return allocate_groups(sorted(papers, key=order, reverse=True), limit, group_weights)


def select_papers(ranked, limit=20, explore_ratio=0.1, min_score=0.0, group_weights=None, now=None):
    eligible = [p for p in ranked if p.score is not None and p.score >= min_score]
    if group_weights:
        now = now or datetime.now(timezone.utc)
        def order(p):
            date = publication_date(p)
            fresh = date is not None and (now - date).days < 7
            quartile = (p.metrics or {}).get("jcr")
            authority = {"Q1": 2, "Q2": 1, "Q3": 0}.get(quartile, 1)
            return (*research_priority(p), fresh, round(p.score * 2) / 2, authority, p.score, p.published)
        eligible.sort(key=order, reverse=True)
        selected = allocate_groups(eligible, limit, group_weights)
        for p in selected:
            p.exploratory = p.recommendation_group == "explore"
        return sorted(selected, key=order, reverse=True)
    exploration = min(round(limit * explore_ratio), max(0, len(eligible) - (limit - round(limit * explore_ratio))))
    main = eligible[:limit - exploration]
    rest = eligible[len(main):]
    selected_topics = {t for p in main for t in p.topics}
    rest.sort(key=lambda p: (bool(set(p.topics) - selected_topics), hashlib.sha256(p.url.encode()).hexdigest()), reverse=True)
    extra = rest[:exploration]
    for p in extra:
        p.exploratory = True
    return main + extra
