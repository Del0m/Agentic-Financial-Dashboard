import re
from typing import Optional

# regex scrubs for the few free-text fields the privacy gateway sends (account names, merchants).
# the gateway's field allowlist is the real defense; this catches PII hiding inside allowed strings.

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# (407) 555-1234 / 407-555-1234 / +1 407 555 1234; runs before DIGITS so a phone isn't half-replaced
PHONE = re.compile(r"(?:\+1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}")
# 4+ digits: card and account numbers, masks, reference ids. "Route 66" and "7-Eleven" survive
DIGITS = re.compile(r"\d{4,}")
# A payment label may omit to/from or use an asterisk. Consume the whole counterparty,
# including text after a redacted email/number; retaining a surname would still leak it.
P2P = re.compile(
    r"\b(?P<service>zelle|venmo|cash\s*app|apple\s*cash|paypal)\b"
    r"(?P<connector>\s+(?:payment\s+)?(?:to|from)\b\s*|[\s*:/-]+)(?P<person>.*)$",
    re.IGNORECASE | re.DOTALL,
)
REDACTIONS = re.compile(r"\[(?:email|phone|number)\]")
# Account labels are user-provided: remove the whole prefix before the last possessive.
# This covers James', THE JONES', Mary-Jane's, O'Brien's and joint/multiword names.
POSSESSIVE = re.compile(r"^.+(?=['’][sS]\b|(?<=[sS])['’](?:\s|$))", re.DOTALL)


def scrub_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    s = EMAIL.sub("[email]", s)
    s = PHONE.sub("[phone]", s)
    return DIGITS.sub("[number]", s)


def scrub_merchant(s: Optional[str]) -> Optional[str]:
    s = scrub_text(s)
    def redact(match):
        markers = REDACTIONS.findall(match["person"])
        return match["service"] + match["connector"] + "[person]" + (
            " " + " ".join(markers) if markers else "")
    return P2P.sub(redact, s) if s else s


def scrub_note(s: Optional[str]) -> Optional[str]:
    """Extract bounded hints, never forward unrestricted identifying text.

    Structured preferences remain authoritative. Notes support a priority kind
    and a payoff strategy; other wording stays local and has no model effect.
    """
    if not s:
        return s
    if re.search(r"\b(?:not|never|avoid|don['’]t)\b", s, re.I):
        return "Use structured skip_kinds for exclusions; no free-text exclusion was forwarded."
    hints = []
    priorities = ((r"emergency[ _-]*fund", "Prioritize the emergency fund."),
                  (r"debt[ _-]*payoff|\bcard\b|\bdebt\b", "Prioritize debt payoff."),
                  (r"\bsavings?\b", "Prioritize savings."))
    # Require an affirmative priority phrase rather than interpreting every mention.
    for term, hint in priorities:
        if re.search(r"(?:" + term + r").{0,30}(?:first|priority|matters most)", s, re.I):
            hints.append(hint)
            break
    for strategy in ("avalanche", "snowball"):
        if re.search(r"\b(?:use|prefer|choose)\s+(?:the\s+)?" + strategy + r"\b", s, re.I):
            hints.append("Use the " + strategy + " strategy.")
            break
    return " ".join(hints) or "No supported priority or strategy hint was supplied."


def scrub_account_name(s: Optional[str]) -> Optional[str]:
    s = scrub_text(s)
    return POSSESSIVE.sub("[person]", s) if s else s
