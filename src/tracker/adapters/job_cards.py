"""Parse synthetic job-card HTML and load-more API responses offline."""

from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re


@dataclass(frozen=True)
class Card:
    source_job_id: str
    title: str
    url: str
    city: str | None
    contract_type: str | None
    employment_type: str | None
    category: str | None


@dataclass(frozen=True)
class CardParse:
    cards: tuple[Card, ...]
    problems: tuple[str, ...]
    duplicates: int
    has_container: bool
    ignored: int = 0


@dataclass(frozen=True)
class ApiPage:
    cards: tuple[Card, ...]
    problems: tuple[str, ...]
    duplicates: int
    jobs_count: int
    nothing_more: bool
    ignored: int = 0


class _Node:
    def __init__(
        self,
        tag: str,
        attrs: dict[str, str | None],
        parent: "_Node | None" = None,
    ) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list["_Node"] = []
        self.parts: list["_Node | str"] = []


class _HtmlTreeParser(HTMLParser):
    VOID_TAGS = {"img", "br", "input", "hr", "meta", "link"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("[root]", {})
        self.open_nodes = [self.root]
        self.card_nodes: list[_Node] = []
        self.count_nodes: list[_Node] = []
        self.container_found = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        node_attrs = dict(attrs)
        node = _Node(tag, node_attrs, self.open_nodes[-1])
        self.open_nodes[-1].children.append(node)
        self.open_nodes[-1].parts.append(node)

        classes = _class_tokens(node_attrs.get("class"))
        if tag == "div" and "job-card" in classes:
            self.card_nodes.append(node)
        if "jobs-count" in classes:
            self.count_nodes.append(node)
        if "cards-container" in classes:
            self.container_found = True

        if tag not in self.VOID_TAGS:
            self.open_nodes.append(node)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID_TAGS:
            return
        for index in range(len(self.open_nodes) - 1, 0, -1):
            if self.open_nodes[index].tag == tag:
                del self.open_nodes[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.open_nodes[-1].parts.append(data)


def _class_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return set(value.split())


def _descendants(node: _Node) -> list[_Node]:
    found: list[_Node] = []
    for child in node.children:
        found.append(child)
        found.extend(_descendants(child))
    return found


def _node_text(node: _Node) -> str:
    text_parts: list[str] = []
    for part in node.parts:
        if isinstance(part, str):
            text_parts.append(part)
        else:
            text_parts.append(_node_text(part))
    return _collapsed_text("".join(text_parts))


def _collapsed_text(text: str) -> str:
    return " ".join(text.split())


def _first_descendant_with_classes(
    node: _Node,
    required_classes: set[str],
) -> _Node | None:
    for descendant in _descendants(node):
        if required_classes.issubset(_class_tokens(descendant.attrs.get("class"))):
            return descendant
    return None


def _optional_text(node: _Node | None) -> str | None:
    if node is None:
        return None
    text = _node_text(node)
    return text if text else None


def parse_cards(html: str) -> CardParse:
    """Parse all complete job-card elements and report skipped cards."""
    parser = _HtmlTreeParser()
    parser.feed(html)
    parser.close()

    cards: list[Card] = []
    problems: list[str] = []
    seen_ids: set[str] = set()
    duplicates = 0
    ignored = 0

    for position, card_node in enumerate(parser.card_nodes, start=1):
        card_id = _collapsed_text(card_node.attrs.get("id") or "")
        if not card_id:
            ignored += 1
            continue

        overlay = _first_descendant_with_classes(card_node, {"job-card-overlay"})
        anchor: _Node | None = None
        if overlay is not None:
            for descendant in _descendants(overlay):
                if descendant.tag == "a":
                    anchor = descendant
                    break

        url = ""
        aria_label = ""
        if anchor is not None:
            url = _collapsed_text(anchor.attrs.get("href") or "")
            aria_label = _collapsed_text(anchor.attrs.get("aria-label") or "")
        if not url:
            problems.append(f"card {position} skipped: missing link")
            continue

        title_node = _first_descendant_with_classes(card_node, {"job-card-title"})
        title = _optional_text(title_node) or aria_label
        if not title:
            problems.append(f"card {position} skipped: missing title")
            continue

        if card_id in seen_ids:
            duplicates += 1
            continue
        seen_ids.add(card_id)

        city_node = _first_descendant_with_classes(card_node, {"icon", "city"})
        contract_node = _first_descendant_with_classes(
            card_node,
            {"icon", "contract-type"},
        )
        employment_node = _first_descendant_with_classes(
            card_node,
            {"icon", "employment-type"},
        )

        category_node: _Node | None = None
        for descendant in _descendants(card_node):
            if descendant.tag == "span" and "Badge" in _class_tokens(
                descendant.attrs.get("class")
            ):
                category_node = descendant
                break

        cards.append(
            Card(
                source_job_id=card_id,
                title=title,
                url=url,
                city=_optional_text(city_node),
                contract_type=_optional_text(contract_node),
                employment_type=_optional_text(employment_node),
                category=_optional_text(category_node),
            )
        )

    return CardParse(
        cards=tuple(cards),
        problems=tuple(problems),
        duplicates=duplicates,
        has_container=parser.container_found,
        ignored=ignored,
    )


def parse_total_count(html: str) -> int | None:
    """Return the first digit run in the first jobs-count element."""
    parser = _HtmlTreeParser()
    parser.feed(html)
    parser.close()
    if not parser.count_nodes:
        return None
    text = _node_text(parser.count_nodes[0])
    match = re.search(r"\d+", text)
    if match is None:
        return None
    return int(match.group(0))


def parse_jobs_api(text: str) -> ApiPage:
    """Parse and validate a load-more JSON response."""
    try:
        response = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError(
            f"unexpected API response: invalid JSON ({error})"
        ) from None

    if not isinstance(response, dict):
        raise ValueError("unexpected API response: expected a JSON object")
    if "html" not in response:
        raise ValueError("unexpected API response: missing key 'html'")
    if "jobsCount" not in response:
        raise ValueError("unexpected API response: missing key 'jobsCount'")
    if "nothingMoreToDisplay" not in response:
        raise ValueError(
            "unexpected API response: missing key 'nothingMoreToDisplay'"
        )

    html = response["html"]
    jobs_count = response["jobsCount"]
    nothing_more = response["nothingMoreToDisplay"]

    if not isinstance(html, str):
        raise ValueError("unexpected API response: 'html' must be a string")
    if not isinstance(jobs_count, int) or isinstance(jobs_count, bool):
        raise ValueError(
            "unexpected API response: 'jobsCount' must be an integer"
        )
    if not isinstance(nothing_more, bool):
        raise ValueError(
            "unexpected API response: 'nothingMoreToDisplay' must be a boolean"
        )

    card_parse = parse_cards(html)
    return ApiPage(
        cards=card_parse.cards,
        problems=card_parse.problems,
        duplicates=card_parse.duplicates,
        jobs_count=jobs_count,
        nothing_more=nothing_more,
        ignored=card_parse.ignored,
    )
