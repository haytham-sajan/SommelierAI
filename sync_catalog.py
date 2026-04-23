from __future__ import annotations

import argparse
from pathlib import Path

from app.services.shopware_catalog import create_client, iter_all_products, write_products_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Schlumberger Shopware catalog into a local JSON dataset.")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "app" / "data" / "schlumberger_products.json"),
        help="Output JSON path",
    )
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--sleep", type=float, default=0.2, help="Seconds to sleep between page requests")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    client = create_client()
    products = iter_all_products(client=client, page_size=args.page_size, sleep_s=args.sleep)
    count = write_products_json(products=products, out_path=str(out_path))

    print(f"Wrote {count} products to {out_path}")


if __name__ == "__main__":
    main()

