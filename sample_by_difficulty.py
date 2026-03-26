import json
from pathlib import Path

INPUT_JSONL = Path("html_table_difficulty_scores.jsonl")
OUTPUT_JSON = Path("sampled_25hard_15medium_10easy.json")

N_HARD = 25
N_MEDIUM = 15
N_EASY = 10


def load_jsonl(path: Path):
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def get_page_band(page: dict) -> str | None:
    score = page.get("page_max_score")
    if score is None:
        return None
    if score <= 2:
        return "easy"
    if score <= 5:
        return "medium"
    return "hard"


def count_tables_at_or_above(page: dict, threshold: int) -> int:
    count = 0
    for table in page.get("tables", []):
        score = table.get("hardness_assessment", {}).get("final_score")
        if isinstance(score, (int, float)) and score >= threshold:
            count += 1
    return count


def sort_key_within_band(page: dict):
    page_max_score = page.get("page_max_score", -1)
    page_avg_score = page.get("page_avg_score", -1)
    table_count = page.get("table_count", 0)

    hardish_count = count_tables_at_or_above(page, threshold=6)
    mediumish_count = count_tables_at_or_above(page, threshold=3)

    return (
        page_max_score,
        page_avg_score,
        hardish_count,
        mediumish_count,
        table_count,
    )


def main():
    pages = load_jsonl(INPUT_JSONL)

    # keep only valid pages with tables
    MAX_TABLES_PER_PAGE = 5

    pages = [
        p
        for p in pages
        if (p.get("parse_ok") and 0 < p.get("table_count", 0) <= MAX_TABLES_PER_PAGE)
    ]
    hard_pages = []
    medium_pages = []
    easy_pages = []

    for page in pages:
        band = get_page_band(page)
        if band == "hard":
            hard_pages.append(page)
        elif band == "medium":
            medium_pages.append(page)
        elif band == "easy":
            easy_pages.append(page)

    hard_pages = sorted(hard_pages, key=sort_key_within_band, reverse=True)
    medium_pages = sorted(medium_pages, key=sort_key_within_band, reverse=True)
    easy_pages = sorted(easy_pages, key=sort_key_within_band, reverse=True)

    selected_hard = hard_pages[:N_HARD]
    selected_medium = medium_pages[:N_MEDIUM]
    selected_easy = easy_pages[:N_EASY]

    sampled = selected_hard + selected_medium + selected_easy
    total_tables = sum(p.get("table_count", 0) for p in sampled)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        output = {
            "dataset_summary": {
                "total_pages": len(sampled),
                "hard_pages": len(selected_hard),
                "medium_pages": len(selected_medium),
                "easy_pages": len(selected_easy),
                "total_tables": total_tables,
                "max_tables_per_page": MAX_TABLES_PER_PAGE,
            },
            "pages": sampled,
        }
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("Done.")
    print(f"Hard available: {len(hard_pages)} | selected: {len(selected_hard)}")
    print(f"Medium available: {len(medium_pages)} | selected: {len(selected_medium)}")
    print(f"Easy available: {len(easy_pages)} | selected: {len(selected_easy)}")

    print(f"\nTotal selected pages: {len(sampled)}")
    print(f"Total tables in selected pages: {total_tables}")

    print(f"\nOutput: {OUTPUT_JSON.resolve()}")


if __name__ == "__main__":
    main()
