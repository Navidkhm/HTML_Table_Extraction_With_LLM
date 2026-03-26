import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import html as lxml_html
from lxml import etree

# config
BASE_DIR = Path(__file__).resolve().parent
HTML_DIR = BASE_DIR / "html_pages"
OUTPUT_JSON = BASE_DIR / "html_table_difficulty_scores.json"

HTML_PARSER = lxml_html.HTMLParser(encoding="utf-8", recover=True)

SUMMARY_KEYWORDS = {
    "total",
    "totals",
    "sum",
    "average",
    "avg",
    "overall",
    "subtotal",
    "mean",
}

NOISE_HEADER_PATTERNS = [
    r"^ref\.?$",
    r"^reference(s)?$",
    r"^notes?$",
    r"^image(s)?$",
    r"^flag(s)?$",
    r"^icon(s)?$",
    r"^refs\.?$",
]

UNIT_PATTERNS = [
    r"\busd\b",
    r"\beur\b",
    r"\bgbp\b",
    r"\birr\b",
    r"\bkg\b",
    r"\bg\b",
    r"\bkm\b",
    r"\bcm\b",
    r"\bmm\b",
    r"\bm\b",
    r"\bmi\b",
    r"\b°c\b",
    r"\b°f\b",
    r"\bpercent\b",
    r"\bpercentage\b",
    r"%",
    r"\bmillion(s)?\b",
    r"\bbillion(s)?\b",
    r"\bthousand\b",
    r"\bmn\b",
    r"\bbn\b",
]

COMPOUND_PATTERNS = [
    r"\band\b",
    r"/",
    r">",
    r"<",
    r"≥",
    r"≤",
    r"\[[^\]]+\]",
    r",\s+\w+",
]

INT_RE = re.compile(r"\d+")
NON_DIGIT_NUM_ATTR_RE = re.compile(r"^\s*\d+\s*$")
PAREN_RE = re.compile(r"\(([^)]+)\)")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def save_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_html_file(path: Path):
    return lxml_html.document_fromstring(path.read_bytes(), parser=HTML_PARSER)


def remove_unwanted_tables(doc):
    # drop nav/infobox/warning tables before scoring
    root_copy = etree.fromstring(etree.tostring(doc))
    unwanted = root_copy.xpath(
        "//table["
        "contains(concat(' ', normalize-space(@class), ' '), ' nowraplinks ') "
        "or "
        "contains(concat(' ', normalize-space(@class), ' '), ' infobox ') "
        "or "
        "contains(concat(' ', normalize-space(@class), ' '), ' ambox ')"
        "]"
    )
    for t in unwanted:
        p = t.getparent()
        if p is not None:
            p.remove(t)
    return root_copy


def get_abs_xpath(node) -> str:
    return node.getroottree().getpath(node)


def norm_space(s: str) -> str:
    return " ".join((s or "").split())


def safe_int_attr(val: Optional[str], default: int = 1) -> int:
    if not val:
        return default
    m = INT_RE.search(str(val))
    if not m:
        return default
    try:
        n = int(m.group(0))
        return n if n >= 1 else default
    except Exception:
        return default


def is_bad_span_attr(val: Optional[str]) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    if s == "":
        return True
    return NON_DIGIT_NUM_ATTR_RE.match(s) is None


def score_to_band(score: int) -> str:
    if score <= 2:
        return "easy"
    if score <= 5:
        return "medium"
    return "hard"


# table structure
def get_rows(table) -> List[Any]:
    return table.xpath(".//tr")


def get_direct_cells(tr) -> List[Any]:
    cells = tr.xpath("./th|./td")
    if not cells:
        cells = tr.xpath(".//th|.//td")
    return cells


def get_header_rows(table) -> List[List[str]]:
    header_rows = []
    trs = get_rows(table)

    for tr in trs:
        ths = tr.xpath("./th")
        if ths:
            header_rows.append([clean_text(" ".join(th.itertext())) for th in ths])
        else:
            break

    return header_rows


def get_header_texts(table) -> List[str]:
    flattened = []
    for row in get_header_rows(table):
        flattened.extend(row)
    return [x for x in flattened if x]


def header_row_count(table) -> int:
    count = 0
    for tr in get_rows(table):
        ths = tr.xpath("./th")
        if ths:
            count += 1
        else:
            break
    return count


def body_rows(table) -> List[Any]:
    rows = get_rows(table)
    return rows[header_row_count(table) :]


def estimate_total_columns(table) -> int:
    max_cols = 0
    for tr in get_rows(table)[:10]:
        width = 0
        cells = get_direct_cells(tr)
        for c in cells:
            width += safe_int_attr(c.get("colspan"), 1)
        max_cols = max(max_cols, width)
    return max_cols


# detectors
def detect_rowspan_semantic_dependency(table) -> int:
    for tr in body_rows(table):
        for cell in get_direct_cells(tr):
            if safe_int_attr(cell.get("rowspan"), 1) > 1:
                return 1
    return 0


def detect_colspan_header_hierarchy(table) -> int:
    for tr in get_rows(table):
        ths = tr.xpath("./th")
        if not ths:
            break
        for th in ths:
            if safe_int_attr(th.get("colspan"), 1) > 1:
                return 1
    return 0


def detect_nested_tables(table) -> int:
    desc = table.xpath(".//table")
    return 1 if len(desc) > 0 else 0


def detect_section_rows_inside_body(table) -> int:
    total_cols = max(1, estimate_total_columns(table))

    for tr in body_rows(table):
        cells = get_direct_cells(tr)
        if not cells:
            continue

        visible_texts = []
        for c in cells:
            txt = clean_text(" ".join(c.itertext()))
            if txt:
                visible_texts.append(txt)

        if len(visible_texts) == 1:
            return 1

        if len(cells) == 1:
            colspan = safe_int_attr(cells[0].get("colspan"), 1)
            if colspan >= total_cols - 1:
                return 1

    return 0


def detect_summary_or_total_rows(table) -> int:
    for tr in body_rows(table):
        cells = get_direct_cells(tr)
        if not cells:
            continue
        first = clean_text(" ".join(cells[0].itertext())).lower()
        if first in SUMMARY_KEYWORDS or any(k in first for k in SUMMARY_KEYWORDS):
            return 1
    return 0


def detect_compound_cell_values(table) -> int:
    texts = []

    for tr in get_rows(table)[:12]:
        cells = get_direct_cells(tr)
        for c in cells[:8]:
            texts.append(clean_text(" ".join(c.itertext())))

    for text in texts:
        if not text:
            continue
        for pat in COMPOUND_PATTERNS:
            if re.search(pat, text, flags=re.IGNORECASE):
                return 1
    return 0


def detect_units_or_scales_in_headers(table) -> int:
    headers = get_header_texts(table)

    for h in headers:
        h = clean_text(h)
        for pat in UNIT_PATTERNS:
            if re.search(pat, h, flags=re.IGNORECASE):
                return 1
        if "(" in h and ")" in h:
            return 1
    return 0


def detect_nonsemantic_noise_columns(table) -> int:
    headers = [clean_text(h).lower() for h in get_header_texts(table) if clean_text(h)]
    for h in headers:
        for pat in NOISE_HEADER_PATTERNS:
            if re.match(pat, h, flags=re.IGNORECASE):
                return 1
    return 0


def detect_visual_grouping_proxy(table) -> int:
    for td in table.xpath(".//td"):
        score = 0
        if td.get("rowspan") or td.get("colspan"):
            score += 1
        if len(td.xpath(".//a")) >= 2:
            score += 1
        if td.xpath(".//br"):
            score += 1
        if td.xpath(".//img"):
            score += 1
        if td.xpath(".//span[contains(@style, 'display:none')]"):
            score += 1
        if score >= 2:
            return 1
    return 0


def detect_dirty_table(table, root) -> int:
    bad_cells = table.xpath(".//*[@rowspan or @colspan]")
    for c in bad_cells:
        if is_bad_span_attr(c.get("rowspan")) or is_bad_span_attr(c.get("colspan")):
            return 1

    allowed = {"tr", "thead", "tbody", "tfoot", "caption", "colgroup", "col"}
    direct_children = [ch for ch in table if isinstance(ch.tag, str)]
    weird = [ch.tag.lower() for ch in direct_children if ch.tag.lower() not in allowed]
    if weird:
        return 1

    if root.xpath("//tr[not(ancestor::table)]"):
        return 1
    if root.xpath("//td[not(ancestor::tr)]"):
        return 1
    if root.xpath("//th[not(ancestor::tr)]"):
        return 1

    return 0


def table_signals(table) -> Dict[str, Any]:
    sig: Dict[str, Any] = {}
    sig["rowspan_ge2"] = bool(
        table.xpath(
            ".//td[@rowspan and number(@rowspan) >= 2] | .//th[@rowspan and number(@rowspan) >= 2]"
        )
    )
    sig["colspan_ge2"] = bool(
        table.xpath(
            ".//td[@colspan and number(@colspan) >= 2] | .//th[@colspan and number(@colspan) >= 2]"
        )
    )
    sig["multi_links_cell"] = bool(table.xpath(".//td[count(.//a) >= 2]"))
    sig["br_in_cell"] = bool(table.xpath(".//td[.//br]"))
    sig["lists_in_cell"] = bool(table.xpath(".//td[.//ul or .//ol or .//li]"))
    sig["thead_ge2"] = bool(table.xpath("count(.//thead/tr) >= 2"))
    sig["header_colspan"] = bool(
        table.xpath(".//tr[1]//th[@colspan and number(@colspan) >= 2]")
    )
    refs = table.xpath(
        ".//sup[contains(@class,'reference')] | .//a[contains(@href,'#cite_note')]"
    )
    sig["cite_count"] = len(refs)
    sig["cite_ge5"] = len(refs) >= 5
    sig["has_hidden_span"] = bool(
        table.xpath(".//span[contains(@style,'display:none')]")
    )
    sig["has_img"] = bool(table.xpath(".//img"))
    sig["hidden_plus_img"] = sig["has_hidden_span"] and sig["has_img"]

    rows = table.xpath(".//tr")
    sig["row_count"] = len(rows)

    max_cols = 0
    for tr in rows[:50]:
        max_cols = max(max_cols, len(tr.xpath("./th|./td")))
    sig["max_cols"] = max_cols

    sig["text_len"] = len(norm_space(" ".join(table.itertext())))
    return sig


def detect_llm_break_proxy(table) -> int:
    sig = table_signals(table)
    signals = 0

    if sig["rowspan_ge2"] or sig["colspan_ge2"]:
        signals += 1
    if sig["multi_links_cell"] and (sig["br_in_cell"] or sig["lists_in_cell"]):
        signals += 1
    if sig["thead_ge2"] or sig["header_colspan"]:
        signals += 1
    if sig["cite_ge5"]:
        signals += 1
    if sig["hidden_plus_img"]:
        signals += 1

    return 1 if signals >= 2 else 0


def detect_multiple_plausible_schemas_proxy(table) -> int:
    # multi-row headers + colspan = ambiguous schema
    hrows = get_header_rows(table)
    if len(hrows) >= 2 and detect_colspan_header_hierarchy(table):
        return 1
    return 0


# scoring
def build_hardness_assessment(table, root) -> Dict[str, Any]:
    criteria_auto = {
        "rowspan_semantic_dependency": detect_rowspan_semantic_dependency(table),
        "colspan_header_hierarchy": detect_colspan_header_hierarchy(table),
        "nested_tables": detect_nested_tables(table),
        "visual_grouping_not_dom": detect_visual_grouping_proxy(table),
        "section_rows_inside_body": detect_section_rows_inside_body(table),
        "summary_or_total_rows": detect_summary_or_total_rows(table),
        "compound_cell_values": detect_compound_cell_values(table),
        "units_or_scales_in_headers": detect_units_or_scales_in_headers(table),
        "non_semantic_noise_columns": detect_nonsemantic_noise_columns(table),
        "multiple_plausible_schemas": detect_multiple_plausible_schemas_proxy(table),
        "dirty_table_markup": detect_dirty_table(table, root),
        "llm_break_candidate_proxy": detect_llm_break_proxy(table),
    }

    auto_score = sum(v for v in criteria_auto.values() if isinstance(v, int))

    return {
        "auto_score": auto_score,
        "manual_adjustment": 0,
        "final_score": auto_score,
        "difficulty_band": score_to_band(auto_score),
        "criteria": {
            key: {"auto": value, "manual": None} for key, value in criteria_auto.items()
        },
        "notes": "",
        "table_stats": {
            "row_count": table_signals(table)["row_count"],
            "max_cols": table_signals(table)["max_cols"],
            "text_len": table_signals(table)["text_len"],
        },
    }


# file processing
def process_html_file(path: Path, base_dir: Path) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "file_name": path.name,
        "relative_path": str(path.relative_to(base_dir)),
        "source_path": str(path.resolve()),
        "parse_ok": False,
        "table_count": 0,
        "page_max_score": None,
        "page_avg_score": None,
        "tables": [],
    }

    try:
        doc = parse_html_file(path)
        filtered = remove_unwanted_tables(doc)
    except Exception as e:
        record["error"] = str(e)
        return record

    record["parse_ok"] = True
    tables = filtered.xpath("//table")
    record["table_count"] = len(tables)

    scores = []

    for i, table in enumerate(tables, start=1):
        assessment = build_hardness_assessment(table, filtered)
        scores.append(assessment["final_score"])

        record["tables"].append(
            {
                "table_index": i,
                "table_xpath": get_abs_xpath(table),
                "hardness_assessment": assessment,
            }
        )

    if scores:
        record["page_max_score"] = max(scores)
        record["page_avg_score"] = round(sum(scores) / len(scores), 4)

    return record


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Score difficulty of tables in all HTML files."
    )
    parser.add_argument(
        "--html-dir",
        type=str,
        default=str(HTML_DIR),
        help="Directory containing HTML files.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(OUTPUT_JSON),
        help="Output JSON path.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit for number of HTML files.",
    )
    args = parser.parse_args()

    html_dir = Path(args.html_dir)
    output_path = Path(args.output)

    html_files = sorted(list(html_dir.rglob("*.html")) + list(html_dir.rglob("*.htm")))
    if args.limit is not None:
        html_files = html_files[: args.limit]

    print(f"Found {len(html_files)} HTML files.")

    results = []
    processed = 0
    failed = 0

    kept = 0
    skipped_no_tables = 0

    for idx, path in enumerate(html_files, start=1):
        rec = process_html_file(path, html_dir)

        processed += 1

        if not rec["parse_ok"]:
            failed += 1
        elif rec["table_count"] == 0:
            skipped_no_tables += 1
        else:
            results.append(rec)
            kept += 1

        if idx % 1000 == 0:
            print(
                f"Processed {idx}/{len(html_files)} files... "
                f"failed={failed} kept={kept} skipped_no_tables={skipped_no_tables}"
            )

    output = {
        "source_dir": str(html_dir.resolve()),
        "total_files": len(html_files),
        "processed_files": processed,
        "parse_failed": failed,
        "results": results,
    }

    save_json(output_path, output)

    print("Done.")
    print(f"Processed: {processed}")
    print(f"Parse failed: {failed}")
    print(f"Output written to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
