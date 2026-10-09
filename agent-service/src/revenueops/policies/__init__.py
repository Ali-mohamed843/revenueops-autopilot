"""The store's policies, as short markdown files with numbered rules (e.g. COD-3).

Retrieval is deliberately simple: each file lists the case types it applies to, and a case gets every
file tagged with its type. With five short documents that is exact and complete; a vector store would
add a way to miss a rule and nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources

RULE = re.compile(r"\*\*([A-Z]+-\d+)\*\*")
RULE_LINE = re.compile(r"^- \*\*([A-Z]+-\d+)\*\* (.+)$", re.M)


@dataclass(frozen=True)
class Policy:
    id: str
    title: str
    applies_to: tuple[str, ...]
    text: str  # the markdown body, without the header
    rules: tuple[str, ...]  # rule ids in order of appearance

    def rule_texts(self) -> dict[str, str]:
        """Rule id -> its sentence, for showing a cited rule in full."""
        return dict(RULE_LINE.findall(self.text))


def parse(source: str) -> Policy:
    match = re.match(r"---\n(.*?)\n---\n(.*)", source.replace("\r\n", "\n"), re.S)
    if not match:
        raise ValueError("a policy starts with a --- header ---")
    header = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    fields = {k.strip(): v.strip() for k, v in header.items()}
    for key in ("id", "title", "applies_to"):
        if not fields.get(key):
            raise ValueError(f"policy header is missing {key}")
    body = match.group(2).strip()
    return Policy(
        id=fields["id"],
        title=fields["title"],
        applies_to=tuple(t.strip() for t in fields["applies_to"].split(",")),
        text=body,
        rules=tuple(RULE.findall(body)),
    )


@cache
def all_policies() -> tuple[Policy, ...]:
    files = sorted((f for f in resources.files(__package__).iterdir() if f.name.endswith(".md")), key=lambda f: f.name)
    return tuple(parse(f.read_text(encoding="utf-8")) for f in files)


def policies_for(case_type: str) -> list[Policy]:
    return [p for p in all_policies() if case_type in p.applies_to]
