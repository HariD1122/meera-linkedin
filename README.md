# meera-linkedin

Telegram-to-LinkedIn pipeline written in the voice of Meera Pillai (Skinstinct), driven by the
[`meera-skinstinct-voice`](skill/SKILL.md) skill bundled in this repo.

## How it works

1. A message is sent to the Telegram bot (any chat it can see).
2. Telegram pushes the update to `POST /api/webhook` (a Vercel serverless function), authenticated
   via the `X-Telegram-Bot-Api-Secret-Token` header.
3. Gemini scores the message 0-10 for relevance to the skill.
   - Score <= 5: sends "Sorry the info is not relevant" to the configured channel.
   - Score >= 6: drafts a LinkedIn post in Meera's voice, generates a matching 9:16 image (via a
     visual-concept step grounded strictly in the draft's own content), and sends both directly to
     the channel. No human review step -- this is intentional, see the commit history / conversation
     that built this for the tradeoffs.

## Local development

`main.py` runs the same pipeline via long-polling instead of a webhook, useful for local testing
without a public URL:

```
python main.py listen --wait 120
```

Requires a local `.env` with `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `GEMINI_API_KEY` (see
`.env` -- gitignored, never committed).

## Deployment

Deployed on Vercel as `api/webhook.py`, using a single-entrypoint Python runtime configured in
`pyproject.toml`. Required production environment variables (set in the Vercel dashboard, not in
this repo): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `GEMINI_API_KEY`,
`TELEGRAM_WEBHOOK_SECRET`.

After deploying, point the bot's webhook at the deployment:

```
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://<deployment>/api/webhook" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```
