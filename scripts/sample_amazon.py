"""Create a balanced, rated-only Amazon sample for SmartReco.

Strategy (from eda/amazon_eda.ipynb):
- Keep only rated products (stars > 0) — 47% of the 2.2M-row source.
- Stratified random sample: up to `--per-category` rows per category
  (default 100), seeded for reproducibility. All 296 categories stay
  represented; mega-categories like "Sports & Outdoors" are capped.

Output CSV has the exact columns the `load_amazon` CLI expects:
asin, title, imgUrl, productURL, stars, reviews, price,
isBestSeller, boughtInLastMonth, categoryName

Usage:
    python scripts/sample_amazon.py --csv <big.csv> --out data/amazon_sample.csv
    python scripts/sample_amazon.py --csv <big.csv> --per-category 50
"""
import argparse
from pathlib import Path

import pandas as pd


def sample(csv_path: Path, out_path: Path, per_category: int, seed: int) -> None:
    chunks: list[pd.DataFrame] = []
    total = 0
    for chunk in pd.read_csv(csv_path, chunksize=500_000):
        total += len(chunk)
        chunk["stars"] = pd.to_numeric(chunk["stars"], errors="coerce")
        chunks.append(chunk[chunk["stars"] > 0])

    df = pd.concat(chunks, ignore_index=True)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")

    frames = []
    for _, group in df.groupby("categoryName"):
        frames.append(group.sample(n=min(per_category, len(group)), random_state=seed))
    sampled = pd.concat(frames, ignore_index=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sampled.to_csv(out_path, index=False)
    n_categories = sampled["categoryName"].nunique()
    print(f"source rows scanned: {total:,}")
    print(f"rated rows retained: {len(df):,}")
    print(f"sampled products:    {len(sampled):,} across {n_categories} categories")
    print(f"wrote: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to amz_uk_processed_data.csv")
    parser.add_argument("--out", default="data/amazon_sample.csv")
    parser.add_argument("--per-category", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    sample(Path(args.csv), Path(args.out), args.per_category, args.seed)


if __name__ == "__main__":
    main()
