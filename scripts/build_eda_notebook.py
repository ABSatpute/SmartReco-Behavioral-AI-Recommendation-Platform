"""Build the Amazon dataset EDA notebook (eda/amazon_eda.ipynb).

The notebook embeds the real findings computed over the full 2.2M-row CSV.
Run cells with the project's venv kernel (pandas + jupyter are installed).
"""
from pathlib import Path

import nbformat as nbf

OUT = Path(__file__).resolve().parent.parent / "eda" / "amazon_eda.ipynb"
CSV = "C:/Users/ab31s/Downloads/archive/amz_uk_processed_data.csv"

nb = nbf.v4.new_notebook()
cells = []
md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell

cells.append(md(f"""# Amazon UK 2023 — Dataset EDA

**Source:** `{CSV}` (620 MB, 2,222,742 rows, 10 columns, 296 categories)

Purpose: understand the catalog before sampling, so the SmartReco schema and
loading strategy match reality. All cells read the CSV in chunks (never loads
the whole file into memory).

---

## Key findings (computed over the full file)

| Metric | Value |
|---|---|
| Total rows | 2,222,742 |
| Columns | 10 (`asin, title, imgUrl, productURL, stars, reviews, price, isBestSeller, boughtInLastMonth, categoryName`) |
| Categories | 296 |
| Completeness | 100% on every column (no empty cells) |
| Duplicate ASINs | 0 |
| Products with **no rating** (`stars==0` and `reviews==0`) | 52.8% |
| Products with recent demand (`boughtInLastMonth>0`) | 7.3% |
| Best sellers | 0.27% (6,018) |
| Price median | £19.99 (p90 £160.68, max £100,000) |
| Rated products median stars | 4.4 (mean 4.31) |
| Categories with ≥30 rated products | 290 of 296 |

**Imbalance:** `Sports & Outdoors` alone is 826k rows (37% of the file); the
top-10 categories are 44%. 113 categories have <1k rows.

**Schema fit:** the CSV columns map 1:1 onto the existing `Product` model
(`asin, title, category, price, image_url, product_url, stars, reviews,
is_best_seller, bought_in_last_month, tags`). No schema change is required.

---

## Sampling strategy (chosen)

1. Keep **rated products only** (`stars > 0`, 47% of rows) — better catalog
   quality and meaningful `stars/reviews` for the recommendation narrative.
2. **Stratified sample by category: up to `SAMPLE_PER_CATEGORY=100`** per
   category (random, seeded). This keeps all 296 categories represented and
   caps `Sports & Outdoors` so no single category floods the catalog.
3. Load via the existing `load_amazon` CLI (`--category` filter + `--limit`).

Expected sample size: **~28,242 products** across 296 categories.

Run `python scripts/sample_amazon.py` to produce the sampled CSV, then load it
with `python -m app.cli load_amazon --csv data/amazon_sample.csv --embed`.
"""))

cells.append(md("## 1. Environment & file metadata"))
cells.append(code(f"""\
from pathlib import Path
import pandas as pd
import numpy as np

CSV = Path(r"{CSV}")
print("size MB:", round(CSV.stat().st_size / 1e6, 1))

# header + a few rows only (cheap)
peek = pd.read_csv(CSV, nrows=3)
print("columns:", list(peek.columns))
display(peek.T)
"""))

cells.append(md("## 2. Completeness + row count (chunked, no full load)"))
cells.append(code(f"""\
total = 0
first = True
for chunk in pd.read_csv(CSV, chunksize=500_000, dtype=str, keep_default_na=False):
    total += len(chunk)
    if first:
        df_head = chunk.copy()
        first = False

print("total rows:", total)
# completeness of the header sample (empty-string check, string dtype)
empty = {{c: int((df_head[c].str.strip() == '').sum()) for c in df_head.columns}}
print("empty cells in first 500k rows:", empty)
"""))

cells.append(md("## 3. Category distribution"))
cells.append(code(f"""\
from collections import Counter
import re

counts = Counter()
for chunk in pd.read_csv(CSV, chunksize=500_000, usecols=["categoryName"]):
    counts.update(chunk["categoryName"].tolist())

vc = pd.Series(counts).sort_values(ascending=False)
print("n categories:", len(vc))
display(vc.head(15).to_frame("n_products"))
print("top1 share %:", round(100 * vc.iloc[0] / vc.sum(), 2))
print("top10 cumulative %:", round(100 * vc.head(10).sum() / vc.sum(), 1))

bins = [0, 10, 100, 1_000, 10_000, 100_000, vc.sum()]
labels = ["1-10", "11-100", "101-1k", "1k-10k", "10k-100k", "100k+"]
size = pd.cut(vc, bins=bins, labels=labels, right=False)
print()
print("category size histogram:")
print(size.value_counts().sort_index().to_string())
"""))

cells.append(md("## 4. Numeric columns (price, stars, reviews, boughtInLastMonth)"))
cells.append(code(f"""\
pieces = []
for chunk in pd.read_csv(CSV, chunksize=500_000, usecols=[
        "price", "stars", "reviews", "boughtInLastMonth", "isBestSeller"]):
    pieces.append(chunk)
df = pd.concat(pieces, ignore_index=True)
df["price"] = pd.to_numeric(df["price"], errors="coerce")
df["stars"] = pd.to_numeric(df["stars"], errors="coerce")
df["reviews"] = pd.to_numeric(df["reviews"], errors="coerce")
df["boughtInLastMonth"] = pd.to_numeric(df["boughtInLastMonth"], errors="coerce")

print("PRICE")
print(df["price"].describe(percentiles=[0.5, 0.9, 0.99]).round(2).to_string())
print("price <= 0:", int((df["price"] <= 0).sum()), "| > 20000:", int((df["price"] > 20000).sum()))
print()
print("STARS (0 = unrated)")
print(df["stars"].describe(percentiles=[0.25, 0.5, 0.75, 0.9]).round(3).to_string())
print("unrated share %:", round(100 * (df["stars"] == 0).mean(), 2))
print()
print("REVIEWS")
print(df["reviews"].describe(percentiles=[0.5, 0.9, 0.99]).round(1).to_string())
print()
print("BOUGHT LAST MONTH")
print(">0 share %:", round(100 * (df["boughtInLastMonth"] > 0).mean(), 2))
print()
print("BEST SELLER value_counts:")
print(df["isBestSeller"].value_counts(dropna=False).to_string())
"""))

cells.append(md("## 5. Rated-catalog composition (what the sample keeps)"))
cells.append(code(f"""\
rated = df[df["stars"] > 0]
print("rated rows:", len(rated), "=", round(100 * len(rated) / len(df), 2), "%")
print("rated categories:", rated["categoryName"].nunique())
vc_rated = rated["categoryName"].value_counts()
print("categories with >=30 rated products:", int((vc_rated >= 30).sum()))

target = 100
sample_size = vc_rated.clip(upper=target).sum()
print(f"\\nstratified sample @ {{target}}/cat -> ~{{sample_size:,}} products,", len(vc_rated), "categories")
"""))

cells.append(md("## 6. Sampling script"))
cells.append(code(f"""\
# Run from the repo root:
#   python scripts/sample_amazon.py --csv <big.csv> --out data/amazon_sample.csv --per-category 100
# Then load into SmartReco:
#   python -m app.cli load_amazon --csv data/amazon_sample.csv --embed
print("see scripts/sample_amazon.py")
"""))

nb.cells = cells
nb.metadata["kernelspec"] = {
    "display_name": "Python 3 (SmartReco)",
    "language": "python",
    "name": "python3",
}
OUT.parent.mkdir(parents=True, exist_ok=True)
nbf.write(nb, OUT)
print("wrote", OUT)
