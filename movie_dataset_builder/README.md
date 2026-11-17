# Movie Dataset Builder (2023–2026)

This project builds a custom dataset of up to 5,000 worldwide and Indian movies released from **2023-01-01 through 2026-11-17** (2026 is therefore a year-to-date slice).

## What it creates

- `output/movies_2023_2026.csv` — one row per movie
- `output/movies_2023_2026.jsonl` — same records as JSON Lines
- `output/posters/<tmdb_id>.jpg` — downloaded poster images when available
- `output/run_state.json` — checkpoint metadata

Fields include title, release date/year, original language, production countries, genres, overview, top 10 cast, director(s), runtime, TMDB rating/vote count/popularity, poster/backdrop URLs, and Indian watch-provider lists for streaming (`flatrate`), rent and buy.

## 1. Get a TMDB API token

1. Create/sign in to a TMDB account.
2. Open **Account Settings → API** and request API access.
3. Copy the **API Read Access Token** (Bearer token), not your account password.
4. Put it in the `.env` file:

```env
TMDB_BEARER_TOKEN=PASTE_YOUR_TMDB_API_READ_ACCESS_TOKEN_HERE
TARGET_MOVIES=5000
DOWNLOAD_POSTERS=true
POSTER_SIZE=w500
```

Do not share or commit your token. `.env` is excluded by `.gitignore`.

Official docs:
- https://developer.themoviedb.org/docs/getting-started
- https://developer.themoviedb.org/docs/finding-data
- https://developer.themoviedb.org/docs/image-basics

## 2. Install and run

Python 3.10+ recommended.

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .venv\\Scripts\\Activate.ps1

pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your token
python build_dataset.py
```

The script checkpoints every 25 movie records. If it stops, run it again; it reuses the existing CSV and poster files.

## Data notes and limitations

- **5,000 is a target, not a guaranteed count.** TMDB discover results are popularity-sorted and paginated; the script deduplicates IDs and fetches details. If fewer than 5,000 matching records are returned, it reports the actual count.
- **2026 is incomplete.** The end date is set to 2026-11-17, so it does not include future releases after this date.
- **Streaming availability is India-specific and time-sensitive.** TMDB's `watch/providers` data can be missing or change. Empty provider fields mean no India provider data was returned, not proof that a title is unavailable in India. The fields distinguish subscription streaming, rent and buy.
- **Cast is the top 10 credited cast members returned by TMDB.** Director(s) are selected from crew entries whose job is `Director`.
- **Posters:** URL fields are retained even if an image download fails. Missing poster paths remain blank.
- **Rights and attribution:** TMDB data and images are subject to TMDB's terms and the rights of their respective owners. Review TMDB's current API terms before publishing or using the dataset commercially. TMDB requires attribution for API use; include the TMDB logo and the notice: “This product uses the TMDB API but is not endorsed or certified by TMDB.” Watch-provider data is powered by JustWatch and requires appropriate credit. Do not assume that downloading an image grants redistribution rights.
- **Regional scope:** The discover request uses India as the region while collecting worldwide titles; it does not restrict the dataset to Indian-produced movies. Production countries and original language are included to help distinguish Indian and international films.

## Suggested next step

After generating the dataset, inspect missing poster/provider values and decide whether to enrich them. For a recommendation model, keep `tmdb_id` as the stable join key and avoid using popularity/rating as the only recommendation signal.
