# M14 LLVM DSP mailbox frontier — 2026-09-15

## Status

The LLVM-native candidate has crossed the previous CPU/compiler cold-entry frontier.

Runtime authority for this note is the local Windows run at repo revision
`c1b8fd79681d`, captured in `launch(20260915-004843).log`.

The earlier stationary FPU exception-restart PC `0x802F09B4` is gone. The
candidate now reaches Aurora/GX initialization, submits the first GX stream,
arms the native Revolution retrace path, and repeatedly delivers retraces at the
60 Hz host-owned simulation cadence without a guest Program exception.

The next deterministic frontier is a Revolution DSP mailbox handshake, not a
DolRecomp entry-point fault.

## Exact R8PE01 loop

Disassembly of the verified player-owned `main.dol` identifies:

```text
8028986C  lis     r3,0xCC00
80289870  lhz     r0,0x5000(r3)
80289874  rlwinm  r3,r0,17,31,31
80289878  blr
```

This is `DSPCheckMailToDSP()`: it reads the CPU->DSP mailbox high half at
`0xCC005000` and returns its full/pending bit.

The caller is:

```text
8028A14C  bl      0x802898A0    ; DSPSendMailToDSP
8028A150  bl      0x8028986C    ; DSPCheckMailToDSP
8028A154  cmpwi   r3,0
8028A158  bne     0x8028A150
```

The runtime heartbeat alternates around `0x80289870` / `0x8028A154`, with
`LR=0x8028A154`, no guest exception, and the native-region query counter growing
by roughly 25 million per second. That is exactly the SDK busy-wait above.

## Root cause

`core/wii_boot_platform.c` already models the other Broadway-visible DSP
bootstrap observables because OpenSPM intentionally does not execute Nintendo
DSP microcode:

- ARAM DMA completes synchronously and raises its completion status;
- releasing the bootstrap DSP program synthesizes the one ready mail the SDK
  waits for;
- reading the DSP->CPU mailbox low half consumes the mail by clearing only the
  high-half full bit while preserving payload bits.

But CPU->DSP mail was incomplete. `WII_DSP_MAIL_TO_HI` and
`WII_DSP_MAIL_TO_LO` only latched their values. Nothing represented the absent
DSP microcode consuming a committed CPU->DSP mailbox, so bit 15 of
`dsp_mail_to_hi` could remain asserted forever and `DSPCheckMailToDSP()` could
never return zero.

This is a platform-model omission exposed after LLVM finally progressed far
enough into DSP task startup. It is not another LLVM cold-entry class.

## Fix

Commit `2cce92c34e6c0e9a6c761305cbddec24fd1b6359` adds the missing observable:

- the high-half write still latches the complete high half and can expose the
  full bit;
- the low-half write commits the complete CPU->DSP mailbox word;
- because no DSP microcode runtime exists, OpenSPM consumes that committed mail
  synchronously by clearing only `dsp_mail_to_hi[15]`;
- high/low payload bits remain readable, symmetric with the existing DSP->CPU
  mailbox consumption rule.

No game address or command payload is hard-coded.

## Regression contract

Commit `1e3f9bd8a644baf2f94bc63551d78b6c06571f08` extends
`m14_wii_boot_platform_test` with the SDK-visible sequence:

1. write CPU->DSP high `0x80F4`;
2. prove the full bit is visible before commit;
3. write CPU->DSP low `0x9F01`;
4. prove high becomes `0x00F4` rather than remaining full;
5. prove the low payload remains `0x9F01`.

Commit `ed5c9817a3b1de383a040054f4d71cb1d87905c3` adds
`m14_wii_boot_platform_test` to the transactional LLVM runner's host contract
build and CTest filter. A future LLVM A/B therefore refuses to launch if this
DSP mailbox completion behavior regresses.

## Next local acceptance

Run:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

Expected pre-launch result: `m14_wii_boot_platform` passes together with the
existing Wii/GX/EXI contracts.

Runtime acceptance:

- the `0x8028986C` / `0x8028A154` mailbox loop must no longer dominate the
  heartbeats;
- `region_queries` should not grow by tens of millions per second solely from
  that poll;
- the guest should advance beyond DSP task-mail submission.

If the next frontier is another DSP mailbox wait, decode the exact caller and
model only the next hardware-visible DSP observable required by the SDK. Do not
reintroduce Nintendo DSP microcode or hard-code R8PE01 PCs into the platform
layer.

## Build/cache impact

This fix changes OpenSPM host platform semantics and a host-side unit test only.
It does not change the DOL, relF image, DolRecomp codegen source, LLVM native ABI,
or guest object entry set. Existing exact-stamped LLVM object cache entries
should remain reusable.
