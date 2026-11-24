**Download `CS2-Inventory-Intelligence.exe` below and double-click it**: no Python or setup needed.
It starts the app and opens the dashboard in your browser. Keep its window open while you use it;
close the window to stop.

### What's new in v1.5.6

- **"Confirm all as set" straight after confirming one change** no longer sends that change again: you'd
  see "That change has already been dealt with" and a "still need you to choose" count one too high
  (nothing was saved twice). Changes already on their way are left out.

### v1.5.5

- **The Sell Planner keeps some of each item for later**: "Keep for later" (next to "Undercut by") holds back part
  of every item in case its price goes up - half of each by default, or nothing, a quarter or three quarters. The
  plan sells the rest of every item first, so the amount is spread over more of them (say 30 of your 60 Fracture
  Cases and 20 of 40 Dreams & Nightmares rather than all of one), and only sells what's held back if the amount
  can't be reached otherwise. A single item can't be split, so it's held back too. Each row says how many you keep.
- **Kept items, in one place**: the items you've pressed Keep on (so the plan never sells them) are listed under
  "Kept items" above the plan, with a count - most recently kept first, how many, where they're stored and what
  they'd fetch. Unkeep brings one back, Unkeep all brings back the lot, and the message after pressing Keep has
  an Undo (Ctrl+Z works too). Before, the only way back was one link at the bottom that restored everything.

### v1.5.4

- **Much faster**: the dashboard reuses the valuation and its price history until something actually
  changes, checks saved prices for unrealistic listings several times faster, and answers its heavy views one
  after another rather than all at once. On a 400-item inventory everything a page asks for went from about
  8.4 to 1.4 seconds; the Sell Planner from 2.8 seconds to under half a second, an item's details from
  0.7 to 0.1; switching "Values from" no longer starts from scratch.
- **24-hour and 7-week changes compare like with like**: a day a market wasn't checked (CSFloat is asked one
  item at a time, and sometimes not at all on a day it asks the app to slow down) now keeps that market's last
  price, for up to 3 days, instead of the day's "best price" being whichever market happened to be checked.
  So an item no longer shows, say, +5% with every market counted and -20% with cash-out markets only when its
  best market (CSFloat) barely moved. A market that had stopped listing the item isn't carried over.
- **Selling prices you can actually get**: a market's cheapest listing is only an asking price, and on thinly
  traded items it's often far above what anyone pays - say a capsule whose cheapest Market.CSGO listing is
  three times what it sells for on every other market. The check that ignores such listings now compares each
  one with what the item goes for elsewhere (its other markets and recent sales together), sets aside listings
  more than twice that, and works for cheap items too (it used to need $1 above, so items under a dollar or two
  were never checked). Steam keeps its wider margin (its price is real sales, in wallet money), and a listing
  that matches what the item recently sold for on that same market is always kept. Totals saved on earlier days
  are corrected once, the same way.
- **Notifications when an update starts**: when the scheduled background update, or the open app's own
  automatic update, starts collecting prices, a Windows notification says so; another says what it found when
  it's done (now for the open app's automatic updates too).
- **Up-to-date exchange rates**: rates are checked again with every price update (at most every half hour)
  instead of once a day, and when the ECB's latest are more than a day old the newer source is used.
- **Old tournament capsules are Cases & Capsules**: items you add by hand are now checked against the item
  catalogue's list of every container, so ones whose names don't say what they are - "MLG Columbus 2016
  Challengers (Holo-Foil)", "EMS Katowice 2014 Legends", gift packages, music kit boxes - are filed properly
  (ones you added before are moved over once). Steam items already used Steam's own item type.
- **"Show data table" columns are evenly spaced**: every number column is the same width, whatever its
  heading, and the table still fits its card - each table works out the room it needs from what it shows.
  Below 1240px wide, Biggest movers and Portfolio mix sit one above the other (they did below 1100px), so the
  mix's table has room for a column per group.

### v1.5.3

- **Its own window and icon**: the app now opens a small window of its own, with its icon on the taskbar,
  instead of a console window (which Windows 11 showed as Windows Terminal). It says what the app is doing
  (prices updated 5 minutes ago, or a refresh's progress) and has **Open dashboard** and **Stop** buttons.
  Closing it stops the app, as before. The scheduled background update no longer flashes a window up either.
- **Choose what the Sell Planner sells**: filters above the slider, the same as your inventory's - search,
  kinds of item, Filter by event, collection, type, weapon, rarity, wear or container, and stored items - plus
  the markets to sell on. Markets you can't withdraw money from (Steam, BUFF163) are listed too, switched on
  with their own tick box (replacing "Count Steam Wallet sales").
- **The slider shows what your filters can raise**: it always runs to everything you could sell on the
  chosen markets; the part your filtered items can raise is blue, with a marker where it ends (drag close to
  it and it snaps there). The amount you chose stays when you change filters: if it's more than they can
  raise, the slider stays put, the gap is striped, and a note says the most you can get with those filters.
- The Sell Planner answers instantly as you drag or change anything, and works fully in the live demo.
- **Messages last longer and can't slip by**: messages in the corner now stay 15 seconds (20 for warnings
  and problems), with a bar along the bottom running down to when they go, and × to close one. Pointing at a
  message, or switching to another browser tab, pauses it; the same message again starts over rather than
  showing twice; they slide away when they go.
- **Recent messages**: the button in the bottom corner (or press H) lists every message from the last day,
  newest first, with how long ago each came ("25 seconds ago", "3 hours ago"). Its count shows warnings you
  haven't seen yet. Messages are kept for 24 hours in your browser.
- **Changes from Steam syncs slide away** when you confirm or dismiss them - one at a time or with "Confirm all
  as set" - or when a later sync settles them; new ones drop in, and the yellow box folds away once the last
  one goes (and comes back the same way after Undo). Counts on the Containers tab and the price-alerts bell
  pop when they change and shrink away at zero; deleted price alerts and Profit & Loss entries slide away too.
- **CS2's terminals are cases**: "Sealed Genesis Terminal", "Sealed Dead Hand Terminal" and the like now count
  as Cases & Capsules, including ones you added by hand before (moved over once, when you update).
- **Category counts follow the search**: with something typed in the search box, each category button's
  count is what you'd see picking it - so a button no longer says 8 and then shows nothing.
- A stacked row of separate entries you added (say, ones you typed in and ones a Steam sync moved in) says
  "2 entries" under its quantity, instead of a bare "×".
- The "Steam Market prices haven't changed" note now says they come from CSGOTrader's daily price file, and
  that the app keeps checking for a newer one.
- **"Show data table" fits its box**: under Portfolio mix and the other charts, the table's headers wrap and
  its cells are tighter, so it no longer scrolls sideways (on a phone-sized window it still can).
- **← [ and ] →** at the ends of the tabs: arrows show which way each goes (click them, or press [ or ]).
- **The Sell Planner undercuts for you**: it now assumes you list each copy a little under the market's price
  so yours sell first - 2% by default, or choose none, 1, 3, 5 or 10% ("Undercut by"), always at least 1p/1c
  under - and works out fees and what you raise from that lower price. The listing guide says what to list
  each item at as well as what you get.

### v1.5.2

- **Cases & capsules apart from storage units**: they're now separate categories ("Cases & Capsules" and
  "Storage Units") in the filters, counts and the Category column.
- **Details open instantly** from Biggest movers, Market Comparison, the Sell Planner and price alerts,
  even when the item is hidden by a filter (the window shows straight away and fills in).
- **Every window opens at the top**, not where the last one was scrolled to.
- **Hovering a column header lights the whole column** (tabbing to it with the keyboard too).
- **Sort arrows always sit just after the header text**, the same gap in every header and table (room
  for them is kept, so nothing moves when they appear); hovering a table's first or last header can no
  longer make it scroll sideways.
- **Profit & Loss entries open their details**: a purchase of something you still have opens its full
  details with what you paid, what it's worth now and the difference shown first; a sale (or a purchase
  of something you no longer have) shows the deal itself - prices, profit and your history with that
  item - without market data. Best / worst sales open too.
- **Scrolling lists never get stuck**: keep turning the mouse wheel over Biggest movers, the Containers
  tab's changes, the Profit & Loss list or a data table, and the list scrolls all the way to its top or
  bottom, rests there for a moment, then - if you're still turning - the page carries on, with no need
  to stop or move the mouse first. Stop at the end and the page stays put; scroll again and it moves
  straight away. Lists inside a window carry on into the window, never the page behind it, and
  scrolling the page past a list doesn't get caught by the list.
- Scrolling while the page is still loading no longer gets jumped back to your last position.
- In narrow windows (about 860px and below) a long item name in Biggest movers no longer makes the whole
  page scroll sideways; it's shortened with "…" instead.
- **The page behind a window stays still**: scrolling past the top or bottom of a window (or over the
  dimmed background) no longer scrolls the page underneath.
- Clicking filters, the Market Comparison list choice or the planner amount quickly can no longer show
  an older answer over the newer one; switching items quickly can't draw the last item's price history.

### v1.5.1

- **Tables fit the window**: a container's contents (and the inventory, Market Comparison and item
  details) no longer need sideways scrolling - long notes and market names wrap instead, and cells
  tighten up in narrower windows. If a window is too narrow for a table anyway, its sideways scrollbar
  stays pinned to the bottom of the window, so you don't have to scroll down to the end to reach it.
- Column headers no longer slide over the rows of a table that scrolls sideways, and the header copy
  that stays under the tabs as you scroll down now actually shows.
- **Biggest movers** open an item's details even when a filter or search hides it in the list below.
- **[ and ]** at either end of the tabs (the previous / next tab keys) - click them too.
- Items in containers say "In Long Holds" rather than "In Storage Unit “Long Holds”".
- Profit & Loss's best / worst sales no longer look clickable.

### v1.5.0

- **Values from: All markets / Cash-out markets only.** A switch above the figures (or press W). Steam
  often pays most, but into your Steam Wallet, which can't be withdrawn. With "Cash-out markets only",
  every value, total, chart, 24h / 7-week change, mover and price alert counts only markets you can
  withdraw money from. Steam is still shown: in an item's details it's listed last (marked ⤓) and its
  price line is greyed; in the tables it's greyed with "not counted". Items only Steam has a price for
  are left out of totals. The tiles always show the other figure too. It starts on "All markets".
- **Striped best-price line** in an item's details: blue, striped with the colour of the market paying
  most on each day, so you can see when a different market took over.
- Table headers in an item's details no longer float over the rows when you scroll down.

### v1.4.0

- **Steam prices without asking Steam.** Steam Market prices now come from CSGOTrader's free daily price
  file (what each item sold for on average over the last day, or week/month for items that sell rarely),
  downloaded at most every few hours and only when it has changed. The app no longer sends Steam any
  price requests, so it can't be rate-limited or blocked by Steam, and Steam prices show up straight away.
- **Undo.** After removing or moving items, deleting a container, confirming or dismissing a change from a
  Steam sync, or deleting a Profit & Loss entry, the message has an **Undo** button (or press Ctrl+Z).
- **Several items at once**: tick items in a container (Shift+click ticks a run) and move or remove them
  together.
- **Price alerts**: from any item's details, "tell me when it sells for above / below £X" (after fees).
  You're told in the dashboard and with a Windows notification after a price refresh; the bell at the top
  lists your alerts and counts the ones that went off.
- **Choose your columns** in the inventory tables and container contents (the Columns button); each table
  remembers its own.
- **Profit & Loss export** to CSV.
- **Keyboard shortcuts**: 1-6 or [ and ] for tabs, / to search, R refresh, S sync, A add an item, N new
  container, E export, B alerts, Ctrl+Z undo, Esc to close. Every button with a shortcut shows its key, and
  ? lists them all.
- **Each tab remembers where you'd scrolled to**, even after reloading.
- Changes to items in an open container show up faster.

### v1.3.0

- **Smoother to use**: toggles have a highlight that slides to your choice, the tab underline slides
  between tabs, pop-ups and tabs fade in, confirmed changes slide away, and buttons and filters respond
  as you click them. Switching toggles (Selling / Buying, the Profit & Loss views, the Sell Planner's
  wallet option) no longer makes the page jump.
- **Column headers**: hovering one highlights it without moving any column or re-wrapping any text, and
  sort arrows no longer change a column's width.
- **One row per market** in an item's details: a market whose only listing is unrealistic now says so
  once ("Ignored: its lowest listing (£0.78) is more than 3 times what it has recently sold for…"),
  linking to that listing, instead of also saying it isn't listed there. Market Comparison marks those
  as "ignored" (hover for why) rather than "—".
- The market table in an item's details has evenly spaced number columns, with fees on two lines.
- **Market Comparison**: click any row to see that item's details.
- **Changes from Steam syncs**: what you choose or type on each row is kept when you confirm another
  one (and if you reload), and "Confirm all as set" confirms every row exactly as you've set it.

### v1.2.2

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
