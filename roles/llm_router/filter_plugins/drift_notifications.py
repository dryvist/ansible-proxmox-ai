"""Compact sanitized router drift findings into bounded per-file messages."""

from __future__ import annotations

from collections import defaultdict


def drift_notification_batches(findings, max_bytes=3500):
    """Group stable item names by source file without losing any finding.

    Return oversized item identities separately so the caller can fail visibly
    before sending any partial alert set.
    """
    if not isinstance(findings, list):
        raise ValueError("drift_notification_batches expects a list")
    if not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")

    grouped = defaultdict(list)
    for finding in findings:
        grouped[str(finding["git_file"])].append(str(finding["item"]))

    messages = []
    oversized = []
    for git_file in sorted(grouped):
        items = sorted(grouped[git_file])
        digits = len(str(len(items)))
        placeholder = "9" * digits
        sizing_header = (
            f"LiteLLM router drift — back-port to {git_file} "
            f"({len(items)} items, part {placeholder}/{placeholder})"
        )
        parts = []
        current = []
        for item in items:
            line = f"• {item}"
            candidate = "\n".join([sizing_header, *(current + [line])])
            if len(candidate.encode("utf-8")) <= max_bytes:
                current.append(line)
                continue
            if current:
                parts.append(current)
                current = []
            single = "\n".join([sizing_header, line])
            if len(single.encode("utf-8")) > max_bytes:
                oversized.append({"git_file": git_file, "item": item})
            else:
                current.append(line)
        if current:
            parts.append(current)

        total = len(parts)
        for index, lines in enumerate(parts, start=1):
            header = (
                f"LiteLLM router drift — back-port to {git_file} "
                f"({len(items)} items, part {index}/{total})"
            )
            text = "\n".join([header, *lines])
            if len(text.encode("utf-8")) > max_bytes:
                raise ValueError("drift notification exceeded its byte limit after packing")
            messages.append({
                "git_file": git_file,
                "item": f"{git_file} part {index}/{total}",
                "text": text,
            })

    return {"messages": messages, "oversized": oversized}


class FilterModule:
    def filters(self):
        return {"drift_notification_batches": drift_notification_batches}
