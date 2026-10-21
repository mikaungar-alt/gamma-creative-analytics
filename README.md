# Gamma Creative Analytics

Live Meta creative analytics for the editors. Every night a GitHub Action pulls 90 days of ad-level data
with a read-only Meta system user, encrypts it with the team password, and publishes it to GitHub Pages.
The page decrypts it in the browser.

- Editor rules, accounts and series names: `config.json`
- Data pull: `fetch.py`
- Page: `site/index.html`
- Secrets: `META_TOKEN`, `DASH_PASSWORD`
