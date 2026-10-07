# Telegram dividend journey

The existing OpenClaw bot keeps a private holdings file on its host. The website keeps a separate browser copy. Neither reads a brokerage account. Transfers between the two copies are manual.

## Update from the website

1. Enter total shares and your brokerage's average purchase prices in **Already invested**.
2. Click **Record holdings update** to preserve a dated browser snapshot.
3. Expand **Use these holdings for Telegram updates**.
4. Click **Copy update for Telegram**.
5. Paste the copied command into your connected bot's private chat.
6. Check the bot's confirmation of the saved totals.

The command includes shares, average prices, tax, income goal, costs, and the chosen expense. It updates the bot's private plan and adds a dated report when holdings change. It preserves the bot's existing history. It does not transfer the browser's entire history. **Download holdings backup** exports the full browser record, including purchase prices, for private backup.

## Update from Telegram

```text
/holdings GPIX 57.71 54.70 GPIQ 3 56.43
```

Each fund has two numbers: total shares currently owned and average purchase price in USD. The command replaces totals. It does not add the entered shares to the previous total. Use actual brokerage figures.

```text
/expense Claude 20
/dividends
/retire_history
/retire_export
```

`/expense` chooses one expense and its monthly USD cost. `/dividends` shows current estimates. `/retire_history` shows the latest reported changes. `/retire_export` sends a private JSON file containing current holdings, average prices, the chosen expense, and the complete history.

To update the website, download that file from Telegram. Choose **Import from Telegram** on the website. Review the incoming values. Click **Apply this file** to replace current values and merge both histories. The file does not import automatically.

## What the record means

- Each snapshot preserves total shares and average purchase prices at the time reported.
- A share increase can reflect a purchase, reinvestment, transfer, or correction. It is labeled a reported change.
- The service does not infer trade prices or execution dates from average prices.
- Milestone dates record the first observation of an income estimate. Milestones present at the starting point are labeled as baseline observations.
- A later decline does not erase a milestone memory or create another celebration on recovery.
- Income changes are split into share-count, payout-per-share, and tax-rate effects. Purchase prices do not affect dividends.
- The weekly digest compares the previous weekly report. The website compares its recent holdings reports. Each names its comparison date.

## Install on an OpenClaw host

This project includes a native command plugin in `scripts/openclaw-retirement`. It uses [OpenClaw custom commands](https://docs.openclaw.ai/plugins/sdk-overview/tools-and-commands), which bypass model interpretation.

Copy `scripts/retirement_service.cjs` and `docs/retirement-journal.js` into a private directory. Put a validated exported plan in that directory as `plan.json`. Restrict the directory to your user and files to mode 600. No bot token belongs in this project.

Install the local plugin with the host's supported plugin installation command. Configure its entry with `ownerId`, `planPath`, `servicePath`, and `nodePath`. Limit `ownerId` to the verified private Telegram user. The handler also checks the Telegram account and private destination. Exported files are copied into OpenClaw's private media directory for delivery.

Run the service once with action `baseline` before enabling the weekly job. That establishes a comparison and marks existing milestones without sending a message.

```sh
node /private/path/retirement_service.cjs /private/path/plan.json baseline
```

Configure one existing OpenClaw command job with the argv array below. Use the host's verified Telegram account and private chat destination. Inspect existing jobs to avoid duplicates.

```json
["/path/to/node", "/private/path/retirement_service.cjs", "/private/path/plan.json", "weekly"]
```

The standard schedule is Sundays at 7 p.m. Korea time, using timezone `Asia/Seoul`. There are no separate milestone notifications. On-demand commands reply only when requested. Disable the OpenClaw job to stop the digest.

The service refreshes the public GPIX and GPIQ feeds on each estimate. It rejects stale data, incomplete payouts for owned funds, and reported source failures. A holdings command can still save reported shares during an outage. It does not award income milestones without usable data.

The older Python digest remains available for simple exports and milestone-only deployments. The connected journey uses the shared Node service.
