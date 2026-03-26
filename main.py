import os
import time
import json
import re
import argparse
from collections import deque
from urllib.parse import quote

import requests

API = "https://en.wikipedia.org/w/api.php"
WIKI_BASE = "https://en.wikipedia.org/wiki/"

HEADERS = {
    "User-Agent": "WikiCategoryCrawler/1.0 (thesis; contact: your-email@example.com)"
}


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def api_get(params, sleep_s=0.1):
    for attempt in range(3):
        r = requests.get(API, params=params, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            time.sleep(sleep_s)
            return r.json()
        time.sleep(1 + attempt)
    r.raise_for_status()


def list_category_members(cmtitle: str, cmtype: str, limit=500):
    members = []
    cmcontinue = None

    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": cmtitle,
            "cmtype": cmtype,  # "page" or "subcat"
            "cmlimit": str(limit),
            "format": "json",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        data = api_get(params)
        members.extend(data.get("query", {}).get("categorymembers", []))

        cont = data.get("continue", {})
        cmcontinue = cont.get("cmcontinue")
        if not cmcontinue:
            break

    return members


def crawl_categories(seed_category: str, max_depth=2):
    seed = (
        seed_category
        if seed_category.startswith("Category:")
        else f"Category:{seed_category}"
    )
    visited = set()
    q = deque([(seed, 0)])

    while q:
        cat, depth = q.popleft()
        if cat in visited:
            continue
        visited.add(cat)

        if depth >= max_depth:
            continue

        subcats = list_category_members(cmtitle=cat, cmtype="subcat")
        for sc in subcats:
            sc_title = sc["title"]
            if sc_title not in visited:
                q.append((sc_title, depth + 1))

    return visited


def collect_pages_from_categories(categories):
    pages_by_category = {}
    all_pages = {}

    for i, cat in enumerate(sorted(categories), 1):
        pages = list_category_members(cmtitle=cat, cmtype="page")
        pages_by_category[cat] = pages
        for p in pages:
            title = normalize_title(p["title"])
            all_pages[title] = p["pageid"]

        if i % 25 == 0:
            print(f"[progress] processed {i}/{len(categories)} categories")

    return pages_by_category, all_pages


def title_to_url(title: str) -> str:
    return WIKI_BASE + quote(title.replace(" ", "_"), safe="()'_:%")


def load_existing_categories(path="categories.json"):
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return set(data)


def load_existing_pages_index(path="pages_index.jsonl"):
    """
    Returns dict: title -> {"pageid":..., "url":...}
    """
    if not os.path.exists(path):
        return {}

    idx = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            title = normalize_title(rec["title"])
            idx[title] = {"pageid": rec["pageid"], "url": rec["url"]}
    return idx


def load_existing_pages_by_category(path="pages_by_category.jsonl"):
    """
    Returns set of tuples (category, title) to dedupe membership edges.
    """
    if not os.path.exists(path):
        return set()

    edges = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cat = rec["category"]
            title = normalize_title(rec["title"])
            edges.add((cat, title))
    return edges


def write_pages_index_jsonl(index_dict, path="pages_index.jsonl"):
    # rewrite merged index (clean + deduped)
    with open(path, "w", encoding="utf-8") as f:
        for title in sorted(index_dict.keys()):
            rec = {
                "title": title,
                "pageid": index_dict[title]["pageid"],
                "url": index_dict[title]["url"],
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_pages_by_category_jsonl(
    edge_set, pageid_lookup, path="pages_by_category.jsonl"
):
    """
    edge_set: set[(category, title)]
    pageid_lookup: dict title->pageid (for filling pageid)
    """
    with open(path, "w", encoding="utf-8") as f:
        for cat, title in sorted(edge_set):
            rec = {
                "category": cat,
                "title": title,
                "pageid": pageid_lookup.get(title),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed",
        default="Category:Rivers_by_country",
        help="Wikipedia category to start from (e.g. 'Category:Airports_by_country')",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=2,
        help="How many subcategory levels to crawl (default: 2)",
    )
    args = parser.parse_args()

    seed = args.seed if args.seed.startswith("Category:") else f"Category:{args.seed}"
    max_depth = args.depth

    # 0) Load existing data (if any)
    existing_cats = load_existing_categories("categories.json")
    existing_pages = load_existing_pages_index("pages_index.jsonl")
    existing_edges = load_existing_pages_by_category("pages_by_category.jsonl")

    print(
        f"[existing] categories: {len(existing_cats)} | pages: {len(existing_pages)} | edges: {len(existing_edges)}"
    )

    print(f"1) Crawling categories from seed: {seed} (max_depth={max_depth})")
    new_cats = crawl_categories(seed_category=seed, max_depth=max_depth)
    print(f"   Found {len(new_cats)} categories (including seed).")

    print("2) Collecting pages from each category...")
    pages_by_cat, new_pages = collect_pages_from_categories(new_cats)
    print(f"   Found {len(new_pages)} unique pages in THIS run.")

    merged_cats = existing_cats.union(new_cats)

    # Keep existing if present; otherwise add new
    merged_pages = dict(existing_pages)
    for title, pageid in new_pages.items():
        if title not in merged_pages:
            merged_pages[title] = {"pageid": pageid, "url": title_to_url(title)}
        else:
            pass

    merged_edges = set(existing_edges)
    for cat, pages in pages_by_cat.items():
        for p in pages:
            title = normalize_title(p["title"])
            merged_edges.add((cat, title))

    with open("categories.json", "w", encoding="utf-8") as f:
        json.dump(sorted(list(merged_cats)), f, ensure_ascii=False, indent=2)

    write_pages_index_jsonl(merged_pages, "pages_index.jsonl")

    # pageid lookup for edges
    pageid_lookup = {t: merged_pages[t]["pageid"] for t in merged_pages}
    write_pages_by_category_jsonl(
        merged_edges, pageid_lookup, "pages_by_category.jsonl"
    )

    print("\nSaved (MERGED, not overwritten):")
    print(f" - categories.json: {len(merged_cats)} categories")
    print(f" - pages_index.jsonl: {len(merged_pages)} pages")
    print(f" - pages_by_category.jsonl: {len(merged_edges)} edges")


if __name__ == "__main__":
    main()


# Category:Universities_and_colleges_by_country
# Category:Countries
# Category:Association_football_clubs_by_country
# Category:Clothing_brands
# Category:Airports_by_country
# Category:American_films
# Category:Rivers_by_country
