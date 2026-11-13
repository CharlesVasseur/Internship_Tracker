"""Flag sentences about automated access in a terms-of-use page (step T3.3).

Item 10 (may we read this site automatically?) is a HUMAN judgment. This module
never decides it. It does three things only:

    flagged       one or two sentences that look like a ban on robots, scraping
                  or automated access are quoted, with a language guess, so a
                  person can read them
    not_flagged   nothing was found. This is NOT a clearance: the patterns can
                  miss a clause, and the old survey script missed a real one
    not_assessed  the page could not be read: a PDF, a failed or blocked fetch,
                  a page built by JavaScript (an empty app shell, or no text at
                  all), or a language we have no patterns for. It says why, so a
                  person reads the page by hand

What counts as a hit: ONE sentence that contains both
    1. an automation term: robot, spider, crawler, scraping, bot, data mining,
       or "automatic/automated" next to a word like means, methods, tools,
       programs, access, algorithms; and
    2. a prohibition term: not, forbidden, prohibited, without permission...
Both lists exist in English, French, Spanish, German, Dutch, Portuguese and Italian.

LISTS. Terms pages often ban things in a list: a lead-in sentence ending in ":"
("You agree not to make any use of this site that:") followed by items, each
with no "not" of its own. A real captured page did exactly this, and a sentence
by sentence check missed it. So a lead-in that ends with ":" and contains a
prohibition term applies to the items after it, until another lead-in, a long
prose sentence (over 500 characters), or 25 sentences have passed. The quote then
shows the lead-in followed by the item. These limits are first guesses.

HONEST LIMITS. The patterns come from general legal phrasing and from three
clause wordings described in the survey notes. No real terms page has been
captured yet, so none of this is verified on real pages. The numbers below
(12% and 1.25 for language guessing) are first guesses, not tuned. There is no
minimum page length: terms pages can be short, so a readable page says how many
characters were read instead. Text is compared without accents and without
capitals, so "Interdit" matches "interdit". Only the text of the raw HTML is read.
Script, style, template and head content is skipped (it is code, not prose);
<noscript> text IS read, because it is what a reader without JavaScript sees, as we do.

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser

from tracker.triage.contract import MAX_QUOTE, TermsFlag
from tracker.triage.fetch_result import FetchResult
from tracker.triage.html_scan import scan_html

MAX_QUOTES = 2                # the contract allows one or two quotes
MIN_SENTENCE_CHARS = 15
LIST_REACH = 25               # how many sentences after a lead-in still count as its list items
MAX_LIST_ITEM_CHARS = 500     # a longer sentence is prose, not a list item
MAX_LEAD_IN_QUOTE = 150       # characters of the lead-in kept in a quote
MIN_LANGUAGE_SHARE = 0.12     # the best language must cover this share of the words
MIN_LANGUAGE_MARGIN = 1.25    # and beat the second best by this factor


# ---------------------------------------------------------------------------
# Visible text of an HTML page
# ---------------------------------------------------------------------------

_SKIP_TAGS = frozenset({"script", "style", "template", "head"})
_BLOCK_TAGS = frozenset({
    "p", "div", "li", "br", "tr", "td", "th", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "table", "dd", "dt", "blockquote", "pre", "hr", "header", "footer", "main",
})


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def visible_text(page: str) -> str:
    """The text a reader would see, with a line break between blocks."""
    collector = _TextCollector()
    collector.feed(page)
    collector.close()
    return "".join(collector.parts)


_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+|\n+")


def sentences(text: str) -> list[str]:
    found = []
    for raw in _SENTENCE_END.split(text):
        sentence = " ".join(raw.split())
        # Short pieces are dropped as noise, except lead-ins ("Includes:"), which can end a list.
        if len(sentence) >= MIN_SENTENCE_CHARS or (sentence.endswith(":") and len(sentence) > 1):
            found.append(sentence)
    return found


def _normalise(text: str) -> str:
    """Lowercase, no accents, straight apostrophes: patterns are written in plain letters."""
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u00a0", " ")
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


# ---------------------------------------------------------------------------
# The seven languages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Language:
    name: str
    stopwords: frozenset[str]   # very common words, only used to guess the language
    strong: str                 # regex pieces: words that mean robots / scraping by themselves
    adjective: str              # regex pieces: automatic, automated
    noun: str                   # regex pieces: means, methods, tools, programs, access...
    prohibition: str            # regex pieces: not, forbidden, without permission...


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


LANGUAGES = (
    Language(
        "English",
        _words("the and of to in is you that for with are this by or on not be as any your we our may will "
               "from use which these such all shall"),
        r"robots?|spiders?|crawl\w*|scrap(?:e|es|er|ers|ing)|bots?|data[ -]?mining|harvest\w*"
        r"|systematic(?:ally)?\W+(?:\w+\W+){0,3}?(?:retriev|extract|download|collect|cop)\w*",
        r"automated|automatic|automatically",
        r"means|methods?|process(?:es)?|tools?|devices?|software|programs?|scripts?|access|queries|mechanisms?"
        r"|algorithms?|systems?",
        r"not|cannot|prohibit\w*|forbid\w*|forbidden|unauthori[sz]ed|bans?|banned|refrain|restrict\w*"
        r"|without\W+(?:\w+\W+){0,3}?(?:consent|permission|authori[sz]ation)",
    ),
    Language(
        "French",
        _words("le la les de des du et en un une que qui pour dans sur est vous ne pas au par avec ce ou nous "
               "sont aux se ces votre tout son"),
        r"robots?|spiders?|crawl\w*|scrap\w*|bots?|moissonn\w*|aspirat\w*|data[ -]?mining|fouille\W+de\W+donnees",
        r"automatiques?|automatises?|automatisees?",
        r"methodes?|moyens?|procedes?|outils?|mecanismes?|programmes?|logiciels?|algorithmes?|dispositifs?"
        r"|processus|scripts?|acces|requetes?|extraction",
        r"ne|n'\w+|interdit\w*|interdire|defend\w*|prohib\w*|non\W+autoris\w*|nul|nulle"
        r"|sans\W+(?:\w+\W+){0,3}?(?:autorisation|accord|consentement)",
    ),
    Language(
        "Spanish",
        _words("el la los las de del en un una que por para con es se no su al lo como este sus esta sobre ser "
               "pueden usted"),
        r"robots?|spiders?|crawl\w*|scrap\w*|bots?|arana\w*|mineria\W+de\W+datos",
        r"automatic[oa]s?|automatizad[oa]s?",
        r"medios?|metodos?|procesos?|herramientas?|dispositivos?|programas?|scripts?|algoritmos?|sistemas?"
        r"|acceso|consultas|mecanismos?",
        r"no|prohib\w*|queda\W+prohibid\w*|no\W+autorizad\w*"
        r"|sin\W+(?:\w+\W+){0,3}?(?:autorizacion|permiso|consentimiento)",
    ),
    Language(
        "German",
        _words("der die das und ist nicht zu den von mit ein eine fur auf sie im des dem auch werden oder sich "
               "sind bei wir nach an als wird"),
        r"robots?|roboter\w*|spider\w*|crawl\w*|scrap\w*|bots?|data[ -]?mining",
        r"automatisiert\w*|automatisch\w*",
        r"mittel\w*|verfahren|methoden?|prozess\w*|programm\w*|tools?|werkzeug\w*|skript\w*|software|abfrage\w*"
        r"|zugriff\w*|system\w*|algorithm\w*",
        r"nicht|untersagt|verboten|unzulassig|verbietet|unbefugt\w*"
        r"|ohne\W+(?:\w+\W+){0,3}?(?:genehmigung|zustimmung|erlaubnis|einwilligung)",
    ),
    Language(
        "Dutch",
        _words("de het een en van is dat op te niet voor met zijn die aan bij of als wordt worden ook door deze "
               "uw naar"),
        r"robots?|spiders?|crawl\w*|scrap\w*|bots?|data[ -]?mining",
        r"geautomatiseerd\w*|automatisch\w*",
        r"middelen|methoden?|methodes?|processen|programma\w*|tools?|scripts?|software|toegang|systemen"
        r"|algoritmes?",
        r"niet|verboden|verbiedt|ongeoorloofd\w*"
        r"|zonder\W+(?:\w+\W+){0,3}?(?:toestemming|goedkeuring|machtiging)",
    ),
    Language(
        "Portuguese",
        _words("os as de do da dos das em um uma que para com nao por se ao seu ou sua sao no na nos como ser"),
        r"robos?|robots?|spiders?|aranhas?|crawl\w*|scrap\w*|bots?|mineracao\W+de\W+dados",
        r"automatic[oa]s?|automatizad[oa]s?",
        r"meios?|metodos?|processos?|ferramentas?|dispositivos?|programas?|scripts?|algoritmos?|sistemas?"
        r"|acesso|consultas|mecanismos?",
        r"nao|proibid\w*|proibe|proibicao|sem\W+(?:\w+\W+){0,3}?(?:autorizacao|permissao|consentimento)",
    ),
    Language(
        "Italian",
        _words("il lo la gli le di del della in un una che per con non si al sono dei delle nel nella questo "
               "come essere suo"),
        r"robots?|spiders?|crawl\w*|scrap\w*|bots?|data[ -]?mining",
        r"automatic(?:o|i|a|he)|automatizzat(?:o|i|a|e)",
        r"mezzi|metodi|processi|strumenti|dispositivi|programmi|script|algoritmi|sistemi|accesso|meccanismi"
        r"|software",
        r"non|vietat\w*|vieta|proibit\w*|senza\W+(?:\w+\W+){0,3}?(?:autorizzazione|consenso|permesso)",
    ),
)


@dataclass(frozen=True)
class _Patterns:
    automation: re.Pattern[str]
    prohibition: re.Pattern[str]


def _near(first: str, second: str) -> str:
    """`first` and `second` within six words of each other, in either order."""
    gap = r"(?:\W+\w+){0,6}?\W+"
    return rf"(?<!\w)(?:{first})(?!\w){gap}(?<!\w)(?:{second})(?!\w)"


@lru_cache(maxsize=None)
def _patterns(language_name: str) -> _Patterns:
    language = next(item for item in LANGUAGES if item.name == language_name)
    automation = "|".join([
        rf"(?<!\w)(?:{language.strong})(?!\w)",
        _near(language.adjective, language.noun),
        _near(language.noun, language.adjective),
    ])
    return _Patterns(
        re.compile(automation),
        re.compile(rf"(?<!\w)(?:{language.prohibition})(?!\w)"),
    )


# ---------------------------------------------------------------------------
# Guessing the language
# ---------------------------------------------------------------------------


def detect_language(text: str) -> str | None:
    """The best of the seven languages, or None if no language clearly wins."""
    words = re.findall(r"[^\W\d_]+", _normalise(text))[:20000]
    if not words:
        return None
    shares = sorted(
        ((sum(1 for word in words if word in language.stopwords) / len(words), language.name)
         for language in LANGUAGES),
        reverse=True,
    )
    (best, name), (second, _) = shares[0], shares[1]
    if best < MIN_LANGUAGE_SHARE or best < MIN_LANGUAGE_MARGIN * second:
        return None
    return name


# ---------------------------------------------------------------------------
# Finding the sentences
# ---------------------------------------------------------------------------


def _window(text: str, position: int, size: int) -> str:
    """`text`, or if longer than `size`, a window of it around `position` with "..." marks."""
    if len(text) <= size:
        return text
    room = size - 8                                         # space for the two "... " marks
    start = max(0, min(position - 150, len(text) - room))
    end = start + room
    return ("... " if start > 0 else "") + text[start:end] + (" ..." if end < len(text) else "")


def _excerpt(sentence: str, position: int) -> str:
    return _window(sentence, position, MAX_QUOTE)


def _list_quote(lead_in: str, item: str, position: int) -> str:
    """Lead-in and item together, within the quote limit; the lead-in keeps its start."""
    lead = lead_in if len(lead_in) <= MAX_LEAD_IN_QUOTE else lead_in[: MAX_LEAD_IN_QUOTE - 4] + " ..."
    return f"{lead} {_window(item, position, MAX_QUOTE - len(lead) - 1)}"


def find_hits(text: str, preferred: str | None = None) -> list[tuple[str, str]]:
    """(language, quote) for every sentence that has an automation and a prohibition term.

    A prohibition in a list's lead-in counts for the items under it (see the module
    docstring). Sentences in the detected language come first; the rest keep the
    page order.
    """
    order = sorted(LANGUAGES, key=lambda language: language.name != preferred)
    hits: list[tuple[str, str]] = []
    lead_in: str | None = None        # the current list lead-in sentence, if it has a prohibition term
    reach = 0
    for sentence in sentences(text):
        normalised = _normalise(sentence)
        found = False
        for language in order:
            patterns = _patterns(language.name)
            match = patterns.automation.search(normalised)
            if match and patterns.prohibition.search(normalised):
                hits.append((language.name, _excerpt(sentence, match.start())))
                found = True
                break

        if not found and lead_in is not None and len(sentence) <= MAX_LIST_ITEM_CHARS:
            lead_normalised = _normalise(lead_in)
            for language in order:
                patterns = _patterns(language.name)
                match = patterns.automation.search(normalised)
                if match and patterns.prohibition.search(lead_normalised):
                    hits.append((language.name, _list_quote(lead_in, sentence, match.start())))
                    break

        if sentence.rstrip().endswith(":"):
            has_prohibition = any(_patterns(language.name).prohibition.search(normalised) for language in LANGUAGES)
            lead_in, reach = (sentence, LIST_REACH) if has_prohibition else (None, 0)
        elif lead_in is not None:
            reach -= 1
            if reach <= 0 or len(sentence) > MAX_LIST_ITEM_CHARS:
                lead_in = None
    hits.sort(key=lambda hit: hit[0] != preferred)          # stable: page order inside each group
    return hits


# ---------------------------------------------------------------------------
# The two functions the rest of the project calls
# ---------------------------------------------------------------------------


def analyze_terms(terms_url: str | None, page: str) -> TermsFlag:
    """Flag, or say why the page could not be assessed. `page` is HTML or plain text."""
    text = visible_text(page)
    detected = detect_language(text)
    hits = find_hits(text, detected)
    if hits:
        quotes = tuple(quote for _, quote in hits[:MAX_QUOTES])
        return TermsFlag(terms_url, "flagged", quotes=quotes, language_guess=hits[0][0])
    if scan_html(page).empty_root:
        return TermsFlag(
            terms_url, "not_assessed",
            why='the page is an empty app shell (an empty <div id="root">): its text is built by '
                "JavaScript; read the page by hand")
    length = len(" ".join(text.split()))                    # real characters, line breaks and gaps not counted
    if length == 0:
        return TermsFlag(
            terms_url, "not_assessed",
            why="no visible text in the raw HTML: it is probably built by JavaScript; read the page by hand")
    if detected is None:
        names = ", ".join(language.name for language in LANGUAGES)
        return TermsFlag(
            terms_url, "not_assessed",
            why=f"language not recognised among {names}; read the page by hand")
    return TermsFlag(
        terms_url, "not_flagged", language_guess=detected,
        why=f"read {length} characters of visible text; this is not a clearance")


def terms_from_fetch(result: FetchResult) -> TermsFlag:
    """The terms flag for a fetched page, including the cases where the fetch itself failed."""
    if result.status == "ok":
        return analyze_terms(result.final_url, result.text or "")
    if result.status == "blocked" and result.block is not None:
        return TermsFlag(
            result.url, "not_assessed",
            why=f"the terms page could not be fetched: blocked ({result.block.reason}); read it by hand")
    error = result.error
    kind = error.kind if error else "unknown"
    message = error.message if error else "no detail"
    extra = " (a PDF or other file)" if kind == "not_text" else ""
    return TermsFlag(
        result.url, "not_assessed",
        why=f"the terms page could not be read: fetch failed ({kind}){extra}: {message}; read it by hand"[:400])
