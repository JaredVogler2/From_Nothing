# Late-delivery penalty proxy (synthetic planning assumption)

No customer contracts are available, so a day of late delivery is priced as the ticket revenue the aircraft would have
earned that day, per customer and 787 minor model (`penalty_proxy.csv`). These are planning assumptions, not
contractual liability.

**Formula.** daily penalty = trips per day x sum over cabins of (seats x typical one-way fare x load factor).
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
