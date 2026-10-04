#!/bin/bash
# Xiaoyuzhou (xiaoyuzhoufm.com) podcast transcription script
# Usage: bash transcribe.sh [--polish] <xiaoyuzhou-url> [output-file]
# Environment: GROQ_API_KEY (required)
#
# --polish: after transcribing, call Groq Llama 3.3 70B to add Chinese punctuation and
#           sensible paragraph breaks (Whisper is weak at Chinese punctuation, so this
#           makes the transcript much easier to read)

set -e

POLISH=0
while [ $# -gt 0 ]; do
    case "$1" in
        --polish) POLISH=1; shift ;;
        --) shift; break ;;
        -h|--help)
            echo "Usage: bash transcribe.sh [--polish] <xiaoyuzhou-url> [output-file]"
            exit 0 ;;
        --*)
            echo "Unknown option: $1" >&2
            exit 1 ;;
        *) break ;;
    esac
done

URL="${1:?Usage: bash transcribe.sh [--polish] <xiaoyuzhou-url> [output-file]}"
OUTPUT="${2:-}"

PYTHON_CMD=()
ensure_python() {
    if [ "${#PYTHON_CMD[@]}" -gt 0 ]; then
        return 0
    fi
    # Proving the name exists is not enough: the candidate must also run.
    # On Windows `command -v python3` often resolves to the Microsoft Store App
    # Execution Alias (WindowsApps/python3.exe, exit code 49). It exists but
    # cannot run, so every later Python call would fail and the URL check would
    # misreport valid links as unsupported hosts.
    if command -v python3 >/dev/null 2>&1 && python3 -c "" >/dev/null 2>&1; then
        PYTHON_CMD=(python3)
    elif command -v python >/dev/null 2>&1 && python -c "" >/dev/null 2>&1; then
        PYTHON_CMD=(python)
    elif command -v py >/dev/null 2>&1 && py -3 -c "" >/dev/null 2>&1; then
        PYTHON_CMD=(py -3)
    else
        echo "❌ No working Python found (tried python3, python, py -3)" >&2
        return 1
    fi
}

ensure_python || exit 1
if ! XIAOYUZHOU_URL="$URL" "${PYTHON_CMD[@]}" <<'PY'
import os
from urllib.parse import urlsplit

try:
    parsed = urlsplit(os.environ["XIAOYUZHOU_URL"])
    hostname = (parsed.hostname or "").lower()
except ValueError:
    raise SystemExit(1)

allowed_host = (
    hostname == "xiaoyuzhoufm.com"
    or hostname.endswith(".xiaoyuzhoufm.com")
)
raise SystemExit(0 if parsed.scheme.lower() in {"http", "https"} and allowed_host else 1)
PY
then
    echo "❌ Only http/https links on xiaoyuzhoufm.com and its subdomains are supported" >&2
    exit 1
fi

# Try env var first, then agent-reach config.yaml
if [ -z "$GROQ_API_KEY" ]; then
    CONFIG_FILE="$HOME/.agent-reach/config.yaml"
    if [ -f "$CONFIG_FILE" ]; then
        ensure_python || exit 1
        CONFIG_FOR_PYTHON="$CONFIG_FILE"
        if command -v cygpath >/dev/null 2>&1; then
            CONFIG_FOR_PYTHON=$(cygpath -w "$CONFIG_FILE")
        fi
        GROQ_API_KEY=$(AGENT_REACH_CONFIG_FILE="$CONFIG_FOR_PYTHON" \
            "${PYTHON_CMD[@]}" -c 'import os, yaml; print((yaml.safe_load(open(os.environ["AGENT_REACH_CONFIG_FILE"])) or {}).get("groq_api_key", ""))' \
            2>/dev/null || true)
    fi
fi
GROQ_API_KEY="${GROQ_API_KEY:?Set the GROQ_API_KEY environment variable or run agent-reach configure groq-key}"

# Groq API limit: 25MB per file
MAX_CHUNK_SIZE_MB=20
AUDIO_BITRATE="64k"
CURL_CONNECT_TIMEOUT=15
PAGE_TIMEOUT=60
AUDIO_TIMEOUT=1800
GROQ_TIMEOUT=600
MAX_PAGE_BYTES=5242880
MAX_AUDIO_BYTES=1073741824
MAX_API_RESPONSE_BYTES=33554432
MAX_DURATION_SECONDS=10800

TEMP_ROOT="${TMPDIR:-/tmp}"
if ! WORK_DIR=$(mktemp -d "${TEMP_ROOT%/}/agent-reach-xiaoyuzhou.XXXXXX"); then
    echo "❌ Could not create a temporary directory" >&2
    exit 1
fi

cleanup() {
    rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT

# Keep the Groq key out of argv (visible to every user via `ps`): curl reads the
# Authorization header from a 0600 config file inside the private work dir.
case "$GROQ_API_KEY" in
    *$'\n'*|*$'\r'*)
        echo "❌ GROQ_API_KEY contains a line break" >&2
        exit 1 ;;
esac
GROQ_CURL_CONFIG="$WORK_DIR/groq-auth.curlrc"
GROQ_KEY_ESCAPED=${GROQ_API_KEY//\\/\\\\}
GROQ_KEY_ESCAPED=${GROQ_KEY_ESCAPED//\"/\\\"}
(
    umask 077
    printf 'header = "Authorization: Bearer %s"\n' "$GROQ_KEY_ESCAPED" > "$GROQ_CURL_CONFIG"
)
unset GROQ_KEY_ESCAPED

# The Chinese Whisper prompt is functional: it asks for Mandarin output with full
# Chinese punctuation, which Whisper otherwise omits. It is passed to curl as
# -F "prompt=<file" so non-ASCII text never travels through argv (which breaks
# under GBK code pages on Windows, #720).
WHISPER_PROMPT_FILE="$WORK_DIR/whisper-prompt.txt"
printf '%s' "以下是一段中文普通话播客录音，请输出包含完整中文标点（，。？！：；“”‘’）的转写文本。" > "$WHISPER_PROMPT_FILE"

echo "📻 Xiaoyuzhou podcast transcription"
echo "===================="

# Step 1: extract the audio URL and title
echo "🔍 Parsing page..."
PAGE=$(curl --fail --show-error --location --silent \
    --connect-timeout "$CURL_CONNECT_TIMEOUT" \
    --max-time "$PAGE_TIMEOUT" \
    --max-filesize "$MAX_PAGE_BYTES" \
    "$URL")
AUDIO_URL=$(echo "$PAGE" | perl -ne 'while (/(https:\/\/media\.xyzcdn\.net\/[^"]*\.(?:m4a|mp3))/gi) { print "$1\n" }' | head -1)
TITLE=$(echo "$PAGE" | perl -ne 'if (/"title":"([^"]*)"/) { print "$1\n"; last }' | head -1)

if [ -z "$AUDIO_URL" ]; then
    echo "❌ Could not extract an audio link from the page"
    exit 1
fi

echo "📝 Title: $TITLE"
echo "🔗 Audio: $AUDIO_URL"

# Step 2: download the audio
echo "⬇️  Downloading audio..."
EXT="${AUDIO_URL##*.}"
curl --fail --show-error --location --silent \
    --connect-timeout "$CURL_CONNECT_TIMEOUT" \
    --max-time "$AUDIO_TIMEOUT" \
    --max-filesize "$MAX_AUDIO_BYTES" \
    -o "$WORK_DIR/original.$EXT" \
    "$AUDIO_URL"
FILE_SIZE=$(ls -lh "$WORK_DIR/original.$EXT" | awk '{print $5}')
echo "📦 File size: $FILE_SIZE"

# Step 3: get the duration
if ! DURATION_RAW=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 \
    "$WORK_DIR/original.$EXT" 2>/dev/null </dev/null); then
    echo "❌ ffprobe could not read the audio duration" >&2
    exit 1
fi
if DURATION=$(DURATION_RAW="$DURATION_RAW" MAX_DURATION_SECONDS="$MAX_DURATION_SECONDS" \
    "${PYTHON_CMD[@]}" -c '
import os
import sys
from decimal import Decimal, InvalidOperation

raw = os.environ["DURATION_RAW"]
try:
    value = Decimal(raw)
except InvalidOperation:
    raise SystemExit(2)
if not value.is_finite() or value < 0:
    raise SystemExit(2)
if value > Decimal(os.environ["MAX_DURATION_SECONDS"]):
    raise SystemExit(3)
print(int(value))
'); then
    :
else
    duration_status=$?
    if [ "$duration_status" -eq 3 ]; then
        echo "❌ Audio duration exceeds the 3-hour limit" >&2
    else
        echo "❌ ffprobe returned an invalid audio duration: ${DURATION_RAW:-<empty>}" >&2
    fi
    exit 1
fi
DURATION_MIN=$((DURATION / 60))
DURATION_SEC=$((DURATION % 60))
echo "⏱️  Duration: ${DURATION_MIN}m ${DURATION_SEC}s"

# Step 4: convert to low-bitrate mono MP3
echo "🔄 Transcoding..."
ffmpeg -nostdin -y -i "$WORK_DIR/original.$EXT" -t "$MAX_DURATION_SECONDS" -b:a "$AUDIO_BITRATE" -ac 1 "$WORK_DIR/mono.mp3" 2>/dev/null
MONO_SIZE=$(stat -c%s "$WORK_DIR/mono.mp3" 2>/dev/null || stat -f%z "$WORK_DIR/mono.mp3")
MONO_SIZE_MB=$(awk -v bytes="$MONO_SIZE" 'BEGIN { printf "%.1f", bytes / 1024 / 1024 }')
echo "📦 After transcoding: ${MONO_SIZE_MB}MB"

# Step 5: split into chunks by size
MAX_BYTES=$((MAX_CHUNK_SIZE_MB * 1024 * 1024))

if [ "$MONO_SIZE" -le "$MAX_BYTES" ]; then
    # No split needed
    cp "$WORK_DIR/mono.mp3" "$WORK_DIR/chunk_0.mp3"
    NUM_CHUNKS=1
    echo "📎 No split needed"
else
    # Work out how many chunks are needed
    NUM_CHUNKS=$(( (MONO_SIZE / MAX_BYTES) + 1 ))
    CHUNK_DURATION=$(( DURATION / NUM_CHUNKS + 10 ))  # add a 10-second buffer
    echo "✂️  Splitting into $NUM_CHUNKS chunks (about $((CHUNK_DURATION / 60)) minutes each)..."
    
    for i in $(seq 0 $((NUM_CHUNKS - 1))); do
        START=$((i * CHUNK_DURATION))
        ffmpeg -nostdin -y -i "$WORK_DIR/mono.mp3" -ss "$START" -t "$CHUNK_DURATION" -c copy "$WORK_DIR/chunk_${i}.mp3" 2>/dev/null
        CHUNK_SIZE=$(ls -lh "$WORK_DIR/chunk_${i}.mp3" | awk '{print $5}')
        echo "   Chunk $((i+1))/$NUM_CHUNKS: $CHUNK_SIZE"
    done
fi

# Step 6: transcribe with the Groq Whisper API
echo "🎙️  Transcribing (Groq Whisper large-v3)..."

for i in $(seq 0 $((NUM_CHUNKS - 1))); do
    echo -n "   Chunk $((i+1))/$NUM_CHUNKS... "
    
    RESPONSE=$(curl --silent --show-error \
        --connect-timeout "$CURL_CONNECT_TIMEOUT" \
        --max-time "$GROQ_TIMEOUT" \
        --max-filesize "$MAX_API_RESPONSE_BYTES" \
        -w "\n%{http_code}" \
        https://api.groq.com/openai/v1/audio/transcriptions \
        -K "$GROQ_CURL_CONFIG" \
        -F file="@$WORK_DIR/chunk_${i}.mp3" \
        -F model="whisper-large-v3" \
        -F language="zh" \
        -F "prompt=<$WHISPER_PROMPT_FILE" \
        -F response_format="text")
    
    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" != "200" ]; then
        echo "❌ API error (HTTP $HTTP_CODE)"
        echo "$BODY"
        
        # On a rate limit, wait and retry
        if [ "$HTTP_CODE" = "429" ]; then
            # Extract the wait time from the error message (defaults to 2 minutes)
            WAIT_SEC=$(echo "$BODY" | perl -ne 'if (/in (\d+)m/) { print "$1\n"; exit }')
            WAIT_SEC=${WAIT_SEC:-2}
            WAIT_SEC=$((WAIT_SEC * 60 + 30))
            if [ "$WAIT_SEC" -gt 900 ]; then
                WAIT_SEC=900
            fi
            echo "   ⏳ Rate limited, retrying in ${WAIT_SEC} seconds..."
            sleep "$WAIT_SEC"
            RESPONSE=$(curl --silent --show-error \
                --connect-timeout "$CURL_CONNECT_TIMEOUT" \
                --max-time "$GROQ_TIMEOUT" \
                --max-filesize "$MAX_API_RESPONSE_BYTES" \
                -w "\n%{http_code}" \
                https://api.groq.com/openai/v1/audio/transcriptions \
                -K "$GROQ_CURL_CONFIG" \
                -F file="@$WORK_DIR/chunk_${i}.mp3" \
                -F model="whisper-large-v3" \
                -F language="zh" \
                -F "prompt=<$WHISPER_PROMPT_FILE" \
                -F response_format="text")
            HTTP_CODE=$(echo "$RESPONSE" | tail -1)
            BODY=$(echo "$RESPONSE" | sed '$d')
            
            if [ "$HTTP_CODE" != "200" ]; then
                echo "   ❌ Retry failed"
                exit 1
            fi
        else
            case "$HTTP_CODE" in
                5??)
                    echo "   Fallback to try: agent-reach transcribe \"$AUDIO_URL\""
                    ;;
            esac
            exit 1
        fi
    fi
    
    echo "$BODY" > "$WORK_DIR/transcript_${i}.txt"
    CHARS=$(wc -m < "$WORK_DIR/transcript_${i}.txt")
    echo "✅ ($CHARS chars)"
done

# Step 6.5 (optional): use Llama 3.3 70B to add punctuation and paragraph breaks
if [ "$POLISH" = "1" ]; then
    ensure_python || exit 1
    echo "✨ Polishing (Llama 3.3 70B adds punctuation and paragraph breaks)..."
    for i in $(seq 0 $((NUM_CHUNKS - 1))); do
        echo -n "   Chunk $((i+1))/$NUM_CHUNKS... "
        IN_FILE="$WORK_DIR/transcript_${i}.txt" \
        OUT_FILE="$WORK_DIR/polished_${i}.txt" \
        GROQ_API_KEY="$GROQ_API_KEY" \
        "${PYTHON_CMD[@]}" <<'PY'
import json, os, sys, urllib.request, urllib.error

KEY = os.environ["GROQ_API_KEY"]
IN = os.environ["IN_FILE"]
OUT = os.environ["OUT_FILE"]

MODEL = "llama-3.3-70b-versatile"
MAX_DEPTH = 3
# The prompt stays in Chinese on purpose: the input is a Mandarin transcript, and it
# tells the model to only add Chinese punctuation/paragraphs without changing any
# characters, rewriting, summarizing or adding commentary.
PROMPT_TMPL = (
    "以下是一段中文普通话播客的语音转写片段，由于 Whisper 对中文标点支持较弱，"
    "整段几乎没有标点。请你**只做一件事**：在合适位置补充中文标点（，。！？：；），"
    "可以适度分段。\n\n"
    "**严格要求**：\n"
    "- 不得修改、删除、增加任何汉字或英文/数字\n"
    "- 不得改写、润色、总结\n"
    "- 不得添加任何解释、前言、后记\n"
    "- 直接输出加好标点+合理分段后的全文\n\n"
    "原文：\n{}"
)

def call_groq(text):
    body = json.dumps({
        "model": MODEL,
        "temperature": 0.2,
        "max_completion_tokens": 8192,
        "messages": [{"role": "user", "content": PROMPT_TMPL.format(text)}],
    }).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "User-Agent": "agent-reach-xiaoyuzhou/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        payload = r.read(32 * 1024 * 1024 + 1)
    if len(payload) > 32 * 1024 * 1024:
        raise ValueError("polish response exceeds 32 MiB limit")
    resp = json.loads(payload)
    return (
        resp["choices"][0]["message"]["content"].strip(),
        resp["choices"][0].get("finish_reason"),
    )

def polish(text, depth=0):
    try:
        out, fr = call_groq(text)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"polish HTTP {e.code}: {e.read().decode(errors='replace')[:200]}\n")
        return text  # fallback to raw
    except Exception as e:
        sys.stderr.write(f"polish error: {e}\n")
        return text
    if fr != "length" or depth >= MAX_DEPTH:
        return out
    # Output was truncated: split at the midpoint and recurse on both halves
    mid = len(text) // 2
    return polish(text[:mid], depth + 1) + polish(text[mid:], depth + 1)

content = open(IN, encoding="utf-8").read().strip()
result = polish(content)
open(OUT, "w", encoding="utf-8").write(result + "\n")
print(f"✅ ({len(result)} chars)")
PY
    done
fi

# Step 7: merge the output
echo "📄 Merging transcript..."

if [ -z "$OUTPUT" ]; then
    if ! OUTPUT=$(mktemp "${TEMP_ROOT%/}/agent-reach-transcript.XXXXXX"); then
        echo "❌ Could not safely create the output file" >&2
        exit 1
    fi
fi

{
    echo "# $TITLE"
    echo ""
    echo "Source: $URL"
    echo "Duration: ${DURATION_MIN}m ${DURATION_SEC}s"
    echo "Transcribed: $(date '+%Y-%m-%d %H:%M')"
    if [ "$POLISH" = "1" ]; then
        echo "Polished by: Groq Llama 3.3 70B"
    fi
    echo ""
    echo "---"
    echo ""

    for i in $(seq 0 $((NUM_CHUNKS - 1))); do
        if [ "$POLISH" = "1" ] && [ -f "$WORK_DIR/polished_${i}.txt" ]; then
            cat "$WORK_DIR/polished_${i}.txt"
        else
            cat "$WORK_DIR/transcript_${i}.txt"
        fi
        echo ""
    done
} > "$OUTPUT"

TOTAL_CHARS=$(wc -m < "$OUTPUT")
echo ""
echo "✅ Done!"
echo "📄 Output: $OUTPUT"
echo "📊 Total characters: $TOTAL_CHARS"
echo "===================="
