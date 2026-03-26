import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
GROUND_TRUTH_DIR = BASE_DIR / "ground_truth"


def normalize_column(name):
    name = name.lower()
    name = name.replace(".", "")
    name = name.replace(" ", "_")
    name = name.replace("-", "_")
    return name


def convert_preview_to_rows(preview):
    if not preview or len(preview) < 2:
        return None, None

    header = preview[0]
    rows = preview[1:]

    schema = [normalize_column(h) for h in header]

    parsed_rows = []
    for r in rows:
        row = {}

        for i, col in enumerate(schema):
            if i < len(r):
                value = r[i]

                # convert numbers if possible
                if isinstance(value, str) and value.isdigit():
                    value = int(value)

                row[col] = value
            else:
                row[col] = None

        parsed_rows.append(row)

    return schema, parsed_rows


def process_table_file(path):
    with open(path) as f:
        data = json.load(f)

    preview = data["table_preview"]["text_preview"]

    schema, rows = convert_preview_to_rows(preview)

    if schema is None:
        return False

    # fill schema if empty
    if not data["flattened_schema"]:
        data["flattened_schema"] = schema

    # fill rows if empty
    if not data["ground_truth"]["rows"]:
        data["ground_truth"]["rows"] = rows

    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    return True


def main():

    table_files = list(GROUND_TRUTH_DIR.rglob("table_*.json"))

    print("Tables found:", len(table_files))

    filled = 0

    for table_file in table_files:
        if process_table_file(table_file):
            filled += 1

    print("Tables auto-filled:", filled)


if __name__ == "__main__":
    main()
