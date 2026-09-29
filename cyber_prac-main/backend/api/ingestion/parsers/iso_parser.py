import fitz
import re


# ====================================
# TEXT NORMALIZATION
# ====================================

def normalize_text(text):

    # --------------------------------
    # REMOVE WEIRD UNICODE
    # --------------------------------

    text = (
        text
        .replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("\xa0", " ")
        .replace("\n", " ")
        .replace("\t", " ")
        .replace("\b", "")
        .replace("­", "")
        .strip()
    )

    # --------------------------------
    # FIX PDF SPLIT WORDS
    # --------------------------------

    replacements = {

        # split words

        "secu rity": "security",
        "de fined": "defined",
        "seg regated": "segregated",
        "clas sification": "classification",
        "commu nication": "communication",
        "infor mation": "information",
        "tech nology": "technology",
        "re quirements": "requirements",
        "man agement": "management",
        "identi ties": "identities",
        "proce dures": "procedures",
        "orga nization": "organization",
        "speci fic": "specific",
        "appro priate": "appropriate",
        "de veloped": "developed",
        "main tained": "maintained",
        "imple mented": "implemented",
        "confi dentiality": "confidentiality",
        "avail ability": "availability",
        "top ic": "topic",
        "busi ness": "business",
        "authen tication": "authentication",
        "sup plier": "supplier",
        "phys ical": "physical",
        "tech nological": "technological",
        "in for mation": "information",

        # joined words

        "shallbe": "shall be",
        "shallbedefined": "shall be defined",
        "shallbeidentified": "shall be identified",
        "shallbeimplemented": "shall be implemented",
        "shallbeestablished": "shall be established",
        "shallbecollected": "shall be collected",
        "shallbeintegrated": "shall be integrated",
        "shallbeclassified": "shall be classified",
        "shallbemanaged": "shall be managed",
        "shallbedeveloped": "shall be developed",
        "shallbemaintained": "shall be maintained",

        "accordingto": "according to",
        "relatedto": "related to",
        "basedon": "based on",

        "informationsecurity": "information security",
        "securitypolicy": "security policy",
        "projectmanagement": "project management",
        "supplierrelationships": "supplier relationships",
        "returnofassets": "return of assets",
        "inventoryofinformation": "inventory of information",
        "classificationofinformation": "classification of information",
        "acceptableuseofinformation": "acceptable use of information",
        "accessrights": "access rights",
        "threatintelligence": "threat intelligence",
        "identitymanagement": "identity management",

        "Segregationofduties": "Segregation of duties",
        "Informationsecurity": "Information security",
        "Contactwith": "Contact with",

        "informationsecurityin": "information security in",
        "securityin": "security in",

        "partiesas": "parties as",
        "assetsin": "assets in",
        "changeor": "change or",
        "terminationof": "termination of",

        "contractoragreement":
        "contract or agreement",

        "supplier’sproductsorservices":
        "supplier’s products or services",

        "specialinterestgroupsorother":
        "special interest groups or other"
    }

    for wrong, correct in replacements.items():

        text = re.sub(
            re.escape(wrong),
            correct,
            text,
            flags=re.IGNORECASE
        )

    # --------------------------------
    # FIX MISSING SPACES BETWEEN WORDS
    # --------------------------------

    text = re.sub(
        r"([a-z])([A-Z])",
        r"\1 \2",
        text
    )

    # --------------------------------
    # REMOVE EXTRA SPACES
    # --------------------------------

    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ====================================
# ISO PARSER
# ====================================

def parse_iso_framework(pdf_path):

    doc = fitz.open(pdf_path)

    framework = {
        "code": "ISO_27001_2022",
        "name": "ISO 27001",
        "version": "2022",
        "description": "ISO 27001 Annex A canonical model",
        "domains": []
    }

    # ====================================
    # DOMAIN MAP
    # ====================================

    domain_map = {
        "5": "Organizational controls",
        "6": "People controls",
        "7": "Physical controls",
        "8": "Technological controls"
    }

    domains = {}

    for key, value in domain_map.items():

        domains[key] = {
            "code": f"ISO_{key}",
            "name": value,
            "description": "",
            "categories": [
                {
                    "code": "CONTROLS",
                    "name": "Controls",
                    "description": "",
                    "controls": []
                }
            ]
        }

    annex_started = False

    current_control = None
    current_domain = None

    statement_buffer = []

    # ====================================
    # NOISE FILTERS
    # ====================================

    noise_patterns = [

        "SNV / licensed",
        "ISO/IEC 27001:2022(E)",
        "Table A.1",
        "Table A.1 (continued)",
        "All rights reserved",
        "licensed to",
        "Price based on",
        "ICS 03.100.70",
        "Reference number",
        "INTERNATIONAL STANDARD",
        "ISO/IEC 2022",
        "2022-10-31",
        "S105748"
    ]

    # ====================================
    # ITERATE PAGES
    # ====================================

    for page in doc:

        text = page.get_text("text")

        raw_lines = text.splitlines()

        cleaned_lines = []

        # ====================================
        # CLEAN RAW LINES
        # ====================================

        for raw in raw_lines:

            line = (
                raw
                .replace("\xa0", " ")
                .replace("\t", " ")
                .replace("­", "")
                .replace("—", " ")
                .replace("–", " ")
                .replace("ﬁ", "fi")
                .replace("ﬂ", "fl")
                .replace("©", "")
                .strip()
            )

            line = normalize_text(line)

            if line:
                cleaned_lines.append(line)

        i = 0

        while i < len(cleaned_lines):

            line = cleaned_lines[i]

            # ====================================
            # START ANNEX A
            # ====================================

            window = " ".join(
                cleaned_lines[
                    max(0, i - 5):
                    min(len(cleaned_lines), i + 5)
                ]
            ).lower()

            if (
                "annex a" in window
                and "controls" in window
            ):

                annex_started = True

            if not annex_started:

                i += 1
                continue

            # ====================================
            # STOP AT BIBLIOGRAPHY
            # ====================================

            if line.lower().startswith(
                "bibliography"
            ):
                break

            # ====================================
            # SKIP NOISE
            # ====================================

            if any(
                noise.lower() in line.lower()
                for noise in noise_patterns
            ):

                i += 1
                continue

            # ====================================
            # SKIP PAGE NUMBERS
            # ====================================

            if re.match(r"^\d+$", line):

                i += 1
                continue

            # ====================================
            # CONTROL DETECTION
            # ====================================

            control_match = re.match(
                r"^(5|6|7|8)\.(\d+)$",
                line
            )

            if control_match:

                # --------------------------------
                # SAVE PREVIOUS CONTROL
                # --------------------------------

                if (
                    current_control is not None
                    and current_domain is not None
                ):

                    statement = " ".join(
                        statement_buffer
                    )

                    statement = normalize_text(
                        statement
                    )

                    current_control[
                        "statement"
                    ] = statement

                    current_domain[
                        "categories"
                    ][0][
                        "controls"
                    ].append(
                        current_control
                    )

                # --------------------------------
                # CREATE NEW CONTROL
                # --------------------------------

                domain_code = control_match.group(1)

                current_domain = domains[
                    domain_code
                ]

                control_id = line

                title_parts = []

                j = i + 1

                while j < len(cleaned_lines):

                    temp = cleaned_lines[j]

                    # stop conditions

                    if temp == "Control":
                        break

                    if re.match(
                        r"^(5|6|7|8)\.\d+$",
                        temp
                    ):
                        break

                    if temp.lower().startswith(
                        "control"
                    ):
                        break

                    if any(
                        noise.lower() in temp.lower()
                        for noise in noise_patterns
                    ):
                        break

                    title_parts.append(temp)

                    j += 1

                control_name = " ".join(
                    title_parts
                )

                control_name = normalize_text(
                    control_name
                )

                current_control = {

                    "code": control_id,
                    "name": control_name,
                    "description": "",
                    "statement": "",
                    "maturity_level": None,
                    "cross_refs": [],
                    "questions": [],
                    "expected_evidence_types": []
                }

                statement_buffer = []

                i = j + 1
                continue

            # ====================================
            # CAPTURE STATEMENTS
            # ====================================

            if current_control is not None:

                if (

                    line not in [
                        "Control",
                        "Purpose",
                        "Attribute"
                    ]

                    and not re.match(
                        r"^\d+$",
                        line
                    )

                    and not any(
                        noise.lower() in line.lower()
                        for noise in noise_patterns
                    )
                ):

                    statement_buffer.append(line)

            i += 1

    # ====================================
    # SAVE LAST CONTROL
    # ====================================

    if (
        current_control is not None
        and current_domain is not None
    ):

        statement = " ".join(
            statement_buffer
        )

        statement = normalize_text(
            statement
        )

        current_control[
            "statement"
        ] = statement

        current_domain[
            "categories"
        ][0][
            "controls"
        ].append(
            current_control
        )

    # ====================================
    # FINAL DOMAIN ORDER
    # ====================================

    framework["domains"] = [

        domains["5"],
        domains["6"],
        domains["7"],
        domains["8"]
    ]

    return framework