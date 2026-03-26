#!/usr/bin/env python3
# Run from the directory containing sampled_pages/
# deps: tabulate matplotlib numpy

import argparse
import json
import math
import os
import sys
import collections
from pathlib import Path

try:
    from tabulate import tabulate
except ImportError:
    print("[ERROR] Run: pip install tabulate")
    sys.exit(1)

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError:
    print("[ERROR] Run: pip install matplotlib numpy")
    sys.exit(1)

# config

FAIL_ROW_F1 = 0.80
FAIL_CELL_ACC = 0.80
FAIL_SCHEMA_F1 = 0.80
DIFF_ORDER = ["easy", "medium", "hard", "unknown"]

MODEL_DISPLAY = {
    "Claude_Sonnet_4.6": "Claude",
    "Claude_Sonnet_4.6_Extended": "Claude Extended",
    "GPT_5.3_Instant": "GPT Instant",
    "GPT_5.4_Thinking": "GPT Thinking",
    "Gemini_3_fast": "Gemini",
}
MODEL_ORDER = ["GPT Instant", "GPT Thinking", "Gemini", "Claude", "Claude Extended"]
COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3"]


# data loading
def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [WARN] {path}: {e}", file=sys.stderr)
        return None


def load_all_data(root: Path):
    records = []
    all_models_seen = set()

    page_dirs = sorted(
        [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]
    )
    if not page_dirs:
        print(f"[ERROR] No subdirectories in: {root}")
        sys.exit(1)

    print(f"\nDiscovered {len(page_dirs)} page folders")

    for page_dir in page_dirs:
        page_id = page_dir.name

        manifest = load_json(page_dir / "page_manifest.json") or {}
        table_diff = {}
        for t in manifest.get("tables", []):
            tid = t.get("table_id", "")
            band = (t.get("difficulty_band") or "unknown").lower()
            table_diff[tid] = band

        if table_diff:
            counter = collections.Counter(table_diff.values())
            page_difficulty = counter.most_common(1)[0][0]
        else:
            page_difficulty = "unknown"

        score_data = load_json(page_dir / "page.score.json") or {}
        for model_entry in score_data.get("models", []):
            raw_model = model_entry.get("model", "")
            display = MODEL_DISPLAY.get(raw_model, raw_model)
            all_models_seen.add(display)

            for tbl in model_entry.get("tables", []):
                tid = tbl.get("table_id", "")
                t_diff = table_diff.get(tid, "unknown")

                row_f1 = tbl.get("row", {}).get("f1")
                cell_acc = tbl.get("cell_accuracy_exact")
                hall = len(tbl.get("hallucinated_rows", []))
                drop = len(tbl.get("dropped_rows", []))
                mism = len(tbl.get("mismatches", []))

                schema = tbl.get("schema", {})
                schema_f1 = schema.get("f1")
                schema_prec = schema.get("precision")
                schema_rec = schema.get("recall")
                col_mismatches = schema.get("mismatches", [])
                col_missing = schema.get("missing_from_pred", [])
                col_extra = schema.get("extra_in_pred", [])

                records.append(
                    {
                        "page_id": page_id,
                        "page_name": page_id.split("__")[0],
                        "page_diff": page_difficulty,
                        "table_id": tid,
                        "table_diff": t_diff,
                        "model": display,
                        "row_f1": row_f1,
                        "cell_acc": cell_acc,
                        "hallucinated": hall,
                        "dropped": drop,
                        "mismatches": mism,
                        "schema_f1": schema_f1,
                        "schema_prec": schema_prec,
                        "schema_rec": schema_rec,
                        "n_renamed": len(col_mismatches),
                        "n_missing": len(col_missing),
                        "n_extra": len(col_extra),
                        "has_schema_err": (schema_f1 is not None and schema_f1 < 1.0),
                        "col_mismatches": col_mismatches,
                        "col_missing": col_missing,
                        "col_extra": col_extra,
                    }
                )

    ordered = [m for m in MODEL_ORDER if m in all_models_seen]
    ordered += sorted(all_models_seen - set(MODEL_ORDER))
    print(f"Models : {ordered}")
    print(f"Records: {len(records)} (table x model)")
    return records, ordered


def stats(values):
    v = [x for x in values if x is not None]
    if not v:
        return dict(
            n=0, mean=float("nan"), std=float("nan"), min=float("nan"), max=float("nan")
        )
    n = len(v)
    m = sum(v) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / n)
    return dict(n=n, mean=m, std=sd, min=min(v), max=max(v))


def pct(v, d=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "N/A"
    return f"{v * 100:.{d}f}%"


def section(title, width=74):
    bar = "=" * width
    return f"\n{bar}\n  {title}\n{bar}"


def sec_overall(records, models):
    rows = []
    for m in models:
        mr = [r for r in records if r["model"] == m]
        rf1 = stats([r["row_f1"] for r in mr])
        cacc = stats([r["cell_acc"] for r in mr])
        sf1 = stats([r["schema_f1"] for r in mr])
        nf_row = sum(
            1 for r in mr if r["row_f1"] is not None and r["row_f1"] < FAIL_ROW_F1
        )
        nf_cell = sum(
            1 for r in mr if r["cell_acc"] is not None and r["cell_acc"] < FAIL_CELL_ACC
        )
        nf_sch = sum(1 for r in mr if r["has_schema_err"])
        n = rf1["n"]
        rows.append(
            [
                m,
                len({r["page_id"] for r in mr}),
                n,
                pct(rf1["mean"]),
                pct(cacc["mean"]),
                pct(sf1["mean"]),
                f"{nf_row}/{n}",
                f"{nf_cell}/{n}",
                f"{nf_sch}/{n}",
                sum(r["hallucinated"] for r in mr),
                sum(r["dropped"] for r in mr),
                sum(r["mismatches"] for r in mr),
            ]
        )
    hdrs = [
        "Model",
        "Pages",
        "Tables",
        "Row F1",
        "Cell Acc",
        "Schema F1",
        f"RowF1<{int(FAIL_ROW_F1*100)}%",
        f"Cell<{int(FAIL_CELL_ACC*100)}%",
        "Schema<100%",
        "Hall.Rows",
        "Drop.Rows",
        "CellMism",
    ]
    return tabulate(rows, headers=hdrs, tablefmt="grid")


def sec_schema_overview(records, models):
    rows = []
    for m in models:
        mr = [r for r in records if r["model"] == m]
        f1_st = stats([r["schema_f1"] for r in mr])
        prec_st = stats([r["schema_prec"] for r in mr])
        rec_st = stats([r["schema_rec"] for r in mr])
        n_err = sum(1 for r in mr if r["has_schema_err"])
        n = f1_st["n"]
        rows.append(
            [
                m,
                pct(f1_st["mean"]),
                pct(f1_st["std"]),
                pct(prec_st["mean"]),
                pct(rec_st["mean"]),
                f"{n_err}/{n}",
                f"{sum(1 for r in mr if r['n_renamed']>0)}/{n} ({sum(r['n_renamed'] for r in mr)} cols)",
                f"{sum(1 for r in mr if r['n_missing']>0)}/{n} ({sum(r['n_missing'] for r in mr)} cols)",
                f"{sum(1 for r in mr if r['n_extra']>0)}/{n} ({sum(r['n_extra'] for r in mr)} cols)",
            ]
        )
    hdrs = [
        "Model",
        "Avg Schema F1",
        "+/-Std",
        "Avg Precision",
        "Avg Recall",
        "Tables w/ Error",
        "Renamed Cols",
        "Missing Cols",
        "Extra (Halluc) Cols",
    ]
    return tabulate(rows, headers=hdrs, tablefmt="grid")


def sec_schema_by_difficulty(records, models):
    rows = []
    for m in models:
        row = [m]
        for d in DIFF_ORDER:
            vals = [
                r["schema_f1"]
                for r in records
                if r["model"] == m
                and r["table_diff"] == d
                and r["schema_f1"] is not None
            ]
            st = stats(vals)
            row.append(f"{pct(st['mean'])} (n={st['n']})" if st["n"] else "---")
        rows.append(row)
    hdrs = ["Model"] + [d.capitalize() for d in DIFF_ORDER]
    return tabulate(rows, headers=hdrs, tablefmt="grid")


def sec_schema_error_detail(records, models):
    lines = []
    for m in models:
        mr = [r for r in records if r["model"] == m and r["has_schema_err"]]
        all_ = [r for r in records if r["model"] == m]
        lines.append(f"\n  +-- {m}  ({len(mr)}/{len(all_)} tables with schema errors)")
        lines.append(f"  |")

        by_page = collections.defaultdict(list)
        for r in mr:
            by_page[r["page_name"]].append(r)

        for page_name in sorted(by_page):
            tbls = by_page[page_name]
            lines.append(f"  |  [{tbls[0]['table_diff'].upper()}]  {page_name}")
            for r in tbls:
                lines.append(
                    f"  |    {r['table_id']}  F1={pct(r['schema_f1'])}  "
                    f"Precision={pct(r['schema_prec'])}  Recall={pct(r['schema_rec'])}"
                )
                if r["col_missing"]:
                    lines.append(
                        f"  |      MISSING cols ({len(r['col_missing'])}): "
                        + ", ".join(f'"{c}"' for c in r["col_missing"][:5])
                        + (" ..." if len(r["col_missing"]) > 5 else "")
                    )
                if r["col_extra"]:
                    lines.append(
                        f"  |      EXTRA cols  ({len(r['col_extra'])}): "
                        + ", ".join(f'"{c}"' for c in r["col_extra"][:5])
                        + (" ..." if len(r["col_extra"]) > 5 else "")
                    )
                if r["col_mismatches"]:
                    lines.append(f"  |      RENAMED cols ({len(r['col_mismatches'])}):")
                    for cm in r["col_mismatches"][:4]:
                        lines.append(
                            f"  |        gold: \"{cm['gold']}\"  ->  pred: \"{cm['pred']}\""
                        )
                    if len(r["col_mismatches"]) > 4:
                        lines.append(
                            f"  |        ... and {len(r['col_mismatches'])-4} more"
                        )
        lines.append(f"  +{'--'*30}")
    return "\n".join(lines)


def sec_schema_cross_model(records, models):
    pt_errors = collections.defaultdict(dict)
    for r in records:
        if r["has_schema_err"]:
            key = (r["page_name"], r["table_id"], r["table_diff"])
            types = []
            if r["n_missing"]:
                types.append(f"missing({r['n_missing']})")
            if r["n_extra"]:
                types.append(f"extra({r['n_extra']})")
            if r["n_renamed"]:
                types.append(f"renamed({r['n_renamed']})")
            pt_errors[key][r["model"]] = (r["schema_f1"], types)

    rows = []
    for (page, tid, diff), model_errs in sorted(
        pt_errors.items(), key=lambda x: -len(x[1])
    ):
        if len(model_errs) < 2:
            continue
        avg_f1 = sum(v[0] for v in model_errs.values() if v[0] is not None) / max(
            len(model_errs), 1
        )
        all_types = collections.Counter()
        for _, types in model_errs.values():
            for t in types:
                all_types[t.split("(")[0]] += 1
        dominant = ", ".join(f"{k}x{v}" for k, v in all_types.most_common(3))
        rows.append(
            [
                page[:40],
                tid,
                diff.capitalize(),
                f"{len(model_errs)}/{len(models)}",
                pct(avg_f1),
                dominant,
                ", ".join(model_errs.keys()),
            ]
        )

    hdrs = [
        "Page",
        "Table",
        "Diff",
        "Models Affected",
        "Avg Schema F1",
        "Error Types",
        "Affected Models",
    ]
    return tabulate(rows, headers=hdrs, tablefmt="grid")


def sec_combined_failure(records, models):
    lines = [
        "\n  Pages failing on Row F1, Cell Accuracy AND Schema F1 simultaneously:\n"
    ]
    for m in models:
        mr = [r for r in records if r["model"] == m]
        pids = {r["page_id"] for r in mr}
        triple_fail = []
        for pid in sorted(pids):
            pr = [r for r in mr if r["page_id"] == pid]
            avg_rf1 = stats([r["row_f1"] for r in pr])["mean"]
            avg_cacc = stats([r["cell_acc"] for r in pr])["mean"]
            avg_sf1 = stats([r["schema_f1"] for r in pr])["mean"]
            if (
                (not math.isnan(avg_rf1) and avg_rf1 < FAIL_ROW_F1)
                and (not math.isnan(avg_cacc) and avg_cacc < FAIL_CELL_ACC)
                and (not math.isnan(avg_sf1) and avg_sf1 < FAIL_SCHEMA_F1)
            ):
                triple_fail.append(
                    (pid.split("__")[0], pr[0]["page_diff"], avg_rf1, avg_cacc, avg_sf1)
                )
        lines.append(f"  {m}: {len(triple_fail)} pages")
        for name, diff, rf1, cacc, sf1 in triple_fail:
            lines.append(f"    * [{diff.upper()}] {name}")
            lines.append(
                f"        RowF1={pct(rf1)}  CellAcc={pct(cacc)}  SchemaF1={pct(sf1)}"
            )
    return "\n".join(lines)


def sec_by_difficulty_all(records, models):
    result = {}
    for metric, key in [
        ("Row F1", "row_f1"),
        ("Cell Accuracy", "cell_acc"),
        ("Schema F1", "schema_f1"),
    ]:
        rows = []
        for m in models:
            row = [m]
            for d in DIFF_ORDER:
                vals = [
                    r[key]
                    for r in records
                    if r["model"] == m and r["table_diff"] == d and r[key] is not None
                ]
                st = stats(vals)
                row.append(f"{pct(st['mean'])} (n={st['n']})" if st["n"] else "---")
            rows.append(row)
        hdrs = ["Model"] + [d.capitalize() for d in DIFF_ORDER]
        result[metric] = tabulate(rows, headers=hdrs, tablefmt="grid")
    return result


def sec_ranking(records, models):
    ranking = []
    for m in models:
        mr = [r for r in records if r["model"] == m]
        rf1 = stats([r["row_f1"] for r in mr])["mean"]
        cacc = stats([r["cell_acc"] for r in mr])["mean"]
        sf1 = stats([r["schema_f1"] for r in mr])["mean"]
        vals = [v for v in [rf1, cacc, sf1] if not math.isnan(v)]
        comp = sum(vals) / len(vals) if vals else float("nan")
        ranking.append(
            (
                m,
                rf1,
                cacc,
                sf1,
                comp,
                sum(r["hallucinated"] for r in mr),
                sum(r["dropped"] for r in mr),
                sum(r["n_missing"] for r in mr),
                sum(r["n_extra"] for r in mr),
                sum(r["n_renamed"] for r in mr),
            )
        )
    ranking.sort(key=lambda x: x[4] if not math.isnan(x[4]) else -1, reverse=True)
    rows = [
        [
            i + 1,
            m,
            pct(rf1),
            pct(cacc),
            pct(sf1),
            pct(comp),
            hall,
            drop,
            miss,
            extra,
            ren,
        ]
        for i, (m, rf1, cacc, sf1, comp, hall, drop, miss, extra, ren) in enumerate(
            ranking
        )
    ]
    hdrs = [
        "Rank",
        "Model",
        "Row F1",
        "Cell Acc",
        "Schema F1",
        "Composite",
        "Hall.Rows",
        "Drop.Rows",
        "Missing Cols",
        "Extra Cols",
        "Renamed Cols",
    ]
    return tabulate(rows, headers=hdrs, tablefmt="grid")


def sec_per_page(records, models):
    page_ids = sorted({r["page_id"] for r in records})
    diff_map = {r["page_id"]: r["page_diff"] for r in records}
    rows = []
    for pid in page_ids:
        name = pid.split("__")[0][:32]
        row = [name, diff_map.get(pid, "?").capitalize()]
        for m in models:
            pr = [r for r in records if r["page_id"] == pid and r["model"] == m]
            if not pr:
                row += ["---", "---", "---"]
            else:
                row += [
                    pct(stats([r["row_f1"] for r in pr])["mean"]),
                    pct(stats([r["cell_acc"] for r in pr])["mean"]),
                    pct(stats([r["schema_f1"] for r in pr])["mean"]),
                ]
        rows.append(row)
    hdrs = ["Page", "Diff"]
    for m in models:
        s = m[:9]
        hdrs += [f"{s}\nF1", f"{s}\nCell", f"{s}\nSchema"]
    return tabulate(rows, headers=hdrs, tablefmt="grid")


def make_charts(records, models, out_dir):
    saved = []
    diffs = [d for d in DIFF_ORDER if any(r["table_diff"] == d for r in records)]

    # 1. Three-metric overview
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for ax_i, (title, key, thresh) in enumerate(
        [
            ("Avg Row F1", "row_f1", FAIL_ROW_F1),
            ("Avg Cell Accuracy", "cell_acc", FAIL_CELL_ACC),
            ("Avg Schema F1", "schema_f1", FAIL_SCHEMA_F1),
        ]
    ):
        means, stds = [], []
        for m in models:
            v = [r[key] for r in records if r["model"] == m and r[key] is not None]
            st = stats(v)
            means.append(st["mean"] * 100 if not math.isnan(st["mean"]) else 0)
            stds.append(st["std"] * 100 if not math.isnan(st["std"]) else 0)
        ax = axes[ax_i]
        x = np.arange(len(models))
        bars = ax.bar(
            x,
            means,
            yerr=stds,
            capsize=5,
            color=COLORS[: len(models)],
            alpha=0.88,
            edgecolor="black",
            linewidth=0.6,
        )
        ax.set_xticks(x)
        ax.set_xticklabels(models, rotation=25, ha="right", fontsize=8)
        ax.set_ylabel(f"{title} (%)", fontsize=9)
        ax.set_title(title, fontsize=11, fontweight="bold")
        ax.set_ylim(0, 115)
        ax.axhline(
            thresh * 100,
            color="red",
            linestyle="--",
            linewidth=1.2,
            label=f"Fail ({thresh*100:.0f}%)",
        )
        ax.legend(fontsize=7)
        ax.bar_label(bars, labels=[f"{m:.1f}%" for m in means], padding=2, fontsize=7)
    plt.suptitle(
        "LLM Table Extraction: Row F1 / Cell Accuracy / Schema F1",
        fontsize=12,
        fontweight="bold",
    )
    plt.tight_layout()
    p = out_dir / "chart1_three_metrics_overview.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    saved.append(p)

    # 2. All 3 metrics by difficulty
    for key, label in [
        ("row_f1", "Row F1"),
        ("cell_acc", "Cell Accuracy"),
        ("schema_f1", "Schema F1"),
    ]:
        fig, ax = plt.subplots(figsize=(11, 5))
        n_g, n_b = len(diffs), len(models)
        w = 0.7 / n_b
        x = np.arange(n_g)
        for i, m in enumerate(models):
            heights = []
            for d in diffs:
                v = [
                    r[key]
                    for r in records
                    if r["model"] == m and r["table_diff"] == d and r[key] is not None
                ]
                st = stats(v)
                heights.append(st["mean"] * 100 if not math.isnan(st["mean"]) else 0)
            ax.bar(
                x + (i - n_b / 2 + 0.5) * w,
                heights,
                w * 0.9,
                label=m,
                color=COLORS[i % len(COLORS)],
                alpha=0.88,
                edgecolor="black",
                linewidth=0.5,
            )
        ax.set_xticks(x)
        ax.set_xticklabels([d.capitalize() for d in diffs], fontsize=11)
        ax.set_ylabel(f"{label} (%)")
        ax.set_ylim(0, 115)
        ax.set_title(f"{label} by Difficulty Band", fontsize=12, fontweight="bold")
        ax.legend(fontsize=8, loc="lower left")
        ax.axhline(80, color="red", linestyle="--", linewidth=1)
        plt.tight_layout()
        p = out_dir / f"chart2_{key}_by_difficulty.png"
        fig.savefig(p, dpi=150)
        plt.close(fig)
        saved.append(p)

    # 3. Three heatmaps
    for key, label in [
        ("row_f1", "Row F1"),
        ("cell_acc", "Cell Accuracy"),
        ("schema_f1", "Schema F1"),
    ]:
        mat = np.full((len(models), len(diffs)), np.nan)
        for i, m in enumerate(models):
            for j, d in enumerate(diffs):
                v = [
                    r[key]
                    for r in records
                    if r["model"] == m and r["table_diff"] == d and r[key] is not None
                ]
                if v:
                    mat[i, j] = np.mean(v)
        fig, ax = plt.subplots(
            figsize=(max(7, len(diffs) * 1.8), max(4, len(models) * 0.9))
        )
        im = ax.imshow(mat, vmin=0, vmax=1, cmap="RdYlGn", aspect="auto")
        plt.colorbar(im, ax=ax, shrink=0.85)
        ax.set_xticks(range(len(diffs)))
        ax.set_xticklabels([d.capitalize() for d in diffs], fontsize=10)
        ax.set_yticks(range(len(models)))
        ax.set_yticklabels(models, fontsize=9)
        ax.set_title(
            f"{label} Heatmap: Model x Difficulty", fontsize=11, fontweight="bold"
        )
        for ii in range(len(models)):
            for jj in range(len(diffs)):
                v = mat[ii, jj]
                if not np.isnan(v):
                    ax.text(
                        jj,
                        ii,
                        f"{v*100:.0f}%",
                        ha="center",
                        va="center",
                        fontsize=9,
                        fontweight="bold",
                        color="black" if 0.3 < v < 0.75 else "white",
                    )
        plt.tight_layout()
        p = out_dir / f"chart3_heatmap_{key}.png"
        fig.savefig(p, dpi=150)
        plt.close(fig)
        saved.append(p)

    # 4. Schema error type breakdown
    fig, ax = plt.subplots(figsize=(11, 5))
    x = np.arange(len(models))
    renamed_v = [
        sum(r["n_renamed"] for r in records if r["model"] == m) for m in models
    ]
    missing_v = [
        sum(r["n_missing"] for r in records if r["model"] == m) for m in models
    ]
    extra_v = [sum(r["n_extra"] for r in records if r["model"] == m) for m in models]
    ax.bar(x, renamed_v, label="Renamed cols", color="#1f77b4", alpha=0.88)
    ax.bar(
        x,
        missing_v,
        bottom=renamed_v,
        label="Missing cols (model forgot)",
        color="#ff7f0e",
        alpha=0.88,
    )
    ax.bar(
        x,
        extra_v,
        bottom=[r + m for r, m in zip(renamed_v, missing_v)],
        label="Extra cols (hallucinated)",
        color="#d62728",
        alpha=0.88,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(models, rotation=20, ha="right", fontsize=9)
    ax.set_ylabel("Total columns affected")
    ax.legend(fontsize=9)
    ax.set_title(
        "Schema Error Types per Model (column-level, cumulative)",
        fontsize=12,
        fontweight="bold",
    )
    plt.tight_layout()
    p = out_dir / "chart4_schema_error_types.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    saved.append(p)

    # 5. Schema error rate by difficulty
    fig, ax = plt.subplots(figsize=(10, 5))
    n_g, n_b = len(diffs), len(models)
    w = 0.7 / n_b
    x = np.arange(n_g)
    for i, m in enumerate(models):
        rates = []
        for d in diffs:
            dr = [r for r in records if r["model"] == m and r["table_diff"] == d]
            rates.append(
                sum(1 for r in dr if r["has_schema_err"]) / len(dr) * 100 if dr else 0
            )
        ax.bar(
            x + (i - n_b / 2 + 0.5) * w,
            rates,
            w * 0.9,
            label=m,
            color=COLORS[i % len(COLORS)],
            alpha=0.88,
            edgecolor="black",
            linewidth=0.5,
        )
    ax.set_xticks(x)
    ax.set_xticklabels([d.capitalize() for d in diffs], fontsize=11)
    ax.set_ylabel("Schema Error Rate (%)")
    ax.set_ylim(0, 105)
    ax.set_title("Schema Error Rate by Difficulty Band", fontsize=12, fontweight="bold")
    ax.legend(fontsize=8)
    plt.tight_layout()
    p = out_dir / "chart5_schema_error_rate_by_difficulty.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    saved.append(p)

    # 6. Content error types
    fig, ax = plt.subplots(figsize=(11, 5))
    x = np.arange(len(models))
    hall_v = [
        sum(r["hallucinated"] for r in records if r["model"] == m) for m in models
    ]
    drop_v = [sum(r["dropped"] for r in records if r["model"] == m) for m in models]
    mism_v = [sum(r["mismatches"] for r in records if r["model"] == m) for m in models]
    ax.bar(x, hall_v, label="Hallucinated rows", color="#d62728", alpha=0.85)
    ax.bar(x, drop_v, bottom=hall_v, label="Dropped rows", color="#ff7f0e", alpha=0.85)
    ax.bar(
        x,
        mism_v,
        bottom=[h + d for h, d in zip(hall_v, drop_v)],
        label="Cell value mismatches",
        color="#1f77b4",
        alpha=0.85,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(models, rotation=20, ha="right", fontsize=9)
    ax.set_ylabel("Count")
    ax.legend(fontsize=9)
    ax.set_title(
        "Content Error Types per Model (cumulative)", fontsize=12, fontweight="bold"
    )
    plt.tight_layout()
    p = out_dir / "chart6_content_error_types.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    saved.append(p)

    # 7. Per-page Schema F1 line
    page_ids = sorted({r["page_id"] for r in records})
    fig, ax = plt.subplots(figsize=(max(14, len(page_ids) * 0.38), 5))
    for i, m in enumerate(models):
        xs, ys = [], []
        for j, pid in enumerate(page_ids):
            pr = [r for r in records if r["model"] == m and r["page_id"] == pid]
            if not pr:
                continue
            st = stats([r["schema_f1"] for r in pr])
            if not math.isnan(st["mean"]):
                xs.append(j)
                ys.append(st["mean"] * 100)
        ax.plot(
            xs,
            ys,
            "o-",
            color=COLORS[i % len(COLORS)],
            label=m,
            markersize=5,
            linewidth=1.2,
            alpha=0.8,
        )
    ax.set_xticks(range(len(page_ids)))
    ax.set_xticklabels(
        [p.split("__")[0][:18] for p in page_ids], rotation=75, ha="right", fontsize=6.5
    )
    ax.set_ylabel("Avg Schema F1 (%)")
    ax.set_ylim(-5, 115)
    ax.set_title("Schema F1 per Page per Model", fontsize=12, fontweight="bold")
    ax.axhline(
        100, color="green", linestyle="--", linewidth=0.8, label="Perfect (100%)"
    )
    ax.axhline(
        80, color="red", linestyle="--", linewidth=1, label="Fail threshold (80%)"
    )
    ax.legend(fontsize=8, loc="lower left")
    plt.tight_layout()
    p = out_dir / "chart7_per_page_schema_f1.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    saved.append(p)

    return saved


def export_json(records, models, out_dir):
    out = {}
    for m in models:
        mr = [r for r in records if r["model"] == m]
        by_diff = {}
        for d in DIFF_ORDER:
            dr = [r for r in mr if r["table_diff"] == d]
            rf1 = stats([r["row_f1"] for r in dr])
            cacc = stats([r["cell_acc"] for r in dr])
            sf1 = stats([r["schema_f1"] for r in dr])
            by_diff[d] = {
                "n_tables": rf1["n"],
                "avg_row_f1": (
                    round(rf1["mean"], 4) if not math.isnan(rf1["mean"]) else None
                ),
                "avg_cell_acc": (
                    round(cacc["mean"], 4) if not math.isnan(cacc["mean"]) else None
                ),
                "avg_schema_f1": (
                    round(sf1["mean"], 4) if not math.isnan(sf1["mean"]) else None
                ),
                "schema_errors": sum(1 for r in dr if r["has_schema_err"]),
                "total_renamed_cols": sum(r["n_renamed"] for r in dr),
                "total_missing_cols": sum(r["n_missing"] for r in dr),
                "total_extra_cols": sum(r["n_extra"] for r in dr),
            }
        rf1_all = stats([r["row_f1"] for r in mr])
        cacc_all = stats([r["cell_acc"] for r in mr])
        sf1_all = stats([r["schema_f1"] for r in mr])
        out[m] = {
            "overall": {
                "n_tables": rf1_all["n"],
                "avg_row_f1": (
                    round(rf1_all["mean"], 4)
                    if not math.isnan(rf1_all["mean"])
                    else None
                ),
                "avg_cell_acc": (
                    round(cacc_all["mean"], 4)
                    if not math.isnan(cacc_all["mean"])
                    else None
                ),
                "avg_schema_f1": (
                    round(sf1_all["mean"], 4)
                    if not math.isnan(sf1_all["mean"])
                    else None
                ),
                "total_hallucinated": sum(r["hallucinated"] for r in mr),
                "total_dropped": sum(r["dropped"] for r in mr),
                "total_cell_mismatches": sum(r["mismatches"] for r in mr),
                "tables_with_schema_error": sum(1 for r in mr if r["has_schema_err"]),
                "total_renamed_cols": sum(r["n_renamed"] for r in mr),
                "total_missing_cols": sum(r["n_missing"] for r in mr),
                "total_extra_cols": sum(r["n_extra"] for r in mr),
            },
            "by_difficulty": by_diff,
        }
    p = out_dir / "experiment_summary.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"  -> {p}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze LLM table-extraction experiment"
    )
    parser.add_argument("--root", default="./sampled_pages")
    parser.add_argument("--out", default="./results")
    parser.add_argument("--fail-row-f1", type=float, default=0.80)
    parser.add_argument("--fail-cell-acc", type=float, default=0.80)
    parser.add_argument("--fail-schema-f1", type=float, default=0.80)
    args = parser.parse_args()

    global FAIL_ROW_F1, FAIL_CELL_ACC, FAIL_SCHEMA_F1
    FAIL_ROW_F1 = args.fail_row_f1
    FAIL_CELL_ACC = args.fail_cell_acc
    FAIL_SCHEMA_F1 = args.fail_schema_f1

    root = Path(args.root).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not root.exists():
        print(f"[ERROR] Folder not found: {root}")
        sys.exit(1)

    print("=" * 74)
    print("  LLM TABLE EXTRACTION — FULL ANALYSIS (Row F1 + Cell Acc + Schema)")
    print("=" * 74)
    print(f"Root   : {root}")
    print(f"Output : {out_dir}")

    records, models = load_all_data(root)

    lines = [
        "LLM TABLE EXTRACTION EXPERIMENT — FULL ANALYSIS REPORT",
        f"Pages: {len({r['page_id'] for r in records})}  |  Records: {len(records)} (table x model)",
        f"Models: {', '.join(models)}",
        f"Thresholds: RowF1<{FAIL_ROW_F1*100:.0f}%  CellAcc<{FAIL_CELL_ACC*100:.0f}%  SchemaF1<{FAIL_SCHEMA_F1*100:.0f}%",
        "",
    ]

    print("\n[1] Overall summary (all 3 metrics)...")
    lines += [
        section("1. OVERALL PERFORMANCE: ROW F1 / CELL ACCURACY / SCHEMA F1"),
        sec_overall(records, models),
    ]

    print("[2] Model ranking...")
    lines += [
        section("2. MODEL RANKING (composite: Row F1 + Cell Acc + Schema F1)"),
        sec_ranking(records, models),
    ]

    print("[3] By difficulty...")
    diff_tbls = sec_by_difficulty_all(records, models)
    lines += [section("3. PERFORMANCE BY DIFFICULTY BAND")]
    for metric, tbl in diff_tbls.items():
        lines += [f"\n  -- {metric} --", tbl]

    print("[4] Schema deep-dive...")
    lines += [
        section("4. SCHEMA ANALYSIS: PRECISION / RECALL / F1 + ERROR TYPES"),
        sec_schema_overview(records, models),
    ]

    lines += [
        section("4a. SCHEMA F1 BY DIFFICULTY"),
        sec_schema_by_difficulty(records, models),
    ]

    lines += [
        section("4b. SCHEMA ERROR DETAIL PER MODEL (every affected table)"),
        sec_schema_error_detail(records, models),
    ]

    lines += [
        section("4c. CROSS-MODEL SCHEMA FAILURES (tables failing in 2+ models)"),
        sec_schema_cross_model(records, models),
    ]

    print("[5] Triple failure analysis...")
    lines += [
        section("5. TRIPLE FAILURE (Row F1 + Cell Acc + Schema all below threshold)"),
        sec_combined_failure(records, models),
    ]

    print("[6] Per-page full table...")
    lines += [
        section("6. PER-PAGE SCORES: Row F1 / Cell Acc / Schema F1 for every model"),
        sec_per_page(records, models),
    ]

    report = "\n".join(lines)

    print("\n" + "=" * 74)
    print(report)
    print("=" * 74)

    rp = out_dir / "experiment_report.txt"
    rp.write_text(report, encoding="utf-8")
    print(f"\n[OK] Report saved -> {rp}")

    print("\nGenerating 10 charts...")
    saved = make_charts(records, models, out_dir)
    for p in saved:
        print(f"  -> {p.name}")

    print("\nExporting JSON summary...")
    export_json(records, models, out_dir)

    print(f"\n[DONE] All outputs in: {out_dir}")
    print("Files:")
    for f in sorted(out_dir.iterdir()):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
