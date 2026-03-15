import re
from pathlib import Path
from typing import Any

import frontmatter


def load_sop_file(filepath: str) -> dict[str, Any]:
    """Parse SOP markdown file. Returns metadata + all cases."""
    path = Path(filepath)
    post = frontmatter.load(str(path))

    metadata: dict[str, Any] = dict(post.metadata)
    entry_case_id: str = metadata.get("case_id", "")

    # front matter 描述的是入口 case，is_entry 預設為 True
    if "is_entry" not in metadata:
        metadata["is_entry"] = True

    cases = _parse_cases(post.content, entry_case_id)

    return {
        "metadata": metadata,
        "sop_file": path.name,
        "cases": cases,
    }


def _parse_cases(body: str, entry_case_id: str) -> dict[str, dict[str, Any]]:
    """將 markdown 正文依 '## case N' 標題切割成各個 case。"""
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

        cases[case_id] = {
            "case_id": case_id,
            "is_entry": case_id == entry_case_id,
            "symptom": _extract_subsection(section, "symptom"),
            "question": _extract_subsection(section, "question"),
            "action": _extract_subsection(section, "action"),
            "note": _extract_subsection(section, "note"),
            "raw": section,
        }

    return cases


def _extract_subsection(case_text: str, name: str) -> str:
    """從 case markdown 中取出指定 ### 小節的內容。"""
    pattern = rf"### {name}\s*\n(.*?)(?=\n### |\Z)"
    match = re.search(pattern, case_text, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def get_case(sop_data: dict[str, Any], case_id: str) -> str:
    """取得特定 case 的完整 markdown 內容。"""
    case = sop_data["cases"].get(case_id)
    if not case:
        raise KeyError(f"Case '{case_id}' not found in SOP '{sop_data['sop_file']}'")
    return case["raw"]


def get_candidate_cases(
    sop_data: dict[str, Any], case_ids: list[str]
) -> list[dict[str, str]]:
    """取得多個候選 case 的 case_id + symptom 摘要，供條件比對用。

    刻意只回傳 symptom，不暴露 action / note 細節，
    確保 LLM 做的是條件比對而非自由推理。
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


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def extract_sql_placeholders(sql: str) -> list[str]:
    """找出 SQL 中所有 {param_name} 佔位符，回傳不重複的清單。"""
    return list(dict.fromkeys(_PLACEHOLDER_RE.findall(sql)))


def fill_sql_params(sql: str, params: dict[str, str]) -> str:
    """將 SQL 中的 {param_name} 佔位符填入實際值。"""
    def replacer(match: re.Match) -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(f"Missing SQL parameter: '{name}'")
        return str(params[name])

    return _PLACEHOLDER_RE.sub(replacer, sql)
