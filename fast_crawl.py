import os
import re
import json
import time
import hashlib
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import requests

INPUT_JSONL = "pages_index.jsonl"
OUT_DIR = "html_pages"
OUT_INDEX = "download_index.jsonl"

USE_RENDER = True

# configs
WORKERS = 12
TIMEOUT = 30
MAX_RETRIES = 4
SLEEP_BETWEEN_REQUESTS = 0.05  # per worker
FLUSH_EVERY = 50

HEADERS = {
    "User-Agent": "WikiHTMLDownloader/1.1 (thesis; contact: your-email@example.com)",
    "Accept-Language": "en",
}

write_lock = threading.Lock()


def safe_filename(title: str, pageid: int | None = None, max_len: int = 120) -> str:
    base = title.strip()
    base = re.sub(r"[\\/:*?\"<>|]+", "_", base)
    base = re.sub(r"\s+", " ", base).strip()
    h = hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
    pid = str(pageid) if pageid is not None else "na"
    base_short = base[:max_len].rstrip()
    return f"{base_short}__{pid}__{h}.html"


def add_action_render(url: str) -> str:
    parsed = urlparse(url)

    if parsed.path.endswith("/w/index.php"):
        q = parse_qs(parsed.query)
        q["action"] = ["render"]
        new_query = urlencode({k: v[0] for k, v in q.items()})
        return urlunparse(parsed._replace(query=new_query))

    if "/wiki/" in parsed.path:
        title = parsed.path.split("/wiki/", 1)[1]
        q = {"title": title, "action": "render"}
        new_query = urlencode(q)
        return urlunparse(parsed._replace(path="/w/index.php", query=new_query))

    q = parse_qs(parsed.query)
    q["action"] = ["render"]
    new_query = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse(parsed._replace(query=new_query))


def read_jsonl(path: str):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def fetch_with_retries(session: requests.Session, url: str) -> tuple[int, str]:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            status = r.status_code
            if status == 200 and r.text and len(r.text) > 200:
                return status, r.text
            if status in (429, 500, 502, 503, 504):
                time.sleep(min(2**attempt, 15))
                continue
            return status, r.text or ""
        except requests.RequestException as e:
            last_err = e
            time.sleep(min(2**attempt, 15))
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {last_err}")


def worker_task(rec: dict):
    title = rec.get("title")
    pageid = rec.get("pageid")
    url = rec.get("url")
    if not title or not url:
        return None

    target_url = add_action_render(url) if USE_RENDER else url
    filename = safe_filename(title, pageid)
    filepath = os.path.join(OUT_DIR, filename)

    # resume skip
    if os.path.exists(filepath) and os.path.getsize(filepath) > 500:
        return {
            "title": title,
            "pageid": pageid,
            "url": url,
            "fetched_url": target_url,
            "status": "skipped",
            "file": filepath,
        }

    # session per thread (keep-alive)
    session = requests.Session()

    if SLEEP_BETWEEN_REQUESTS:
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    try:
        status, html = fetch_with_retries(session, target_url)
        if status == 200:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)
            return {
                "title": title,
                "pageid": pageid,
                "url": url,
                "fetched_url": target_url,
                "status": 200,
                "file": filepath,
            }
        return {
            "title": title,
            "pageid": pageid,
            "url": url,
            "fetched_url": target_url,
            "status": status,
            "error": f"HTTP {status}",
        }
    except Exception as e:
        return {
            "title": title,
            "pageid": pageid,
            "url": url,
            "fetched_url": target_url,
            "status": None,
            "error": str(e),
        }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    records = list(read_jsonl(INPUT_JSONL))
    total = len(records)
    print(f"Total URLs: {total} | workers={WORKERS}")

    done = 0
    ok = 0
    skipped = 0
    failed = 0

    with open(OUT_INDEX, "a", encoding="utf-8") as out_f:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = [ex.submit(worker_task, rec) for rec in records]

            for fut in as_completed(futures):
                res = fut.result()
                done += 1
                if not res:
                    continue

                if res.get("status") == 200:
                    ok += 1
                elif res.get("status") == "skipped":
                    skipped += 1
                else:
                    failed += 1

                with write_lock:
                    out_f.write(json.dumps(res, ensure_ascii=False) + "\n")
                    if done % FLUSH_EVERY == 0:
                        out_f.flush()

                if done % 200 == 0:
                    print(
                        f"[progress] {done}/{total} | ok={ok} skipped={skipped} failed={failed}"
                    )

    print(f"\nDone. ok={ok} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
