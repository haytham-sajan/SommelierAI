from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


FALLBACK_MESSAGE = "Keine passenden Produkte in der Schlumberger-Datenbank gefunden."


def _norm_text(s: str) -> str:
    s = s.lower()
    s = s.replace("ß", "ss")
    s = re.sub(r"[^a-z0-9äöü\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokenize(s: str) -> List[str]:
    s = _norm_text(s)
    return [t for t in re.split(r"[\s\-]+", s) if t]


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [str(v)]


def _extract_price_ceiling_eur(query: str) -> Optional[float]:
    """
    Very small heuristic:
    - "unter 20", "bis 25", "max 30", "<= 40"
    - "20€" (as a ceiling only if used with unter/bis/max)
    If unclear, returns None.
    """
    q = _norm_text(query)

    m = re.search(r"(unter|bis|max(?:imal)?)\s*(\d{1,4}(?:[.,]\d{1,2})?)", q)
    if m:
        return float(m.group(2).replace(",", "."))

    m = re.search(r"(<=|<)\s*(\d{1,4}(?:[.,]\d{1,2})?)", q)
    if m:
        return float(m.group(2).replace(",", "."))

    return None


def _preference_from_query(tokens: Sequence[str]) -> Dict[str, str]:
    prefs: Dict[str, str] = {}

    if any(t in tokens for t in ["leicht", "light"]):
        prefs["body"] = "light"
    if any(t in tokens for t in ["mittel", "medium", "mittelschwer"]):
        prefs["body"] = "medium"
    if any(t in tokens for t in ["voll", "vollmundig", "kräftig", "power", "full"]):
        prefs["body"] = "full"

    if any(t in tokens for t in ["trocken", "dry"]):
        prefs["sweetness"] = "trocken"
    if any(t in tokens for t in ["halbtrocken", "feinherb", "offdry", "semi"]):
        prefs["sweetness"] = "halbtrocken"
    if any(t in tokens for t in ["suss", "süß", "dessert", "edelsuss", "edelsüß", "sweet"]):
        prefs["sweetness"] = "süß"

    if any(t in tokens for t in ["saure", "säure", "frisch", "crisp", "acid", "sour"]):
        prefs["acidity"] = "high"
    if any(t in tokens for t in ["mild"]):
        prefs["acidity"] = "low"

    return prefs


def _food_synonyms() -> Dict[str, List[str]]:
    return {
        "fisch": ["fisch", "seafood", "meeresfruechte", "meeresfrüchte", "lachs", "thunfisch", "garnelen"],
        "fleisch": ["steak", "rind", "kalb", "lamm", "fleisch", "bbq", "grill", "grillabend", "grillabende"],
        "salat": ["salat", "salate"],
        "kaese": ["kaese", "käse", "blauschimmelkaese", "blauschimmelkäse"],
        "dessert": ["dessert", "suss", "süß", "kuchen", "schokolade", "praline", "nuss", "karamell"],
        "spargel": ["spargel"],
        "pilze": ["pilz", "pilze", "mushroom"],
        "aperitif": ["aperitif"],
    }


def _query_synonyms() -> Dict[str, List[str]]:
    return {
        "dry": ["trocken"],
        "trocken": ["dry"],
        "semi": ["halbtrocken", "feinherb"],
        "halbtrocken": ["semi", "offdry", "feinherb"],
        "feinherb": ["semi", "offdry", "halbtrocken"],
        "red": ["rot", "rotwein"],
        "rot": ["red", "rotwein"],
        "rotwein": ["red", "redwein"],
        "white": ["weiß", "weiss", "weißwein", "weisswein"],
        "wein": ["wine"],
        "wine": ["wein"],
        "weiß": ["white"],
        "weiss": ["white"],
        "weißwein": ["white"],
        "weisswein": ["white"],
        "rose": ["rosé", "roséwein", "rosewein"],
        "rosé": ["rose", "roséwein", "rosewein"],
        "sparkling": ["sekt", "schaumwein", "perlwein"],
        "sekt": ["sparkling"],
        "schaumwein": ["sparkling"],
        "perlwein": ["sparkling"],
        "chicken": ["hähnchen", "haehnchen", "geflügel", "poultry"],
        "hähnchen": ["chicken", "haehnchen", "geflügel", "poultry"],
        "haehnchen": ["chicken", "hähnchen", "geflügel", "poultry"],
        "geflügel": ["chicken", "hähnchen", "poultry"],
        "poultry": ["geflügel", "hähnchen", "chicken"],
        "italy": ["italien"],
        "italien": ["italy"],
        "germany": ["deutschland"],
        "deutschland": ["germany"],
        "austria": ["österreich"],
        "österreich": ["austria"],
        "france": ["frankreich"],
        "frankreich": ["france"],
        "cheese": ["kaese", "käse"],
        "mushroom": ["pilz", "pilze"],
        "beef": ["rind"],
        "lamb": ["lamm"],
        "salmon": ["lachs"],
    }


def _expand_query_tokens(tokens: Sequence[str]) -> List[str]:
    synonyms = _query_synonyms()
    expanded = list(tokens)
    seen = set(tokens)
    for token in tokens:
        for syn in synonyms.get(token, []):
            syn_norm = _norm_text(syn)
            if syn_norm and syn_norm not in seen:
                expanded.append(syn_norm)
                seen.add(syn_norm)
    return expanded


def _flatten_properties(props: Any) -> List[str]:
    """
    Supports both old schema (lists like aroma_notes) and new Shopware schema:
    {"properties": {"Land": ["Italien"], "Rebsorte": ["Riesling"]}}
    """
    out: List[str] = []
    if not props:
        return out
    if isinstance(props, dict):
        for k, vals in props.items():
            out.append(str(k))
            out.extend(_as_list(vals))
        return out
    if isinstance(props, list):
        out.extend([str(x) for x in props if str(x).strip()])
        return out
    out.append(str(props))
    return out


def _score_item(query_tokens: Sequence[str], item: Dict[str, Any], prefs: Dict[str, str]) -> float:
    score = 0.0

    searchable_fields: List[str] = []
    # Works for both legacy "wine" dataset and Shopware "product" dataset
    for k in [
        "name",
        "producer",
        "manufacturer",
        "region",
        "country",
        "sweetness",
        "body",
        "acidity",
        "tannin",
        "description",
    ]:
        if item.get(k):
            searchable_fields.append(str(item[k]))

    for k in ["categories", "grape_varieties", "flavor_profile", "aroma_notes", "food_pairings", "occasion"]:
        searchable_fields.extend(_as_list(item.get(k)))
    searchable_fields.extend(_flatten_properties(item.get("properties")))

    haystack = set(_tokenize(" ".join(searchable_fields)))

    # token overlap
    overlap = len([t for t in query_tokens if t in haystack])
    score += overlap * 2.0

    # food synonym boost when wine has those pairings
    food_map = _food_synonyms()
    for _, syns in food_map.items():
        if any(_norm_text(s) in [_norm_text(t) for t in query_tokens] for s in syns):
            if any(_norm_text(s) in haystack for s in syns):
                score += 3.0

    # preferences boost
    for pref_key, pref_val in prefs.items():
        wv = str(item.get(pref_key, "")).lower()
        if wv and pref_val.lower() in wv:
            score += 2.5

    # small tie-breakers: more structured data -> slightly higher confidence
    completeness = 0
    for k in ["food_pairings", "aroma_notes", "grape_varieties", "sweetness", "categories"]:
        if _as_list(item.get(k)):
            completeness += 1
    if item.get("properties"):
        completeness += 1
    score += completeness * 0.1

    return score


def filter_wines(query: str, wines: Sequence[Dict[str, Any]], *, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Returns the top 3-5 matches (max_results) matching the query.

    IMPORTANT: this function must never add wines not present in `wines`.
    """
    query = (query or "").strip()
    if not query:
        return []

    query_tokens = _tokenize(query)
    query_tokens = _expand_query_tokens(query_tokens)
    prefs = _preference_from_query(query_tokens)
    price_ceiling = _extract_price_ceiling_eur(query)

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for w in wines:
        # if dataset has price and user asked for a ceiling, respect it
        if price_ceiling is not None:
            price = w.get("price")
            if isinstance(price, (int, float)) and price > price_ceiling:
                continue

        scored.append((_score_item(query_tokens, w, prefs), w))

    scored.sort(key=lambda x: x[0], reverse=True)

    # keep only meaningful matches; if everything scores 0, return empty
    if not scored or scored[0][0] <= 0:
        return []

    results = [w for _, w in scored[: max(3, min(max_results, 5))]]
    return results

