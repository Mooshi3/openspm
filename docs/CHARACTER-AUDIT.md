# Character id audit (generated)

> Incomplete: this tool misses reads in functions that receive MarioWork as an
> argument. tools/content/character_reads.py finds 402 (docs/CHARACTER-IDS.md).

Groundwork for real character ids 4+ (docs/CONTENT-API.md). Plan per kind:
**compare** (a check like "is this Luigi"): a new id matches none of them and
gets the plain behaviour; abilities come from the mod. **index** (a
per-character table): the table is relocated (tools/content/table_refs.py)
and gets a row per registered character. **argument** / **store** / **other**:
reviewed one by one (hooks or stand-in mapping). relF addresses below are in
the analysis layout (the build places relF itself); main.dol addresses are
exact.

Every read of the current character (MarioWork+0x38) in R8PE01 rev 2 main.dol + relF,
from `tools/content/character_audit.py`. Kinds: argument 15, compare 155, index 51, other 9, store 2

| read at | kind | detail |
| --- | --- | --- |
| 0x800256E4 | argument | 0x800256EC (in r3) |
| 0x80027520 | argument | 0x80027528 (in r3) |
| 0x800C9CA8 | compare | 0x800C9CAC with 2 |
| 0x800CE188 | other | 0x800CE198 (value overwritten) |
| 0x800CEE1C | index | 0x800CEE28 table 0x803CC5D8 |
| 0x800CEE3C | index | 0x800CEE48 table 0x803CC5D8 |
| 0x800D021C | index | 0x800D0268 |
| 0x800D2AB8 | compare | 0x800D2AC0 with 2 |
| 0x800D2E98 | compare | 0x800D2EA4 with 2 |
| 0x800D2F7C | compare | 0x800D2F88 with 2 |
| 0x800E322C | index | 0x800E3248 table 0x803D4880 |
| 0x800E3440 | index | 0x800E345C table 0x803D4880 |
| 0x800E34F4 | index | 0x800E3508 table 0x803D4880 |
| 0x800E64F4 | compare | 0x800E64FC with 2 |
| 0x800ED5F8 | compare | 0x800ED60C with 2 |
| 0x800EFA40 | other | no use found |
| 0x800F33FC | compare | 0x800F3408 with 2 |
| 0x801121D8 | compare | 0x801121E0 with 2 |
| 0x80112698 | compare | 0x801126A4 with 3 |
| 0x80112BDC | compare | 0x80112BE4 with 2 |
| 0x80113080 | compare | 0x8011308C with 3 |
| 0x80113360 | compare | 0x8011336C with 3 |
| 0x8011348C | compare | 0x80113498 with 3 |
| 0x801137D8 | compare | 0x801137E0 with 2 |
| 0x80113894 | compare | 0x801138A0 with 3 |
| 0x80117550 | compare | 0x8011755C with 3 |
| 0x801175B8 | compare | 0x801175C4 with 2 |
| 0x8011788C | index | 0x801178AC table 0x80330C70 |
| 0x801178C8 | index | 0x801178D8 table 0x80330C60 |
| 0x8011988C | compare | 0x80119894 with 2 |
| 0x801199CC | compare | 0x801199D4 with 2 |
| 0x8011A890 | compare | 0x8011A898 with 2 |
| 0x8011C8D4 | index | 0x8011C8F0 table 0x803E80B0 |
| 0x8011C948 | index | 0x8011C95C table 0x803E80C0 |
| 0x8011C9C0 | index | 0x8011C9DC table 0x803E80B0 |
| 0x8011CDA8 | index | 0x8011CDBC table 0x803E80C0 |
| 0x8011CE58 | index | 0x8011CE74 table 0x803E80B0 |
| 0x80120FDC | index | 0x80120FF4 table 0x803E8118 |
| 0x80121088 | index | 0x801210A4 table 0x803E8108 |
| 0x801210F4 | index | 0x8012110C table 0x803E8118 |
| 0x801215E4 | index | 0x801215F8 table 0x803E8128 |
| 0x801215FC | index | 0x80121618 table 0x803E8138 |
| 0x80121948 | compare | 0x80121950 with 2 |
| 0x801219C8 | compare | 0x801219D0 with 1 |
| 0x80121D10 | index | 0x80121D68 |
| 0x801220E4 | compare | 0x801220F8 with 2 |
| 0x8012230C | index | 0x8012231C table 0x803E8228 |
| 0x80122330 | index | 0x80122340 table 0x803E8228 |
| 0x80122354 | compare | 0x8012235C with 2 |
| 0x80122CA4 | index | 0x80122CB0 table 0x803E8148 |
| 0x80122CC0 | index | 0x80122CCC table 0x803E8168 |
| 0x801230D8 | other | no use found |
| 0x80126FFC | other | 0x8012700C (value overwritten) |
| 0x80127210 | compare | 0x80127214 with 3 |
| 0x8012731C | compare | 0x80127324 with 2 |
| 0x8012851C | compare | 0x80128524 with 2 |
| 0x8012878C | compare | 0x80128794 with 2 |
| 0x801289F4 | compare | 0x80128A00 with 2 |
| 0x80129DEC | compare | 0x80129DF0 with 2 |
| 0x8012A014 | index | 0x8012A030 table 0x803E82E0 |
| 0x8012A1B4 | index | 0x8012A1D0 table 0x803E82E0 |
| 0x8012CC34 | index | 0x8012CC44 |
| 0x8012CC54 | index | 0x8012CC64 |
| 0x8012D410 | store | 0x8012D418 |
| 0x8012D4E8 | index | 0x8012D504 table 0x803E8588 |
| 0x8012EFA4 | compare | 0x8012EFA8 with 2 |
| 0x8012F304 | compare | 0x8012F30C with 2 |
| 0x8012F404 | compare | 0x8012F40C with 2 |
| 0x8012F504 | compare | 0x8012F50C with 2 |
| 0x8012F6AC | compare | 0x8012F6B4 with 2 |
| 0x8012F854 | compare | 0x8012F85C with 2 |
| 0x8012F9D8 | compare | 0x8012F9E0 with 2 |
| 0x8012FB5C | compare | 0x8012FB64 with 2 |
| 0x8012FCE0 | compare | 0x8012FCE8 with 2 |
| 0x801311D8 | compare | 0x801311E4 with 3 |
| 0x801312D0 | compare | 0x801312D4 with 2 |
| 0x80131374 | compare | 0x80131378 with 2 |
| 0x801314DC | compare | 0x801314E0 with 2 |
| 0x80131624 | compare | 0x80131628 with 2 |
| 0x801317A0 | compare | 0x801317A4 with 2 |
| 0x80131984 | compare | 0x8013198C with 2 |
| 0x80131FF0 | compare | 0x80131FF4 with 2 |
| 0x801320AC | compare | 0x801320B0 with 2 |
| 0x801321A8 | compare | 0x801321AC with 2 |
| 0x8013232C | compare | 0x80132330 with 2 |
| 0x80132410 | compare | 0x8013241C with 3 |
| 0x80132520 | compare | 0x80132524 with 2 |
| 0x801337A0 | compare | 0x801337A4 with 2 |
| 0x80133844 | compare | 0x80133848 with 2 |
| 0x80133988 | compare | 0x8013398C with 2 |
| 0x80133A2C | compare | 0x80133A30 with 2 |
| 0x80137440 | compare | 0x80137444 with 2 |
| 0x801385C4 | compare | 0x801385C8 with 2 |
| 0x80138828 | compare | 0x8013882C with 2 |
| 0x80138938 | compare | 0x8013893C with 2 |
| 0x80139370 | compare | 0x80139374 with 2 |
| 0x8013942C | compare | 0x80139430 with 2 |
| 0x8013A400 | compare | 0x8013A404 with 2 |
| 0x8013A410 | compare | 0x8013A414 with 2 |
| 0x8013A4A4 | compare | 0x8013A4A8 with 2 |
| 0x8013A620 | compare | 0x8013A624 with 2 |
| 0x8013B0A0 | compare | 0x8013B0A4 with 2 |
| 0x8013B144 | compare | 0x8013B148 with 2 |
| 0x8013B2E8 | compare | 0x8013B2EC with 2 |
| 0x8013BA54 | index | 0x8013BA68 table 0x803E86F8 |
| 0x80141C98 | argument | 0x80141CAC (in r3) |
| 0x80142038 | compare | 0x80142048 with 1 |
| 0x8014240C | argument | 0x80142420 (in r3) |
| 0x80142580 | index | 0x80142598 table 0x803E8708 |
| 0x80142FA8 | index | 0x80142FB8 |
| 0x80143850 | index | 0x80143860 |
| 0x80143878 | index | 0x80143888 |
| 0x80143894 | index | 0x801438A4 |
| 0x801438B0 | index | 0x801438C0 |
| 0x80143BE4 | index | 0x80143C00 table 0x803E8988 |
| 0x801457D0 | compare | 0x801457D4 with 2 |
| 0x80145BB4 | compare | 0x80145BBC with 2 |
| 0x801464F4 | compare | 0x801464F8 with 2 |
| 0x80146668 | compare | 0x80146670 with 1 |
| 0x80146E14 | compare | 0x80146E1C with a register |
| 0x80146F38 | compare | 0x80146F40 with a register |
| 0x80146F8C | compare | 0x80146F98 with 3 |
| 0x80147020 | compare | 0x80147024 with 3 |
| 0x801470D4 | argument | 0x801470F4 (in r3) |
| 0x801475D8 | compare | 0x801475E4 with 2 |
| 0x801478BC | argument | 0x801478DC (in r3) |
| 0x80147ED0 | compare | 0x80147ED8 with 2 |
| 0x80148158 | compare | 0x80148160 with 2 |
| 0x8014820C | compare | 0x80148214 with 2 |
| 0x801482B0 | compare | 0x801482B8 with 2 |
| 0x80151018 | other | 0x8015101C word 0x3803FFFF |
| 0x80151184 | compare | 0x8015118C with 2 |
| 0x80151584 | compare | 0x8015158C with 2 |
| 0x801531EC | compare | 0x801531F4 with 2 |
| 0x80153900 | index | 0x80153920 table 0x803E91E0 |
| 0x80158254 | index | 0x80158264 |
| 0x80159CC8 | compare | 0x80159CD0 with 3 |
| 0x801606C8 | compare | 0x801606CC with 2 |
| 0x80160D70 | argument | 0x80160D90 (in r3) |
| 0x80160E68 | argument | 0x80160E88 (in r3) |
| 0x80167594 | index | 0x801675A4 |
| 0x8016A2BC | argument | 0x8016A2DC (in r3) |
| 0x8016A3AC | argument | 0x8016A3CC (in r3) |
| 0x8016B0D0 | argument | 0x8016B0F0 (in r3) |
| 0x8016C31C | index | 0x8016C330 table 0x803E9A60 |
| 0x8016C394 | index | 0x8016C3A8 table 0x803E9A70 |
| 0x8016C72C | index | 0x8016C740 table 0x803E9A70 |
| 0x8016C824 | index | 0x8016C838 table 0x803E9A60 |
| 0x8016C868 | index | 0x8016C87C table 0x803E9A60 |
| 0x8016C8BC | index | 0x8016C8D0 table 0x803E9A70 |
| 0x8016E5D0 | compare | 0x8016E5E0 with 2 |
| 0x8016FC4C | compare | 0x8016FC54 with 2 |
| 0x80170454 | compare | 0x8017045C with 2 |
| 0x801710AC | compare | 0x801710B4 with 2 |
| 0x801748F4 | compare | 0x801748FC with 2 |
| 0x80174A04 | index | 0x80174A20 table 0x803EA038 |
| 0x80176B6C | compare | 0x80176B70 with 2 |
| 0x801933F0 | index | 0x80193410 table 0x803EBB88 |
| 0x80194388 | other | 0x8019439C word 0x38050159 |
| 0x8019570C | compare | 0x80195718 with a register |
| 0x801D3814 | compare | 0x801D3818 with 1 |
| 0x801F21E8 | compare | 0x801F21EC with 1 |
| 0x802093BC | compare | 0x802093C4 with 2 |
| 0x80217604 | compare | 0x80217608 with 1 |
| 0x80220954 | compare | 0x80220958 with 1 |
| 0x8023D198 | other | 0x8023D1C4 (value overwritten) |
| 0x80245490 | argument | 0x802454B0 (in r3) |
| 0x80C05830 (relF, analysis layout) | argument | 0x80C05850 (in r3) |
| 0x80C05C6C (relF, analysis layout) | compare | 0x80C05C70 with 1 |
| 0x80C05CF0 (relF, analysis layout) | compare | 0x80C05CF8 with 1 |
| 0x80C05DC0 (relF, analysis layout) | compare | 0x80C05DC8 with 1 |
| 0x80C05E90 (relF, analysis layout) | compare | 0x80C05E98 with 1 |
| 0x80C05F44 (relF, analysis layout) | compare | 0x80C05F48 with 1 |
| 0x80C06A48 (relF, analysis layout) | compare | 0x80C06A4C with 2 |
| 0x80C06ACC (relF, analysis layout) | compare | 0x80C06AD0 with 1 |
| 0x80C06B38 (relF, analysis layout) | compare | 0x80C06B3C with 1 |
| 0x80C06BF0 (relF, analysis layout) | compare | 0x80C06BF8 with 1 |
| 0x80C06CC0 (relF, analysis layout) | compare | 0x80C06CC8 with 1 |
| 0x80C06D90 (relF, analysis layout) | compare | 0x80C06D94 with 1 |
| 0x80C087AC (relF, analysis layout) | compare | 0x80C087B4 with 1 |
| 0x80C0E9B8 (relF, analysis layout) | compare | 0x80C0E9BC with 1 |
| 0x80C107B0 (relF, analysis layout) | compare | 0x80C107B4 with 1 |
| 0x80C1376C (relF, analysis layout) | compare | 0x80C13770 with 1 |
| 0x80C137D4 (relF, analysis layout) | compare | 0x80C137D8 with 1 |
| 0x80C13844 (relF, analysis layout) | compare | 0x80C13848 with 1 |
| 0x80C14934 (relF, analysis layout) | compare | 0x80C1493C with 1 |
| 0x80C149E8 (relF, analysis layout) | compare | 0x80C149EC with 1 |
| 0x80C15A14 (relF, analysis layout) | other | no use found |
| 0x80C18F98 (relF, analysis layout) | argument | 0x80C18FB8 (in r3) |
| 0x80C190BC (relF, analysis layout) | other | no use found |
| 0x80C1A500 (relF, analysis layout) | compare | 0x80C1A504 with 1 |
| 0x80C1A6B4 (relF, analysis layout) | compare | 0x80C1A6B8 with 1 |
| 0x80C1AEC0 (relF, analysis layout) | compare | 0x80C1AEC4 with 1 |
| 0x80C1AF28 (relF, analysis layout) | compare | 0x80C1AF30 with 2 |
| 0x80C1B4E8 (relF, analysis layout) | compare | 0x80C1B4EC with 1 |
| 0x80C1B5CC (relF, analysis layout) | compare | 0x80C1B5D0 with 1 |
| 0x80C1BE0C (relF, analysis layout) | compare | 0x80C1BE10 with 1 |
| 0x80C1BE74 (relF, analysis layout) | compare | 0x80C1BE7C with 1 |
| 0x80C25014 (relF, analysis layout) | compare | 0x80C25018 with 1 |
| 0x80C25088 (relF, analysis layout) | compare | 0x80C2508C with 1 |
| 0x80C262D4 (relF, analysis layout) | compare | 0x80C262D8 with 1 |
| 0x80C291A4 (relF, analysis layout) | compare | 0x80C291A8 with 1 |
| 0x80C2920C (relF, analysis layout) | compare | 0x80C29210 with 1 |
| 0x80C29274 (relF, analysis layout) | compare | 0x80C29278 with 1 |
| 0x80C29450 (relF, analysis layout) | compare | 0x80C29454 with 1 |
| 0x80C294DC (relF, analysis layout) | compare | 0x80C294E0 with 1 |
| 0x80C2B308 (relF, analysis layout) | index | 0x80C2B31C table 0x80DAC230 |
| 0x80C2C320 (relF, analysis layout) | compare | 0x80C2C324 with 3 |
| 0x80C2C380 (relF, analysis layout) | compare | 0x80C2C384 with 2 |
| 0x80C2C390 (relF, analysis layout) | compare | 0x80C2C394 with 3 |
| 0x80C2D590 (relF, analysis layout) | index | 0x80C2D5A4 table 0x80DAC230 |
| 0x80C32EF8 (relF, analysis layout) | compare | 0x80C32EFC with 1 |
| 0x80C33224 (relF, analysis layout) | index | 0x80C3323C table 0x80DC3EC8 |
| 0x80C3327C (relF, analysis layout) | index | 0x80C33288 table 0x80DC3EC8 |
| 0x80C34984 (relF, analysis layout) | compare | 0x80C34988 with 1 |
| 0x80C34CF4 (relF, analysis layout) | compare | 0x80C34CF8 with 1 |
| 0x80C35024 (relF, analysis layout) | compare | 0x80C3502C with 2 |
| 0x80C35C24 (relF, analysis layout) | compare | 0x80C35C28 with 1 |
| 0x80C36454 (relF, analysis layout) | compare | 0x80C36458 with 1 |
| 0x80C364C4 (relF, analysis layout) | compare | 0x80C364C8 with 2 |
| 0x80C36550 (relF, analysis layout) | compare | 0x80C36554 with 2 |
| 0x80C36980 (relF, analysis layout) | compare | 0x80C36984 with 1 |
| 0x80C39768 (relF, analysis layout) | compare | 0x80C3976C with 1 |
| 0x80C3C718 (relF, analysis layout) | compare | 0x80C3C71C with 1 |
| 0x80C3C780 (relF, analysis layout) | compare | 0x80C3C784 with 1 |
| 0x80C3C808 (relF, analysis layout) | compare | 0x80C3C80C with 1 |
| 0x80C3EBB4 (relF, analysis layout) | argument | 0x80C3EBD4 (in r3) |
| 0x80C41F50 (relF, analysis layout) | compare | 0x80C41F54 with 1 |
| 0x80C4D444 (relF, analysis layout) | compare | 0x80C4D44C with 2 |
| 0x80C4D830 (relF, analysis layout) | compare | 0x80C4D838 with 2 |
| 0x80C5C554 (relF, analysis layout) | store | 0x80C5C56C |
| 0x80C61684 (relF, analysis layout) | compare | 0x80C6168C with 2 |
