import json
from pathlib import Path

INPUT_JSON = Path("html_table_difficulty_scores.json")
OUTPUT_JSONL = Path("html_table_difficulty_scores.jsonl")

with open(INPUT_JSON, "r", encoding="utf-8") as f:
    data = json.load(f)

results = data.get("results", [])

with open(OUTPUT_JSONL, "w", encoding="utf-8") as out:
    for item in results:
        out.write(json.dumps(item, ensure_ascii=False) + "\n")

print("Done.")
print(f"Converted {len(results)} records.")
print(f"Output: {OUTPUT_JSONL.resolve()}")
