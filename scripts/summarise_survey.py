"""Collect the key lines of every survey_output/*.md into one short text file.

Run from the project root:   python scripts\\summarise_survey.py
It only reads files. It contacts no website and changes nothing except
writing survey_summary.txt, the one file to upload to the chat.
"""

import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "survey_output"
SUMMARY = ROOT / "survey_summary.txt"
MAX_LINE = 400  # long lines are cut so the summary stays small

# A line is kept if it starts with one of these (after stripping spaces).
KEEP_STARTS = (
    "FIRM:",
    "2. HTTP status",
    "- Result:",
    "4. JSON-LD",
    "5. RSS",
    "- Job page domain",
    "- Vendor names found",
    "- Conclusion",
    "- Is a visible job title",
    "- Mentions automated access",
)


def clip(line: str) -> str:
    line = line.rstrip()
    return line if len(line) <= MAX_LINE else line[:MAX_LINE] + " [...cut]"


def summarise_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = [f"##### {path.name}"]
    in_obstacles = False
    in_terms_quote = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("8. Obstacles"):
            in_obstacles = True
            continue
        if stripped.startswith("9. Notes"):
            in_obstacles = False
            continue
        if in_obstacles:
            if stripped and "429, stop and write" not in stripped:
                kept.append("  obstacles: " + clip(stripped))
            continue
        if stripped.startswith("- If yes: paste the sentence"):
            in_terms_quote = True
            continue
        if in_terms_quote:
            if stripped:
                kept.append("  terms quote: " + clip(stripped))
            continue
        if stripped.startswith(KEEP_STARTS):
            kept.append(clip(stripped))
    return kept


def main() -> int:
    if not OUTPUT_DIR.is_dir():
        print(f"Folder not found: {OUTPUT_DIR}")
        return 1
    files = sorted(OUTPUT_DIR.glob("*.md"))
    if not files:
        print(f"No .md files in {OUTPUT_DIR}")
        return 1
    blocks: list[str] = []
    for path in files:
        blocks.extend(summarise_file(path))
        blocks.append("")
    SUMMARY.write_text("\n".join(blocks), encoding="utf-8")
    print(f"{len(files)} files summarised into {SUMMARY} ({SUMMARY.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
