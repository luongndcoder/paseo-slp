"""Schema loading/validation and markdown front-matter parsing shared by the slp-* tools."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
FRONT_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.S)

# artifact kind -> schema file
SCHEMAS = {
    "result": "result.schema.json",
    "verify": "verify.schema.json",
    "review": "review.schema.json",
    "final-review": "review.schema.json",
    "brief": "brief.frontmatter.schema.json",
    "workstream": "workstream.frontmatter.schema.json",
    "report": "report.frontmatter.schema.json",
}


class ArtifactError(Exception):
    """An artifact is missing, unparsable or violates its schema."""


def load_schema(kind: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / SCHEMAS[kind]).read_text())


def schema_errors(instance: Any, kind: str) -> list[str]:
    """Return human-readable schema violations (empty list = valid)."""
    validator = Draft202012Validator(load_schema(kind))
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message[:200]}"
        for e in errors
    ]


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Split `---\\n<yaml>\\n---\\n<body>` into (front-matter dict, body)."""
    m = FRONT_RE.match(text)
    if not m:
        raise ArtifactError("missing YAML front-matter block (--- ... ---) at top of file")
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise ArtifactError(f"front-matter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ArtifactError("front-matter must be a YAML mapping")
    # JSON round-trip: YAML dates etc. become strings so JSON Schema sees plain JSON types.
    return json.loads(json.dumps(data, default=str)), m.group(2)


def load_front_matter(path: Path, kind: str) -> tuple[dict[str, Any], str]:
    """Read a markdown artifact, validate its front-matter against the `kind` schema."""
    try:
        text = path.read_text()
    except OSError as exc:
        raise ArtifactError(f"cannot read {path}: {exc}") from exc
    data, body = parse_front_matter(text)
    errors = schema_errors(data, kind)
    if errors:
        raise ArtifactError(f"{path}: front-matter invalid: " + "; ".join(errors))
    return data, body


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except OSError as exc:
        raise ArtifactError(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise ArtifactError(f"{path}: not valid JSON: {exc}") from exc


def detect_kind(path: Path) -> str:
    """Guess the artifact kind from its filename."""
    name = path.name
    for kind, pat in (
        ("result", r"^result\.json$"),
        ("verify", r"^verify(\.\d+)?\.json$"),
        ("final-review", r"^final-review(\.checked)?\.json$"),
        ("review", r"^review(\.\d+)?(\.checked)?\.json$"),
        ("brief", r"^brief\.md$"),
        ("workstream", r"^workstream\.md$"),
        ("report", r"^report\.md$"),
    ):
        if re.match(pat, name):
            return kind
    raise ArtifactError(f"cannot detect artifact kind from {name}; pass --kind")
