"""Re-record the StoreForge fixtures from a running StoreForge with demo data (`npm run seed:demo`).

    uv run python tests/fixtures/storeforge/record.py http://localhost:3000/api <INTEGRATION_API_KEY>

Responses are saved as returned, except that each list is cut to a few items and marked as one page.
The demo data is fictional (demo.storeforge.test emails, 010990 phones).
"""

import json
import sys
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).parent


def main(base_url: str, api_key: str) -> None:
    http = httpx.Client(base_url=base_url.rstrip("/") + "/integration", headers={"x-api-key": api_key}, timeout=30)

    def get(path: str, **params: Any) -> Any:
        response = http.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def one_page(body: dict[str, Any], keep: int) -> dict[str, Any]:
        items = body["items"][:keep]
        return {**body, "items": items, "total": len(items), "page": 1, "limit": 100, "pages": 1}

    def save(name: str, body: Any) -> None:
        (HERE / f"{name}.json").write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"saved {name}.json")

    save("orders", one_page(get("/orders", status="pending,confirmed,processing", limit=20), 3))

    # An order that went out with a courier and came back: shipment, attempts and history are all filled in.
    returned = get("/shipments", status="returned", limit=1)["items"][0]
    detail = get(f"/orders/{returned['orderId']}")
    save("order_detail", detail)
    save("customer", get(f"/customers/{detail['customer']['phoneKey']}/summary"))
    save("carts", one_page(get("/carts", idleHours=24, limit=20), 2))
    save("late_shipments", one_page(get("/shipments", overdue="true", limit=20), 2))
    save("returns", one_page(get("/returns", status="requested", limit=20), 2))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
