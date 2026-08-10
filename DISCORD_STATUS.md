# Discord server-status panel (BETA 1)

The BETA backend exposes live metrics at:

`http://127.0.0.1:8099/local/beta/metrics`

`tools/discord_status_publisher.py` can publish those values as one persistent Discord webhook embed and update the same message every 60 seconds.

1. Create a webhook in the Discord channel where the status panel should live.
2. Set the webhook URL in your own Windows environment (do not put it in the ZIP or database):
   `set FIFA14_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`
3. Launch `RUN_FIFA14_LOCAL_BETA.cmd`. When the environment variable is present, the BETA starts the publisher automatically. You can also run it manually with:
   `.venv\Scripts\python.exe tools\discord_status_publisher.py`

The webhook publisher stores only the Discord message ID in your user profile so subsequent updates edit the existing panel. It does not store the webhook URL in the FUT database.

Discord **account authentication/linking** is a separate BETA milestone. The account schema already has immutable Discord-user-ID fields, but BETA 1 deliberately does not pretend manual IDs are secure authentication; OAuth/device-style linking will be enabled once the Discord application client ID/redirect URI are configured.
