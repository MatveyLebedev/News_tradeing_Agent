"""
Regex / lexical featurizer — the rule-based half of the hybrid significance model.

Pure-stdlib (only `re`), deterministic and unit-testable. Produces a fixed-width
numeric feature vector from a news headline (+ optional summary). Designed to be
the cheap, interpretable complement to the semantic embedding features.

Feature groups:
  evt_<cat>_n      count of event-keyword hits per category (earnings, m&a, ...)
  evt_total_n      total event-keyword hits
  evt_n_cat        number of distinct event categories triggered
  dir_pos_n/neg_n  directional move words (surge/jump vs plunge/drop)
  dir_net          dir_pos_n - dir_neg_n
  strong_move_n    high-intensity movers (surge/plunge/soar/crash ...)
  has_pct/max_pct  presence and magnitude of the largest % figure
  has_dollar, has_big_amount, n_numbers
  len_words/chars, caps_ratio, n_upper_tokens, has_question, has_colon, has_quote

Keep LEXICON_VERSION bumped whenever lexicons change (versioned per experiment plan).
"""
from __future__ import annotations
import re
from typing import Dict, List, Iterable

LEXICON_VERSION = "0.1.0"

# --- event-type lexicons (regex stems, matched case-insensitively) -----------
EVENT_LEXICON: Dict[str, List[str]] = {
    "earnings":   [r"earning", r"\beps\b", r"revenue", r"\bprofit", r"quarterly",
                   r"\bresults?\b", r"guidance", r"outlook", r"forecast", r"\bbeat", r"\bmiss",
                   r"same-store", r"comparable sales"],
    "mna":        [r"acqui", r"\bmerg", r"takeover", r"buyout", r"\bbid\b", r"\bstake\b",
                   r"\bdeal\b", r"combin", r"tie-up", r"divest", r"spin-?off"],
    "analyst":    [r"upgrade", r"downgrade", r"price target", r"\brating", r"initiat",
                   r"overweight", r"underweight", r"\breiterat", r"\bcut to\b", r"\braise[ds]? to\b"],
    "legal_reg":  [r"lawsuit", r"\bsues?\b", r"\bsec\b", r"\bdoj\b", r"\bftc\b", r"antitrust",
                   r"\bprobe", r"investigat", r"\bfine[ds]?\b", r"settlement", r"subpoena",
                   r"recall", r"violat", r"sanction"],
    "mgmt":       [r"\bceo\b", r"\bcfo\b", r"\bcoo\b", r"resign", r"step[\s-]?down", r"\boust",
                   r"appoint", r"succession", r"\bnames?\b .* (chief|ceo|cfo)"],
    "capital":    [r"dividend", r"buyback", r"repurchase", r"stock split", r"\bsplit\b",
                   r"offering", r"\bipo\b", r"secondary", r"\bbond", r"\bdebt\b", r"refinanc"],
    "restruct":   [r"layoff", r"\bcut[s]? jobs", r"restructur", r"bankrupt", r"chapter 11",
                   r"profit warning", r"\bwarn", r"shut(down| down)", r"\bclos(e|ing) plant"],
    "product_ops":[r"\bfda\b", r"approval", r"\btrial", r"\bpatent", r"\blaunch", r"\boutage",
                   r"\bstrike\b", r"supply", r"production", r"\brollout", r"data breach", r"hack"],
    "contract":   [r"contract", r"partnership", r"agreement", r"\bdeal to\b", r"\bwins?\b .* (deal|contract|order)",
                   r"\border\b", r"\baward", r"collaborat"],
    "guidance_fin":[r"raises? guidance", r"cuts? guidance", r"\bbeats? estimates", r"\bmisses? estimates",
                   r"\btops? estimates", r"record (revenue|profit|sales)"],
    "macro":      [r"\bfed\b", r"\brate (hike|cut|decision)", r"inflation", r"\bcpi\b", r"\bgdp\b",
                   r"\bjobs report", r"tariff", r"\btrade war", r"\byields?\b"],
    "rating_news":[r"short[\s-]?seller", r"\bhindenburg", r"\bmuddy waters", r"\bdefault", r"credit rating"],
}

# --- directional / intensity move words --------------------------------------
DIR_POS = [r"surg", r"soar", r"\bjump", r"\brally", r"\bgain", r"\bris(e|es|ing)\b", r"\bclimb",
           r"\bsoar", r"\brebound", r"\bspike", r"\bpop\b", r"\bsky", r"\bup\b", r"\btop\b", r"\bbeat"]
DIR_NEG = [r"plung", r"plummet", r"tumbl", r"\bdrop", r"\bfall", r"slump", r"\bsink", r"\bcrash",
           r"\bslip", r"declin", r"\bsink", r"\bmiss", r"\bcut", r"\bwarn", r"\bhalt", r"\bsell-?off", r"\bdown\b"]
STRONG_MOVE = [r"surg", r"plung", r"soar", r"plummet", r"\bcrash", r"tumbl", r"skyrocket",
               r"\brally", r"collaps", r"\bspike", r"record", r"\bhalt"]

_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_DOLLAR = re.compile(r"\$\s?\d|\b\d+(?:\.\d+)?\s?(?:billion|million|trillion|bn|mn)\b", re.I)
_BIG_AMT = re.compile(r"\b(billion|trillion|\bbn\b)\b", re.I)
_UPPER_TOKEN = re.compile(r"\b[A-Z]{2,5}\b")
_WORD = re.compile(r"\b\w+\b")


def _compile(stems: Iterable[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{s})" for s in stems), re.IGNORECASE)


class RegexFeaturizer:
    """Deterministic lexical featurizer.  `transform(text) -> dict[str, float]`."""

    def __init__(self) -> None:
        self._evt = {cat: _compile(stems) for cat, stems in EVENT_LEXICON.items()}
        self._dir_pos = _compile(DIR_POS)
        self._dir_neg = _compile(DIR_NEG)
        self._strong = _compile(STRONG_MOVE)
        self.version = LEXICON_VERSION
        self._names = self._build_names()

    def _build_names(self) -> List[str]:
        names = [f"evt_{c}_n" for c in EVENT_LEXICON]
        names += ["evt_total_n", "evt_n_cat",
                  "dir_pos_n", "dir_neg_n", "dir_net", "strong_move_n",
                  "has_pct", "max_pct", "has_dollar", "has_big_amount", "n_numbers",
                  "len_words", "len_chars", "caps_ratio", "n_upper_tokens",
                  "has_question", "has_colon", "has_quote"]
        return names

    @property
    def feature_names(self) -> List[str]:
        return list(self._names)

    def transform(self, text: str) -> Dict[str, float]:
        t = text or ""
        f: Dict[str, float] = {}

        total, n_cat = 0, 0
        for cat, pat in self._evt.items():
            n = len(pat.findall(t))
            f[f"evt_{cat}_n"] = float(n)
            total += n
            n_cat += 1 if n else 0
        f["evt_total_n"] = float(total)
        f["evt_n_cat"] = float(n_cat)

        pos = len(self._dir_pos.findall(t))
        neg = len(self._dir_neg.findall(t))
        f["dir_pos_n"] = float(pos)
        f["dir_neg_n"] = float(neg)
        f["dir_net"] = float(pos - neg)
        f["strong_move_n"] = float(len(self._strong.findall(t)))

        pcts = [float(x) for x in _PCT.findall(t)]
        f["has_pct"] = 1.0 if pcts else 0.0
        f["max_pct"] = max(pcts) if pcts else 0.0
        f["has_dollar"] = 1.0 if _DOLLAR.search(t) else 0.0
        f["has_big_amount"] = 1.0 if _BIG_AMT.search(t) else 0.0
        f["n_numbers"] = float(len(_NUM.findall(t)))

        words = _WORD.findall(t)
        nw = len(words)
        f["len_words"] = float(nw)
        f["len_chars"] = float(len(t))
        upper = _UPPER_TOKEN.findall(t)
        f["n_upper_tokens"] = float(len(upper))
        f["caps_ratio"] = (len(upper) / nw) if nw else 0.0
        f["has_question"] = 1.0 if "?" in t else 0.0
        f["has_colon"] = 1.0 if ":" in t else 0.0
        f["has_quote"] = 1.0 if ("'" in t or '"' in t or "’" in t) else 0.0
        return f

    def transform_frame(self, texts: Iterable[str]):
        """Vectorized: list/Series of texts -> pandas DataFrame (cols = feature_names)."""
        import pandas as pd
        rows = [self.transform(t) for t in texts]
        return pd.DataFrame(rows, columns=self._names).fillna(0.0)


if __name__ == "__main__":
    rf = RegexFeaturizer()
    for s in [
        "Nvidia Surges 12% After Blowout Earnings, Raises Guidance",
        "SEC Probes Tesla Over Self-Driving Claims; Shares Tumble",
        "Fed Holds Rates Steady as Inflation Cools",
        "Apple to Acquire AI Startup in $2 Billion Deal",
    ]:
        f = rf.transform(s)
        hot = {k: v for k, v in f.items() if v}
        print(f"\n{s}\n  -> {hot}")
