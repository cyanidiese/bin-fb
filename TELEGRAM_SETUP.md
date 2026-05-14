# Telegram Bot Setup

## Step 1 — Create a bot via BotFather

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`.
3. Choose a name (e.g. "My Trading Bot Alerts") and a username ending in `bot` (e.g. `mytrading_alerts_bot`).
4. BotFather replies with your **bot token** — a string like `7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Copy it.

## Step 2 — Find your Chat ID

1. Start a conversation with your new bot: search for its username and click Start.
2. Send any message to the bot (e.g. "hello").
3. Open this URL in your browser (replace `<TOKEN>` with your bot token):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
4. Find `"chat": { "id": 123456789 }` in the response. That number is your **chat ID**.

## Step 3 — Enter in dashboard

1. Open the dashboard → Settings page → Telegram Alerts section.
2. Paste your bot token and chat ID.
3. Click **Send test notification**.
4. You should receive a message from your bot.

## Troubleshooting

- **No getUpdates response?** Make sure you sent a message to the bot first.
- **401 Unauthorized?** Double-check the token — include the full string including the colon.
- **Chat not found?** Ensure you sent the bot a message before calling getUpdates.
