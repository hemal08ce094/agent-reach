# -*- coding: utf-8 -*-
"""Fork regression tests for transcribe_xiaoyuzhou.sh (A18, #720, #717, #769).

curl/ffprobe/ffmpeg are Bash functions injected through BASH_ENV, so nothing
touches the network or real media tools.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "agent_reach" / "scripts" / "transcribe_xiaoyuzhou.sh"
SECRET = "gsk_fork_test_secret_value"

_FAKE_CURL = r'''
args="$*"
output=""
prompt_file=""
config_file=""
prev=""
for arg in "$@"; do
    case "$prev" in
        -o) output="$arg" ;;
        -K) config_file="$arg" ;;
    esac
    case "$arg" in
        prompt=\<*) prompt_file="${arg#prompt=<}" ;;
    esac
    prev="$arg"
done
if [ -n "$output" ]; then
    printf 'fake audio' > "$output"
elif [ -n "$config_file" ]; then
    printf '%s\n' "$args" >> "$CURL_LOG"
    cp "$config_file" "$CAPTURE_DIR/curlrc"
    (stat -c '%a' "$config_file" 2>/dev/null || stat -f '%Lp' "$config_file") > "$CAPTURE_DIR/curlrc.mode"
    cp "$prompt_file" "$CAPTURE_DIR/prompt.txt"
    printf 'fixture transcript\n200'
else
    printf '%s' '<html>"title":"Fixture"https://media.xyzcdn.net/test.mp3</html>'
fi
'''


def _bash_path(path: Path) -> str:
    rendered = path.resolve().as_posix()
    if os.name == "nt" and len(rendered) >= 3 and rendered[1:3] == ":/":
        return f"/{rendered[0].lower()}{rendered[2:]}"
    return rendered


def _functions(tmp_path: Path, extra: str = "") -> Path:
    env_file = tmp_path / "bash-env.sh"
    env_file.write_text(
        "curl() {\n" + _FAKE_CURL + "\n}\n"
        "ffprobe() { printf '42'; }\n"
        'ffmpeg() { printf "%s\\n" "$*" >> "$FFMPEG_LOG"; '
        'for last in "$@"; do :; done; printf "mp3" > "$last"; }\n' + extra,
        encoding="utf-8",
    )
    return env_file


def _run(tmp_path, bash_executable, extra_functions=""):
    capture = tmp_path / "capture"
    capture.mkdir()
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "BASH_ENV": _bash_path(_functions(tmp_path, extra_functions)),
            "CURL_LOG": _bash_path(tmp_path / "curl.log"),
            "FFMPEG_LOG": _bash_path(tmp_path / "ffmpeg.log"),
            "CAPTURE_DIR": _bash_path(capture),
            "GROQ_API_KEY": SECRET,
            "TMPDIR": _bash_path(temp_root),
        }
    )
    result = subprocess.run(
        [
            bash_executable,
            SCRIPT.relative_to(ROOT).as_posix(),
            "https://www.xiaoyuzhoufm.com/episode/123",
            _bash_path(tmp_path / "out.md"),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
    )
    return result, capture, temp_root


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_groq_key_never_appears_in_curl_argv(tmp_path, bash_executable):
    result, capture, temp_root = _run(tmp_path, bash_executable)

    assert result.returncode == 0, result.stdout + result.stderr
    groq_calls = (tmp_path / "curl.log").read_text(encoding="utf-8")
    assert "api.groq.com" in groq_calls
    assert SECRET not in groq_calls
    assert "Authorization" not in groq_calls
    assert (capture / "curlrc").read_text(encoding="utf-8") == (
        f'header = "Authorization: Bearer {SECRET}"\n'
    )
    assert (capture / "curlrc.mode").read_text().strip() == "600"
    assert list(temp_root.glob("agent-reach-xiaoyuzhou.*")) == []  # cleaned up
    assert "fixture transcript" in (tmp_path / "out.md").read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_whisper_prompt_is_sent_from_a_file_not_argv(tmp_path, bash_executable):
    result, capture, _ = _run(tmp_path, bash_executable)

    assert result.returncode == 0, result.stdout + result.stderr
    groq_calls = (tmp_path / "curl.log").read_text(encoding="utf-8")
    assert "prompt=<" in groq_calls
    assert "普通话" not in groq_calls
    assert "普通话" in (capture / "prompt.txt").read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="POSIX fixture")
def test_ffmpeg_runs_with_nostdin(tmp_path, bash_executable):
    result, _, _ = _run(tmp_path, bash_executable)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "ffmpeg.log").read_text(encoding="utf-8").splitlines()
    assert calls and all(call.startswith("-nostdin ") for call in calls)


def test_script_source_keeps_secrets_and_cjk_out_of_argv():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Authorization: Bearer $GROQ_API_KEY" not in text
    assert '-F prompt="' not in text
    assert text.count('-K "$GROQ_CURL_CONFIG"') == 2
    assert text.count('-F "prompt=<$WHISPER_PROMPT_FILE"') == 2
    assert "umask 077" in text


@pytest.mark.skipif(os.name == "nt", reason="POSIX fixture")
def test_unrunnable_python3_stub_is_skipped(tmp_path, bash_executable):
    """A Microsoft Store style python3 alias must not shadow a working python."""
    stub = (
        "python3() { echo 'Python was not found; install from the Microsoft Store' >&2; "
        "return 49; }\n"
        f'python() {{ "{_bash_path(Path(sys.executable))}" "$@"; }}\n'
    )
    result, _, _ = _run(tmp_path, bash_executable, stub)

    assert "Only http/https links" not in result.stderr
    assert result.returncode == 0, result.stdout + result.stderr


def test_script_rejects_api_key_with_line_break(tmp_path, bash_executable):
    env = os.environ.copy()
    env.update(
        {
            "BASH_ENV": _bash_path(_functions(tmp_path)),
            "GROQ_API_KEY": "abc\nheader = evil",
            "TMPDIR": _bash_path(tmp_path),
            "CURL_LOG": _bash_path(tmp_path / "curl.log"),
        }
    )
    result = subprocess.run(
        [bash_executable, SCRIPT.relative_to(ROOT).as_posix(),
         "https://www.xiaoyuzhoufm.com/episode/1"],
        capture_output=True, encoding="utf-8", errors="replace", env=env, cwd=ROOT,
    )
    assert result.returncode != 0
    assert "line break" in result.stderr
    assert not (tmp_path / "curl.log").exists()
