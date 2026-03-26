import json
import re
from pathlib import Path

INPUT_ROOT = Path("ground_truth/sampled_pages")
TABLE_FILE_RE = re.compile(r"^table_\d+\.json$")

TYPE_OF_NORMALIZATION_OPERATIONS = [
    "flatten_multilevel_header",
    "drop_reference_column",
    "drop_summary_row",
    "merge_nested_tables",
    "ignore_outer_layout_table",
    "normalize_missing_value_to_null",
    "split_compound_cell",
    "separate_value_and_comparator",
]


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def normalize_html_quotes(html: str) -> str:
    """Replace double-quoted HTML attributes with single quotes to avoid JSON escaping."""
    return re.sub(r'="([^"]*)"', r"='\1'", html)


def extract_table_prompt(table_data: dict) -> dict:
    raw_html = table_data.get("table_preview", {}).get("html_snippet", "")
    return {
        "table_id": table_data.get("table_id"),
        "table_xpath": table_data.get("table_xpath"),
        "table_order_in_page": table_data.get("table_order_in_page"),
        "input": {
            "html_snippet": normalize_html_quotes(raw_html)
        },
        "output": {
            "normalization_operations": [],
            "flattened_schema": [],
            "ground_truth": {"format": "row_records", "rows": []},
        },
    }


def process_page_folder(page_dir: Path) -> bool:
    table_files = sorted(
        p for p in page_dir.iterdir() if p.is_file() and TABLE_FILE_RE.match(p.name)
    )

    if not table_files:
        print(f"[SKIP] No table_*.json files in {page_dir}")
        return False

    tables = []
    first_table_data = None

    for table_file in table_files:
        try:
            table_data = load_json(table_file)
        except Exception as e:
            print(f"[WARN] Failed to read {table_file}: {e}")
            continue

        if first_table_data is None:
            first_table_data = table_data

        tables.append(extract_table_prompt(table_data))

    if not tables or first_table_data is None:
        print(f"[SKIP] No valid table files in {page_dir}")
        return False

    page_prompt = {
        "page_id": first_table_data.get("page_id"),
        "source_file": first_table_data.get("source_file"),
        "bucket": first_table_data.get("bucket"),
        "page_folder": page_dir.name,
        "is_json_valid": True,
        "allowed_values": {
            "type_of_normalization_operations": TYPE_OF_NORMALIZATION_OPERATIONS,
        },
        "tables": tables,
    }

    output_path = page_dir / "page.prompt.json"
    write_json(output_path, page_prompt)
    print(f"[OK] Wrote {output_path}")
    return True


def main():
    if not INPUT_ROOT.exists():
        raise FileNotFoundError(f"Input root does not exist: {INPUT_ROOT}")

    processed = 0

    for page_dir in sorted(INPUT_ROOT.iterdir()):
        if not page_dir.is_dir():
            continue

        if process_page_folder(page_dir):
            processed += 1

    print("\nDone.")
    print(f"Page folders processed: {processed}")


if __name__ == "__main__":
    main()
