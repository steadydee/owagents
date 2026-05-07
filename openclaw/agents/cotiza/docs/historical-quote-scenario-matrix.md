# Historical Quote Scenario Matrix

This matrix comes from old Owl's Watch quote sheets in the `Cotizaciones` Google Drive folder.

Historical sheets are examples only. They are used here for request-shape coverage, wording, and formatting precedent. Current prices must still come from Operations.

## Historical Shapes Observed

- Operator cabin stay with one or two guest cabins, daily meals, and bird tours.
- Operator cabin stay with an outside guide/driver and guide lodging or guide meals.
- Direct cabin stay with transport, multiple birding days, and included breakfasts.
- Birding day trip for clients plus outside guide/driver.
- Birding day trip for larger operators with trip leaders, guides, and drivers.
- Lunch-only or breakfast-and-lunch day trips.
- No-client-name operator day trips where the operator name is enough.
- Bilingual guide / guide-only variations with no cabin.
- Event or venue-rental quotes, such as area rental plus refreshments.
- Ambiguous requests missing year, audience, guest count, or visit type.

## Canonical Test Cases

| ID | Source Precedent | Synthetic Request Shape | Expected Cotiza Behavior |
| --- | --- | --- | --- |
| H01 | Juan Manuel day trip | 5 clients, operator, 1 guide, birding day trip, breakfast and lunch | Ready preview; no client-name question; paid client breakfast/lunch; free guide breakfast; discounted guide lunch |
| H02 | Juan Manuel lunch-only | 5 clients, 1 guide, 1 driver, lunch only | Ready preview; no breakfast charged; guide/driver lunch line item |
| H03 | Birding By Bus / Manakin | 10 clients, 2 trip leaders, 1 guide, 1 driver, breakfast and lunch | Ready preview; bird tour priced for clients only; trip leaders get paid meal lines; guide/driver breakfast free and lunch discounted |
| H04 | Patti / direct cabin | Direct client, cross-month cabin stay, 3 adults, 2 cabins, meals, birding, transport | Ready preview; parses cross-month date range; direct audience; transport flagged/requested without inventing transport price |
| H05 | Jaguarundi / guide cabin | Operator, 3 clients + guide, 2 cabins, guide room, meals, one bird tour | Ready preview; 2 guest cabins; guide represented separately; meals and birding preserved |
| H06 | The Colombian Project / bilingual guide | Operator, 2 adults, bilingual guide tours, no cabin | Ready preview; does not create lodging from "no cabin"; bilingual guide flagged as approximate |
| H07 | Generic cabin request | Dates, guests, cabin, meals, birding, but no audience | Needs one question: operator/agency or direct client |
| H08 | Missing year | Operator day trip with month/day only | Needs one question for dates/year before pricing |
| H09 | COTELCO / event rental | Company event, area rental, refreshments | Needs info / unsupported by current cabin-or-birding pricebook; do not auto-price as lodging |

## Regression File

The executable regression coverage lives in:

`/Users/agent/.openclaw/workspace-owlswatch-cotiza/tools/owlswatch_quotes/tests/test_historical_quote_scenarios.py`

Run it with the local test runner used for this workspace.

## Latest Test Results

Local mock-mode regression:

- 17 total quote-tool tests passed.
- 9 historical scenario tests passed.
- Mock create was used only for local contract coverage, so no Operations quote rows were created.

Production prepare-only pass against Operations calculator:

| ID | Result |
| --- | --- |
| H01 | Ready, COP 1,300,000 |
| H02 | Ready, COP 1,050,000 |
| H03 | Ready, COP 2,840,000 |
| H04 | Blocked: direct-client cabin/transport quote returned no line items from Operations calculate |
| H05 | Ready, COP 4,936,000 |
| H06 | Ready, COP 600,000 with bilingual-guide confirmation flag |
| H07 | Correctly asks operator/agency vs direct |
| H08 | Correctly asks for dates/year |
| H09 | Correctly does not auto-price the custom event/rental request |

H04 is the main remaining contract gap: the deployed calculator priced the same request when framed as an operator quote, but not as a direct-client quote. Cotiza should avoid creating direct-client drafts until Operations returns line items for direct/rack pricing or the user confirms operator pricing.
