#!/usr/bin/env python3
"""Preserve original-Wii GX display-list commands after an internal DrawDone.

OpenSPM's raw CALL_DL adaptation recursively calls fifo::process() on the guest
list. Aurora intentionally returns from process() at GXDrawDone so the outer FIFO
can publish a completion boundary. The first OpenSPM adapter propagated that
boolean immediately from the nested call, but returned the *parent* CALL_DL
reader offset. Any bytes remaining in the called display list after DrawDone were
therefore forgotten permanently.

Astra's second M14 gameplay audit identified this source-confirmed loss path.
This migration consumes the complete called list in bounded process() segments,
retains all state/draw commands after internal DrawDone, and then reports one
completion to the parent at the CALL_DL boundary. Existing cached Aurora trees
have passed through several later command-processor diagnostics, so this script
intentionally anchors on the semantic CALL_DL tail instead of requiring the
entire block to remain byte-identical to the first raw-GX adaptation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 nested CALL_DL continuation: preserve bytes after DrawDone"
CASE_START = "    case CP_CMD_CALL_DL: {\n"
CASE_NEXT = "    case CP_CMD_INVAL_VTX: {\n"
DEPTH_START = "      ++sDisplayListDepth;\n"
FINAL_BREAK = "      break;\n"

NEW_TAIL = '''      // M14 nested CALL_DL continuation: preserve bytes after DrawDone.
      // process() returns at each semantic DrawDone. A called display list is a
      // complete command stream, so continue from the exact nested byte offset
      // instead of throwing its remainder away when the parent resumes.
      ++sDisplayListDepth;
      u32 nestedOffset = 0;
      u32 nestedDrawDoneCount = 0;
      while (nestedOffset < displayListSize) {
        const ProcessResult nested = process(span.data + nestedOffset, displayListSize - nestedOffset);
        if (nested.bytesProcessed == 0 || nested.bytesProcessed > displayListSize - nestedOffset) {
          FATAL("GX_CMD_CALL_DL made invalid progress: address=0x{:08X}, size={}, offset={}, processed={}",
                guestAddress, displayListSize, nestedOffset, nested.bytesProcessed);
        }
        nestedOffset += nested.bytesProcessed;
        if (nested.drawDone) ++nestedDrawDoneCount;
      }
      --sDisplayListDepth;

      if (nestedDrawDoneCount > 1) {
        static u64 sMultiNestedDrawDoneWarnings = 0;
        ++sMultiNestedDrawDoneWarnings;
        if (sMultiNestedDrawDoneWarnings <= 8 ||
            (sMultiNestedDrawDoneWarnings & (sMultiNestedDrawDoneWarnings - 1)) == 0) {
          Log.warn("M14 nested CALL_DL contained {} DrawDone packets; command remainder preserved, parent completion coalesced (count={})",
                   nestedDrawDoneCount, sMultiNestedDrawDoneWarnings);
        }
      }
      if (nestedDrawDoneCount != 0) {
        return {static_cast<u32>(reader.offset()), true};
      }
'''


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def extract_call_dl(text: str) -> tuple[int, int, str]:
    starts = []
    pos = 0
    while True:
        pos = text.find(CASE_START, pos)
        if pos < 0:
            break
        starts.append(pos)
        pos += len(CASE_START)
    if len(starts) != 1:
        raise RuntimeError(
            f"command_processor.cpp: expected one CP_CMD_CALL_DL case, found {len(starts)}"
        )
    start = starts[0]
    end = text.find(CASE_NEXT, start + len(CASE_START))
    if end < 0:
        raise RuntimeError("command_processor.cpp: CP_CMD_INVAL_VTX case missing after CALL_DL")
    return start, end, text[start:end]


def validate_active_block(block: str) -> None:
    required = (
        SIGNATURE,
        "u32 nestedOffset = 0;",
        "u32 nestedDrawDoneCount = 0;",
        "process(span.data + nestedOffset, displayListSize - nestedOffset)",
        "nestedOffset += nested.bytesProcessed;",
        "--sDisplayListDepth;",
    )
    missing = [token for token in required if token not in block]
    if missing:
        raise RuntimeError(
            "command_processor.cpp: nested CALL_DL marker is present but continuation is incomplete: "
            + ", ".join(missing)
        )
    if "process(span.data, displayListSize)" in block:
        raise RuntimeError("command_processor.cpp: old one-shot nested display-list process remains beside continuation")


def migrate_block(block: str) -> str:
    # Preserve all mapper/bounds/diagnostic code before the execution tail. The
    # exact wording there has changed across M14 migrations, but these semantic
    # prerequisites must remain before we are willing to rewrite anything.
    prerequisites = (
        "const u32 guestAddress = reader.read<u32>();",
        "const u32 displayListSize = reader.read<u32>();",
        "guest_memory::map",
        "sDisplayListDepth",
        "MaxDisplayListDepth",
    )
    missing = [token for token in prerequisites if token not in block]
    if missing:
        excerpt = block[:1200].replace("\n", "\\n")
        raise RuntimeError(
            "command_processor.cpp: CALL_DL is not the expected raw-guest form; missing "
            + ", ".join(missing)
            + f"; block={excerpt}"
        )

    tail_start = block.find(DEPTH_START)
    tail_end = block.rfind(FINAL_BREAK)
    if tail_start < 0 or tail_end <= tail_start:
        excerpt = block[:1200].replace("\n", "\\n")
        raise RuntimeError(
            "command_processor.cpp: could not isolate nested CALL_DL execution tail; "
            f"depth_start={tail_start} final_break={tail_end}; block={excerpt}"
        )

    old_tail = block[tail_start:tail_end]
    tail_required = (
        "process(span.data",
        "--sDisplayListDepth;",
        "nested.drawDone",
        "reader.offset()",
    )
    missing_tail = [token for token in tail_required if token not in old_tail]
    if missing_tail:
        excerpt = old_tail[:1000].replace("\n", "\\n")
        raise RuntimeError(
            "command_processor.cpp: CALL_DL execution tail has unexpected semantics; missing "
            + ", ".join(missing_tail)
            + f"; tail={excerpt}"
        )

    migrated = block[:tail_start] + NEW_TAIL + block[tail_end:]
    validate_active_block(migrated)
    return migrated


def transform(source: Path, check_only: bool) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = original = path.read_text(encoding="utf-8")
    start, end, block = extract_call_dl(text)

    if SIGNATURE in block:
        validate_active_block(block)
        result = "already active"
    else:
        migrated = migrate_block(block)
        text = text[:start] + migrated + text[end:]
        result = "applied"

    # Global structural checks catch accidental duplicate insertions while still
    # permitting unrelated later migrations elsewhere in command_processor.cpp.
    if text.count(SIGNATURE) != 1:
        raise RuntimeError("command_processor.cpp: nested CALL_DL continuation marker missing/duplicated")
    _, _, final_block = extract_call_dl(text)
    validate_active_block(final_block)

    changed = text != original
    if changed and not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    if check_only:
        return "verified"
    return result if changed else "already active"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()

    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
        if actual != expected:
            raise RuntimeError(f"Aurora revision mismatch: expected {expected}, got {actual}")
        result = transform(source, args.check_only)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"OpenSPM nested display-list continuation adaptation failed: {exc}", file=sys.stderr)
        return 1

    print("Aurora nested display-list continuation " + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
