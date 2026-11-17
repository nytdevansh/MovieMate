#!/usr/bin/env python3
"""
Build a 2023-2026 worldwide movie dataset using the official TMDB API.

Outputs:
  output/movies_2023_2026.csv
  output/movies_2023_2026.jsonl
  output/posters/<tmdb_id>.jpg
  output/run_state.json

Requires:
  pip install requests pandas python-dotenv tqdm

Set TMDB_BEARER_TOKEN in a .env file or environment variable.
The script checkpoints completed records and can be re-run safely.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

API_ROOT = "https://api.themoviedb.org/3"
IMAGE_ROOT = "https://image.tmdb.org/t/p"
TOKEN = os.getenv("TMDB_BEARER_TOKEN", "").strip()
START_DATE = "2023-01-01"
# The 2026 year is partial: this run uses the current date.
END_DATE = "2026-11-17"
TARGET_MOVIES = int(os.getenv("TARGET_MOVIES", "5000"))
POSTER_SIZE = os.getenv("POSTER_SIZE", "w500")
DOWNLOAD_POSTERS = os.getenv("DOWNLOAD_POSTERS", "true").lower() in {"1", "true", "yes"}
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
POSTER_DIR = OUTPUT_DIR / "posters"
CSV_PATH = OUTPUT_DIR / "movies_2023_2026.csv"
JSONL_PATH = OUTPUT_DIR / "movies_2023_2026.jsonl"
STATE_PATH = OUTPUT_DIR / "run_state.json"

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "accept": "application/json",
    "User-Agent": "MovieDatasetBuilder/1.0"
})

COLUMNS = [
    "tmdb_id", "imdb_id", "title", "original_title", "release_date", "year",
    "original_language", "production_countries", "genres", "overview",
    "cast", "director", "runtime_minutes", "vote_average", "vote_count",
    "popularity", "poster_path", "poster_url", "poster_local_path",
    "backdrop_path", "backdrop_url", "india_streaming", "india_rent",
    "india_buy", "homepage", "adult"
]

def api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET with retry/backoff for rate limits and transient server errors."""
    url = f"{API_ROOT}{path}"
    for attempt in range(8):
        response = SESSION.get(url, params=params, timeout=45)
        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", "2"))
            time.sleep(max(wait, 2))
            continue
        if response.status_code in (500, 502, 503, 504):
            time.sleep(min(2 ** attempt, 60))
            continue
        response.raise_for_status()
        time.sleep(0.06)  # conservative pacing; server may impose its own limits
        return response.json()
    raise RuntimeError(f"API request failed after retries: {url}")

def discover_ids() -> list[int]:
    """Collect candidate IDs year-by-year; page size is 20, TMDB caps pages."""
    ids: list[int] = []
    seen: set[int] = set()
    years = range(2023, 2027)
    for year in years:
        params = {
            "include_adult": "false",
            "include_video": "false",
            "language": "en-US",
            "sort_by": "popularity.desc",
            "page": 1,
            "primary_release_date.gte": f"{year}-01-01",
            "primary_release_date.lte": f"{year}-12-31" if year < 2026 else END_DATE,
            "region": "IN",
        }
        first = api_get("/discover/movie", params)
        total_pages = min(int(first.get("total_pages", 1)), 500)
        pages = range(1, total_pages + 1)
        for page in tqdm(pages, desc=f"Discover {year}"):
            params["page"] = page
            payload = first if page == 1 else api_get("/discover/movie", params)
            for item in payload.get("results", []):
                mid = item.get("id")
                if mid and mid not in seen:
                    seen.add(mid)
                    ids.append(mid)
                    if len(ids) >= TARGET_MOVIES:
                        return ids
    return ids

def image_url(path: str | None, size: str) -> str:
    return f"{IMAGE_ROOT}/{size}{path}" if path else ""

def download_image(url: str, destination: Path) -> str:
    if not url:
        return ""
    if destination.exists() and destination.stat().st_size > 0:
        return str(destination.as_posix())
    try:
        response = requests.get(url, timeout=40)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            return ""
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination.as_posix()
    except requests.RequestException as exc:
        print(f"Poster download failed ({url}): {exc}")
        return ""

def names(items: list[dict[str, Any]], limit: int | None = None) -> str:
    values = [x.get("name", "").strip() for x in items if x.get("name")]
    return " | ".join(values[:limit] if limit else values)

def provider_names(provider_list: list[dict[str, Any]]) -> str:
    # Keep provider names and avoid implying a specific subscription tier.
    return " | ".join(dict.fromkeys(
        x.get("provider_name", "") for x in provider_list if x.get("provider_name")
    ))

def make_record(movie_id: int) -> dict[str, Any] | None:
    detail = api_get(
        f"/movie/{movie_id}",
        {"language": "en-US", "append_to_response": "credits,watch/providers"}
    )
    release_date = detail.get("release_date") or ""
    if not (START_DATE <= release_date <= END_DATE):
        return None

    credits = detail.get("credits") or {}
    cast = credits.get("cast") or []
    crew = credits.get("crew") or []
    directors = [p for p in crew if p.get("job") == "Director"]
    providers = ((detail.get("watch/providers") or {}).get("results") or {}).get("IN") or {}

    poster_path = detail.get("poster_path") or ""
    backdrop_path = detail.get("backdrop_path") or ""
    poster_url = image_url(poster_path, POSTER_SIZE)
    backdrop_url = image_url(backdrop_path, "w1280")
    local_path = ""
    if DOWNLOAD_POSTERS and poster_url:
        local_path = download_image(poster_url, POSTER_DIR / f"{movie_id}.jpg")

    return {
        "tmdb_id": detail.get("id"),
        "imdb_id": detail.get("imdb_id") or "",
        "title": detail.get("title") or "",
        "original_title": detail.get("original_title") or "",
        "release_date": release_date,
        "year": int(release_date[:4]) if len(release_date) >= 4 else None,
        "original_language": detail.get("original_language") or "",
        "production_countries": names(detail.get("production_countries") or []),
        "genres": names(detail.get("genres") or []),
        "overview": detail.get("overview") or "",
        "cast": names(cast, 10),
        "director": names(directors),
        "runtime_minutes": detail.get("runtime"),
        "vote_average": detail.get("vote_average"),
        "vote_count": detail.get("vote_count"),
        "popularity": detail.get("popularity"),
        "poster_path": poster_path,
        "poster_url": poster_url,
        "poster_local_path": local_path,
        "backdrop_path": backdrop_path,
        "backdrop_url": backdrop_url,
        "india_streaming": provider_names(providers.get("flatrate") or []),
        "india_rent": provider_names(providers.get("rent") or []),
        "india_buy": provider_names(providers.get("buy") or []),
        "homepage": detail.get("homepage") or "",
        "adult": detail.get("adult", False),
    }

def load_existing() -> dict[int, dict[str, Any]]:
    if not CSV_PATH.exists():
        return {}
    df = pd.read_csv(CSV_PATH).fillna("")
    records = {}
    for row in df.to_dict(orient="records"):
        try:
            records[int(row["tmdb_id"])] = row
        except (ValueError, TypeError, KeyError):
            continue
    return records

def save(records: dict[int, dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = list(records.values())
    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df = df.sort_values(["release_date", "title"], ascending=[False, True])
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    with JSONL_PATH.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    STATE_PATH.write_text(json.dumps({
        "records_saved": len(records),
        "target": TARGET_MOVIES,
        "last_updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "date_window": [START_DATE, END_DATE],
    }, indent=2), encoding="utf-8")

def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "Missing TMDB_BEARER_TOKEN. Create a TMDB API Read Access Token, "
            "put it in .env, and run again. See README.md."
        )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    POSTER_DIR.mkdir(parents=True, exist_ok=True)

    records = load_existing()
    print(f"Already saved: {len(records)}")
    candidate_ids = discover_ids()
    pending = [mid for mid in candidate_ids if mid not in records]
    print(f"Candidates discovered: {len(candidate_ids)}; remaining to fetch: {len(pending)}")

    for index, movie_id in enumerate(tqdm(pending, desc="Fetch details"), start=1):
        try:
            record = make_record(movie_id)
            if record:
                records[movie_id] = record
        except requests.HTTPError as exc:
            print(f"Skipping TMDB ID {movie_id}: {exc}")
        except Exception as exc:
            print(f"Error on TMDB ID {movie_id}: {exc}")

        if index % 25 == 0:
            save(records)

        if len(records) >= TARGET_MOVIES:
            break

    save(records)
    print(f"\nFinished. Saved {len(records)} movies.")
    print(f"CSV: {CSV_PATH.resolve()}")
    print(f"JSONL: {JSONL_PATH.resolve()}")
    print(f"Posters: {POSTER_DIR.resolve()}")
    if len(records) < TARGET_MOVIES:
        print("Fewer than the requested target were returned. Re-run later or widen the date window.")

if __name__ == "__main__":
    main()
