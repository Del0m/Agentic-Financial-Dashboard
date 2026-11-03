# Tests: `frontend/components/ForecastChart.test.tsx`

Run from `frontend/`: `npm test -- components/ForecastChart.test.tsx`.

## Shared setup

Typed forecast data has a $5–$412 rent-day range, a $100 buffer and a Landlord event. Recharts is mocked because jsdom has no container dimensions; Testing Library checks the accessible chart description, daily table, empty states and buffer form.

## `headlines a range, never the expected value alone`

**What it does:** Headlines a range, never the expected value alone.

**Why it's there:** Makes the range and buffer understandable and usable without reading the plot.

## `labels the chart with the lowest likely balance and the buffer`

**What it does:** Labels the chart with the lowest likely balance and the buffer.

**Why it's there:** Makes the range and buffer understandable and usable without reading the plot.

## `lists every day in the table and names the dips bill`

**What it does:** Lists every day in the table and names the dips bill.

**Why it's there:** Makes the range and buffer understandable and usable without reading the plot.

## `explains %s`

**What it does:** Explains %s.

**Why it's there:** Makes the range and buffer understandable and usable without reading the plot.

## `explains when no income is assumed`

**What it does:** Explains when no income is assumed.

**Why it's there:** Makes the range and buffer understandable and usable without reading the plot.

## `rejects a buffer outside 0 to 10000 before calling the api`

**What it does:** Rejects a buffer outside 0 to 10000 before calling the api.

**Why it's there:** Makes the range and buffer understandable and usable without reading the plot.

## `saves a valid buffer through the refresh callback`

**What it does:** Saves a valid buffer through the refresh callback.

**Why it's there:** Makes the range and buffer understandable and usable without reading the plot.
