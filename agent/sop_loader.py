import re
from pathlib import Path
from typing import Any

import frontmatter


def load_sop_file(filepath: str) -> dict[str, Any]:
    """Parse SOP markdown file with new front matter format (cases array).

    Returns:
        {
            "metadata": {"scenario": ..., "cases": [{"case_id", "title", "keywords", "jumps_to"}, ...]},
            "sop_file": "filename.md",
            "cases": {"case_1": {"case_id", "title", "keywords", "jumps_to",
                                  "symptom", "problem_to_verify", "how_to_verify", "note", "raw"}, ...}
        }
    """
    path = Path(filepath)
    post = frontmatter.load(str(path))

    metadata: dict[str, Any] = dict(post.metadata)
    cases_meta: list[dict[str, Any]] = metadata.get("cases", [])

    # Build lookup: case_id -> metadata from front matter
    meta_by_id: dict[str, dict[str, Any]] = {
        c["case_id"]: c for c in cases_meta
    }

    cases = _parse_cases(post.content, meta_by_id)

    return {
        "metadata": metadata,
        "sop_file": path.name,
        "cases": cases,
    }


def _parse_cases(
    body: str,
    meta_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Split markdown body by '## case N' headers and merge with front matter metadata."""
    sections = re.split(r"(?=^## case \d+)", body, flags=re.MULTILINE)
    cases: dict[str, dict[str, Any]] = {}

    for section in sections:
        section = section.strip()
        if not section:
            continue

        header_match = re.match(r"^## case (\d+)", section)
        if not header_match:
            continue

        case_id = f"case_{header_match.group(1)}"
        meta = meta_by_id.get(case_id, {})

        cases[case_id] = {
            "case_id": case_id,
            "title": meta.get("title", ""),
            "keywords": meta.get("keywords", []),
            "jumps_to": meta.get("jumps_to", []),
            "symptom": _extract_subsection(section, "symptom"),
            "problem_to_verify": _extract_subsection(section, "problem_to_verify"),
            "how_to_verify": _extract_subsection(section, "how_to_verify"),
            "note": _extract_subsection(section, "note"),
            "raw": section,
        }

    return cases


def _extract_subsection(case_text: str, name: str) -> str:
    """Extract content of a ### subsection from a case block."""
    pattern = rf"### {name}\s*\n(.*?)(?=\n### |\Z)"
    match = re.search(pattern, case_text, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def get_case(sop_data: dict[str, Any], case_id: str) -> str:
    """Return the full markdown text of a specific case."""
    case = sop_data["cases"].get(case_id)
    if not case:
        raise KeyError(f"Case '{case_id}' not found in SOP '{sop_data['sop_file']}'")
    return case["raw"]


def get_case_symptom_summary(
    sop_data: dict[str, Any], case_ids: list[str]
) -> list[dict[str, str]]:
    """Return case_id + symptom for the given case IDs, for LLM candidate selection.

    Only exposes symptom to prevent LLM from free-reasoning on how_to_verify content.
    """
    result = []
    for case_id in case_ids:
        case = sop_data["cases"].get(case_id)
        if case:
            result.append({
                "case_id": case_id,
                "symptom": case["symptom"],
            })
    return result


_PLACEHOLDER_RE = re.compile(r"&(\w+)")


def extract_sql_placeholders(sql: str) -> list[str]:
    """Return deduplicated list of &param_name placeholders found in the SQL."""
    return list(dict.fromkeys(_PLACEHOLDER_RE.findall(sql)))


def fill_sql_params(sql: str, params: dict[str, str]) -> str:
    """Replace &param_name placeholders in SQL with actual values."""
    def replacer(match: re.Match) -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(f"Missing SQL parameter: '{name}'")
        return str(params[name])

    return _PLACEHOLDER_RE.sub(replacer, sql)
