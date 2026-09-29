import fitz
import re


def parse_nist_framework(pdf_path):

    # -----------------------------
    # OPEN PDF
    # -----------------------------

    doc = fitz.open(pdf_path)

    full_text = ""

    for page in doc:

        full_text += page.get_text() + "\n"

    # -----------------------------
    # EXTRACT APPENDIX A ONLY
    # -----------------------------

    start_marker = "Appendix A. CSF Core"

    start_index = full_text.rfind(
        start_marker
    )

    if start_index == -1:

        raise Exception(
            "Appendix A not found"
        )

    core_text = full_text[start_index:]

    # -----------------------------
    # REMOVE EXTRA APPENDIXES
    # -----------------------------

    cut_markers = [
        "Appendix B.",
        "Appendix C.",
        "Glossary"
    ]

    for marker in cut_markers:

        marker_index = core_text.find(
            marker
        )

        if marker_index != -1:

            core_text = core_text[
                :marker_index
            ]

            break

    # -----------------------------
    # REGEX PATTERNS
    # -----------------------------

    function_pattern = re.compile(
        r"^([A-Z][A-Z\s]+)\s\(([A-Z]{2})\):"
    )

    category_pattern = re.compile(
        r"•\s(.+?)\s\(([A-Z]{2}\.[A-Z]{2})\):"
    )

    control_pattern = re.compile(
        r"o\s([A-Z]{2}\.[A-Z]{2}-\d{2}):\s(.+)"
    )

    # -----------------------------
    # CANONICAL JSON
    # -----------------------------

    framework = {

    "code": "NIST_CSF_2_0",

    "name": "NIST Cybersecurity Framework",

    "version": "2.0",

    "description": "NIST Cybersecurity Framework canonical model",

    "maturity_levels": [

        {
            "level": 1,
            "code": "INITIAL",
            "name": "Initial"
        },

        {
            "level": 2,
            "code": "REPEATABLE",
            "name": "Repeatable"
        },

        {
            "level": 3,
            "code": "DEFINED",
            "name": "Defined"
        },

        {
            "level": 4,
            "code": "MANAGED",
            "name": "Managed"
        },

        {
            "level": 5,
            "code": "OPTIMIZING",
            "name": "Optimizing"
        }
    ],

    "domains": []
}

    current_domain = None
    current_category = None
    current_control = None

    lines = core_text.split("\n")

    # -----------------------------
    # PARSE LINES
    # -----------------------------

    for line in lines:

        line = line.strip()

        if not line:
            continue

        # -------------------------
        # DOMAIN
        # -------------------------

        function_match = function_pattern.match(
            line
        )

        if function_match:

            function_name = (
                function_match
                .group(1)
                .title()
            )

            function_code = (
                function_match
                .group(2)
            )

            current_domain = {

                "code": function_code,

                "name": function_name,

                "description": "",

                "categories": []
            }

            framework["domains"].append(
                current_domain
            )

            current_category = None
            current_control = None

            continue

        # -------------------------
        # CATEGORY
        # -------------------------

        category_match = category_pattern.match(
            line
        )

        if (
            category_match
            and current_domain
        ):

            category_name = (
                category_match
                .group(1)
                .strip()
            )

            category_code = (
                category_match
                .group(2)
            )

            current_category = {

                "code": category_code,

                "name": category_name,

                "description": "",

                "controls": []
            }

            current_domain[
                "categories"
            ].append(current_category)

            current_control = None

            continue

        # -------------------------
        # CONTROL
        # -------------------------

        control_match = control_pattern.match(
            line
        )

        if (
            control_match
            and current_category
        ):

            control_code = (
                control_match
                .group(1)
            )

            statement = (
                control_match
                .group(2)
            )

            current_control = {

    "code": control_code,

    "name": "",

    "description": "",

    "statement": statement,

    "maturity_level": None,

    "cross_refs": [],

    "questions": [],

    "expected_evidence_types": []
}

            current_category[
                "controls"
            ].append(current_control)

            continue

        # -------------------------
        # MULTILINE CONTROL SUPPORT
        # -------------------------

        if current_control:

            if (
                "NIST CSWP" in line
                or "The NIST Cybersecurity Framework" in line
                or "CSF 2.0" in line
                or "February 26, 2024" in line
                or re.match(r"^\d+$", line)
            ):
                continue

            if (
                function_pattern.match(line)
                or category_pattern.match(line)
                or control_pattern.match(line)
            ):
                continue

            current_control[
                "statement"
            ] += " " + line

    # -----------------------------
    # CLEAN SPACES
    # -----------------------------

    for domain in framework["domains"]:

        for category in domain["categories"]:

            for control in category["controls"]:

                control["statement"] = re.sub(
                    r"\s+",
                    " ",
                    control["statement"]
                ).strip()

    # -----------------------------
    # RETURN STRUCTURED JSON
    # -----------------------------

    return framework