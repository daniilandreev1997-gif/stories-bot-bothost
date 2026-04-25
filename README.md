# stories-bot-bothost

Deploy URL for Bothost:

`https://github.com/daniilandreev1997-gif/stories-bot-bothost.git`

## Required env vars
- `API_TOKEN` (Telegram bot token)
- `VK_TOKEN` (optional fallback for VK requests)

## TikTok sync behavior
- First full sync: exactly 1 post every 5 minutes
- After first full sync: only new posts

## Start
`python runner.py`
