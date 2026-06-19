from __future__ import annotations

from .deterministic import MatchEntry


class DocumentReconstructor:
    def apply_replacements(
        self,
        text: str,
        replacements: list[MatchEntry],
    ) -> str:
        if not replacements:
            return text

        # Sort by start position descending (process end to beginning)
        sorted_reps = sorted(
            replacements,
            key=lambda r: -r.original_start,
        )

        result = text
        for rep in sorted_reps:
            start = rep.original_start
            end = rep.original_end
            if start < 0 or end > len(result) or start >= end:
                continue
            result = result[:start] + rep.replacement + result[end:]

        return result

    def rebuild_sections(
        self,
        sections: list[dict],
        masked_text: str,
    ) -> list[dict]:
        result: list[dict] = []
        for section in sections:
            result.append(
                {
                    "item": section.get("item", ""),
                    "title": section.get("title", ""),
                    "char_count": len(masked_text) if "char_count" in section else 0,
                }
            )
        return result
