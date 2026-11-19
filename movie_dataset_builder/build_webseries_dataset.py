#!/usr/bin/env python3
"""Build a TV-series dataset and a combined movies + series catalogue.

This script does NOT modify the existing movie CSV or its posters. It writes:

  output/webseries_tmdb.csv          fetched TMDB series
  output/webseries_tmdb.jsonl
  output/tv_posters/tv-<id>.jpg      optional local posters
  output/all_titles.csv              movies + series used by the app/model

Requires the same TMDB Read Access Token as build_dataset.py. The token is
read from this directory's .env or the environment and is never written out.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
ENV = {**dotenv_values(HERE / ".env")}
API_ROOT = "https://api.themoviedb.org/3"
IMAGE_ROOT = "https://image.tmdb.org/t/p"
TOKEN = (os.getenv("TMDB_BEARER_TOKEN") or ENV.get("TMDB_BEARER_TOKEN") or "").strip()
TARGET_SERIES = int(os.getenv("TARGET_SERIES") or ENV.get("TARGET_SERIES") or "1000")
POSTER_SIZE = os.getenv("POSTER_SIZE") or ENV.get("POSTER_SIZE") or "w500"
DOWNLOAD_POSTERS = str(
    os.getenv("DOWNLOAD_POSTERS") or ENV.get("DOWNLOAD_POSTERS") or "true"
).lower() in {"1", "true", "yes"}
MAX_POSTER_DOWNLOADS = int(
    os.getenv("MAX_POSTER_DOWNLOADS") or ENV.get("MAX_POSTER_DOWNLOADS") or "1000"
)

OUTPUT_DIR = HERE / "output"
POSTER_DIR = OUTPUT_DIR / "tv_posters"
SERIES_CSV = OUTPUT_DIR / "webseries_tmdb.csv"
SERIES_JSONL = OUTPUT_DIR / "webseries_tmdb.jsonl"
STATE_PATH = OUTPUT_DIR / "webseries_run_state.json"
MOVIES_CSV = OUTPUT_DIR / "movies_2023_2026.csv"
COMBINED_CSV = OUTPUT_DIR / "all_titles.csv"

# Local TV posters in Frontend/tvshow include Peaky Blinders; seed it so the
# image shown in the existing UI resolves to a real catalogue title.
SEED_IDS: tuple[int, ...] = (60574,)

COLUMNS: tuple[str, ...] = (
    "tmdb_id", "imdb_id", "title", "original_title", "release_date", "year",
    "original_language", "production_countries", "genres", "overview",
    "cast", "director", "runtime_minutes", "vote_average", "vote_count",
    "popularity", "poster_path", "poster_url", "poster_local_path",
    "backdrop_path", "backdrop_url", "india_streaming", "india_rent",
    "india_buy", "homepage", "adult", "content_id", "media_type",
    "source_tmdb_id", "model_key", "poster_key", "number_of_seasons",
    "number_of_episodes", "status", "tv_type",
)

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "accept": "application/json",
    "User-Agent": "MovieSeriesDatasetBuilder/1.0",
})


def api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET with retry/backoff for rate limits, transient errors and SSL resets."""
    url = f"{API_ROOT}{path}"
    for attempt in range(8):
        try:
            response = SESSION.get(url, params=params, timeout=45)
        except requests.RequestException:
            time.sleep(min(2 ** attempt, 20))
            continue
        if response.status_code == 429:
            time.sleep(max(int(response.headers.get("Retry-After", "2")), 2))
            continue
        if response.status_code in {500, 502, 503, 504}:
            time.sleep(min(2 ** attempt, 30))
            continue
        response.raise_for_status()
        time.sleep(0.06)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    raise RuntimeError(f"TMDB request failed after retries: {path}")


def discover_ids() -> list[int]:
    """Collect popular, top-rated, on-air and discovered series IDs."""
    ids: list[int] = list(SEED_IDS)
    seen: set[int] = set(ids)

    def add(items: list[dict[str, Any]]) -> bool:
        for item in items:
            identifier = item.get("id")
            if identifier and identifier not in seen:
                seen.add(identifier)
                ids.append(identifier)
                if len(ids) >= TARGET_SERIES:
                    return True
        return False

    for endpoint in ("/tv/popular", "/tv/top_rated", "/tv/on_the_air"):
        for page in range(1, 51):
            payload = api_get(endpoint, {"page": page, "language": "en-US"})
            if add(payload.get("results") or []):
                return ids
            if page >= int(payload.get("total_pages") or 1):
                break
    for page in range(1, 51):
        payload = api_get("/discover/tv", {
            "page": page,
            "language": "en-US",
            "sort_by": "popularity.desc",
            "include_null_first_air_dates": "false",
        })
        if add(payload.get("results") or []):
            return ids
        if page >= int(payload.get("total_pages") or 1):
            break
    return ids


def names(items: list[dict[str, Any]], limit: int | None = None) -> str:
    values = [str(item.get("name", "")).strip() for item in items if item.get("name")]
    unique = list(dict.fromkeys(values))
    return " | ".join(unique[:limit] if limit else unique)


def provider_names(provider_list: list[dict[str, Any]]) -> str:
    return " | ".join(dict.fromkeys(
        str(item.get("provider_name", "")).strip()
        for item in provider_list if item.get("provider_name")
    ))


def image_url(path: str | None, size: str) -> str:
    return f"{IMAGE_ROOT}/{size}{path}" if path else ""


def download_image(url: str, destination: Path) -> str:
    if not url:
        return ""
    if destination.exists() and destination.stat().st_size > 64:
        return destination.as_posix()
    try:
        response = requests.get(url, timeout=40)
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("image/"):
            return ""
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination.as_posix()
    except requests.RequestException:
        return ""


def make_record(series_id: int, download_count: int) -> dict[str, Any] | None:
    detail = api_get(
        f"/tv/{series_id}",
        {"language": "en-US", "append_to_response": "credits,watch/providers"},
    )
    if not detail.get("id"):
        return None
    release_date = str(detail.get("first_air_date") or "")
    credits = detail.get("credits") or {}
    cast = credits.get("cast") or []
    created_by = detail.get("created_by") or []
    directors = list(created_by)
    directors += [
        person for person in (credits.get("crew") or [])
        if person.get("job") == "Director"
    ]
    providers = ((detail.get("watch/providers") or {}).get("results") or {}).get("IN") or {}
    poster_path = str(detail.get("poster_path") or "")
    backdrop_path = str(detail.get("backdrop_path") or "")
    poster_url = image_url(poster_path, POSTER_SIZE)

    local_path = ""
    if DOWNLOAD_POSTERS and poster_url and download_count < MAX_POSTER_DOWNLOADS:
        local_path = download_image(poster_url, POSTER_DIR / f"tv-{series_id}.jpg")

    episode_runtimes = detail.get("episode_run_time") or []
    runtime = episode_runtimes[0] if isinstance(episode_runtimes, list) and episode_runtimes else None
    return {
        "tmdb_id": detail.get("id"),
        "imdb_id": "",
        "title": detail.get("name") or "",
        "original_title": detail.get("original_name") or "",
        "release_date": release_date,
        "year": int(release_date[:4]) if len(release_date) >= 4 else None,
        "original_language": detail.get("original_language") or "",
        "production_countries": names(detail.get("production_countries") or []),
        "genres": names(detail.get("genres") or []),
        "overview": detail.get("overview") or "",
        "cast": names(cast, 10),
        "director": names(directors),
        "runtime_minutes": runtime,
        "vote_average": detail.get("vote_average"),
        "vote_count": detail.get("vote_count"),
        "popularity": detail.get("popularity"),
        "poster_path": poster_path,
        "poster_url": poster_url,
        "poster_local_path": local_path,
        "backdrop_path": backdrop_path,
        "backdrop_url": image_url(backdrop_path, "w1280"),
        "india_streaming": provider_names(providers.get("flatrate") or []),
        "india_rent": provider_names(providers.get("rent") or []),
        "india_buy": provider_names(providers.get("buy") or []),
        "homepage": detail.get("homepage") or "",
        "adult": False,
        "content_id": f"tv-{series_id}",
        "media_type": "tv",
        "source_tmdb_id": series_id,
        "model_key": f"tv-{series_id}",
        "poster_key": f"tv-{series_id}",
        "number_of_seasons": detail.get("number_of_seasons"),
        "number_of_episodes": detail.get("number_of_episodes"),
        "status": detail.get("status") or "",
        "tv_type": detail.get("type") or "",
    }


def load_existing() -> dict[int, dict[str, Any]]:
    if not SERIES_CSV.exists():
        return {}
    frame = pd.read_csv(SERIES_CSV, encoding="utf-8-sig", low_memory=False).fillna("")
    records: dict[int, dict[str, Any]] = {}
    for row in frame.to_dict(orient="records"):
        try:
            records[int(row["tmdb_id"])] = row
        except (TypeError, ValueError, KeyError):
            continue
    return records


def save_series(records: dict[int, dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = list(records.values())
    frame = pd.DataFrame(rows, columns=list(COLUMNS))
    if not frame.empty:
        frame = frame.sort_values(["popularity", "title"], ascending=[False, True])
    frame.to_csv(SERIES_CSV, index=False, encoding="utf-8-sig")
    with SERIES_JSONL.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    STATE_PATH.write_text(json.dumps({
        "records_saved": len(records),
        "target": TARGET_SERIES,
        "last_updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2), encoding="utf-8")


def build_combined(series_records: dict[int, dict[str, Any]]) -> tuple[int, int]:
    """Merge movies + series without modifying or dropping any movie row."""
    if not MOVIES_CSV.is_file():
        raise SystemExit(f"Movie dataset not found: {MOVIES_CSV}")
    movies = pd.read_csv(MOVIES_CSV, encoding="utf-8-sig", low_memory=False)
    original_ids = set(movies["tmdb_id"].astype(int))
    for column in COLUMNS:
        if column not in movies.columns:
            movies[column] = ""
    movies["content_id"] = movies["tmdb_id"].map(lambda value: f"movie-{int(value)}")
    movies["media_type"] = "movie"
    movies["source_tmdb_id"] = movies["tmdb_id"].astype(int)
    movies["model_key"] = movies["tmdb_id"].astype(int).astype(str)
    movies["poster_key"] = movies["tmdb_id"].astype(int).astype(str)

    series_frame = pd.DataFrame(list(series_records.values()), columns=list(COLUMNS))
    combined = pd.concat([movies, series_frame], ignore_index=True, sort=False)
    combined = combined.drop_duplicates(subset=["content_id"], keep="first")
    combined.to_csv(COMBINED_CSV, index=False, encoding="utf-8-sig")

    kept_movies = {
        int(row["tmdb_id"])
        for row in combined[combined["media_type"] == "movie"].to_dict(orient="records")
    }
    missing = original_ids - kept_movies
    if missing:
        raise RuntimeError(f"Combined dataset lost {len(missing)} original movies")
    return len(original_ids), len(series_frame)


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "Missing TMDB_BEARER_TOKEN. Add it to movie_dataset_builder/.env "
            "or export it before running."
        )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if DOWNLOAD_POSTERS:
        POSTER_DIR.mkdir(parents=True, exist_ok=True)

    records = load_existing()
    print(f"Already saved series: {len(records)}")
    candidates = discover_ids()
    pending = [identifier for identifier in candidates if identifier not in records]
    print(f"Candidates discovered: {len(candidates)}; remaining to fetch: {len(pending)}")

    downloads = sum(1 for record in records.values() if record.get("poster_local_path"))
    for index, series_id in enumerate(pending, start=1):
        try:
            record = make_record(series_id, downloads)
            if record:
                records[int(series_id)] = record
                if record.get("poster_local_path"):
                    downloads += 1
        except requests.RequestException as exc:
            print(f"Skipping TV ID {series_id}: {exc}")
        except Exception as exc:
            print(f"Error on TV ID {series_id}: {exc}")

        if index % 25 == 0:
            save_series(records)
        if len(records) >= TARGET_SERIES:
            break

    save_series(records)
    movies_kept, series_count = build_combined(records)
    print(f"\nFinished. Kept {movies_kept} movies and added {series_count} TV series.")
    print(f"Combined CSV: {COMBINED_CSV.resolve()}")
    print(f"Posters: {POSTER_DIR.resolve()}")


if __name__ == "__main__":
    main()
