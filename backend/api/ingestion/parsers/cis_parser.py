import fitz
import re


def parse_cis_framework(pdf_path):

    # ---------------------------------
    # OPEN PDF
    # ---------------------------------

    doc = fitz.open(pdf_path)

    full_text = ""

    # ---------------------------------
    # SKIP INTRO PAGES
    # ---------------------------------

    for page_number in range(len(doc)):

        # Skip intro/table-of-contents pages

        if page_number < 13:
            continue

        page = doc[page_number]

        full_text += (
            page.get_text("text")
            + "\n"
        )

    # ---------------------------------
    # FRAMEWORK STRUCTURE
    # ---------------------------------

    framework = {

        "code": "CIS_CONTROLS_V8",

        "name": "CIS Controls",

        "version": "8",

        "description":
        "CIS Controls Version 8 canonical model",

        "domains": []
    }

    # ---------------------------------
    # SPLIT LINES
    # ---------------------------------

    lines = full_text.splitlines()

    # ---------------------------------
    # REGEX PATTERNS
    # ---------------------------------

    control_pattern = re.compile(
        r"^CONTROL\s+(\d+)$",
        re.IGNORECASE
    )

    safeguard_pattern = re.compile(
        r"^Safeguard\s+(\d+\.\d+):\s+(.+)$",
        re.IGNORECASE
    )

    # ---------------------------------
    # PARSER STATE
    # ---------------------------------

    current_domain = None

    i = 0

    # ---------------------------------
    # NOISE PATTERNS
    # ---------------------------------

    noise_patterns = [

        "CIS Controls v8.1",
        "Implementation Group",
        "Controls and Safeguards Index",
        "www.cisecurity.org",
        "info@cisecurity.org",
        "Center for Internet Security",
        "@CISecurity",
        "TheCISecurity",
        "cisecurity",
        "globally recognized best",
        "world a safer place",
        "community-driven nonprofit",
        "MS-ISAC",
        "EI-ISAC"
    ]

    # ---------------------------------
    # PDF BLEED PATTERNS
    # ---------------------------------

    bleed_patterns = [

        r"^Enterprise Asset Inventory\s+",
        r"^Software Asset Inventory\s+",
        r"^Application Infrastructure\s+",
        r"^Security Components\s+",
        r"^Security Incidents\s+",
        r"^Application Vulnerabilities\s+"
    ]

    # ---------------------------------
    # PARSE LINES
    # ---------------------------------

    while i < len(lines):

        line = lines[i].strip()

        # ---------------------------------
        # SKIP EMPTY
        # ---------------------------------

        if not line:

            i += 1
            continue

        # ---------------------------------
        # SKIP PAGE MARKERS
        # Example:
        # A41
        # ---------------------------------

        if re.match(r"^A\d+$", line):

            i += 1
            continue

        # ---------------------------------
        # SKIP PURE PAGE NUMBERS
        # Example:
        # 17
        # 18
        # ---------------------------------

        if re.match(r"^\d+$", line):

            i += 1
            continue

        # ---------------------------------
        # SKIP NOISE
        # ---------------------------------

        skip_line = any(
            noise.lower() in line.lower()
            for noise in noise_patterns
        )

        if skip_line:

            i += 1
            continue

        # ---------------------------------
        # MATCH CONTROL HEADER
        # Example:
        # CONTROL 17
        # ---------------------------------

        control_match = control_pattern.match(
            line
        )

        if control_match:

            control_code = (
                control_match.group(1)
            )

            # ---------------------------------
            # NEXT LINE IS CONTROL NAME
            # ---------------------------------

            control_name = ""

            if i + 1 < len(lines):

                control_name = (
                    lines[i + 1]
                    .strip()
                )

            # ---------------------------------
            # CLEAN CONTROL NAME
            # ---------------------------------

            control_name = re.sub(
                r"\s+",
                " ",
                control_name
            ).strip()

            # ---------------------------------
            # PREVENT DUPLICATE DOMAINS
            # ---------------------------------

            existing_domain = next(

                (
                    d for d in framework["domains"]
                    if d["code"] == f"CIS_{control_code}"
                ),

                None
            )

            if existing_domain:

                current_domain = existing_domain

            else:

                current_domain = {

                    "code":
                    f"CIS_{control_code}",

                    "name":
                    control_name,

                    "description": "",

                    "categories": [

                        {

                            "code":
                            "SAFEGUARDS",

                            "name":
                            "Safeguards",

                            "description": "",

                            "controls": []
                        }
                    ]
                }

                framework["domains"].append(
                    current_domain
                )

            i += 1
            continue

        # ---------------------------------
        # MATCH SAFEGUARD
        # Example:
        # Safeguard 17.1: Something
        # ---------------------------------

        safeguard_match = safeguard_pattern.match(
            line
        )

        if (
            safeguard_match
            and current_domain
        ):

            safeguard_code = (
                safeguard_match.group(1)
            )

            safeguard_name = (
                safeguard_match.group(2)
            ).strip()

            # ---------------------------------
            # CLEAN SAFEGUARD NAME
            # ---------------------------------

            safeguard_name = re.sub(
                r"\s+",
                " ",
                safeguard_name
            ).strip()

            # ---------------------------------
            # PREVENT DUPLICATE SAFEGUARDS
            # ---------------------------------

            existing_control = next(

                (
                    c
                    for c in current_domain[
                        "categories"
                    ][0]["controls"]

                    if c["code"] == safeguard_code
                ),

                None
            )

            if existing_control:

                i += 1
                continue

            # ---------------------------------
            # METADATA VARIABLES
            # ---------------------------------

            statement_lines = []

            asset_type = None

            security_function = None

            implementation_groups = []

            j = i + 1

            # ---------------------------------
            # CAPTURE STATEMENT
            # ---------------------------------

            while j < len(lines):

                next_line = (
                    lines[j]
                    .strip()
                )

                # ---------------------------------
                # SKIP EMPTY
                # ---------------------------------

                if not next_line:

                    j += 1
                    continue

                # ---------------------------------
                # STOP CONDITIONS
                # ---------------------------------

                if safeguard_pattern.match(
                    next_line
                ):
                    break

                if control_pattern.match(
                    next_line
                ):
                    break

                # ---------------------------------
                # SKIP PAGE MARKERS
                # ---------------------------------

                if re.match(
                    r"^A\d+$",
                    next_line
                ):

                    j += 1
                    continue

                # ---------------------------------
                # SKIP PAGE NUMBERS
                # ---------------------------------

                if re.match(
                    r"^\d+$",
                    next_line
                ):

                    j += 1
                    continue

                # ---------------------------------
                # PARSE METADATA LINE
                # ---------------------------------

                if "Asset Type:" in next_line:

                    asset_match = re.search(
                        r"Asset Type:\s*(.*?)\s*\|",
                        next_line,
                        re.IGNORECASE
                    )

                    if asset_match:

                        asset_type = (
                            asset_match.group(1)
                            .replace("•", "")
                            .replace("▪", "")
                            .strip()
                        )

                    security_match = re.search(
                        r"Security Function:\s*(.*?)\s*\|",
                        next_line,
                        re.IGNORECASE
                    )

                    if security_match:

                        security_function = (
                            security_match.group(1)
                            .replace("•", "")
                            .replace("▪", "")
                            .strip()
                        )

                    implementation_groups = re.findall(
                        r"IG[123]",
                        next_line
                    )

                    j += 1
                    continue

                # ---------------------------------
                # SKIP NOISE
                # ---------------------------------

                skip_next = any(
                    noise.lower() in next_line.lower()
                    for noise in noise_patterns
                )

                if skip_next:

                    j += 1
                    continue

                # ---------------------------------
                # REMOVE CONTROL BLEED
                # Example:
                # Control 2: Inventory...
                # ---------------------------------

                if re.match(
                    r"^Control\s+\d+",
                    next_line,
                    re.IGNORECASE
                ):

                    j += 1
                    continue

                statement_lines.append(
                    next_line
                )

                j += 1

            # ---------------------------------
            # BUILD STATEMENT
            # ---------------------------------

            statement = " ".join(
                statement_lines
            ).strip()

            # ---------------------------------
            # NORMALIZE SPACES
            # ---------------------------------

            statement = re.sub(
                r"\s+",
                " ",
                statement
            )

            # ---------------------------------
            # REMOVE TRAILING PAGE NUMBERS
            # Example:
            # "... more frequently. 17"
            # ---------------------------------

            statement = re.sub(
                r"\s+\d+$",
                "",
                statement
            )

            # ---------------------------------
            # REMOVE CONTROL BLEED
            # ---------------------------------

            statement = re.sub(
                r"Control\s+\d+.*$",
                "",
                statement,
                flags=re.IGNORECASE
            ).strip()

            # ---------------------------------
            # REMOVE PDF TITLE BLEED
            # ---------------------------------

            for pattern in bleed_patterns:

                statement = re.sub(
                    pattern,
                    "",
                    statement,
                    flags=re.IGNORECASE
                )

            # ---------------------------------
            # FINAL CLEANUP
            # ---------------------------------

            statement = re.sub(
                r"\s+",
                " ",
                statement
            ).strip()

            # ---------------------------------
            # BUILD CONTROL OBJECT
            # ---------------------------------

            control = {

                "code":
                safeguard_code,

                "name":
                safeguard_name,

                "description": "",

                "statement":
                statement,

                "asset_type":
                asset_type,

                "security_function":
                security_function,

                "implementation_groups":
                implementation_groups,

                "maturity_level": None,

                "cross_refs": [],

                "questions": [],

                "expected_evidence_types": []
            }

            # ---------------------------------
            # ADD CONTROL
            # ---------------------------------

            current_domain[
                "categories"
            ][0][
                "controls"
            ].append(control)

            i = j
            continue

        i += 1

    # ---------------------------------
    # DEBUG
    # ---------------------------------

    total_controls = 0

    for domain in framework["domains"]:

        total_controls += len(
            domain["categories"][0]["controls"]
        )

    print(
        f"\nParsed CIS safeguards: {total_controls}\n"
    )

    return framework