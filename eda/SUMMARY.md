# Amazon UK 2023 — EDA Summary

Full analysis: [`eda/amazon_eda.ipynb`](amazon_eda.ipynb) · sampler: [`scripts/sample_amazon.py`](../scripts/sample_amazon.py)

## Dataset

- **Source:** `amz_uk_processed_data.csv` — 620 MB, **2,222,742 rows**, 10 columns, **296 categories**.
- 100% of cells populated; **0 duplicate ASINs**.
- Columns map 1:1 onto the existing `Product` model — **no schema change needed**.

## Key findings

| Finding | Value | Impact |
|---|---|---|
| **Massive imbalance** | `Sports & Outdoors` = 826k rows (37%); top-10 = 44% | Must stratify the sample; never uniform-sample |
| **52.8% unrated** | `stars == 0` AND `reviews == 0` (1.17M rows) | Filter to rated products for a quality catalog |
| **Rated catalog** | 47% (1.05M rows) still covers **all 296 categories** | Safe to filter |
| **Recent demand is rare** | only 7.3% have `boughtInLastMonth > 0` | Field is sparse signal — keep, but don't weight heavily |
| **Best sellers are rare** | 0.27% (6,018) | Keep flag; low-frequency filter risk |
| **Price sanity** | median £19.99; p90 £160; only 31 rows outside (0, 20000] | Loader's price bounds reject only 0.001% |
| **Title length** | max 1146; only 0.013% > 255 | Loader truncation handles it |
| **Empty `description`** | not present in the CSV | Embeddings already fall back to title+category+tags |
| **Rating quality (rated set)** | median 4.4 stars, mean 4.31 | Persuasive narrative can lean on ratings |

## Chosen strategy

1. **Keep rated products only** (`stars > 0`) → 47% of rows, all 296 categories intact.
2. **Stratified sample, up to 100 per category** (seeded, reproducible) → **28,242 products across 296 categories**; caps `Sports & Outdoors` at 100 instead of flooding.
3. Load with the existing `load_amazon` CLI (`--embed` for Pinecone sync).

## Sample produced

`data/amazon_sample.csv` (7.6 MB) — 28,242 rows, 296 categories.

- `clean_amazon_row` accepts **100%** of sampled rows (0 rejections).
- No field exceeds schema limits (11 titles >255 → truncated by loader).

## Follow-ups (no code changes required)

- `is_best_seller` remains a boolean flag; the agent already down-weights rarity.
- `bought_in_last_month` stays in vector metadata; it is sparse, so retrieval filters should not require it.
- Pinecone index dimension (1536) and schema unchanged.
