import json
import re
from pathlib import Path

INPUT_ROOT = Path("ground_truth/sampled_pages")
TABLE_FILE_RE = re.compile(r"^table_\d+\.json$")


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def cap_first(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


_HIDDEN_DATE_RE = re.compile(r"\s*\(\s*\d{4}-\d{2}-\d{2}\s*\)")


def clean_value(v):
    if isinstance(v, str):
        return _HIDDEN_DATE_RE.sub("", v).strip()
    return v


def extract_table_gold(table_data: dict) -> dict:
    raw_schema = table_data.get("flattened_schema", [])
    header_grid = table_data.get("raw_header_grid", [])
    display_names = header_grid[0] if header_grid else []

    # prefer the raw display name; fall back to the snake_case key
    schema = []
    for i, col in enumerate(raw_schema):
        if i < len(display_names):
            schema.append(cap_first(display_names[i]))
        else:
            schema.append(cap_first(col))

    key_map = {old: new for old, new in zip(raw_schema, schema)}

    gt = table_data.get("ground_truth", {})
    rows = [
        {key_map.get(k, k): clean_value(v) for k, v in row.items()}
        for row in gt.get("rows", [])
    ]

    return {
        "table_id": table_data.get("table_id"),
        "table_xpath": table_data.get("table_xpath"),
        "table_order_in_page": table_data.get("table_order_in_page"),
        "flattened_schema": schema,
        "ground_truth": {**gt, "rows": rows},
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

        tables.append(extract_table_gold(table_data))

    if not tables or first_table_data is None:
        print(f"[SKIP] No valid table files in {page_dir}")
        return False

    page_gold = {
        "page_id": first_table_data.get("page_id"),
        "source_file": first_table_data.get("source_file"),
        "bucket": first_table_data.get("bucket"),
        "page_folder": page_dir.name,
        "tables": tables,
    }

    output_path = page_dir / "page.gold.json"
    write_json(output_path, page_gold)
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
