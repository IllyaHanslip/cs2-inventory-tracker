**Download `CS2-Inventory-Intelligence.exe` below and double-click it**: no Python or setup needed.
It starts the app and opens the dashboard in your browser. Keep its window open while you use it;
close the window to stop.

### What's new in v1.2.2

- **Links to every market**: Waxpeer and Market.CSGO prices now link straight to the item's page there,
  like Steam, Skinport and CSFloat (prices saved earlier get their links too). In "Best 3 to sell" and the
  Sell Planner, each market's name opens the item on that market.
- Manual quotes can have a link to the listing you saw.

### v1.2.1

- Item names inside containers and in the Sell Planner look like the rest of the app instead of like
  links; click anywhere on the row (or press Enter on it) to see the item's details.

### v1.2.0

- **Profit & Loss tab.** Everything you've bought and sold, with prices only where you gave them. Switch
  between actual profit (sales where you gave both what you paid and what you got), money gained
  (everything you've sold for) and money spent (every purchase with a price), with a chart, a breakdown by kind of item, best and worst sales, and the full list to edit or add to.
- **Changes from Steam syncs**: say an item that left was sold (and for how much, if you like), give what a
  new item cost, or dismiss a change with × for good - it won't come back after a restart.
- Removing an item you added by hand can record it as sold, with its price.
- The in-game storage count is gone: compare with CS2 yourself whenever you like.
- Biggest movers lists up to 30 items each way, filling its box (scroll inside it for more).

### v1.1.2

- **See what's running.** A price refresh the app starts by itself now shows at the top too, with each
  market's progress ("Steam Market 40 of 385 · about 12 minutes left") - and pressing Refresh while one
  is running shows it instead of just saying it's busy.
- **Steam Market prices**: when Steam says "too many requests", it's left alone for an hour (longer each
  time it happens, up to a day) rather than asked again on every refresh, and your most valuable items
  are fetched first when it's tried again.
- **Storage units**: Steam's public inventory doesn't list storage units at all, so it can't give their
  item counts. Type the count CS2 shows for a unit instead: its card tells you how many items aren't
  listed yet (the × hides that until the app restarts), and moves you confirm keep the count up to date.
  Changes noticed by a sync are suggested for the unit already holding the same item.
- The changes list and the biggest movers (now 10 each way) scroll inside their own boxes.

### v1.1.1

- The first Steam sync after updating saves each storage unit's item count from Steam. Until then the
  app can't tell a stored item from a sold one, so it asks where items went instead of guessing
  "sold". From the next sync on, moves are matched up automatically.

### v1.1.0

- **Storage moves noticed for you.** After a Steam sync, items that left your inventory or came back
  are matched with each storage unit's item count from Steam ("15 Clutch Cases left and Long Holds
  went from 63 to 79: put them in it?"). Review them on the Containers tab and confirm one by one or
  all at once; items that left while no unit grew are flagged as sold or traded. Each storage unit's
  card shows whether its list matches Steam's count. Your notes and prices on an item travel with it.
- **"+ From an event…"** in a container: pick a tournament, narrow it to stickers, slabs or capsules
  and Gold, Holo…, and type how many you have of each. They go onto the container's list by their
  exact names for you to check before adding.

### v1.0.2

- The Sell Planner's "why this one" reasons all share one shape - what it is, then any figures in
  brackets: "falling (about 5% a week)", "steady (under 1% a week)", "you have 40 (selling 5)".
  Every item now says how its price is trending, including steady ones.

### v1.0.1

- The value above each chart and the chart's last point are now always the same number, even
  just after you add or remove items.
- The % changes (24h, 7 weeks and each chart range) count price moves only: filling a storage unit
  or adding 50 capsules no longer shows up as a gain.
- "Portfolio mix over time" now ends exactly at today's split, including custom prices, applied
  stickers and big stacks.
- The Public inventory's saved daily value now matches what the Public tab shows when a stack is
  split between Steam and a storage unit.
- Only storage units have an "Open" button: cases, capsules and souvenir packages no longer do,
  and identical cases stack again.

### Good to know

- Your data and settings are kept in `%LOCALAPPDATA%\CS2 Inventory Intelligence` (put an optional
  `.env` file there, e.g. for a CSFloat API key - see the README). Updating is just downloading the
  new file: your data stays where it is.
- Windows may say *"Windows protected your PC"* because the app isn't code-signed: click
  **More info → Run anyway**. The file is built automatically by GitHub Actions from this
  release's source code (see the "Windows app" workflow).
- Background daily updates: run `scripts\install-daily-update.ps1 -Exe "<path to the .exe>"`
  from the source code (see the README).

Try it first without installing anything: [live demo](https://illyahanslip.github.io/cs2-inventory-tracker/).
