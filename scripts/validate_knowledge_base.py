from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "knowledge-base"
EVALUATION_PATH = KB_DIR / "evaluation" / "retrieval-test-cases.json"

EXPECTED_DOCS = {
    Path("global/general-faq.md"): {
        "scope": "global",
        "branch_id": "all",
        "category": "general_faq",
    },
    Path("global/delivery-policy.md"): {
        "scope": "global",
        "branch_id": "all",
        "category": "delivery",
    },
    Path("global/takeaway-policy.md"): {
        "scope": "global",
        "branch_id": "all",
        "category": "takeaway",
    },
    Path("global/payments.md"): {
        "scope": "global",
        "branch_id": "all",
        "category": "payments",
    },
    Path("global/refunds-and-cancellations.md"): {
        "scope": "global",
        "branch_id": "all",
        "category": "refunds_cancellations",
    },
    Path("global/allergies-and-dietary.md"): {
        "scope": "global",
        "branch_id": "all",
        "category": "allergies_dietary",
    },
    Path("global/complaints-and-support.md"): {
        "scope": "global",
        "branch_id": "all",
        "category": "complaints_support",
    },
    Path("global/privacy-and-customer-data.md"): {
        "scope": "global",
        "branch_id": "all",
        "category": "privacy_customer_data",
    },
    Path("branches/default/opening-hours.md"): {
        "scope": "branch",
        "branch_id": "default",
        "category": "opening_hours",
    },
    Path("branches/default/branch-contact.md"): {
        "scope": "branch",
        "branch_id": "default",
        "category": "branch_contact",
    },
}

REQUIRED_METADATA_FIELDS = {
    "scope",
    "branch_id",
    "language",
    "category",
    "status",
    "version",
}

REQUIRED_CASE_FIELDS = {
    "id",
    "question",
    "expected_route",
    "expected_category",
    "expected_document",
    "must_not_claim",
    "notes",
}

ALLOWED_ROUTES = {"knowledge_base", "backend_tool", "human_support"}
PLACEHOLDER_MARKERS = [
    "TODO",
    "FIXME",
    "TBC",
    "TBD",
    "placeholder",
    "example.com",
    "000-000",
    "YOUR_",
    "REPLACE_ME",
]

FORBIDDEN_KB_PHRASES = [
    "ordering assistant",
    "assistant must",
    "must not invent",
    "must not claim",
    "approved restaurant information",
    "approved branch information",
    "approved live system",
    "authorized live system",
    "approved backend tools",
    "backend tools",
    "hidden instructions",
    "knowledge base",
]

ENCODING_CORRUPTION_MARKERS = [
    ("U+00E2", "\u00e2"),
    ("U+00C3", "\u00c3"),
    ("U+00C2", "\u00c2"),
]


SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "AWS secret key label",
        re.compile(r"\baws_secret_access_key\b\s*[:=]\s*['\"]?[^'\"\s]+", re.IGNORECASE),
    ),
    (
        "Plaintext password assignment",
        re.compile(r"\bpassword\b\s*[:=]\s*['\"]?[^'\"\s]+", re.IGNORECASE),
    ),
    ("Bearer token", re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)),
    (
        "Private key",
        re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "Card number",
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    ),
    ("CVV value", re.compile(r"\bcvv\b\s*[:=]\s*\d{3,4}\b", re.IGNORECASE)),
]


class Validator:
    def __init__(self) -> None:
        self.passed = 0
        self.warnings = 0
        self.failures = 0

    def pass_section(self, name: str, detail: str = "") -> None:
        self.passed += 1
        suffix = f" - {detail}" if detail else ""
        print(f"PASS {name}{suffix}")

    def fail_section(self, name: str, detail: str) -> None:
        self.failures += 1
        print(f"FAIL {name} - {detail}")

    def warn_section(self, name: str, detail: str) -> None:
        self.warnings += 1
        print(f"WARN {name} - {detail}")

    def require(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.pass_section(name, detail)
        else:
            self.fail_section(name, detail or "required condition was not met")


def rel(path: Path) -> Path:
    return path.relative_to(KB_DIR)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def scan_secret_patterns(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        text = read_text(path)
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)} matched {label}")
    return findings


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def normalize_expected_document(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    prefix = "knowledge-base/"
    normalized = value.replace("\\", "/")
    return normalized[len(prefix) :] if normalized.startswith(prefix) else normalized


def main() -> int:
    v = Validator()

    v.require("knowledge-base directory exists", KB_DIR.is_dir())
    if not KB_DIR.is_dir():
        print("TOTAL passed=0 warnings=0 failures=1")
        return 1

    expected_abs = {KB_DIR / path for path in EXPECTED_DOCS}
    actual_docs = {
        path
        for path in KB_DIR.rglob("*.md")
        if not path.name.endswith(".metadata.json")
    }
    metadata_files = sorted(KB_DIR.rglob("*.metadata.json"))
    all_scan_files = sorted(
        path
        for path in KB_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".json"}
    )

    missing_docs = sorted(expected_abs - actual_docs)
    unexpected_docs = sorted(actual_docs - expected_abs)
    v.require(
        "exactly expected Markdown documents exist",
        not missing_docs,
        f"{len(EXPECTED_DOCS)} expected documents present",
    )
    if missing_docs:
        v.fail_section(
            "missing Markdown documents",
            ", ".join(str(path.relative_to(ROOT)) for path in missing_docs),
        )
    else:
        v.pass_section("missing Markdown documents", "none")

    v.require(
        "no unexpected Markdown documents exist",
        not unexpected_docs,
        "no extra .md files under knowledge-base",
    )
    if unexpected_docs:
        v.fail_section(
            "unexpected Markdown document list",
            ", ".join(str(path.relative_to(ROOT)) for path in unexpected_docs),
        )
    else:
        v.pass_section("unexpected Markdown document list", "none")

    delivery_area_matches = list(KB_DIR.rglob("delivery-area.md"))
    v.require(
        "delivery-area.md does not exist",
        not delivery_area_matches,
        "delivery-area.md intentionally excluded",
    )

    empty_docs = [path for path in expected_abs if path.exists() and path.stat().st_size == 0]
    v.require("every Markdown document is non-empty", not empty_docs)
    if empty_docs:
        v.fail_section(
            "empty Markdown document list",
            ", ".join(str(path.relative_to(ROOT)) for path in sorted(empty_docs)),
        )
    else:
        v.pass_section("empty Markdown document list", "none")

    missing_h1: list[Path] = []
    for path in sorted(expected_abs):
        if not path.exists():
            continue
        text = read_text(path)
        first_nonempty = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if not first_nonempty.startswith("# ") or first_nonempty.startswith("##"):
            missing_h1.append(path)
    v.require("every Markdown document begins with H1", not missing_h1)
    if missing_h1:
        v.fail_section(
            "missing H1 document list",
            ", ".join(str(path.relative_to(ROOT)) for path in missing_h1),
        )
    else:
        v.pass_section("missing H1 document list", "none")

    missing_sidecars = [
        path.with_name(path.name + ".metadata.json") for path in sorted(expected_abs) if not path.with_name(path.name + ".metadata.json").exists()
    ]
    v.require("every document has adjacent metadata sidecar", not missing_sidecars)
    if missing_sidecars:
        v.fail_section(
            "missing metadata sidecar list",
            ", ".join(str(path.relative_to(ROOT)) for path in missing_sidecars),
        )
    else:
        v.pass_section("missing metadata sidecar list", "none")

    v.require("exactly 10 metadata sidecars exist", len(metadata_files) == 10, f"found {len(metadata_files)}")

    parsed_metadata: dict[Path, dict[str, Any]] = {}
    json_errors: list[str] = []
    for path in metadata_files:
        try:
            parsed = load_json(path)
        except json.JSONDecodeError as exc:
            json_errors.append(f"{path.relative_to(ROOT)}: {exc}")
            continue
        if isinstance(parsed, dict):
            parsed_metadata[path] = parsed
        else:
            json_errors.append(f"{path.relative_to(ROOT)}: top-level JSON is not an object")
    v.require("every metadata file contains valid JSON", not json_errors)
    if json_errors:
        v.fail_section("metadata JSON error list", "; ".join(json_errors))
    else:
        v.pass_section("metadata JSON error list", "none")

    missing_attrs: list[Path] = []
    missing_fields: list[str] = []
    global_errors: list[str] = []
    branch_errors: list[str] = []
    common_errors: list[str] = []
    category_errors: list[str] = []
    metadata_value_errors: list[str] = []
    oversized_metadata: list[Path] = []
    for doc_rel, expected in EXPECTED_DOCS.items():
        doc_path = KB_DIR / doc_rel
        metadata_path = doc_path.with_name(doc_path.name + ".metadata.json")
        parsed = parsed_metadata.get(metadata_path)
        if parsed is None:
            continue
        attrs = parsed.get("metadataAttributes")
        if not isinstance(attrs, dict):
            missing_attrs.append(metadata_path)
            continue
        absent = sorted(REQUIRED_METADATA_FIELDS - set(attrs))
        if absent:
            missing_fields.append(f"{metadata_path.relative_to(ROOT)} missing {', '.join(absent)}")
        scope = attrs.get("scope")
        branch_id = attrs.get("branch_id")
        category = attrs.get("category")
        if scope != expected["scope"]:
            message = f"{metadata_path.relative_to(ROOT)} scope={scope!r}, expected {expected['scope']!r}"
            metadata_value_errors.append(message)
            if expected["scope"] == "global":
                global_errors.append(message)
            else:
                branch_errors.append(message)
        if branch_id != expected["branch_id"]:
            message = f"{metadata_path.relative_to(ROOT)} branch_id={branch_id!r}, expected {expected['branch_id']!r}"
            metadata_value_errors.append(message)
            if expected["scope"] == "global":
                global_errors.append(message)
            else:
                branch_errors.append(message)
        if category != expected["category"]:
            message = f"{metadata_path.relative_to(ROOT)} category={category!r}, expected {expected['category']!r}"
            metadata_value_errors.append(message)
            category_errors.append(message)
        for key, expected_value in {"language": "en", "status": "approved", "version": "1.0"}.items():
            if attrs.get(key) != expected_value:
                message = f"{metadata_path.relative_to(ROOT)} {key}={attrs.get(key)!r}, expected {expected_value!r}"
                metadata_value_errors.append(message)
                common_errors.append(message)
        if len(metadata_path.read_bytes()) >= 1024:
            oversized_metadata.append(metadata_path)

    v.require("every metadata file contains metadataAttributes", not missing_attrs)
    if missing_attrs:
        v.fail_section(
            "missing metadataAttributes list",
            ", ".join(str(path.relative_to(ROOT)) for path in missing_attrs),
        )
    else:
        v.pass_section("missing metadataAttributes list", "none")

    v.require("required metadata fields exist", not missing_fields)
    if missing_fields:
        v.fail_section("missing metadata field list", "; ".join(missing_fields))
    else:
        v.pass_section("missing metadata field list", "none")

    v.require("global documents use global scope and all branch_id", not global_errors)
    v.require("branch documents use branch scope and default branch_id", not branch_errors)
    v.require("all documents use language/status/version constants", not common_errors)
    v.require("metadata categories match exact values", not category_errors)
    if metadata_value_errors:
        v.fail_section("metadata value error list", "; ".join(metadata_value_errors))
    else:
        v.pass_section("metadata value error list", "none")

    v.require("each metadata sidecar is under 1024 bytes", not oversized_metadata)
    if oversized_metadata:
        v.fail_section(
            "oversized metadata sidecar list",
            ", ".join(str(path.relative_to(ROOT)) for path in oversized_metadata),
        )
    else:
        v.pass_section("oversized metadata sidecar list", "none")

    placeholder_findings = []
    for path in all_scan_files:
        text = read_text(path)
        lower_text = text.lower()
        for marker in PLACEHOLDER_MARKERS:
            if marker.lower() in lower_text:
                placeholder_findings.append(f"{path.relative_to(ROOT)} contains {marker}")
    v.require("no placeholder markers appear", not placeholder_findings)
    if placeholder_findings:
        v.fail_section("placeholder marker list", "; ".join(placeholder_findings))
    else:
        v.pass_section("placeholder marker list", "none")

    secret_findings = scan_secret_patterns(all_scan_files)
    v.require("no obvious secret patterns appear", not secret_findings)
    if secret_findings:
        v.fail_section("secret pattern list", "; ".join(secret_findings))
    else:
        v.pass_section("secret pattern list", "none")

    internal_wording_findings: list[str] = []
    encoding_corruption_findings: list[str] = []

    for path in sorted(expected_abs):
        if not path.exists():
            continue

        text = read_text(path)
        normalized = normalize_text(text)

        for phrase in FORBIDDEN_KB_PHRASES:
            if phrase in normalized:
                internal_wording_findings.append(
                    f"{path.relative_to(ROOT)} contains {phrase!r}"
                )

        for label, marker in ENCODING_CORRUPTION_MARKERS:
            if marker in text:
                encoding_corruption_findings.append(
                    f"{path.relative_to(ROOT)} contains {label}"
                )

    v.require(
        "indexed Markdown contains no internal implementation wording",
        not internal_wording_findings,
    )
    if internal_wording_findings:
        v.fail_section(
            "internal implementation wording list",
            "; ".join(internal_wording_findings),
        )
    else:
        v.pass_section(
            "internal implementation wording list",
            "none",
        )

    v.require(
        "indexed Markdown contains no common UTF-8 encoding corruption",
        not encoding_corruption_findings,
    )
    if encoding_corruption_findings:
        v.fail_section(
            "encoding corruption marker list",
            "; ".join(encoding_corruption_findings),
        )
    else:
        v.pass_section(
            "encoding corruption marker list",
            "none",
        )

    complaints_text = read_text(KB_DIR / "global/complaints-and-support.md") if (KB_DIR / "global/complaints-and-support.md").exists() else ""
    branch_contact_text = read_text(KB_DIR / "branches/default/branch-contact.md") if (KB_DIR / "branches/default/branch-contact.md").exists() else ""
    complaint_docs_text = "\n".join([complaints_text, branch_contact_text]).lower()
    v.require("complaint documents do not contain old 24-hour policy", "24-hour" not in complaint_docs_text and "24 hour" not in complaint_docs_text)
    four_hour_pattern = re.compile(r"\b(?:four|4)[ -]?hours?\b", re.IGNORECASE)
    v.require("complaints-and-support.md contains four-hour acknowledgement target", bool(four_hour_pattern.search(complaints_text)))
    v.require("branch-contact.md contains four-hour acknowledgement target", bool(four_hour_pattern.search(branch_contact_text)))

    payments_text = read_text(KB_DIR / "global/payments.md") if (KB_DIR / "global/payments.md").exists() else ""
    payments_lower = payments_text.lower()
    v.require(
        "payments.md states required cash payment facts",
        "cash only" in payments_lower
        and "cash on delivery" in payments_lower
        and (
            "cash on collection" in payments_lower
            or "cash when collecting" in payments_lower
            or "cash when the order is collected" in payments_lower
        ),
    )

    opening_text = read_text(KB_DIR / "branches/default/opening-hours.md") if (KB_DIR / "branches/default/opening-hours.md").exists() else ""
    v.require(
        "opening-hours.md contains required times",
        "11:00 AM" in opening_text and "11:00 PM" in opening_text and "10:30 PM" in opening_text,
    )

    fixed_8km_patterns = ["8 km", "8km"]
    v.require(
        "no document claims fixed 8 km delivery boundary",
        not any(pattern in read_text(path).lower() for path in actual_docs for pattern in fixed_8km_patterns),
    )

    allergies_text = read_text(KB_DIR / "global/allergies-and-dietary.md") if (KB_DIR / "global/allergies-and-dietary.md").exists() else ""
    normalized_allergies = normalize_text(allergies_text)
    approved_halal_statements = [
        "all menu items served by the restaurant are halal",
        "the restaurant confirms that all menu items are prepared according to halal standards",
        "formal third-party certification should not be claimed without separate documentary evidence",
    ]
    v.require(
        "allergies-and-dietary.md contains approved halal policy",
        all(statement in normalized_allergies for statement in approved_halal_statements),
    )

    obsolete_halal_phrases = [
        "halal status cannot be confirmed",
        "must not describe the restaurant or any menu item as halal",
        "must not describe an item as halal without approved evidence",
        "halal suitability requires separate item evidence",
        "the restaurant cannot confirm halal status",
        "cannot confirm halal suitability",
    ]
    obsolete_halal_findings = [
        phrase for phrase in obsolete_halal_phrases if phrase in normalized_allergies
    ]
    v.require(
        "allergies-and-dietary.md contains no obsolete halal wording",
        not obsolete_halal_findings,
        ", ".join(obsolete_halal_findings),
    )

    unsupported_halal_cert_patterns = [
        ("named certification body", re.compile(r"\b(?:certified|certification)\s+by\s+(?!the restaurant\b)[A-Z][A-Za-z&.,' -]{2,}")),
        ("certificate number", re.compile(r"\b(?:halal\s+)?cert(?:ificate)?\s*(?:no\.?|number|#)\s*(?:[:=]|is\s+)[A-Z0-9-]{3,}", re.IGNORECASE)),
        ("certificate expiry date", re.compile(r"\bcert(?:ificate)?\s+(?:expires|expiry|valid until)\b", re.IGNORECASE)),
        ("certificate URL", re.compile(r"\bhalal\s+cert(?:ificate)?\b.{0,80}\bhttps?://", re.IGNORECASE | re.DOTALL)),
        ("government certification", re.compile(r"\b(?:government|religious authority)\s+(?:halal\s+)?certif(?:ied|ication)\b", re.IGNORECASE)),
    ]
    unsupported_halal_cert_findings = [
        label for label, pattern in unsupported_halal_cert_patterns if pattern.search(allergies_text)
    ]
    v.require(
        "allergies-and-dietary.md contains no unsupported formal halal certification claims",
        not unsupported_halal_cert_findings,
        ", ".join(unsupported_halal_cert_findings),
    )

    evaluation: dict[str, Any] | None = None
    evaluation_error = ""
    try:
        loaded = load_json(EVALUATION_PATH)
        if isinstance(loaded, dict):
            evaluation = loaded
        else:
            evaluation_error = "top-level JSON is not an object"
    except FileNotFoundError:
        evaluation_error = "file does not exist"
    except json.JSONDecodeError as exc:
        evaluation_error = str(exc)
    v.require("evaluation JSON exists and contains valid JSON", evaluation is not None, evaluation_error)

    test_cases = evaluation.get("test_cases", []) if evaluation else []
    if not isinstance(test_cases, list):
        test_cases = []
        v.fail_section("evaluation test_cases is a list", "test_cases is missing or not a list")
    else:
        v.pass_section("evaluation test_cases is a list", f"found {len(test_cases)} cases")

    ids = [case.get("id") for case in test_cases if isinstance(case, dict)]
    duplicate_ids = sorted(item for item, count in Counter(ids).items() if count > 1)
    v.require("every evaluation test-case ID is unique", not duplicate_ids)
    if duplicate_ids:
        v.fail_section("duplicate evaluation ID list", ", ".join(str(item) for item in duplicate_ids))
    else:
        v.pass_section("duplicate evaluation ID list", "none")

    missing_case_fields = []
    invalid_routes = []
    invalid_kb_cases = []
    invalid_tool_cases = []
    kb_counts_by_doc: Counter[str] = Counter()
    for index, case in enumerate(test_cases):
        if not isinstance(case, dict):
            missing_case_fields.append(f"case {index} is not an object")
            continue
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            missing_case_fields.append(f"{case.get('id', f'case {index}')} missing {', '.join(missing)}")
        route = case.get("expected_route")
        if route not in ALLOWED_ROUTES:
            invalid_routes.append(f"{case.get('id', f'case {index}')} route {route!r}")
        if route == "knowledge_base":
            if not case.get("expected_category") or not case.get("expected_document"):
                invalid_kb_cases.append(str(case.get("id", f"case {index}")))
            else:
                kb_counts_by_doc[normalize_expected_document(case["expected_document"])] += 1
        if route == "backend_tool" and case.get("expected_document") is not None:
            invalid_tool_cases.append(str(case.get("id", f"case {index}")))

    v.require("every evaluation test case contains required fields", not missing_case_fields)
    if missing_case_fields:
        v.fail_section("missing evaluation field list", "; ".join(missing_case_fields))
    else:
        v.pass_section("missing evaluation field list", "none")

    v.require("expected_route uses only approved values", not invalid_routes)
    if invalid_routes:
        v.fail_section("invalid expected_route list", "; ".join(invalid_routes))
    else:
        v.pass_section("invalid expected_route list", "none")

    v.require("knowledge_base cases include category and document", not invalid_kb_cases)
    if invalid_kb_cases:
        v.fail_section("invalid knowledge_base case list", ", ".join(invalid_kb_cases))
    else:
        v.pass_section("invalid knowledge_base case list", "none")

    v.require("backend_tool cases use null expected_document", not invalid_tool_cases)
    if invalid_tool_cases:
        v.fail_section("invalid backend_tool case list", ", ".join(invalid_tool_cases))
    else:
        v.pass_section("invalid backend_tool case list", "none")

    missing_kb_coverage = []
    for doc_rel in EXPECTED_DOCS:
        expected_document = doc_rel.as_posix()
        count = kb_counts_by_doc[expected_document]
        if count < 5:
            missing_kb_coverage.append(f"{expected_document} has {count}")
    v.require("every approved document has at least five KB retrieval cases", not missing_kb_coverage)
    if missing_kb_coverage:
        v.fail_section("KB coverage gap list", "; ".join(missing_kb_coverage))
    else:
        v.pass_section("KB coverage gap list", "none")

    print(f"TOTAL passed={v.passed} warnings={v.warnings} failures={v.failures}")
    return 1 if v.failures else 0


if __name__ == "__main__":
    sys.exit(main())
