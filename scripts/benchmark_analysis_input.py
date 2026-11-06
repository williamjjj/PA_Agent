"""Prove padded-to-TSV table conversion preserves all cells; report size/token estimates."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pa_agent.ai.prompt_assembler import PromptAssembler


def measure(record, *, encoder=None):
    result = []
    for stage in ("stage1_messages", "stage2_messages"):
        old = "\n".join(m["content"] for m in record[stage] if m["role"] != "assistant")
        new, tables = old, []
        pattern = r"(?m)^序号 \|[^\n]+\n[-+]+\n(?:\d+[^\n]*\n)+"
        for match in list(re.finditer(pattern, old))[::-1]:
            lines = match.group().splitlines()
            compact = PromptAssembler._compact_table(lines)
            old_rows = [[v.strip() for v in line.split("|")] for line in lines if "|" in line]
            new_rows = [line.split("\t") for line in compact.splitlines()[1:] if "\t" in line]
            assert old_rows == new_rows
            tables.append({"rows": len(old_rows)-1, "columns": len(old_rows[0]),
                "cells_sha256": hashlib.sha256(json.dumps(old_rows, ensure_ascii=False).encode()).hexdigest()})
            new = new[:match.start()] + compact + "\n" + new[match.end():]
        entry = {"stage": stage, "old_chars": len(old), "new_chars": len(new), "tables": tables}
        if encoder:
            entry.update(token_estimator="o200k_base", old_estimated_tokens=len(encoder.encode(old)),
                         new_estimated_tokens=len(encoder.encode(new)))
        result.append(entry)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tokens", action="store_true")
    args = parser.parse_args()
    encoder = None
    if args.tokens:
        import tiktoken
        encoder = tiktoken.get_encoding("o200k_base")
    result = measure(json.loads(args.record.read_text(encoding="utf-8-sig")), encoder=encoder)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
