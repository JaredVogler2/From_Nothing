# Late-delivery rates (planning assumption)

No customer contracts are available, so a day of late delivery is priced with a modeled daily rate per customer
airline and 787 minor model. These are planning assumptions, not contractual liability.

## The rate (owner, 2026-11-11; `docs/execution/DECISIONS.md` D-31)

- **Daily rate:** the owner's airline economic proxy, column `daily_airline_economic_proxy_usd` of
  `airline_economic_proxy.csv`, for the aircraft's customer airline and minor model.
- **Due date:** the aircraft's planned unload date at control station 744 plus 2 days, to the end of that local day
  (OD-06, unchanged). Lateness begins the next minute. There is no other grace period.
- **No cap.**

Cost of a late aircraft = daily rate × minutes late ÷ 1,440.

## The table: `airline_economic_proxy.csv`

The owner's rate table, supplied on 2026-11-11: 94 rows, 63 airlines by minor model. It is derived from public data,
and the owner approved keeping it here. Amounts are rounded to cents and flights a day to two decimals, and one
caveat is reworded. Nothing else is changed.

| Column | Content |
| --- | --- |
| `operator` | The airline, as the owner's table names it |
| `minor_model` | `787-8`, `787-9` or `787-10` |
| `mission_profile` | The owner's class for the airline's flying with the model: `LONG`, `ULTRA`, `MED`, `REG`, `LEISURE` or `DOM/LONG` |
| `estimated_flights_per_day` | The flights a day the owner's table assumes |
| `expected_ticket_sales_usd_per_day` | The owner's estimate of ticket sales a day |
| `daily_airline_economic_proxy_usd` | **The daily rate FOCUS uses.** In the table it is 16.6% to 17.9% of the expected ticket sales; the table does not state its formula |
| `caveat` | The owner's note on the row |

- **Range:** $39,010.36 to $84,372.07 a day, median $58,282.43.

  | Minor model | Rows | Lowest | Highest |
  | --- | --- | --- | --- |
  | 787-8 | 31 | $39,010 | $58,155 |
  | 787-9 | 52 | $46,271 | $75,960 |
  | 787-10 | 11 | $53,570 | $84,372 |

- **Caveats.** 92 rows say "Operator may differ from contractual buyer": the table is keyed by the airline that flies
  the aircraft. An aircraft whose customer in the build data is a lessor or another buyer has no row under that name.
  One row is a planned cabin layout (Air Canada 787-10) and one is a leased aircraft (IndiGo 787-9).
- **Left out:** the source file's liquidated-damages estimates and its hypothetical cap per model. The owner uses
  neither. Those estimates implied a 14-day grace period, which the owner confirmed is not real: lateness begins at
  744 unload plus 2 days.

## Which aircraft, and what the table does not cover

The aircraft in a run are whichever have open tasks when the data is cut (owner, 2026-11-11; D-32). So any airline
and minor model can come up, and the whole table is kept, not only today's customers.

**FOCUS never fills a rate in**, from another model, another airline or an average. An aircraft whose airline and
minor model have no row has no rate: its late cost is unknown, the run lists it, and the scheduler cannot weigh it by
cost.

Two customer and minor-model pairs with open orders have no row today:

| Customer and minor model | Open orders | Rate |
| --- | --- | --- |
| Qatar Airways 787-10 | 75 | none in the table |
| Turkish Airlines 787-10 | 15 | none in the table |

- Neither airline flies the 787-10 yet. Together they are 90 of the 509 open orders (18%).
- `penalty_terms()` returns no rate for them and `unpriced_pairs()` lists them.
- **The synthetic fleet gives them no aircraft**: it is split over the customers and minor models that have a rate,
  so every synthetic aircraft is priced.
- **On real data** such an aircraft is listed by `tools/build_economics_rows.py`, and its cost stays unknown until
  the owner supplies a row or a rule (open: `docs/integration/DECISION_PLAYBOOK.md`, DEC-35).

## Loading the table on real data

`tools/build_economics_rows.py` reads the table as it is and writes the economics rows FOCUS reads. The table names
airlines, and the build data carries customer codes, so a list of `CustomerCode, RateCustomer` pairs is needed; names
are matched exactly as written. The command is in the tool's own text and in `docs/data/08-economics.md`.

## The open-order mix, and the rate used before: `ticket_revenue_proxy.csv`

- **Still used:** the customers, their codes and their firm open orders by minor model (30 September 2026). The
  synthetic fleet is split over them in proportion to open orders.
- **No longer used as the rate:** `ticket_revenue_usd_per_day` (named `daily_penalty_usd` until 2026-11-11), the
  ticket revenue the aircraft would have earned in a day. It was 2.7 to 5.2 times the owner's rate (median 3.5).
  Dollar figures in reports written before that date used it.

**How the ticket-revenue figure was built** (kept as the record of the superseded rate).
daily ticket revenue = trips per day x sum over cabins of (seats x typical one-way fare x load factor).
- **Seats per cabin:** the layout new aircraft are delivered with where announced (a late delivery is a new aircraft),
  otherwise the most common current layout. Extra-legroom economy is priced as economy. Sources: airline seat maps,
  aeroLOPA, SEC filings; as of September-October 2026. Qatar's and Turkish's 787-10 cabins are not announced and are
  estimated from the mean two-class 787-10 layout of Singapore, Etihad, EVA and Korean.
- **Fares:** KAYAK cabin averages on each airline's 787 long-haul routes, read 30 October 2026, round trips halved.
  United's and American's FY2025 regional yields put collected revenue at about 0.75 of these published fares.
- **Load factor:** each airline's reported figure (787-specific where DOT T-2 or UK CAA data exist), one value for all
  cabins; no airline publishes premium-cabin load factors.
- **Trips per day:** the aircraft's daily block hours divided by the block time of the routes the fares came from, so
  short domestic hops are not priced as long-haul tickets. Block hours: DOT T-2 (United, American) and UK CAA (British
  Airways) 2025 actuals, otherwise OAG schedules for 29 October to 27 November 2026. The fare-route block times are
  approximate scheduled times. Models with no flying history borrow the airline's 787-9 utilization.
- **Ticket revenue only;** no cargo.

Each row keeps every input, its source basis and a confidence rating.
