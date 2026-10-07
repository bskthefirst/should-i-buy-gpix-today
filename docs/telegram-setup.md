# Optional Telegram dividend updates

The website does not send Telegram messages. An existing OpenClaw Gateway runs the scheduled job and delivers the digest to your private Telegram chat. The website's holdings file stays on your device until you share it.

1. Open **Already invested** on the retirement page.
2. Expand **Use these holdings for Telegram updates**.
3. Click **Download holdings for Telegram**.
4. Put `retirement-telegram-plan.json` on the machine running OpenClaw, outside any public web directory or Git repository.
5. Give that file read permissions only for your user account.
6. Copy `scripts/retirement_digest.py` to that machine.
7. Preview the digest with `python3 scripts/retirement_digest.py --plan /private/path/retirement-telegram-plan.json`.

The digest uses current public GPIX and GPIQ snapshots. It calculates the latest monthly payout estimate and the past year's average after your selected tax rate. Milestones use the past-year average, matching the website. The script rejects missing payouts and data more than four days old. It does not read your brokerage account or infer purchases.

## Connect the existing OpenClaw bot

First identify the private Telegram chat ID and your Gateway version. Run `openclaw automations --help` on the Gateway machine. Inspect existing jobs before adding a new one, so you do not create duplicate reminders.

OpenClaw supports scheduled command jobs with Telegram announcement delivery. [OpenClaw automation documentation](https://docs.openclaw.ai/cli/cron).

This template sends one digest each Sunday at 7 p.m. Korea time. Replace both paths and `YOUR_PRIVATE_CHAT_ID` before running it. Use the schedule the owner selects.

```sh
openclaw automations create '0 19 * * 0' \
  --name 'Dividend progress' \
  --tz Asia/Seoul \
  --command-argv '["python3","/path/to/retirement_digest.py","--plan","/private/path/retirement-telegram-plan.json"]' \
  --announce --channel telegram --to 'YOUR_PRIVATE_CHAT_ID'
```

For a monthly digest, use `0 19 1 * *`. For milestone-only delivery, add `--milestones-only` and `--state /private/path/dividend-milestones.json` to the command arguments. That mode sets a silent baseline on the first run. Later runs announce newly reached milestones once and print `NO_REPLY` otherwise. A lower estimate and later recovery do not send the same milestone again. Schedule the checks no more often than once daily.

Test one delivery after the chat destination is verified. Confirm the job appears in the Gateway's job list. Telegram is connected only after the test message arrives.

Download and replace the private plan file after you change your holdings. This is a manual update: the bot cannot read changes saved only in your browser. Purchase prices and bot credentials are not part of the export. Disable the Gateway job to stop reminders.
