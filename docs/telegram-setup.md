# Telegram dividend journey

Connected browsers and the existing OpenClaw bot use one private record on the Mac mini. Unconnected browsers save their own copies. Neither reads a brokerage account.

## Automatic sync

Connected browsers and the existing Telegram bot share the private `plan.json` on the Mac mini.
The public GitHub Pages website contains no holdings or connection key.

1. Use `/retire_connect` in the bot's private chat to get your private connection link.
2. Open that link once on each browser.
3. If the browser has different numbers, choose the copy you want to keep. An empty browser adopts the shared record.

The bot also sends a connection file. It can be imported under **Connection settings** as an alternative.

After connecting, enter total shares and average purchase prices as usual. Leaving the fields records the change and saves it automatically. You do not need to copy Telegram commands. The weekly digest reads the same private file.

Visible browsers check for changes every 30 seconds and when the page returns to the foreground. Offline edits remain in browser storage and retry automatically. If another device edited the record before a waiting update arrives, the page preserves the draft and asks which copy to keep. Choosing the shared copy saves a browser backup and adopts its history. A rejected purchase stays as a separate draft for review. A device which has never connected still has only its browser copy. Existing mobile data does not reach the server until that mobile browser connects.

The sync includes share counts, average prices, tax, income goal, expenses, forecast settings, and history. It does not read brokerage trades. It does not create extra Telegram notifications.

### Record a purchase

Use **Record a purchase** after buying shares. Enter the additional shares, price per share, purchase date, and optional fees. The preview shows the new total and weighted average purchase price. Saving adds a dated purchase record.

An existing holding must have its average purchase price entered first. Fractional shares are preserved. Connected browsers must finish syncing before recording a purchase. An unconnected browser can save locally.

If two devices record purchases at the same time, the page preserves the rejected purchase as a draft. Use the latest Mac mini totals, review the draft, and save again. Its original ID prevents duplicate application. The service validates purchase chains before accepting updates.

### Mac mini service

Copy `retirement_sync_server.cjs`, `retirement_service.cjs`, and `retirement-journal.js` to the private service directory. Create `sync.json` with a randomly generated 256-bit base64url key, `planPath`, `endpoint`, `origin`, and optional `port`. Restrict the directory to mode 700 and files to mode 600.

The Node server binds only to `127.0.0.1:8787`. A dedicated HTTPS proxy must route to that port. The API exposes only `/v1/plan`. It requires a bearer key, accepts the configured website origin, validates inputs, and rejects stale revisions. It never serves plan files, keys, bot configuration, or other applications.

A macOS LaunchAgent runs the Node service at login and restarts it after failure. HTTPS routing must also persist across restarts. Tailscale Serve works within the private network. [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) makes the dedicated HTTPS port reachable by browsers which do not have Tailscale. Funnel requires the tailnet administrator to enable it. Use a separate port so existing services remain unchanged.

The connection file contains only the HTTPS endpoint and bearer key. It grants read and write access to the private retirement record. Deliver it only through the owner’s private bot chat. The connection link carries the key in a URL fragment, which is removed immediately and is not sent to GitHub. The page accepts only the configured Mac mini endpoint. Open the link once on each browser. **Disconnect this browser** removes that browser’s connection, while its local holdings remain. Rotate the server key to revoke all existing connection files.

## Telegram commands and backups

```text
/holdings GPIX 57.71 54.70 GPIQ 3 56.43
/expense Claude 20
/dividends
/retire_history
/retire_export
/retire_connect
```

Each fund has two numbers: total shares currently owned and average purchase price in USD. `/holdings` replaces totals. It does not add the entered shares to the previous total. Connected browsers receive the changed totals automatically. Use actual brokerage figures.

`/retire_export` sends a private JSON file containing holdings and the complete history. **Download holdings backup** saves the browser record. Manual copy and import controls remain under **Backups and manual transfer** for browsers without a connection. Applying a backup replaces current values and merges histories.

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
