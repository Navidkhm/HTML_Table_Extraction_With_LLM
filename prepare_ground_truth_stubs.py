import json
import re
import shutil
from pathlib import Path
from lxml import html
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
GROUND_TRUTH_DIR = BASE_DIR / "ground_truth"
METADATA_DIR = BASE_DIR / "metadata"
HTML_PAGES_DIR = BASE_DIR / "html_pages"
SAMPLED_FILE = BASE_DIR / "sampled_25hard_15medium_10easy.json"

EXCLUDED_TABLE_CLASSES = {
    "nowraplinks",
    "infobox",
    "ambox",
}


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def safe_page_id(filename: str) -> str:
    return Path(filename).stem


def load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    return json.loads(raw)


def load_sampled_lookup(path: Path) -> dict:
    # (file_name, table_index) -> difficulty metadata
    if not path.exists():
        return {}

    data = load_json(path)
    lookup = {}

    pages = []
    if isinstance(data, dict):
        pages = data.get("pages", [])
    elif isinstance(data, list):
        # fallback if file is directly a list of pages
        pages = data

    for page in pages:
        if not isinstance(page, dict):
            continue

        file_name = page.get("file_name") or page.get("source_file")
        if not file_name:
            continue

        for tbl in page.get("tables", []):
            if not isinstance(tbl, dict):
                continue

            table_index = tbl.get("table_index")
            if table_index is None:
                continue

            try:
                table_index = int(table_index)
            except Exception:
                continue

            ha = tbl.get("hardness_assessment", {})
            if not isinstance(ha, dict):
                ha = {}

            lookup[(file_name, table_index)] = {
                "auto_score": ha.get("auto_score"),
                "manual_adjustment": ha.get("manual_adjustment", 0),
                "final_score": ha.get("final_score"),
                "difficulty_band": ha.get("difficulty_band"),
            }

    return lookup


def get_input_dir_for_bucket(bucket: str) -> Path:
    return HTML_PAGES_DIR


def extract_preview_from_html_snippet(table_html: str, max_rows=6, max_cells=8):
    soup = BeautifulSoup(table_html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    preview = []
    for tr in table.find_all("tr")[:max_rows]:
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            cells = tr.find_all(["th", "td"])
        row = [clean_text(td.get_text(" ", strip=True)) for td in cells[:max_cells]]
        if row:
            preview.append(row)
    return preview


def detect_characteristics(table_html: str):
    soup = BeautifulSoup(table_html, "html.parser")
    table = soup.find("table")

    text = clean_text(table.get_text(" ", strip=True)).lower() if table else ""
    html_lower = table_html.lower()

    return {
        "has_nested_tables": bool(table and table.find("table")),
        "has_rowspan": "rowspan=" in html_lower,
        "has_colspan": "colspan=" in html_lower,
        "has_missing_values": any(
            token in text
            for token in [
                "n/a",
                "not available",
                "information not publicly available",
                "not publicly available",
                "—",
            ]
        ),
        "has_summary_row": any(
            token in text for token in ["totals", "total", "average", "avg", "sum"]
        ),
    }


def extract_raw_header_grid(table_html: str, max_header_rows=3):
    soup = BeautifulSoup(table_html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    header_rows = []
    trs = table.find_all("tr")

    for tr in trs:
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            cells = tr.find_all(["th", "td"])

        row = [clean_text(c.get_text(" ", strip=True)) for c in cells]
        if not row:
            continue

        has_th = bool(tr.find("th"))
        has_td = bool(tr.find("td"))

        if has_td and not has_th and header_rows:
            break

        header_rows.append(row)

        if len(header_rows) >= max_header_rows:
            break

    return header_rows


def get_table_classes_from_html(table_html: str):
    soup = BeautifulSoup(table_html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []
    return table.get("class", [])


def should_exclude_table(table_html: str):
    classes = set(get_table_classes_from_html(table_html))
    matched = sorted(classes.intersection(EXCLUDED_TABLE_CLASSES))
    return len(matched) > 0, matched


def make_table_stub(
    bucket: str,
    source_file: str,
    page_id: str,
    table_idx: int,
    xpath: str,
    table_html: str,
    sampled_meta: dict | None = None,
):
    preview = extract_preview_from_html_snippet(table_html)
    characteristics = detect_characteristics(table_html)
    raw_header_grid = extract_raw_header_grid(table_html)
    table_classes = get_table_classes_from_html(table_html)

    stub = {
        "page_id": page_id,
        "source_file": source_file,
        "bucket": bucket,
        "table_id": f"table_{table_idx:03d}",
        "table_xpath": xpath,
        "table_order_in_page": table_idx,
        "keep_for_evaluation": None,
        "skip_reason": None,
        "html_characteristics": {
            **characteristics,
            "table_classes": table_classes,
        },
        "table_semantics": {
            "table_type": None,
            "entity_granularity": None,
            "notes": None,
        },
        "normalization_operations": [],
        "raw_header_grid": raw_header_grid,
        "flattened_schema": [],
        "ground_truth": {
            "format": "row_records",
            "rows": [],
        },
        "table_preview": {
            "text_preview": preview,
            "html_snippet": table_html[:4000],
        },
    }

    if sampled_meta:
        stub["auto_score"] = sampled_meta.get("auto_score")
        stub["manual_adjustment"] = sampled_meta.get("manual_adjustment", 0)
        stub["final_score"] = sampled_meta.get("final_score")
        stub["difficulty_band"] = sampled_meta.get("difficulty_band")

    return stub


def parse_tables_with_xpaths(html_text: str):
    parser = html.HTMLParser(encoding="utf-8")
    tree = html.fromstring(html_text, parser=parser)
    tables = tree.xpath("//table")

    parsed = []
    for idx, tbl in enumerate(tables, start=1):
        xpath = tree.getroottree().getpath(tbl)
        table_html = html.tostring(tbl, encoding="unicode", pretty_print=False)
        parsed.append(
            {
                "table_idx": idx,
                "xpath": xpath,
                "table_html": table_html,
            }
        )
    return parsed


def main():
    sampled_data = load_json(SAMPLED_FILE)
    pages = (
        sampled_data.get("pages", [])
        if isinstance(sampled_data, dict)
        else sampled_data
    )
    selected_files = [p["file_name"] for p in pages if p.get("file_name")]
    selected_pages = {"sampled_pages": selected_files}
    sampled_lookup = load_sampled_lookup(SAMPLED_FILE)

    if not selected_pages:
        print("[WARN] No pages found in sampled file. Nothing to do.")
        return

    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    global_table_index = []

    for bucket, selected_files in selected_pages.items():
        bucket_input_dir = get_input_dir_for_bucket(bucket)
        bucket_output_dir = GROUND_TRUTH_DIR / bucket
        bucket_output_dir.mkdir(parents=True, exist_ok=True)

        print(
            f"\n=== Processing bucket: {bucket} | selected files: {len(selected_files)} ==="
        )
        print(f"Input dir: {bucket_input_dir}")

        if not bucket_input_dir.exists():
            print(
                f"[WARN] Input directory does not exist for bucket '{bucket}': {bucket_input_dir}"
            )
            continue

        for filename in selected_files:
            html_path = bucket_input_dir / filename

            if not html_path.exists():
                print(f"[WARN] File not found: {html_path}")
                continue

            page_id = safe_page_id(filename)
            page_output_dir = bucket_output_dir / page_id
            page_output_dir.mkdir(parents=True, exist_ok=True)

            try:
                html_text = html_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                print(f"[ERROR] Could not read {html_path}: {e}")
                continue

            shutil.copy2(html_path, page_output_dir / filename)

            try:
                parsed_tables = parse_tables_with_xpaths(html_text)
            except Exception as e:
                print(f"[ERROR] Could not parse tables in {html_path}: {e}")
                continue

            page_manifest = {
                "page_id": page_id,
                "source_file": filename,
                "bucket": bucket,
                "input_file_path": str(html_path),
                "total_tables_found_before_filter": len(parsed_tables),
                "total_tables_kept_after_filter": 0,
                "excluded_tables": [],
                "tables": [],
            }

            kept_count = 0

            for entry in parsed_tables:
                table_idx = entry["table_idx"]
                xpath = entry["xpath"]
                table_html = entry["table_html"]
                original_table_id = f"table_{table_idx:03d}"

                exclude, matched_classes = should_exclude_table(table_html)
                if exclude:
                    page_manifest["excluded_tables"].append(
                        {
                            "table_id": original_table_id,
                            "table_xpath": xpath,
                            "excluded_due_to_classes": matched_classes,
                        }
                    )
                    continue

                kept_count += 1
                kept_table_id = f"table_{kept_count:03d}"

                sampled_meta = sampled_lookup.get((filename, table_idx))

                stub = make_table_stub(
                    bucket=bucket,
                    source_file=filename,
                    page_id=page_id,
                    table_idx=kept_count,
                    xpath=xpath,
                    table_html=table_html,
                    sampled_meta=sampled_meta,
                )

                out_file = page_output_dir / f"{kept_table_id}.json"
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(stub, f, ensure_ascii=False, indent=2)

                manifest_item = {
                    "table_id": kept_table_id,
                    "original_table_position_in_page": table_idx,
                    "table_xpath": xpath,
                    "keep_for_evaluation": None,
                    "annotation_file": str(out_file.relative_to(BASE_DIR).as_posix()),
                }

                if sampled_meta:
                    manifest_item["auto_score"] = sampled_meta.get("auto_score")
                    manifest_item["manual_adjustment"] = sampled_meta.get(
                        "manual_adjustment", 0
                    )
                    manifest_item["final_score"] = sampled_meta.get("final_score")
                    manifest_item["difficulty_band"] = sampled_meta.get(
                        "difficulty_band"
                    )

                page_manifest["tables"].append(manifest_item)

                global_table_index.append(
                    {
                        "bucket": bucket,
                        "source_file": filename,
                        "page_id": page_id,
                        "table_id": kept_table_id,
                        "original_table_position_in_page": table_idx,
                        "table_xpath": xpath,
                        "annotation_file": str(
                            out_file.relative_to(BASE_DIR).as_posix()
                        ),
                        "auto_score": (
                            sampled_meta.get("auto_score") if sampled_meta else None
                        ),
                        "manual_adjustment": (
                            sampled_meta.get("manual_adjustment", 0)
                            if sampled_meta
                            else None
                        ),
                        "final_score": (
                            sampled_meta.get("final_score") if sampled_meta else None
                        ),
                        "difficulty_band": (
                            sampled_meta.get("difficulty_band")
                            if sampled_meta
                            else None
                        ),
                    }
                )

            page_manifest["total_tables_kept_after_filter"] = kept_count

            manifest_path = page_output_dir / "page_manifest.json"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(page_manifest, f, ensure_ascii=False, indent=2)

            print(
                f"[OK] {filename}: "
                f"{len(parsed_tables)} tables found, "
                f"{kept_count} kept after class filter -> {page_output_dir}"
            )

    table_index_path = METADATA_DIR / "table_index_sampled.json"
    with open(table_index_path, "w", encoding="utf-8") as f:
        json.dump(global_table_index, f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(f"Global table index written to: {table_index_path}")


if __name__ == "__main__":
    main()
