### This repository is for the Thesis "A Systematic Evaluation of LLMs on Extracting Structured Data from HTML Tables" and all the scripts inside that thesis are here, ready to run and test.

# Pipeline

This folder contains all scripts for the Wikipedia table extraction and evaluation pipeline.
Run them in the order listed below.

---

## Requirements

```
pip install requests lxml beautifulsoup4 html5lib tabulate matplotlib numpy
```

---

## Step 1 — Crawl Wikipedia categories

```
python main.py --seed "Category:Rivers_by_country" --depth 2
```

Performs a BFS traversal of the Wikipedia category graph starting from the seed category.
Depth is set to 2 by default.

**Outputs:** `categories.json`, `pages_index.jsonl`, `pages_by_category.jsonl`

- `--seed` — Wikipedia category to start from (default: `Category:Rivers_by_country`). The `Category:` prefix is optional.
- `--depth` — how many subcategory levels to crawl (default: 2)

Results are merged into existing output files if they already exist.

The following categories were used in this study and are listed as comments at the bottom of `main.py`:

```
Category:Universities_and_colleges_by_country
Category:Countries
Category:Association_football_clubs_by_country
Category:Clothing_brands
Category:Airports_by_country
Category:American_films
Category:Rivers_by_country
```

---

## Step 2 — Download HTML pages

```
python fast_crawl.py
```

Reads `pages_index.jsonl` and downloads the rendered HTML for each page using the Wikipedia API.
Skips files that already exist and are larger than 500 bytes.

**Outputs:** `html_pages/` folder, `download_index.jsonl`

To adjust concurrency or timeouts, edit the constants at the top of the file:

- `WORKERS` — number of parallel threads (default 12)
- `TIMEOUT` — request timeout in seconds (default 30)
- `MAX_RETRIES` — retry attempts per page (default 4)

---

## Step 3 — Score table difficulty

```
python score_html_difficulty.py
```

Goes through every HTML file in `html_pages/` and scores each table on 12 structural signals.
Maps scores to difficulty bands: easy (0–2), medium (3–5), hard (6+).

**Outputs:** `html_table_difficulty_scores.json`

Optional arguments:

```
python score_html_difficulty.py --html-dir ./html_pages --output ./html_table_difficulty_scores.json --limit 1000
```

- `--html-dir` — path to the folder with HTML files (default: `html_pages/`)
- `--output` — output JSON path (default: `html_table_difficulty_scores.json`)
- `--limit` — process only the first N files (useful for testing)

---

## Step 4 — Convert to JSONL

```
python json_to_jsonl.py
```

Converts `html_table_difficulty_scores.json` to `html_table_difficulty_scores.jsonl`,
one record per line. No arguments needed.

**Outputs:** `html_table_difficulty_scores.jsonl`

---

## Step 5 — Sample by difficulty

```
python sample_by_difficulty.py
```

Reads `html_table_difficulty_scores.jsonl` and picks 25 hard, 15 medium, and 10 easy pages.
Only pages with 1–5 tables are eligible.

**Outputs:** `sampled_25hard_15medium_10easy.json`

To change the sample sizes, edit `N_HARD`, `N_MEDIUM`, `N_EASY` at the top of the file.

---

## Step 6 — Build ground truth stubs

```
python prepare_ground_truth_stubs.py
```

Reads `selected_pages_sampled.json` and creates one folder per page under `ground_truth/sampled_pages/`.
Each folder contains the original `.html` file, a `table_001.json`, `table_002.json`, etc. (one per table) with an empty
`rows` field, plus a `page_manifest.json` with metadata for all tables in that page.

**Outputs:** `ground_truth/sampled_pages/<page_id>/table_*.json` and `page_manifest.json`

---

## Step 7 — Auto-fill rows from preview

```
python auto_fill_from_preview.py
```

Goes through every `table_*.json` and fills in the `flattened_schema` and `ground_truth.rows`
from the text preview extracted in the previous step. Column names are normalized to snake_case
and numeric strings are converted to integers.

**Outputs:** Updates `table_*.json` files in-place.

---

## Step 8 — Build gold files

```
python make_page_gold_files.py
```

Aggregates all `table_*.json` files for each page into a single `page.gold.json`.
Capitalizes display names and removes hidden date artifacts from cell values.

**Outputs:** `ground_truth/sampled_pages/<page_id>/page.gold.json`

---

## Step 9 — Build prompt files

```
python make_page_prompt_files.py
```

Converts each `page.gold.json` into a `page.prompt.json` that can be sent to a model.
The HTML snippet is included as input; schema and rows are left empty for the model to fill.

**Outputs:** `ground_truth/sampled_pages/<page_id>/page.prompt.json`

---

## Step 10 — Run models on prompt files

Use `prompt.md` as the system prompt when calling any LLM. The input to the model is the
content of each `page.prompt.json` file found under `ground_truth/sampled_pages/<page_id>/`.

Feed the model the full JSON as the user message. The model should return the same JSON
structure with `output.normalization_operations`, `output.flattened_schema`, and
`output.ground_truth.rows` filled in.

Save each model's response as `page.pred.json` inside a folder named `<ModelName>_pred/`
within the page folder. For example:

```
ground_truth/sampled_pages/<page_id>/
├── Claude_pred/
│   └── page.pred.json
├── GPT_pred/
│   └── page.pred.json
└── ...
```

---

## Step 11 — Score model predictions

Once all `page.pred.json` files are in place:

```
python score_page_folder.py ground_truth/sampled_pages/
```

Pass a single page folder to score just that page, or the `sampled_pages/` root to score all pages at once.
Looks for any subfolder ending in `_pred` and compares `page.pred.json` against `page.gold.json`.

**Outputs:** `ground_truth/sampled_pages/<page_id>/page.score.json`

---

## Step 12 — Analyze results

```
python analyze_experiment_local.py
```

Reads all `page.score.json` and `page_manifest.json` files and produces a full report.

**Outputs:** `results/` folder containing `experiment_report.txt`, `experiment_summary.json`, and charts.

Optional arguments:

```
python analyze_experiment_local.py --root ./ground_truth/sampled_pages --out ./results --fail-row-f1 0.8 --fail-cell-acc 0.8 --fail-schema-f1 0.8
```

- `--root` — path to the `sampled_pages/` folder (default: `./sampled_pages`)
- `--out` — output folder for results (default: `./results`)
- `--fail-row-f1` — threshold below which row F1 counts as a failure (default: 0.80)
- `--fail-cell-acc` — threshold for cell accuracy failure (default: 0.80)
- `--fail-schema-f1` — threshold for schema F1 failure (default: 0.80)
