"""Tests for terms.py (step T3.3). Offline, firm-neutral.

Every page and sentence here is SYNTHETIC, written for these tests in the seven
supported languages. No real terms page has been captured yet, so these tests
check the rules as written, not their success on real pages. The module itself
says so. The flag is for a person to read: nothing here is a legal reading.
"""

import random
import time

import pytest

from tracker.triage.contract import MAX_QUOTE, BlockInfo, TermsFlag, TriageError
from tracker.triage.fetch_result import FetchResult
from tracker.triage.terms import (
    LANGUAGES,
    analyze_terms,
    detect_language,
    find_hits,
    sentences,
    terms_from_fetch,
    visible_text,
)

URL = "https://www.example.test/terms"

# Filler paragraphs: neutral terms-of-use text, more than 500 characters each.
FILLER = {
    "English": (
        "These terms of use govern your access to this website and the services that are offered on it. "
        "By using the site you agree to follow the rules below. We may update these terms from time to time, "
        "and the version that is published on this page is the one that applies to you. If you do not agree "
        "with the terms, you should stop using the site. All content on the site is protected by law and "
        "belongs to us or to our partners. You are responsible for keeping your account details safe and for "
        "all activity that takes place under your account."),
    "French": (
        "Les présentes conditions d'utilisation régissent votre accès à ce site et aux services qui sont "
        "proposés sur celui-ci. En utilisant le site, vous acceptez de respecter les règles ci-dessous. Nous "
        "pouvons mettre à jour ces conditions de temps en temps, et la version qui est publiée sur cette page "
        "est celle qui s'applique à vous. Si vous n'êtes pas d'accord avec les conditions, vous devez cesser "
        "d'utiliser le site. Tout le contenu du site est protégé par la loi et appartient à notre société ou "
        "à nos partenaires. Vous êtes responsable de la sécurité de vos identifiants et de toute activité."),
    "Spanish": (
        "Estas condiciones de uso regulan su acceso a este sitio web y a los servicios que se ofrecen en él. "
        "Al utilizar el sitio, usted acepta cumplir las normas que figuran a continuación. Podemos actualizar "
        "estas condiciones de vez en cuando, y la versión que se publica en esta página es la que se aplica a "
        "usted. Si no está de acuerdo con las condiciones, debe dejar de utilizar el sitio. Todo el contenido "
        "del sitio está protegido por la ley y pertenece a nuestra empresa o a nuestros socios. Usted es "
        "responsable de mantener seguros los datos de su cuenta y de toda la actividad que se realice."),
    "German": (
        "Diese Nutzungsbedingungen regeln Ihren Zugang zu dieser Website und zu den Diensten, die auf ihr "
        "angeboten werden. Mit der Nutzung der Seite erklären Sie sich damit einverstanden, die folgenden "
        "Regeln zu beachten. Wir können diese Bedingungen von Zeit zu Zeit ändern, und die Fassung, die auf "
        "dieser Seite veröffentlicht ist, ist die für Sie gültige. Wenn Sie mit den Bedingungen nicht "
        "einverstanden sind, sollten Sie die Seite nicht mehr nutzen. Alle Inhalte der Seite sind durch das "
        "Gesetz geschützt und gehören uns oder unseren Partnern. Sie sind für Ihre Zugangsdaten verantwortlich."),
    "Dutch": (
        "Deze gebruiksvoorwaarden zijn van toepassing op uw toegang tot deze website en op de diensten die "
        "daarop worden aangeboden. Door de site te gebruiken, gaat u ermee akkoord de onderstaande regels te "
        "volgen. Wij kunnen deze voorwaarden van tijd tot tijd wijzigen, en de versie die op deze pagina is "
        "gepubliceerd is de versie die voor u geldt. Als u niet akkoord gaat met de voorwaarden, moet u de "
        "site niet meer gebruiken. Alle inhoud van de site wordt beschermd door de wet en is van ons of van "
        "onze partners. U bent verantwoordelijk voor de veiligheid van uw accountgegevens."),
    "Portuguese": (
        "Estes termos de utilização regem o seu acesso a este sítio e aos serviços que são oferecidos nele. "
        "Ao utilizar o sítio, o utilizador aceita cumprir as regras que se seguem. Podemos atualizar estes "
        "termos de tempos a tempos, e a versão que é publicada nesta página é a que se aplica ao utilizador. "
        "Se não concordar com os termos, deve deixar de utilizar o sítio. Todo o conteúdo do sítio está "
        "protegido por lei e pertence à nossa empresa ou aos nossos parceiros. O utilizador é responsável "
        "por manter seguros os dados da sua conta e por toda a atividade que seja realizada."),
    "Italian": (
        "Le presenti condizioni di utilizzo regolano il tuo accesso a questo sito e ai servizi che sono "
        "offerti su di esso. Utilizzando il sito, accetti di rispettare le regole riportate di seguito. "
        "Possiamo aggiornare queste condizioni di tanto in tanto, e la versione che è pubblicata su questa "
        "pagina è quella che si applica a te. Se non sei d'accordo con le condizioni, devi smettere di "
        "utilizzare il sito. Tutti i contenuti del sito sono protetti dalla legge e appartengono alla nostra "
        "società o ai nostri partner. Sei responsabile della sicurezza dei dati del tuo account."),
}

UNSUPPORTED = {
    "Swedish": (
        "Dessa användarvillkor gäller för din åtkomst till webbplatsen och de tjänster som erbjuds på den. "
        "Genom att använda webbplatsen godkänner du att följa reglerna nedan. Vi kan uppdatera villkoren då "
        "och då, och den version som publiceras på den här sidan är den som gäller för dig. Om du inte "
        "godkänner villkoren bör du sluta använda webbplatsen. Allt innehåll på webbplatsen skyddas av lag "
        "och tillhör oss eller våra partners. Du ansvarar för att hålla dina kontouppgifter säkra och för all "
        "aktivitet som sker på ditt konto, och du ska kontakta oss om du upptäcker att någon annan använder det."),
    "Polish": (
        "Niniejszy regulamin określa zasady korzystania z tej strony internetowej oraz usług, które są na "
        "niej oferowane. Korzystając ze strony, zgadzasz się przestrzegać poniższych zasad. Możemy od czasu "
        "do czasu aktualizować te warunki, a wersja opublikowana na tej stronie jest tą, która obowiązuje "
        "Ciebie. Jeżeli nie zgadzasz się z warunkami, powinieneś przestać korzystać ze strony. Cała "
        "zawartość strony jest chroniona prawem i należy do nas lub do naszych partnerów. Odpowiadasz za "
        "bezpieczeństwo danych swojego konta oraz za wszelką aktywność, która odbywa się na Twoim koncie, i "
        "powinieneś skontaktować się z nami, jeśli zauważysz, że ktoś inny z niego korzysta."),
}

# Sentences that SHOULD be flagged, two per language.
BANS = {
    "English": [
        "You may not use robots, spiders or any other automated means to access the site or to copy its content.",
        "Systematic retrieval of data from this site to create a database is prohibited without our written permission.",
        "Users must not use any automatic process or software to extract data from the pages.",
    ],
    "French": [
        "Il est interdit d'utiliser des robots, des aspirateurs de sites ou tout autre moyen automatique pour copier le contenu.",
        "L'utilisateur ne doit pas recourir à des méthodes automatiques pour accéder au site.",
        # a clause worded as a list of nouns followed by one adjective (the wording a past check missed)
        "Vous ne pouvez pas utiliser de mécanismes, programmes ou algorithmes automatiques pour accéder au site ou le copier.",
    ],
    "Spanish": [
        "Queda prohibido el uso de robots, arañas o cualquier medio automático para acceder al sitio.",
        "No está permitido extraer datos de forma sistemática mediante programas automatizados.",
    ],
    "German": [
        "Es ist untersagt, Roboter, Spider oder andere automatisierte Mittel zu verwenden, um auf die Seite zuzugreifen.",
        "Die systematische Abfrage von Daten mit automatischen Programmen ist nicht gestattet.",
    ],
    "Dutch": [
        "Het is verboden om robots, spiders of andere geautomatiseerde middelen te gebruiken om de site te raadplegen.",
        "Het systematisch kopiëren van gegevens met automatische programma's is niet toegestaan.",
    ],
    "Portuguese": [
        "É proibido utilizar robôs, aranhas ou quaisquer meios automáticos para aceder ao sítio.",
        "Não é permitida a extração sistemática de dados através de ferramentas automatizadas.",
    ],
    "Italian": [
        "È vietato utilizzare robot, spider o altri mezzi automatici per accedere al sito.",
        "Non è consentita l'estrazione sistematica dei dati mediante strumenti automatizzati.",
    ],
}

# Sentences that must NOT be flagged: automation without a ban, a ban without automation, or neither.
HARMLESS = {
    "English": ["We use automated tools to improve the quality of the site.",
                "Our newsletter is sent automatically every week.",
                "You may not share your password with anyone."],
    "French": ["Votre abonnement est renouvelé automatiquement chaque année.",
               "Vous ne devez pas communiquer votre mot de passe à des tiers."],
    "Spanish": ["Recibirá un correo de confirmación de forma automática.",
                "No debe compartir su contraseña con terceros."],
    "German": ["Die Bestätigung wird automatisch per E-Mail versendet.",
               "Sie dürfen Ihr Passwort nicht an Dritte weitergeben."],
    "Dutch": ["U ontvangt automatisch een bevestiging per e-mail.",
              "U mag uw wachtwoord niet met anderen delen."],
    "Portuguese": ["A fatura é enviada automaticamente por e-mail.",
                   "Não deve partilhar a sua palavra-passe com terceiros."],
    "Italian": ["Riceverai automaticamente una conferma via e-mail.",
                "Non devi condividere la tua password con altri."],
}


def page(language: str, *extra: str) -> str:
    return "<html><body><p>" + FILLER[language] + "</p>" + "".join(f"<p>{s}</p>" for s in extra) + "</body></html>"


# -- the test data itself is sound -------------------------------------------------------------------

def test_every_language_has_filler_long_enough_and_both_sentence_lists():
    names = {language.name for language in LANGUAGES}
    assert names == set(FILLER) == set(BANS) == set(HARMLESS)
    for name, text in {**FILLER, **UNSUPPORTED}.items():
        assert len(" ".join(visible_text(text).split())) > 400, name


# -- language guess --------------------------------------------------------------------------------------

@pytest.mark.parametrize("language", sorted(FILLER))
def test_each_supported_language_is_recognised_from_a_paragraph(language):
    assert detect_language(FILLER[language]) == language


@pytest.mark.parametrize("language", sorted(UNSUPPORTED))
def test_an_unsupported_language_is_not_taken_for_a_supported_one(language):
    assert detect_language(UNSUPPORTED[language]) is None


def test_no_words_means_no_guess():
    assert detect_language("") is None
    assert detect_language("12345 67890 !!!") is None


# -- flagging -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("language, sentence", [(lang, s) for lang, items in BANS.items() for s in items])
def test_a_ban_on_automated_access_is_flagged_and_quoted_in_every_language(language, sentence):
    flag = analyze_terms(URL, page(language, sentence))
    assert flag.status == "flagged" and flag.url == URL
    assert flag.language_guess == language
    assert flag.quotes == (sentence,)


@pytest.mark.parametrize("language, sentence", [(lang, s) for lang, items in HARMLESS.items() for s in items])
def test_harmless_sentences_are_not_flagged(language, sentence):
    flag = analyze_terms(URL, page(language, sentence))
    assert flag.status == "not_flagged" and flag.language_guess == language and flag.quotes == ()


def test_not_flagged_always_says_it_is_not_a_clearance():
    flag = analyze_terms(URL, page("English", HARMLESS["English"][0]))
    assert flag.status == "not_flagged" and "not a clearance" in flag.why


def test_the_flag_is_found_without_accents_and_in_capitals():
    flag = analyze_terms(URL, page("French", "IL EST INTERDIT D'UTILISER DES ROBOTS POUR ACCEDER AU SITE."))
    assert flag.status == "flagged"


def test_curly_apostrophes_do_not_matter():
    flag = analyze_terms(URL, page("French", "L’utilisateur n’a pas le droit d’utiliser des robots sur ce site."))
    assert flag.status == "flagged"


def test_at_most_two_quotes_and_the_detected_language_comes_first():
    extra = BANS["English"] + [BANS["French"][0]]
    flag = analyze_terms(URL, page("English", *extra))
    assert flag.status == "flagged" and len(flag.quotes) == 2
    assert flag.quotes == tuple(BANS["English"][:2]) and flag.language_guess == "English"


def test_a_ban_in_another_language_than_the_page_is_still_found():
    flag = analyze_terms(URL, page("English", BANS["French"][0]))
    assert flag.status == "flagged" and flag.language_guess == "French"


def test_blocks_are_separated_so_a_ban_does_not_leak_across_list_items():
    html = (FILLER["English"] and "<html><body><p>" + FILLER["English"] +
            "</p><ul><li>Use of robots</li><li>You may not leave this page open</li></ul></body></html>")
    assert analyze_terms(URL, html).status == "not_flagged"


def test_text_in_script_style_template_comments_and_head_is_not_read():
    hidden = BANS["English"][0]
    html = (f"<html><head><title>{hidden}</title><style>/* {hidden} */</style></head><body>"
            f"<script>var s = \"{hidden}\";</script><!-- {hidden} --><template>{hidden}</template>"
            f"<p>{FILLER['English']}</p></body></html>")
    assert analyze_terms(URL, html).status == "not_flagged"


def test_a_plain_text_page_works_too():
    flag = analyze_terms(URL, FILLER["English"] + "\n\n" + BANS["English"][0] + "\n")
    assert flag.status == "flagged"


def test_a_very_long_sentence_is_cut_to_a_quote_that_still_shows_the_match():
    long_sentence = ("The following applies. " * 80) + "You may not use robots to copy this site. " + ("More text. " * 80)
    sentence = " ".join(long_sentence.split())
    one = (sentence.replace(". ", " and ")).strip()               # one very long sentence, no breaks
    flag = analyze_terms(URL, "<p>" + FILLER["English"] + "</p><p>" + one + ".</p>")
    assert flag.status == "flagged"
    (quote,) = flag.quotes
    assert len(quote) <= MAX_QUOTE and "robots" in quote and quote.startswith("... ")


# -- not assessed ------------------------------------------------------------------------------------------------

def test_noscript_text_is_read_because_a_reader_without_javascript_sees_it():
    flag = analyze_terms(URL, page("English") + f"<noscript><p>{BANS['English'][0]}</p></noscript>")
    assert flag.status == "flagged"


def test_an_empty_app_shell_is_not_assessed_and_says_why():
    flag = analyze_terms(URL, '<html><body><div id="root"></div></body></html>')
    assert flag.status == "not_assessed" and "JavaScript" in flag.why and "app shell" in flag.why


def test_a_shell_with_menu_text_around_it_is_still_a_shell():
    html = '<nav><a href="/">Home</a> <a href="/jobs">Jobs</a> Terms of use and privacy</nav><div id="root"></div>'
    assert analyze_terms(URL, html).status == "not_assessed"


def test_a_page_with_no_text_at_all_is_not_assessed():
    for html in ("", "<p></p>", "<html><head><title>x</title></head><body>  \n </body></html>"):
        flag = analyze_terms(URL, html)
        assert flag.status == "not_assessed" and "no visible text" in flag.why


SHORT_TERMS = ("By using this site you agree to these terms. You may not copy the content of the site "
               "without our permission. We may change these terms at any time.")


def test_a_short_readable_terms_page_is_not_flagged_and_says_how_much_was_read():
    """No minimum length: terms pages can be short. The note lets a person judge."""
    flag = analyze_terms(URL, "<p>" + SHORT_TERMS + "</p>")
    assert flag.status == "not_flagged" and flag.language_guess == "English"
    assert f"read {len(SHORT_TERMS)} characters" in flag.why and "not a clearance" in flag.why


def test_a_short_page_with_a_ban_is_flagged():
    assert analyze_terms(URL, "<p>" + BANS["English"][0] + "</p>").status == "flagged"


def test_a_page_with_too_few_words_to_guess_a_language_is_not_assessed_not_cleared():
    flag = analyze_terms(URL, "<p>Terms. Privacy. Cookies. Contact.</p>")
    assert flag.status == "not_assessed" and "language not recognised" in flag.why


@pytest.mark.parametrize("language", sorted(UNSUPPORTED))
def test_an_unsupported_language_without_a_hit_is_not_assessed_and_not_cleared(language):
    flag = analyze_terms(URL, "<p>" + UNSUPPORTED[language] + "</p>")
    assert flag.status == "not_assessed" and "language not recognised" in flag.why
    assert "English" in flag.why and "Italian" in flag.why


# -- from a fetch result ---------------------------------------------------------------------------------------------

def test_an_ok_fetch_is_analysed_and_the_final_address_is_used():
    result = FetchResult("https://a.test/t", "https://a.test/final", "ok", text=page("English", BANS["English"][0]))
    flag = terms_from_fetch(result)
    assert flag.status == "flagged" and flag.url == "https://a.test/final"


def test_a_blocked_fetch_is_not_assessed_with_the_reason():
    result = FetchResult("https://a.test/t", "https://a.test/t", "blocked", block=BlockInfo("http_429", "https://a.test/t", 429))
    flag = terms_from_fetch(result)
    assert flag.status == "not_assessed" and "http_429" in flag.why and flag.url == "https://a.test/t"


def test_a_failed_fetch_is_not_assessed_and_a_pdf_says_so():
    pdf = FetchResult("https://a.test/t", "https://a.test/t", "failed",
                      error=TriageError.of("not_text", "Content-Type is 'application/pdf', which is not text"))
    flag = terms_from_fetch(pdf)
    assert flag.status == "not_assessed" and "PDF" in flag.why and "application/pdf" in flag.why
    tls = FetchResult("https://a.test/t", "https://a.test/t", "failed", error=TriageError.of("tls", "bad certificate"))
    assert "tls" in terms_from_fetch(tls).why


def test_a_failed_fetch_is_never_a_clearance():
    for kind in ("timeout", "dns", "http_4xx", "http_5xx", "too_large", "decode"):
        result = FetchResult("https://a.test/t", "https://a.test/t", "failed", error=TriageError.of(kind, "x"))
        assert terms_from_fetch(result).status == "not_assessed"


# -- text and sentence helpers --------------------------------------------------------------------------------------

def test_visible_text_separates_blocks_and_skips_hidden_parts():
    text = visible_text("<head><title>T</title></head><p>One.</p><div>Two</div><script>no</script>three<br>four")
    assert "T" not in text.split() and "no" not in text
    assert [s for s in text.split("\n") if s.strip()] == ["One.", "Two", "three", "four"]


def test_sentences_split_on_punctuation_and_blocks_and_drop_tiny_pieces_except_lead_ins():
    found = sentences("First sentence is here. Second one follows!\nThird line without a stop\nok\nIncludes:")
    assert found == ["First sentence is here.", "Second one follows!", "Third line without a stop", "Includes:"]


# -- never raises, stays fast ----------------------------------------------------------------------------------------

def test_random_text_never_raises():
    rng = random.Random(9)
    pieces = ["robots", "not", "interdit", "<p>", "</p>", "<script>", "é", " ", ".", "automatic", "means",
              "ohne", "<", ">", "&amp;", "\n", "nao", "scraping", "x" * 50]
    for _ in range(300):
        text = "".join(rng.choice(pieces) for _ in range(rng.randrange(0, 60)))
        assert isinstance(analyze_terms(URL, text), TermsFlag)
        assert isinstance(find_hits(text), list)


def test_long_pages_are_analysed_quickly():
    noise = ("automatic word " * 40 + "the means are not ready. ") * 400        # a long page with near-misses
    start = time.perf_counter()
    analyze_terms(URL, "<p>" + FILLER["English"] + "</p><p>" + noise + "</p>")
    assert time.perf_counter() - start < 3.0


# -- bans written as a list: the prohibition is in the lead-in, not in the item ---------------------------------------
# A real captured page did exactly this and a sentence-by-sentence check missed it.

LISTS = {
    "English": ("You agree not to make any use of this site that:", "uses robots, spiders or scrapers to copy the content;"),
    "French": ("Il est strictement interdit d'utiliser le site de manière à :", "recourir à des robots ou à des aspirateurs de sites ;"),
    "Spanish": ("Queda estrictamente prohibido el siguiente uso del sitio:", "emplear robots o arañas para copiar el contenido;"),
    "German": ("Folgende Nutzung der Seite ist untersagt:", "der Einsatz von Robotern oder Crawlern zum Kopieren der Inhalte;"),
    "Dutch": ("Het is verboden om de site op de volgende manieren te gebruiken:", "robots of spiders inzetten om de inhoud te kopiëren;"),
    "Portuguese": ("É proibida a seguinte utilização do sítio:", "utilizar robôs ou aranhas para copiar o conteúdo;"),
    "Italian": ("È vietato il seguente utilizzo del sito:", "usare robot o spider per copiare i contenuti;"),
}


def list_page(language: str, lead: str, items: list[str]) -> str:
    return (f"<html><body><p>{FILLER[language]}</p><p>{lead}</p><ul>"
            + "".join(f"<li>{item}</li>" for item in items) + "</ul></body></html>")


@pytest.mark.parametrize("language", sorted(LISTS))
def test_a_ban_in_a_list_lead_in_applies_to_the_items_and_the_quote_shows_both(language):
    lead, item = LISTS[language]
    flag = analyze_terms(URL, list_page(language, lead, ["is illegal or abusive;", item]))
    assert flag.status == "flagged" and flag.language_guess == language
    assert flag.quotes == (f"{lead} {item}",)


def test_items_far_down_the_same_list_are_still_covered():
    """The real page had its robots item eight sentences after the lead-in."""
    lead, item = LISTS["English"]
    filler_items = [f"is harmful in way number {n};" for n in range(8)]
    flag = analyze_terms(URL, list_page("English", lead, filler_items + [item]))
    assert flag.status == "flagged"


def test_two_items_give_two_quotes_and_not_more():
    lead, item = LISTS["English"]
    flag = analyze_terms(URL, list_page("English", lead, [item, "harvests personal data;", "scrapes the site;"]))
    assert len(flag.quotes) == 2 and all(quote.startswith(lead) for quote in flag.quotes)


def test_a_lead_in_without_a_prohibition_does_not_ban_its_items():
    page_text = list_page("English", "Our services include:", ["robots that answer your questions;"])
    assert analyze_terms(URL, page_text).status == "not_flagged"


def test_a_new_lead_in_ends_the_previous_list():
    lead, item = LISTS["English"]
    html = (f"<p>{FILLER['English']}</p><p>{lead}</p><ul><li>is illegal;</li></ul>"
            f"<p>We also offer:</p><ul><li>{item}</li></ul>")
    assert analyze_terms(URL, html).status == "not_flagged"


def test_a_short_lead_in_also_ends_the_previous_list():
    lead, item = LISTS["English"]
    html = f"<p>{FILLER['English']}</p><p>{lead}</p><ul><li>is illegal;</li></ul><p>Includes:</p><ul><li>{item}</li></ul>"
    assert analyze_terms(URL, html).status == "not_flagged"


def test_a_long_single_sentence_ends_the_list():
    lead, item = LISTS["English"]
    prose = "This paragraph explains how the service works in general terms and is not a list item and " * 8 + "ends."
    assert len(prose) > 500                                  # one long sentence: prose, not a list item
    html = f"<p>{FILLER['English']}</p><p>{lead}</p><ul><li>is illegal;</li></ul><p>{prose}</p><p>{item}</p>"
    assert analyze_terms(URL, html).status == "not_flagged"


def test_the_list_stops_reaching_after_25_sentences():
    lead, item = LISTS["English"]
    many = [f"is unlawful in way number {n};" for n in range(30)]
    assert analyze_terms(URL, list_page("English", lead, many + [item])).status == "not_flagged"


def test_an_item_that_carries_its_own_ban_is_quoted_alone():
    lead, _ = LISTS["English"]
    own = "You may not use robots to copy this site."
    flag = analyze_terms(URL, list_page("English", lead, [own]))
    assert flag.quotes == (own,)


def test_a_very_long_lead_in_and_item_still_fit_the_quote_limit():
    lead = "You agree not to make any use of this site, its content, its data or its services " + "in any way that " * 15 + "does the following:"
    item = "uses robots to copy content " + "and more " * 30 + ";"             # under the 500-character item limit
    flag = analyze_terms(URL, list_page("English", lead, [item]))
    assert len(lead) > 300 and len(lead) + len(item) > MAX_QUOTE
    assert flag.status == "flagged" and len(flag.quotes[0]) <= MAX_QUOTE and flag.quotes[0].startswith("You agree not to")
    assert "uses robots" in flag.quotes[0]
