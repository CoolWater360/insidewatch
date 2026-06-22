"""Insider verification, name normalization, and role classification for Phase 4."""

import re

# Markers that indicate a legal entity rather than a natural person.
# Checked case-insensitively as whole tokens.
_ENTITY_MARKERS = [
    # Italian legal forms
    r"S\.P\.A\.", r"S\.R\.L\.", r"S\.R\.L\.S\.", r"S\.A\.S\.", r"S\.N\.C\.",
    r"S\.A\.P\.A\.", r"S\.C\.A\.R\.L\.", r"SICAV", r"SICAF", r"SGR",
    r"FIDUCIARI[AE]",
    # International legal forms
    r"LIMITED", r"LTD\b", r"L\.T\.D\.", r"LLC", r"LLP", r"CORP\b",
    r"CORPORATION", r"INC\b", r"INCORPORATED", r"GMBH", r"AG\b", r"N\.V\.",
    r"B\.V\.", r"PLC\b",
    # Entity-type words
    r"\bGROUP\b", r"\bHOLDING\b", r"\bHOLDINGS\b",
    r"\bFUND\b", r"\bFUNDS\b", r"\bTRUST\b",
    r"\bFONDAZIONE\b", r"\bFONDAZIONI\b", r"\bFOUNDATION\b",
    r"\bINVESTMENTS?\b", r"\bPARTNERS\b", r"\bCAPITAL\b",
    r"\bMANAGEMENT\b", r"\bGESTIONE\b", r"\bPATRIMONI\b",
    r"\bINTERMEDIARI\b", r"\bADVISORY\b",
]
_ENTITY_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _ENTITY_MARKERS]

# Role keywords indicating an advisor, auditor, or intermediary — not a genuine PDMR
_NON_PDMR_ROLE_KEYWORDS = (
    "advisor", "advisory", "intermediar", "revisore", "auditor",
    "notaio", "commercialista", "consulente",
)

# Role category classification rules (checked in order, first match wins)
_ROLE_CATEGORIES: list[tuple[tuple[str, ...], str]] = [
    (
        (
            "ceo", "chief executive", "amministratore delegato",
            "direttore generale", "general manager", "managing director",
            "cfo", "chief financial", "cto", "chief technology",
            "coo", "chief operating", "chief commercial", "chief strategy",
            "chief marketing", "chief information", "chief revenue",
            "direttore finanziario", "direttore commerciale",
            "vice president", "vicepresidente", "vice ceo",
        ),
        "executive",
    ),
    (
        (
            "presidente", "chairman", "chair of the",
            "consigliere di amministrazione", "board member",
            "membro del consiglio", "member of the board",
            "independent director", "non-executive",
            "amministratore indipendente", "amministratore non esecutivo",
            "sindaco", "collegio sindacale", "supervisory board",
        ),
        "board",
    ),
    (
        (
            "azionista rilevante", "major shareholder",
            "socio rilevante", "significant shareholder",
            "substantial shareholder", "shareholder",
        ),
        "major_shareholder",
    ),
    (
        (
            "strettamente legata", "closely associated",
            "persona strettamente", "related person",
            "coniuge", "spouse", "figlio", "figlia",
            "controlled by", "controlled entity",
        ),
        "related_person",
    ),
]


def is_entity_name(name: str) -> bool:
    """Return True if the name contains legal-entity markers (not a natural person)."""
    for pat in _ENTITY_PATTERNS:
        if pat.search(name):
            return True
    return False


def is_non_pdmr_role(role: str) -> bool:
    """Return True if the role string indicates an advisor/auditor/intermediary."""
    lower = role.lower()
    return any(kw in lower for kw in _NON_PDMR_ROLE_KEYWORDS)


def normalize_name(name: str) -> str:
    """
    Normalize an insider name from a PDF.
    - Collapses whitespace
    - ALL-CAPS names are converted to Title Case (PDFs often encode names in caps)
    """
    name = " ".join(name.split())
    if name and name == name.upper() and len(name) > 2:
        name = name.title()
    return name


def classify_role(role: str) -> str:
    """
    Map a raw role string to a category.
    Returns one of: executive | board | major_shareholder | related_person | other.
    """
    lower = role.lower()
    for keywords, category in _ROLE_CATEGORIES:
        if any(kw in lower for kw in keywords):
            return category
    return "other"


def assess_insider(name: str, role: str) -> dict:
    """
    Decide whether an insider record represents a genuine PDMR or
    closely-associated natural person (MAR Art. 3(1)(25-26)).

    Returns:
        verified      bool   True = genuine person, False = entity/intermediary
        role_category str    executive | board | major_shareholder | related_person | other
        reason        str    non-empty only when verified=False
    """
    if is_entity_name(name):
        return {
            "verified": False,
            "role_category": "other",
            "reason": f"entity marker in name: {name!r}",
        }
    if is_non_pdmr_role(role):
        return {
            "verified": False,
            "role_category": "other",
            "reason": f"non-PDMR role: {role!r}",
        }
    return {
        "verified": True,
        "role_category": classify_role(role),
        "reason": "",
    }
