"""Rule-based request analysis: category, difficulty and output format from the text alone.

This is the hand-written baseline the plan compares the trained classifier against. It is
written before any benchmark data exists, so it cannot have been tuned on the test set.
Every rule that fires is recorded as a signal, so the routing reason is the real reason.
"""
import re
from dataclasses import dataclass, field

CATEGORIES = ("complaint", "loan_maths", "extraction", "sql", "python", "live_data", "general")
DIFFICULTIES = ("easy", "medium", "hard")

# Checked in this order; the first match wins (code requests often mention money too).
_CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("python", re.compile(r"\bpython\b|```py|\bdef \w+\(", re.I)),
    ("sql", re.compile(r"\bsql\b|\bselect\b[\s\S]+\bfrom\b", re.I)),
    ("extraction", re.compile(r"\bextract\b|\bparse\b|\bpull out\b|\bas json\b", re.I)),
    ("loan_maths", re.compile(r"\bemi\b|\binterest\b|\bprepay|\btenure\b|\bfoir\b|\bamorti[sz]|\bltv\b|"
                              r"\d+(\.\d+)?\s*%", re.I)),
    ("complaint", re.compile(r"\bcomplain|\bclassify\b|\bcategori[sz]e\b|\btag\b|\bintent\b|"
                             r"\bcustomer (writes|says|wrote)\b", re.I)),
]

# Questions only the live web can answer. Checked only when no banking or code category matched,
# so "charged twice today" stays a complaint and "EMI at the current rate of 9%" stays loan maths.
_LIVE_DATA = re.compile(
    r"\bweather\b|\btemperature\b|\bforecast\b|\bhumidity\b|\baqi\b|\bair quality\b|"
    r"\bnews\b|\bheadlines?\b|\bshare price\b|\bstock price\b|\bsensex\b|\bnifty\b|"
    r"\bexchange rate\b|\b(usd|dollar|euro|pound)s? to (inr|rupees?)\b|\b(gold|silver|petrol|diesel) (price|rate)\b|"
    r"\blatest\b|\btoday'?s?\b|\bright now\b|\bcurrent(ly)?\b(?! account)|\bthis week\b|\byesterday\b|"
    r"\blive score\b|\bwho won\b", re.I)

# Words that signal more than one step of reasoning. Each distinct hit adds one point (max 3).
_MULTI_STEP = [
    ("after", r"\bafter\b"), ("then", r"\bthen\b"), ("keep the same", r"\bkeep(s|ing)? the same\b"),
    ("how many months", r"\bhow many (months|years|emis)\b"), ("compare", r"\bcompar"),
    ("which first", r"\bwhich\b[\s\S]{0,60}\bfirst\b"), ("why", r"\bwhy\b"), ("prioritise", r"\bprioriti[sz]"),
    ("each", r"\beach\b"), ("share of", r"\bshare of\b"), ("ties", r"\bties\b"), ("rank", r"\brank"),
]
# Distinct banking problems in one complaint; two or more make it harder to handle correctly.
_ISSUES = [
    ("duplicate charge", r"charged twice|double(d)? charge|debited twice|duplicate"),
    ("card blocked", r"\bblock(ed)?\b|\bfrozen\b"), ("refund", r"\brefund"), ("fraud", r"\bfraud|unauthori[sz]ed"),
    ("fee", r"\bfee\b|\bcharges?\b(?! twice)"), ("transfer not received", r"not (been )?(received|credited)"),
    ("loan or EMI", r"\bemi\b|\bloan\b"),
]
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(\.\d+)?")
_JSON = re.compile(r"\bjson\b|\bstructured output\b", re.I)


@dataclass
class Analysis:
    category: str
    difficulty: str
    structured_output: bool
    score: int
    signals: list[str] = field(default_factory=list)


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            return str(content or "")
    return ""


def _requested_fields(text: str) -> int:
    """Counts fields in 'extract A, B, C and D as JSON from ...'."""
    m = re.search(r"\bextract\b(.+?)(?:\bas json\b|\bfrom\b|:|$)", text, re.I | re.S)
    if not m:
        return 0
    parts = re.split(r",|\band\b", m.group(1))
    return len([p for p in parts if p.strip()])


def analyse(messages: list[dict]) -> Analysis:
    text = _last_user_text(messages)
    category = next((name for name, pattern in _CATEGORY_RULES if pattern.search(text)), "general")
    signals: list[str] = []
    if category == "general":
        live = _LIVE_DATA.search(text)
        if live:
            category = "live_data"
            signals.append(f"needs live data: \"{live.group(0).lower()}\"")
    score = 0

    words = len(text.split())
    if words > 120:
        score += 2
        signals.append(f"{words} words")
    elif words > 60:
        score += 1
        signals.append(f"{words} words")

    numbers = len(_NUMBER.findall(text))
    if numbers >= 4:
        score += 1
        signals.append(f"{numbers} numbers")

    steps = [label for label, pattern in _MULTI_STEP if re.search(pattern, text, re.I)]
    if steps:
        score += min(len(steps), 3)
        signals.append("multi-step: " + ", ".join(steps[:4]))

    if category == "complaint":
        issues = [label for label, pattern in _ISSUES if re.search(pattern, text, re.I)]
        if len(issues) >= 2:
            score += 1
            signals.append(f"{len(issues)} issues: " + ", ".join(issues))

    if category == "extraction":
        fields = _requested_fields(text)
        if fields >= 6:
            score += 2
            signals.append(f"{fields} fields to extract")
        elif fields >= 3:
            score += 1
            signals.append(f"{fields} fields to extract")

    structured = bool(_JSON.search(text))
    if structured:
        signals.append("JSON output requested")

    difficulty = "easy" if score == 0 else "medium" if score <= 2 else "hard"
    return Analysis(category, difficulty, structured, score, signals)


def shift(difficulty: str, mode: str) -> str:
    """Cheapest mode routes one level lower, quality mode one level higher."""
    i = DIFFICULTIES.index(difficulty) + {"cheapest": -1, "quality": 1}.get(mode, 0)
    return DIFFICULTIES[max(0, min(i, len(DIFFICULTIES) - 1))]
