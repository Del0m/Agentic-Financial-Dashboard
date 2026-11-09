# Tests: `frontend/components/ViewSwitch.test.tsx`

These tests cover `components/ViewSwitch.tsx`, the **Demo user / Plaid bank** switch in the Dashboard and Alerts headers. It reads `GET /admin/view`. Choosing the other view calls `PUT /admin/view`, which sets or clears the backend's `view` cookie, then reloads the page so every card reads the new user. It only appears with the admin tools on and a separate demo user configured.

Run them from `frontend/`:

```bash
npm test -- components/ViewSwitch.test.tsx
```

## `shows which user is on, and switching saves then reloads`

**What it does:** On the demo view, "Demo user" is pressed. Clicking "Plaid bank" calls `setView("plaid")` and then reloads.

**Why it's there:** The switch is how the demo moves to the Plaid-linked user without editing `.env` or restarting.

## `clicking the current view does nothing`

**What it does:** On the Plaid view, clicking "Plaid bank" neither saves nor reloads.

**Why it's there:** A reload in the middle of a demo would only lose the page's state.

## `renders nothing when there's nothing to switch`

**What it does:** When the backend says the view isn't available, no switch is drawn.

**Why it's there:** If the configured user already is the Plaid user, both buttons would show the same data.

## `renders nothing without the admin tools`

**What it does:** When `/admin/view` is a 404, no switch is drawn.

**Why it's there:** Outside development the switch doesn't exist, and the header shouldn't show a broken control.

## `a failed switch says why and stays put`

**What it does:** When saving fails, the error is shown, the page doesn't reload, and "Demo user" stays pressed.

**Why it's there:** A silent failure would leave the presenter thinking they had switched users when they hadn't.
