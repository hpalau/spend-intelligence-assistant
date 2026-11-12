"""Parses the policy markdown into addressable sections.

The whole policy goes into the system prompt (it is ~50 lines; retrieval would add
a failure mode and buy nothing). Parsing it into sections gives every clause an id
("2.1", "3", "4.1") so the model can cite clauses and the backend can check that
each citation exists and show the exact text in the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .data import POLICY_FILE

HEADING = re.compile(r"^##\s+(\d+)\.\s+(.*)$")
CLAUSE = re.compile(r"^(\d+\.\d+)\s+(.*)$")
VERSION = re.compile(r"\*\*Version:\*\*\s*([\d.]+)")
DOC_ID = re.compile(r"\*\*Document ID:\*\*\s*([A-Z0-9-]+)")


@dataclass(frozen=True)
class PolicySection:
    id: str
    heading: str
    text: str


class Policy:
    def __init__(self, path: Path = POLICY_FILE):
        self.raw = path.read_text()
        self.version = (VERSION.search(self.raw) or [None, "unknown"])[1]
        self.doc_id = (DOC_ID.search(self.raw) or [None, "unknown"])[1]
        self.sections: dict[str, PolicySection] = {}
        self._parse()

    def _parse(self) -> None:
        heading_id, heading = None, None
        buffer: list[str] = []

        def flush() -> None:
            # Text under a heading that is not a numbered clause (e.g. §1, the §3 table)
            # is addressable by the heading number itself.
            if heading_id and buffer:
                text = "\n".join(buffer).strip()
                if text:
                    self.sections[heading_id] = PolicySection(heading_id, heading, text)
            buffer.clear()

        # Bold markers are formatting, not content; strip them so quotes read cleanly.
        for line in self.raw.replace("**", "").splitlines():
            if m := HEADING.match(line):
                flush()
                heading_id, heading = m.group(1), m.group(2).strip()
            elif heading_id and (m := CLAUSE.match(line)):
                self.sections[m.group(1)] = PolicySection(m.group(1), heading, m.group(2).strip())
            elif heading_id:
                buffer.append(line)
        flush()

    def get(self, section_id: str) -> PolicySection | None:
        return self.sections.get(section_id.strip().lstrip("§").strip())

    def for_prompt(self) -> str:
        """Policy text with an explicit [§id] marker on every section."""
        lines = [f"{self.doc_id} version {self.version}"]
        current = None
        for s in sorted(self.sections.values(), key=lambda s: [int(p) for p in s.id.split(".")]):
            top = s.id.split(".")[0]
            if top != current:
                lines.append(f"\n## {top}. {s.heading}")
                current = top
            lines.append(f"[§{s.id}] {s.text}")
        return "\n".join(lines)
