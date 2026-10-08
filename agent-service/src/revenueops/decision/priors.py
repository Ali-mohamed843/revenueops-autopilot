"""How often each action recovers the revenue: STARTING ASSUMPTIONS, not measurements.

These are hand-set estimates so the scorer has numbers to work with. Phase 6 replaces them with
success rates measured by the outcome simulator, and the README says so. The model never sets them.
"""

# Chance the revenue is recovered if nobody does anything.
BASELINE: dict[str, float] = {
    "unconfirmed_order": 0.25,
    "refusal_risk": 0.45,
    "stalled_fulfilment": 0.60,
    "late_shipment": 0.70,
    "abandoned_cart": 0.05,
    "stale_return": 0.50,
}

# Chance the revenue is recovered with the action, per case type.
WITH_ACTION: dict[tuple[str, str], float] = {
    ("send_confirmation_reminder", "unconfirmed_order"): 0.45,
    ("send_confirmation_reminder", "refusal_risk"): 0.55,
    ("request_phone_confirmation", "unconfirmed_order"): 0.55,
    ("request_phone_confirmation", "refusal_risk"): 0.65,
    ("hold_dispatch", "refusal_risk"): 0.50,
    ("hold_dispatch", "unconfirmed_order"): 0.30,
    ("request_deposit", "refusal_risk"): 0.60,
    ("recommend_cancellation", "unconfirmed_order"): 0.25,
    ("recommend_cancellation", "refusal_risk"): 0.45,
    ("nudge_vendor", "stalled_fulfilment"): 0.80,
    ("notify_customer_of_delay", "stalled_fulfilment"): 0.70,
    ("notify_customer_of_delay", "late_shipment"): 0.80,
    ("open_courier_ticket", "late_shipment"): 0.85,
    ("send_cart_reminder", "abandoned_cart"): 0.12,
    ("offer_discount", "abandoned_cart"): 0.18,
    ("offer_discount", "late_shipment"): 0.82,
    ("process_return", "stale_return"): 0.80,
}
