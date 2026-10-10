# Agent evals

Mode: **catalogue** · scoring with the starting estimates

Every catalogue action is proposed and the decision engine alone picks, as if the Strategist had suggested everything. This checks the engine's guard rails without a model; it runs in CI.

| Metric | Result |
|---|---|
| Scenarios | 40 (0 failed to run) |
| Decision accuracy (right action, right oversight) | 97.5% |
| Right action, any oversight | 97.5% |
| **False-auto** (a person was needed, it would have run alone) | **0** (0.0%) |
| Ran something alone that wasn't on the list | 1 |
| Cost | $0.0000 total, $0.00000 per case (0 tokens) |
| Time | 0.0 s per case |

## By case type

| Case type | Correct |
|---|---|
| abandoned_cart | 6 / 7 |
| late_shipment | 6 / 6 |
| refusal_risk | 8 / 8 |
| stale_return | 6 / 6 |
| stalled_fulfilment | 5 / 5 |
| unconfirmed_order | 8 / 8 |

## Every scenario

| ID | Scenario | Next action | Oversight | Acceptable | Result |
|---|---|---|---|---|---|
| U1 | Unconfirmed for 30h, reliable buyer | send_confirmation_reminder | auto | send_confirmation_reminder (auto) | ok |
| U2 | Unconfirmed 41K order, very reliable buyer | send_confirmation_reminder | auto | send_confirmation_reminder (auto); hold_dispatch (approval) | ok |
| U3 | Reminder unanswered for 30h | request_phone_confirmation | human_only | request_phone_confirmation (human_only); hold_dispatch (auto) | ok |
| U4 | Reminder sent only 6h ago | hold_dispatch | auto | none (wait); hold_dispatch (auto) | ok |
| U5 | Contradictory records, low confidence | send_confirmation_reminder | approval | send_confirmation_reminder (approval); request_phone_confirmation (human_only) | ok |
| U6 | 8 days unconfirmed after reminder and call | hold_dispatch | auto | recommend_cancellation (human_only); hold_dispatch (auto) | ok |
| U7 | 9,800 EGP, just under the large-order line | send_confirmation_reminder | auto | send_confirmation_reminder (auto); hold_dispatch (auto) | ok |
| U8 | Buyer with refusals but under the risk line | send_confirmation_reminder | auto | send_confirmation_reminder (auto); request_phone_confirmation (human_only); hold_dispatch (auto) | ok |
| R1 | Risk 70, 3,500 EGP | request_phone_confirmation | human_only | request_phone_confirmation (human_only); hold_dispatch (auto); request_deposit (approval) | ok |
| R2 | Risk 85, 22,000 EGP | request_phone_confirmation | human_only | request_deposit (approval); request_phone_confirmation (human_only); hold_dispatch (approval) | ok |
| R3 | Risk 58, new customer | request_phone_confirmation | human_only | request_phone_confirmation (human_only); hold_dispatch (auto) | ok |
| R4 | Risk 62, already confirmed by WhatsApp | request_phone_confirmation | human_only | request_phone_confirmation (human_only); hold_dispatch (auto) | ok |
| R5 | Risk 75, 16,000 EGP | request_phone_confirmation | human_only | request_deposit (approval); request_phone_confirmation (human_only); hold_dispatch (approval) | ok |
| R6 | Risk 90, call unanswered for 50h | request_deposit | approval | recommend_cancellation (human_only); hold_dispatch (auto); request_deposit (approval) | ok |
| R7 | Risk 56, unsure investigation | request_phone_confirmation | human_only | request_phone_confirmation (human_only); hold_dispatch (approval) | ok |
| R8 | Risk 66, 12,000 EGP | request_phone_confirmation | human_only | request_phone_confirmation (human_only); hold_dispatch (approval) | ok |
| S1 | Confirmed 52h ago, not packed | nudge_vendor | auto | nudge_vendor (auto) | ok |
| S2 | Vendor nudged 50h ago, still stuck | notify_customer_of_delay | auto | notify_customer_of_delay (auto) | ok |
| S3 | Vendor nudged 10h ago | wait | — | none (wait) | ok |
| S4 | Large order stalled | nudge_vendor | auto | nudge_vendor (auto) | ok |
| S5 | Stalled, unsure investigation | nudge_vendor | approval | nudge_vendor (approval) | ok |
| L1 | 6 days on the road, promised 2 | open_courier_ticket | auto | open_courier_ticket (auto); notify_customer_of_delay (auto) | ok |
| L2 | Courier ticket open 30h | notify_customer_of_delay | auto | notify_customer_of_delay (auto); offer_discount (auto) | ok |
| L3 | 9,000 EGP, loyal customer, late | open_courier_ticket | auto | open_courier_ticket (auto); notify_customer_of_delay (auto); offer_discount (approval) | ok |
| L4 | Late, unsure investigation | open_courier_ticket | approval | open_courier_ticket (approval); notify_customer_of_delay (approval) | ok |
| L5 | 15,000 EGP late | open_courier_ticket | auto | open_courier_ticket (auto); notify_customer_of_delay (auto) | ok |
| L6 | Ticket and notice done, still late | offer_discount | approval | offer_discount (auto/approval); none (wait) | ok |
| A1 | 1,000 EGP cart idle 30h | send_cart_reminder | auto | send_cart_reminder (auto) | ok |
| A2 | Reminder unanswered 30h, 1,000 EGP cart | offer_discount | auto | offer_discount (auto) | ok |
| A3 | Reminder unanswered, 250 EGP cart | offer_discount | approval | offer_discount (approval); none (wait) | ok |
| A4 | Reminder unanswered, 231K cart | offer_discount | approval | offer_discount (approval); none (wait) | ok |
| A5 | Reminder sent 5h ago | wait | — | none (wait) | ok |
| A6 | Customer refused a delivery last month | offer_discount | auto | none (wait) | other action |
| A7 | 5,000 EGP cart idle 40h | send_cart_reminder | auto | send_cart_reminder (auto) | ok |
| T1 | Damaged item, 250 EGP, received back | process_return | approval | process_return (approval) | ok |
| T2 | Damaged item, 900 EGP | process_return | human_only | process_return (human_only/approval) | ok |
| T3 | Changed mind, not received yet | process_return | human_only | process_return (approval/human_only) | ok |
| T4 | Outside the 14-day window | process_return | human_only | process_return (approval/human_only) | ok |
| T5 | Return, unsure investigation | process_return | human_only | process_return (approval/human_only) | ok |
| T6 | Refund exactly 300 EGP | process_return | approval | process_return (approval) | ok |
