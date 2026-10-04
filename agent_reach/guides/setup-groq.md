# Groq Whisper Setup Guide

## Overview
When a YouTube/Bilibili video has no subtitles, use Groq's Whisper API for speech-to-text. Groq offers a free tier.

## Steps the agent can complete automatically

1. Check whether it is already configured:
```bash
agent-reach doctor | grep -i "groq\|whisper"
```

2. If the user provided a key, write it to the config:
```python
from agent_reach.config import Config
c = Config()
c.set("groq_api_key", "KEY_FROM_USER")
```

3. Test (optional):
```bash
curl -s https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer KEY_FROM_USER" \
  -o /dev/null -w "%{http_code}"
```
A response of 200 means it works.

## Steps the user must do manually

Tell the user:

> Video speech-to-text needs a Groq API Key (free).
>
> Steps:
> 1. Open https://console.groq.com
> 2. Sign up with a Google account or email
> 3. Click "API Keys" in the left sidebar
> 4. Click "Create API Key"
> 5. Copy the generated key and send it to me
>
> Groq's free tier is more than enough for everyday use.

## What the agent does after receiving the key

1. Write the config: `config.set("groq_api_key", key)`
2. Test that the API works
3. Reply: "✅ Speech-to-text is on! From now on I can extract content from videos that have no subtitles too."
