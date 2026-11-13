**Download `CS2-Inventory-Intelligence.exe` below and double-click it**: no Python or setup needed.
It starts the app and opens the dashboard in your browser. Keep its window open while you use it;
close the window to stop.

- Your data and settings are kept in `%LOCALAPPDATA%\CS2 Inventory Intelligence` (put an optional
  `.env` file there, e.g. for a CSFloat API key - see the README).
- Windows may say *"Windows protected your PC"* because the app isn't code-signed: click
  **More info → Run anyway**. The file is built automatically by GitHub Actions from this
  release's source code (see the "Windows app" workflow).
- Background daily updates: run `scripts\install-daily-update.ps1 -Exe "<path to the .exe>"`
  from the source code (see the README).

Try it first without installing anything: [live demo](https://illyahanslip.github.io/cs2-inventory-tracker/).
