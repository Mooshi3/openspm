from pathlib import Path

p = Path("m10/module/generated/chunks/chunk_0150_text1_80271940.c")
s = p.read_text().replace("\r\n", "\n")

# Exact R8PE01 Nintendo OS SelectThread idle signature. We intentionally gate
# the hardcoded idle PC on surrounding behavior, not just the address label.
required = {
    "idle helper": "static void loop_8027551C(CPUState* ctx)",
    "idle label": "label_8027551C:",
    "run queue load": "// 8027551C: lwz     r0, -29552(r13)",
    "zero compare": "// 80275520: cmpwi   r0, 0",
    "self branch": "// 80275524: bc    12, 2, 0x8027551C",
    "enable interrupts before wait": "// 80275518: bl      0x80271E3C",
    "disable interrupts after wake": "// 80275528: bl      0x80271E28",
    "recheck run queue": "// 8027552C: lwz     r0, -29552(r13)",
    "retry scheduler idle": "// 80275534: bc    12, 2, 0x80275518",
}

missing = [name for name, needle in required.items() if needle not in s]
if missing:
    raise SystemExit("M11D idle signature mismatch: missing " + ", ".join(missing))

# The tight helper must remain bounded by the generated loop-cycle escape so
# the baseline and M11D differ only in what the chassis does after it returns.
helper_start = s.index("static void loop_8027551C(CPUState* ctx)")
helper_end = s.index("static void loop_8027575C(CPUState* ctx)", helper_start)
helper = s[helper_start:helper_end]
for needle in (
    "ctx->downcount -= 3;",
    "DOLRECOMP_C_LOOP_CYCLE_BUDGET",
    "ctx->pc = 0x8027551Cu;",
    "goto label_8027551C;",
    "ctx->pc = 0x80275528u;",
):
    if needle not in helper:
        raise SystemExit(f"M11D idle helper mismatch: missing {needle!r}")

print("M11D idle signature OK: R8PE01 SelectThread RunQueueBits spin at 0x8027551C")
