import json
import re
import argparse
from pathlib import Path
from difflib import SequenceMatcher


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def normalize_text(x):
    if x is None:
        return ""
    s = str(x).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_key(x):
    s = normalize_text(x).casefold()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def normalize_value(x):
    return normalize_text(x)


def str_sim(a, b):
    a = normalize_text(a).casefold()
    b = normalize_text(b).casefold()
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def safe_div(a, b):
    return a / b if b else 0


def prf1(matched, predicted, gold):
    if predicted == 0 and gold == 0:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    p = safe_div(matched, predicted)
    r = safe_div(matched, gold)
    f1 = safe_div(2 * p * r, p + r) if (p + r) else 0
    return {"precision": p, "recall": r, "f1": f1}


def compare_ops(pred, gold):
    p = {normalize_key(x) for x in pred}
    g = {normalize_key(x) for x in gold}
    matched = len(p & g)
    return prf1(matched, len(p), len(g))


def compare_schema(pred, gold):
    pred_norm = {normalize_key(x): x for x in pred}
    gold_norm = {normalize_key(x): x for x in gold}
    matched = len(set(pred_norm) & set(gold_norm))
    metrics = prf1(matched, len(pred_norm), len(gold_norm))

    # positional name mismatches (same index, different name)
    mismatches = []
    mismatch_gold_keys = set()
    mismatch_pred_keys = set()
    for i, (pv, gv) in enumerate(zip(pred, gold)):
        if normalize_key(pv) != normalize_key(gv):
            mismatches.append({"column": i, "gold": gv, "pred": pv})
            mismatch_gold_keys.add(normalize_key(gv))
            mismatch_pred_keys.add(normalize_key(pv))

    # cols missing from pred (skip ones already flagged as mismatches)
    missing_from_pred = [
        col for key, col in gold_norm.items()
        if key not in pred_norm and key not in mismatch_gold_keys
    ]

    # extra cols in pred not in gold (same exclusion)
    extra_in_pred = [
        col for key, col in pred_norm.items()
        if key not in gold_norm and key not in mismatch_pred_keys
    ]

    metrics["mismatches"] = mismatches
    metrics["missing_from_pred"] = missing_from_pred
    metrics["extra_in_pred"] = extra_in_pred
    return metrics


def map_headers(pred, gold):
    mapping = {}
    for ph in pred:
        for gh in gold:
            if normalize_key(ph) == normalize_key(gh):
                mapping[ph] = gh
    return mapping


def get_row_value(row, key):
    """Row dict lookup; falls back to case-insensitive match."""
    if key in row:
        return row[key]
    key_norm = normalize_key(key)
    for k, v in row.items():
        if normalize_key(k) == key_norm:
            return v
    return ""


ROW_MATCH_THRESHOLD = 0.5


def row_similarity(pred_row, gold_row, header_map):
    """Average cell similarity between a pred row and a gold row over shared headers."""
    if not header_map:
        return 0.0
    total = sum(
        str_sim(
            normalize_value(get_row_value(pred_row, ph)),
            normalize_value(get_row_value(gold_row, gh)),
        )
        for ph, gh in header_map.items()
    )
    return total / len(header_map)


def score_rows(pred_schema, pred_rows, gold_schema, gold_rows):
    header_map = map_headers(pred_schema, gold_schema)

    n_pred = len(pred_rows)
    n_gold = len(gold_rows)

    # Build all (sim, pred_idx, gold_idx) pairs and sort best-first
    pairs = sorted(
        (row_similarity(pred_rows[pi], gold_rows[gi], header_map), pi, gi)
        for pi in range(n_pred)
        for gi in range(n_gold)
    )
    pairs.reverse()

    # Greedy one-to-one assignment
    matched_pred = set()
    matched_gold = set()
    assignments = {}  # pred_idx -> gold_idx

    for sim, pi, gi in pairs:
        if pi in matched_pred or gi in matched_gold:
            continue
        if sim >= ROW_MATCH_THRESHOLD:
            assignments[pi] = gi
            matched_pred.add(pi)
            matched_gold.add(gi)

    # Score matched pairs cell-by-cell
    exact_cells = 0
    total_cells = 0
    mismatches = []

    for pi, gi in sorted(assignments.items()):
        pr = pred_rows[pi]
        gr = gold_rows[gi]
        for ph, gh in header_map.items():
            pv = normalize_value(get_row_value(pr, ph))
            gv = normalize_value(get_row_value(gr, gh))
            total_cells += 1
            if pv == gv:
                exact_cells += 1
            else:
                mismatches.append({
                    "pred_row": pi,
                    "gold_row": gi,
                    "column": gh,
                    "gold": gv,
                    "pred": pv,
                })

    hallucinated_rows = [
        {"pred_row": pi, "pred": pred_rows[pi]}
        for pi in range(n_pred)
        if pi not in matched_pred
    ]
    dropped_rows = [
        {"gold_row": gi, "gold": gold_rows[gi]}
        for gi in range(n_gold)
        if gi not in matched_gold
    ]

    row_metrics = prf1(len(assignments), n_pred, n_gold)

    cell_accuracy = 1.0 if total_cells == 0 and n_gold == 0 else safe_div(exact_cells, total_cells)

    return {
        "row": row_metrics,
        "cell_accuracy_exact": cell_accuracy,
        "mismatches": mismatches,
        "hallucinated_rows": hallucinated_rows,
        "dropped_rows": dropped_rows,
    }


def score_model(model_name, pred_path, gold, _unused):
    pred = load_json(pred_path)

    gold_tables = {t["table_id"]: t for t in gold["tables"]}
    pred_tables = {t["table_id"]: t for t in pred["tables"]}

    results = []

    for table_id, gold_table in gold_tables.items():

        pred_table = pred_tables.get(table_id)

        if not pred_table:
            continue

        pred_out = pred_table.get("output") or pred_table

        table_result = {"table_id": table_id}

        gold_norm_ops = gold_table.get("normalization_operations")
        pred_norm_ops = pred_out.get("normalization_operations", [])

        if gold_norm_ops is None:
            table_result["normalization_ops"] = None
        else:
            table_result["normalization_ops"] = compare_ops(pred_norm_ops, gold_norm_ops)

        pred_schema = pred_out.get("flattened_schema", [])
        gold_schema = gold_table.get("flattened_schema", [])

        table_result["schema"] = compare_schema(pred_schema, gold_schema)

        pred_rows = pred_out.get("ground_truth", {}).get("rows", [])
        gold_rows = gold_table.get("ground_truth", {}).get("rows", [])

        table_result.update(score_rows(pred_schema, pred_rows, gold_schema, gold_rows))
        results.append(table_result)

    summary = {
        "tables": len(results),
        "avg_row_f1": (
            sum(t["row"]["f1"] for t in results) / len(results) if results else 0
        ),
        "avg_cell_accuracy": (
            sum(t["cell_accuracy_exact"] for t in results) / len(results)
            if results
            else 0
        ),
        "total_hallucinated_rows": sum(len(t["hallucinated_rows"]) for t in results),
        "total_dropped_rows": sum(len(t["dropped_rows"]) for t in results),
    }

    return {"model": model_name, "summary": summary, "tables": results}


def score_page_folder(page_dir):
    page_dir = Path(page_dir)
    gold_path = page_dir / "page.gold.json"

    if not gold_path.exists():
        print(f"[SKIP] No page.gold.json in {page_dir}")
        return

    gold = load_json(gold_path)

    pred_folders = sorted(
        d for d in page_dir.iterdir()
        if d.is_dir() and d.name.endswith("_pred")
    )

    if not pred_folders:
        print(f"[SKIP] No *_pred folders in {page_dir}")
        return

    models = []
    for pred_folder in pred_folders:
        model_name = pred_folder.name.removesuffix("_pred")
        pred_path = pred_folder / "page.pred.json"

        if not pred_path.exists():
            print(f"[WARN] Missing page.pred.json in {pred_folder}")
            continue

        models.append(score_model(model_name, pred_path, gold, None))
        print(f"[{model_name}] Scored")

    score_path = page_dir / "page.score.json"
    write_json(score_path, {"page_id": gold["page_id"], "models": models})
    print(f"Score written to: {score_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        help="A single page folder or the sampled_pages root to score all folders",
    )
    args = parser.parse_args()

    target = Path(args.path)

    # single page folder vs. root of many pages
    has_pred_subfolder = any(
        d.is_dir() and d.name.endswith("_pred") for d in target.iterdir()
    )

    if has_pred_subfolder:
        score_page_folder(target)
    else:
        for page_dir in sorted(target.iterdir()):
            if page_dir.is_dir():
                score_page_folder(page_dir)


if __name__ == "__main__":
    main()