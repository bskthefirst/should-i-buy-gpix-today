# Business revenue dashboard

`business.html` records one after-tax revenue amount per month and currency. USD and KRW totals are separate. Empty months are missing observations, not zero revenue. The page calculates revenue changes, year-to-date totals, averages, quarterly totals, and a monthly amount multiplied by 12. It does not calculate tax, profit, expenses, or exchange rates.

The browser saves entries in `business-records-v1`. The selected month and currency are in `business-view-v1`. A private JSON backup includes all entered months in both currencies.

Connected browsers reuse the existing `retire-sync-v1` connection. The authenticated `/v1/business` API stores a separate `business.json` beside the private retirement file. Neither business records nor keys are published in the website source. Business records do not appear in the retirement Telegram digest.

Deploy `business-data.js`, `business_service.cjs`, and the updated `retirement_sync_server.cjs` to the private service directory. Use `docs/business-data.js` for the deployed module. The repository's `scripts/business-data.js` is only a development wrapper. Keep the directory private and files at mode 600. Restart the existing sync LaunchAgent after deployment. No connection key or proxy change is required.

The API rejects stale revisions. The browser merges edits to different months. Conflicting amounts for the same month require a choice. Offline entries persist and retry. Open pages check every 30 seconds and when returning to the foreground.

Run `node --test scripts/test_business.cjs scripts/test_retire.cjs scripts/test_retirement_journal.cjs scripts/test_retirement_sync.cjs` to verify calculations, persistence, authentication, isolated files, and conflicting updates.
