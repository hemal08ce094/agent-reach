"""Read-only helpers for interpreting mcporter configuration."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from agent_reach.utils.paths import (
    PrivatePathError,
    read_small_text_no_follow,
)

_MAX_CONFIG_BYTES = 1024 * 1024
_MISSING = object()
_JSONC_COMMENTS = re.compile(r'("(?:\\.|[^"\\])*")|//[^\r\n]*|/\*[\s\S]*?\*/')
_JSONC_TRAILING_COMMAS = re.compile(r'("(?:\\.|[^"\\])*")|,(?=\s*[}\]])')


class McporterConfigError(ValueError):
    """Raised when mcporter configuration is not trustworthy."""


@dataclass(frozen=True)
class McporterConfigInspection:
    """Minimal, non-secret routing facts from one mcporter config layer."""

    server_names: frozenset[str]
    source: str | None
    imports_unchecked: bool = False
    #: Non-explicit layers that could not be parsed and were ignored, as
    #: ``"<source>: <reason>"`` strings (mcporter warns and ignores them too).
    skipped_layers: tuple[str, ...] = ()


def skipped_layers_note(inspection: McporterConfigInspection) -> str:
    """Human-readable note about ignored config layers ("" when none)."""
    if not inspection.skipped_layers:
        return ""
    return (
        "Some mcporter config layers could not be parsed and were ignored "
        "(mcporter ignores them as well): " + "; ".join(inspection.skipped_layers)
    )


def inspect_mcporter_config(
    root_dir: str | Path | None = None,
) -> McporterConfigInspection:
    """Read the effective local mcporter config without starting mcporter.

    An explicit ``MCPORTER_CONFIG`` is a single layer. Otherwise mcporter
    0.7.3 loads the first home config
    (``~/.mcporter/mcporter.json`` / ``mcporter.jsonc``) and then
    ``<cwd>/config/mcporter.json``; project entries override duplicate home
    names. Only exact ``mcpServers`` keys are returned. Editor imports are
    deliberately not opened because Doctor must not expand its
    credential-read boundary.
    """
    selected_layers = _select_config_layers(root_dir)
    if not selected_layers:
        return McporterConfigInspection(frozenset(), None)

    names: set[str] = set()
    imports_unchecked = False
    sources = []
    skipped = []
    for config_path, source in selected_layers:
        try:
            payload = _read_config_object(config_path)
            layer_names, layer_imports_unchecked = _inspect_layer(payload)
        except _LayerSyntaxError as exc:
            if source == "explicit":
                raise McporterConfigError(str(exc)) from exc
            # mcporter only warns about a malformed home/project layer and
            # treats it as empty; do the same instead of failing every channel.
            skipped.append(f"{source} ({config_path}): {exc}")
            continue
        names.update(layer_names)
        imports_unchecked = imports_unchecked or layer_imports_unchecked
        sources.append(source)

    return McporterConfigInspection(
        frozenset(names),
        "+".join(sources) or None,
        imports_unchecked=imports_unchecked,
        skipped_layers=tuple(skipped),
    )


class _LayerSyntaxError(McporterConfigError):
    """A layer that was read safely but is not a valid mcporter config."""


def _inspect_layer(payload: dict) -> tuple[set[str], bool]:
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        raise _LayerSyntaxError("mcporter config is missing the mcpServers mapping")

    names = set()
    for name, definition in servers.items():
        if not isinstance(name, str) or not name.strip():
            raise _LayerSyntaxError("mcporter config contains an invalid server name")
        if not isinstance(definition, dict):
            raise _LayerSyntaxError("mcporter server definition must be a mapping")
        names.add(name.casefold())

    imports = payload.get("imports", _MISSING)
    if imports is _MISSING:
        # mcporter defaults to importing supported editor configs when the
        # key is omitted. Doctor intentionally does not open those files.
        return names, True
    if not isinstance(imports, list) or not all(isinstance(item, str) for item in imports):
        raise _LayerSyntaxError("mcporter imports must be a list of strings")
    return names, bool(imports)


def _select_config_layers(
    root_dir: str | Path | None,
) -> list[tuple[Path, str]]:
    root = Path(os.path.abspath(os.fspath(root_dir or Path.cwd())))
    explicit = os.environ.get("MCPORTER_CONFIG", "").strip()
    if explicit:
        expanded = Path(os.path.expanduser(explicit))
        if not expanded.is_absolute():
            expanded = root / expanded
        return [(Path(os.path.abspath(os.fspath(expanded))), "explicit")]

    layers = []
    home_base = Path.home() / ".mcporter"
    for name in ("mcporter.json", "mcporter.jsonc"):
        candidate = home_base / name
        if os.path.lexists(candidate):
            layers.append((candidate, "home"))
            break

    project_path = root / "config" / "mcporter.json"
    if os.path.lexists(project_path):
        layers.append((project_path, "project"))
    return layers


def _read_config_object(config_path: Path) -> dict:
    try:
        raw = read_small_text_no_follow(
            config_path,
            follow_symlinks=True,
            max_bytes=_MAX_CONFIG_BYTES,
        )
    except PrivatePathError as exc:
        raise McporterConfigError(
            f"mcporter config file cannot be read safely: {exc}"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise McporterConfigError("mcporter config file cannot be read safely") from exc
    if raw is None:
        raise McporterConfigError("mcporter config file does not exist")
    try:
        payload = _parse_config_json(raw)
    except json.JSONDecodeError as exc:
        raise _LayerSyntaxError("mcporter config is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise _LayerSyntaxError("mcporter config top level must be a mapping")
    return payload


def _parse_config_json(raw: str):
    """Accept mcporter's JSONC (comments, trailing commas), keeping JSON validation.

    mcporter parses every config layer with ``jsonc-parser`` and
    ``allowTrailingComma``. String tokens win over comment delimiters so URLs
    and escapes survive; comments become whitespace so they cannot join
    otherwise invalid tokens.
    """
    without_comments = _JSONC_COMMENTS.sub(
        lambda match: match.group(1) if match.group(1) is not None else " " * len(match.group()),
        raw,
    )

    def trailing_comma(match):
        if match.group(1) is not None:
            return match.group(1)
        previous = match.start() - 1
        while previous >= 0 and without_comments[previous].isspace():
            previous -= 1
        # Empty containers and missing values are not trailing-comma syntax.
        if previous < 0 or without_comments[previous] in "[{,:":
            return ","
        return ""

    normalized = _JSONC_TRAILING_COMMAS.sub(trailing_comma, without_comments)
    return json.loads(normalized)


def configured_server_names(output: str) -> set[str]:
    """Return exact configured server names from ``mcporter ... --json``.

    Paths, descriptions, endpoints, and other metadata are deliberately
    ignored: only each server object's ``name`` field is a routing signal.
    """
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError) as exc:
        raise McporterConfigError("could not parse the JSON returned by mcporter") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
        raise McporterConfigError("mcporter JSON is missing the servers list")

    return {
        name.casefold()
        for server in payload["servers"]
        if isinstance(server, dict)
        if isinstance(name := server.get("name"), str) and name.strip()
    }
