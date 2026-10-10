# Demo video script (about 3 minutes)

Record at 1440 px wide, dashboard on http://localhost:3001, StoreForge on 3000,
agent service on 8000. Speak over the screen; times are targets.

## Before recording

1. `docker compose up -d postgres`, StoreForge `npm run start:dev`, the agent
   service (`uv run uvicorn revenueops.main:app --port 8000`) and the dashboard
   (`npm run dev -- --port 3001`).
2. The demo data is ready (re-seeded on 10 October): 142 cases, two
   refusal-risk orders waiting for a phone call, five reminders sent, three
   reminders stopped for invalid phone numbers. Don't re-run `act` before
   recording, or the queue will change.
3. Set your operator name with the round button at the top right.
4. Close other tabs, hide the bookmarks bar, zoom 100%.

## 0:00–0:20 The problem

**Screen:** the Overview, top of the page.

> "In Egypt most online orders are cash on delivery. Stores lose money when
> customers refuse at the door, nobody confirms the order, shipments stall, or
> carts are abandoned. This store has 2.3 million pounds at risk right now.
> RevenueOps finds those cases, decides what to do, does the safe things itself
> and asks a person about the risky ones."

## 0:20–0:45 Where the risk is

**Screen:** scroll to the charts; point at *Where the risk is* and *How actions
were decided*.

> "Detection is plain rules over the store's data. Each case gets one owner
> subject, a value at risk and a priority. Most actions so far ran on their
> own, and every one of them was either harmless or undoable."

## 0:45–1:35 One case, end to end

**Screen:** open an unconfirmed-order case from *Waiting for you* or Cases.

> "This order has been unconfirmed for six days. An Investigator agent pulled
> the order, the shipment, the confirmations and the customer's history using
> read-only tools, and wrote this report: the evidence, the likely cause, how
> sure it is."

Scroll to *Why this action*.

> "A Strategist agent proposed three actions from a fixed catalogue and cited
> the store's policies. Then plain code takes over: it scores each action by
> expected value, the difference it makes to the chance of delivery times the
> money at stake minus its cost, and decides who may run it. The model never
> decides that. The reminder runs automatically. Holding dispatch needs
> approval. The phone call is a person's job, and it only becomes the next step
> after the reminder has gone unanswered for a day."

Point at the Arabic message in *Actions*.

> "The reminder was written in Egyptian Arabic by a third agent. Code chose the
> recipient and checked the message contains the order facts and never mentions
> risk."

## 1:35–2:05 A person in the loop

**Screen:** Approvals. Open the 83,886 EGP refusal-risk order waiting for a
phone call.

> "Some things only a person should do. This customer's refusal risk is 61, so
> the store's policy says a WhatsApp message isn't enough: someone has to call
> before an 84,000-pound order ships. The service never pretends to make that
> call; it waits for a person to report back. Anything that needs approval
> waits here too, and is scored again at the moment it's approved."

Then open **Activity**: three reminders show as failed because the customer's
phone number wasn't valid.

> "Code checks the recipient, not the model: these three were stopped because
> the phone numbers on the orders were not real numbers."

Still in **Activity**, undo one action.

> "Everything is recorded with before and after snapshots, and anything that
> can be undone, can be."

## 2:05–2:35 Measuring instead of guessing

**Screen:** Simulation. Run a dry run, then show *Measured vs assumed*.

> "The chances the scorer uses started as estimates. A seeded simulation of
> customer behaviour, with a random control group, measures how often each
> action actually works, with confidence intervals. Some estimates were wrong:
> calling a customer flagged as likely to refuse works 81% of the time, not
> 65%, and recommending cancellation does nothing that waiting doesn't. These
> outcomes are simulated, and the README says so."

## 2:35–3:00 Safety, measured

**Screen:** `docs/evals.md` on GitHub, the summary table.

> "I test the decisions on 40 labelled cases. The number that has to stay at
> zero is false-auto: a case where a person was needed but the system would
> have acted alone. It is zero. With the real model proposing, all 40 decisions
> were right, at under a cent per case, and the decision engine has 100% branch
> coverage in CI. The agents run on any model behind one interface, and the
> store sits behind an adapter, so the next step is a second platform."

End on the Overview.
