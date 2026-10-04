# Video / Podcasts

Subtitles and transcripts for YouTube, Bilibili and Xiaoyuzhou podcasts.

## YouTube (yt-dlp)

### Get video metadata

```bash
yt-dlp --dump-json "URL"
```

### Download subtitles

```bash
# Download subtitles (without downloading the video)
yt-dlp --write-sub --write-auto-sub --sub-lang "zh-Hans,zh,en" --skip-download -o "/tmp/%(id)s" "URL"

# Then read the .vtt file
cat /tmp/VIDEO_ID.*.vtt
```

### Get comments

```bash
# Extract comments (best-effort, completeness not guaranteed)
yt-dlp --write-comments --skip-download --write-info-json \
  --extractor-args "youtube:max_comments=20" \
  -o "/tmp/%(id)s" "URL"
# Comments are in the comments field of the .info.json
```

### Search videos

```bash
yt-dlp --dump-json "ytsearch5:query"
```

> **Subtitle note**: manually uploaded subtitles extract reliably; auto-generated subtitles may contain repeated lines and need post-processing.
> **Comment note**: `--write-comments` is based on web scraping (not the YouTube Data API), so some comments may be missing.

### Retry chain when subtitles fail (run in order, stop once you get substantive content)

`doctor` only confirms that yt-dlp itself and the JS runtime can execute; it does not request a specific video. So
`active_backend: yt-dlp` does not mean the target video's subtitles have passed a live check.

1. Start with the `yt-dlp --write-sub --write-auto-sub` command above.
2. If you hit a bot check, the subtitle response is empty, or no subtitle file is produced, and OpenCLI is connected:
   `opencli youtube transcript "URL" -f yaml`.
3. If OpenCLI returns `Caption URL returned empty response`, retry at most 3 times; this is an occasional failure of the
   expiring caption URL, and an empty response must not be read as "the video has no subtitles".
4. If it still fails, or the video really has no subtitles: `agent-reach transcribe "URL"` downloads the audio and transcribes it.

The success criterion is actually obtaining non-empty subtitle/transcript content, not a command exit code or `doctor`'s version probe.

### Fallback without subtitles: Whisper audio transcription

```bash
# Fallback when a video has no subtitles: download the audio and transcribe it with Whisper (a free Groq key is enough)
agent-reach transcribe "https://www.youtube.com/watch?v=VIDEO_ID"
agent-reach transcribe ./local_audio.mp3 -o /tmp/transcript.txt
```

> `agent-reach transcribe` accepts only public http(s) URLs or local audio files. When searching with `ytsearch5:`, pick a specific video URL from the yt-dlp results first, then transcribe it.
> A key must be configured first: `agent-reach configure groq-key` (hidden input; free, console.groq.com)
> or `agent-reach configure openai-key`. The default auto mode uses only the first configured provider
> (Groq preferred, otherwise OpenAI) and stops on failure; it never sends the audio to another provider automatically.
> `--allow-provider-fallback` explicitly authorises falling back across providers; the same audio may be processed by both Groq and
> OpenAI and may incur OpenAI charges, so use it only after confirming the content can be shared with both.

## Bilibili (bili-cli first, OpenCLI for subtitles)

> ⚠️ **Do not use yt-dlp for Bilibili**: Bilibili's risk control now blocks yt-dlp everywhere with HTTP 412 (tested with the latest version, direct/proxy/with cookies, all ineffective). Use yt-dlp for YouTube only.

### Video details / search / trending / rankings (bili-cli, read-only, no login needed)

```bash
# Video details (title / uploader / duration / play and engagement stats / subtitle availability)
bili video BVxxx

# Search videos
bili search "query" --type video -n 5

# Trending videos / rankings
bili hot -n 10
bili rank -n 10

# Download audio and split into ASR-ready WAV (use with agent-reach transcribe when there are no subtitles)
bili audio BVxxx
```

### Subtitles (OpenCLI, needs desktop Chrome)

```bash
# Subtitles line by line with timestamps
opencli bilibili subtitle BVxxx

# OpenCLI can also search / read video metadata (alternative)
opencli bilibili search "query" -f yaml
opencli bilibili video BVxxx -f yaml
```

### Zero-config fallback: call the search API directly

```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
curl -s -c /tmp/bili_ck.txt -o /dev/null -A "$UA" "https://www.bilibili.com/"
curl -s -b /tmp/bili_ck.txt -A "$UA" -e "https://www.bilibili.com/" \
  "https://api.bilibili.com/x/web-interface/search/all/v2?keyword=QUERY&page=1"
```

> **Install bili-cli**: `pipx install bilibili-cli` (upstream has been unmaintained since 2026-03 but tested healthy; read-only use needs no login, and `bili login` with a QR scan unlocks personal features such as feeds and favourites).

## Xiaoyuzhou Podcast

### Transcribe a single podcast episode (optional --polish for better punctuation)

```bash
# Writes a Markdown file to /tmp/. --polish has Llama 3.3 70B add Chinese punctuation and sensible paragraph breaks to the transcript
~/.agent-reach/tools/xiaoyuzhou/transcribe.sh --polish "https://www.xiaoyuzhoufm.com/episode/EPISODE_ID"
```

> The transcription prompt already asks Whisper to output Chinese punctuation; if the punctuation is still unsatisfactory, add `--polish` to use the free Llama 3.3 70B on Groq to fix punctuation and paragraphing (about 7 extra seconds for a 9-minute podcast). Each transcription costs one extra LLM call, so use it as needed.

### Prerequisites

1. **ffmpeg**: `brew install ffmpeg`
2. **Groq API Key** (free): https://console.groq.com/keys
3. **Configure the key**: `agent-reach configure groq-key` (hidden input)
4. **First run**: `agent-reach install --env=auto --system --channels=xiaoyuzhou` (requires explicit user authorisation)

### Check status

```bash
agent-reach doctor
```

> The output Markdown file is saved to `/tmp/` by default.

## Choosing a tool

| Scenario | Recommended tool |
|-----|---------|
| YouTube subtitles | yt-dlp; on failure OpenCLI (up to 3 tries) → agent-reach transcribe |
| Bilibili video details / search | bili-cli |
| Bilibili subtitles | opencli bilibili subtitle |
| Podcast transcription | Xiaoyuzhou transcribe.sh |
| Audio/video without subtitles | agent-reach transcribe (for Bilibili audio, run `bili audio` first) |
