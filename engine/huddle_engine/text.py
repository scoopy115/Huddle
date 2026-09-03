"""Dependency-free text helpers: tokenising, sentence splitting, and the extractive fallback
notes used when no local AI model is available. Heuristics only — good enough to give a
meeting a skeleton (overview, decisions, likely commitments, keywords) without inventing
owners or dates; the LLM path replaces all of this when Ollama is present.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

# English + Dutch function words; anything this short or common never becomes a keyword.
_STOP = frozenset(["a", "an", "the", "and", "or", "but", "if", "then", "so", "of", "to", "in", "on", "at", "by", "for", "with", "from", "as", "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did", "have", "has", "had", "not", "no", "yes", "it", "its", "this", "that", "these", "those", "there", "here", "he", "she", "they", "we", "you", "i", "me", "him", "her", "them", "us", "our", "your", "their", "my", "his", "hers", "theirs", "ours", "what", "which", "who", "whom", "whose", "when", "where", "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "only", "own", "same", "than", "too", "very", "can", "will", "just", "should", "would", "could", "may", "might", "must", "shall", "about", "above", "after", "again", "against", "because", "before", "below", "between", "into", "through", "during", "until", "while", "over", "under", "out", "up", "down", "off", "once", "also", "well", "like", "get", "got", "going", "go", "make", "made", "take", "say", "said", "one", "two", "three", "de", "het", "een", "en", "maar", "als", "dan", "dus", "van", "naar", "op", "bij", "voor", "met", "uit", "onder", "door", "tot", "tussen", "na", "voordat", "nadat", "terwijl", "omdat", "want", "zodat", "zijn", "waren", "ben", "bent", "wordt", "worden", "werd", "werden", "heb", "hebt", "heeft", "hebben", "hadden", "niet", "geen", "wel", "ja", "nee", "ik", "jij", "je", "u", "hij", "zij", "ze", "wij", "jullie", "mij", "hem", "haar", "ons", "hun", "mijn", "jouw", "uw", "onze", "dit", "dat", "deze", "die", "er", "hier", "daar", "wat", "welke", "wie", "waar", "waarom", "hoe", "alle", "alles", "elk", "elke", "sommige", "meer", "meest", "ander", "andere", "zo", "te", "ook", "nog", "al", "eens", "even", "toch", "heel", "erg", "veel", "weinig", "kan", "kunnen", "kun", "zal", "zullen", "zou", "zouden", "moet", "moeten", "mag", "mogen", "wil", "willen", "gaan", "gaat", "ging", "doen", "doet", "deed", "maken", "maakt", "zeggen", "zegt", "zei", "krijgen", "krijgt"])

_ACTION_CUES = (
    # English
    "i will", "i'll", "we will", "we'll", "let's", "let us", "can you", "could you", "please", "we need to",
    "need to", "should", "make sure", "follow up", "send", "schedule", "prepare", "check", "todo", "to do",
    "action item", "next step", "by friday", "by monday", "by tomorrow", "next week",
    # Dutch
    "ik zal", "ik ga", "zal ik", "wij zullen", "we gaan", "laten we", "kun jij", "kan jij", "kunt u", "wil jij",
    "moeten we", "we moeten", "je moet", "even", "regelen", "sturen", "opsturen", "afspreken", "inplannen",
    "voorbereiden", "checken", "nakijken", "actiepunt", "volgende stap", "voor vrijdag", "voor maandag", "morgen",
    "volgende week",
)
_DECISION_CUES = (
    "we decided", "decided", "agreed", "we agree", "the decision", "let's go with", "we'll go with", "settled on",
    "final", "approved", "besloten", "we besluiten", "afgesproken", "we spreken af", "akkoord", "de keuze",
    "we kiezen", "gaan we doen", "definitief", "goedgekeurd",
)


def tokenize(text: str) -> list[str]:
    """Lower-case word tokens without stop words; letters with accents count as letters."""
    return [w for w in re.findall(r"[a-zà-ÿ']+", text.lower()) if w not in _STOP and len(w) > 2]


def sentences(text: str) -> list[str]:
    """Split on sentence punctuation; very long punctuation-free runs are cut at ~40 words."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        words = p.split()
        while len(words) > 40:
            out.append(" ".join(words[:40]))
            words = words[40:]
        if words:
            out.append(" ".join(words))
    return out


@dataclass
class ExtractiveSummary:
    overview: str
    decisions: list[str]


def extractive_summary(text: str, max_points: int = 5) -> ExtractiveSummary:
    """Pick the sentences that carry the most frequent content words, in speaking order,
    plus sentences that sound like decisions."""
    sents = sentences(text)
    if not sents:
        return ExtractiveSummary("", [])
    freq = Counter(tokenize(text))
    scored = []
    for i, s in enumerate(sents):
        toks = tokenize(s)
        if len(toks) < 4:
            continue
        score = sum(freq[t] for t in toks) / (len(toks) ** 0.5)
        scored.append((score, i, s))
    top = sorted(sorted(scored, reverse=True)[:max_points], key=lambda x: x[1])
    overview = " ".join(s for _, _, s in top)
    decisions = [s for s in sents if any(c in s.lower() for c in _DECISION_CUES)][:6]
    return ExtractiveSummary(overview, decisions)


def keywords(texts: list[str], top_n: int = 6) -> list[str]:
    """Most frequent content words that occur more than once, capitalised for display."""
    freq = Counter(t for text in texts for t in tokenize(text))
    return [w.capitalize() for w, n in freq.most_common(top_n * 2) if n > 1][:top_n]


def action_sentences(texts: list[str], limit: int = 12) -> list[str]:
    """Sentences that sound like a commitment or request. Owners and dates are never inferred."""
    out: list[str] = []
    for text in texts:
        for s in sentences(text):
            low = s.lower()
            if len(s.split()) >= 4 and any(c in low for c in _ACTION_CUES) and s not in out:
                out.append(s)
    return out[:limit]
