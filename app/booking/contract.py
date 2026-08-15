import re

from jinja2 import StrictUndefined, Template

# Matches the operator-facing "[____]"-style blanks in the legal template
# text (registration numbers, dollar amounts, etc). Distinct from the
# "{{...}}" booking-specific placeholders Jinja renders per-guest.
UNRESOLVED_BLANK_RE = re.compile(r"\[[^\]{}]*\]")


def has_unresolved_blanks(template: str) -> bool:
    return bool(UNRESOLVED_BLANK_RE.search(template))


def render_contract(template: str, context: dict[str, str]) -> str:
    # StrictUndefined: a missing template variable must be a loud error, not
    # a silently-blank field in a document a guest is about to sign.
    return Template(template, undefined=StrictUndefined).render(**context)
