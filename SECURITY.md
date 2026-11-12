# Security & safety

## What this app will never ask for

CS2 Inventory Intelligence runs only on your own computer and only **reads** prices and
your public inventory. It will never ask for, and has no use for:

- your Steam password or Steam Guard codes
- Steam login cookies or "session" data
- permission to trade, buy, sell or move items
- a Steam Web API key (the app does not use one at all)
- your login or cookies for any marketplace (e.g. BUFF163)

**If anyone - a website, a Discord message, a "support" account or a modified copy of this
tool - asks you for any of the above in this project's name, it is a scam.** This project has
no website, no login page, no Discord server and no support staff.

## Your API key

The only key the app can use is an optional CSFloat API key. It goes in a file called `.env`
on your own computer. That file is listed in `.gitignore`, so it is never uploaded; the app
sends the key only to `csfloat.com`, and never to your browser.

- Create the key directly on `csfloat.com` (Profile → Developers). Type the address yourself
  instead of following a link.
- **Never create a Steam Web API key for this tool** - it doesn't need one, and fake "API key"
  pages are a common way to steal items. If `steamcommunity.com/dev/apikey` ever shows a key
  you did not create, revoke it immediately and change your Steam password.
- If a key is ever exposed, revoke it on the site that issued it and create a new one.
  Deleting it from a file or from git history is not enough.

## Contributing safely

Before committing, turn on the privacy check that blocks keys, real SteamIDs, e-mail
addresses, personal paths and private files:

```
git config core.hooksPath .githooks
python scripts/check_secrets.py
```

## Reporting a vulnerability

Please report security problems privately through the repository's **Security** tab →
**Report a vulnerability**, rather than opening a public issue.
