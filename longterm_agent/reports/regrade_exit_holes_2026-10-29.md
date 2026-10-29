# Regrade: exits priced off a bar that was not there — 2026-10-29

Recomputed with exit_rules.exit_close / has_exit_bar (fixed 2026-10-29). Evidence from first landings (`fetch_time`): `hole` = landed after a later bar of the same ticker, still missing at the grade; `after_grade` = landed after the row's grade (07:00 ET on outcome_filled_at); `intraday_bar` = an exit bar first written during its own session; `legacy_convention` = the stored value is the pre-open_t1 close→close return. Columns without evidence are listed, not rewritten. Every row with its evidence: the gitignored `_rows.jsonl` beside this file.

## longterm_agent_results

- by cause: hole 14, after_grade 44
- rows with a differing stored value: **58**; with evidence for at least one column: **58**; rows to rewrite: 58
- differing columns (with evidence / all): sector_benchmark_return_4w 52/52, fwd_return_2w 6/6, fwd_return_4w 2/2, benchmark_return_4w 2/2, excess_beta 2/2, fwd_return_stopped 2/2, benchmark_return_stopped 2/2

| id | row | column: stored → recomputed | evidence (first) |
|---|---|---|---|
| 26 | 0.1.0 TSLA 2026-06-22 SHORT | fwd_return_4w: -0.0378 → -0.0348; benchmark_return_4w: 0.0202 → 0.0197; excess_beta: 0.0742 → 0.0705; fwd_return_stopped: -0.0378 → -0.0348; benchmark_return_stopped: 0.0202 → 0.0197; sector_benchmark_return_4w: 0.0078 → 0.0081 | after_grade TSLA 2026-07-21 @07-21 15:47 |
| 29 | 0.1.0 GOOGL 2026-06-22 SHORT | fwd_return_4w: 0.0239 → 0.019; benchmark_return_4w: 0.0202 → 0.0197; excess_beta: 0.0014 → 0.0058; fwd_return_stopped: 0.0239 → 0.019; benchmark_return_stopped: 0.0202 → 0.0197; sector_benchmark_return_4w: 0.0361 → 0.033 | after_grade GOOGL 2026-07-21 @07-21 15:47 |
| 319 | 0.2.0 GOOG 2026-08-17 LONG | sector_benchmark_return_4w: 0.038 → 0.0286 | after_grade XLC 2026-09-15 @09-16 20:30 |
| 326 | 0.2.0 AMD 2026-08-17 LONG | sector_benchmark_return_4w: -0.0108 → -0.0137 | after_grade XLK 2026-09-15 @09-16 20:30 |
| 331 | 0.2.0 CSCO 2026-08-17 SHORT | sector_benchmark_return_4w: -0.0108 → -0.0137 | after_grade XLK 2026-09-15 @09-16 20:30 |
| 336 | 0.2.0 NOW 2026-08-17 SHORT | sector_benchmark_return_4w: -0.0108 → -0.0137 | after_grade XLK 2026-09-15 @09-16 20:30 |
| 343 | 0.2.0 VZ 2026-08-17 SHORT | sector_benchmark_return_4w: 0.038 → 0.0286 | after_grade XLC 2026-09-15 @09-16 20:30 |
| 356 | 0.2.0 BRK-B 2026-08-17 LONG | sector_benchmark_return_4w: -0.0121 → -0.0152 | after_grade XLF 2026-09-15 @09-16 20:30 |
| 358 | 0.2.0 HD 2026-08-17 SHORT | sector_benchmark_return_4w: -0.033 → -0.0499 | after_grade XLY 2026-09-15 @09-16 20:30 |
| 359 | 0.2.0 COST 2026-08-17 SHORT | sector_benchmark_return_4w: -0.0156 → -0.0237 | after_grade XLP 2026-09-15 @09-16 20:30 |
| 362 | 0.2.0 SBUX 2026-08-17 SHORT | sector_benchmark_return_4w: -0.033 → -0.0499 | after_grade XLY 2026-09-15 @09-16 20:30 |
| 378 | 0.2.0 MRK 2026-08-17 SHORT | sector_benchmark_return_4w: -0.0055 → -0.006 | after_grade XLV 2026-09-15 @09-16 20:30 |
| 379 | 0.2.0 ABBV 2026-08-17 LONG | sector_benchmark_return_4w: -0.0055 → -0.006 | after_grade XLV 2026-09-15 @09-16 20:30 |
| 383 | 0.2.0 GOOG 2026-08-24 LONG | sector_benchmark_return_4w: 0.0208 → 0.01 | hole XLC 2026-09-22 @09-25 20:30 |
| 393 | 0.2.0 CRM 2026-08-24 LONG | sector_benchmark_return_4w: 0.07 → 0.0778 | hole XLK 2026-09-22 @09-25 20:30 |
| 400 | 0.2.0 NOW 2026-08-24 SHORT | sector_benchmark_return_4w: 0.07 → 0.0778 | hole XLK 2026-09-22 @09-25 20:30 |
| 405 | 0.2.0 CMCSA 2026-08-24 SHORT | sector_benchmark_return_4w: 0.0208 → 0.01 | hole XLC 2026-09-22 @09-25 20:30 |
| 420 | 0.2.0 BRK-B 2026-08-24 LONG | sector_benchmark_return_4w: -0.0394 → -0.0583 | after_grade XLF 2026-09-22 @09-23 20:30 |
| 423 | 0.2.0 COST 2026-08-24 LONG | sector_benchmark_return_4w: -0.0569 → -0.0475 | hole XLP 2026-09-22 @09-25 20:30 |
| 426 | 0.2.0 SBUX 2026-08-24 SHORT | sector_benchmark_return_4w: -0.0522 → -0.0513 | hole XLY 2026-09-22 @09-25 20:30 |
| 436 | 0.2.0 LMT 2026-08-24 LONG | sector_benchmark_return_4w: -0.0596 → -0.058 | hole XLI 2026-09-22 @09-25 20:30 |
| 442 | 0.2.0 MRK 2026-08-24 SHORT | sector_benchmark_return_4w: -0.0312 → -0.0261 | hole XLV 2026-09-22 @09-25 20:30 |
| 449 | 0.2.0 NVDA 2026-08-31 LONG | sector_benchmark_return_4w: 0.059 → 0.0588 | after_grade XLK 2026-09-29 @09-30 20:30 |
| 450 | 0.2.0 META 2026-08-31 SHORT | sector_benchmark_return_4w: 0.0056 → 0.0082 | after_grade XLC 2026-09-29 @09-30 20:30 |
| 451 | 0.2.0 TSLA 2026-08-31 SHORT | sector_benchmark_return_4w: -0.0556 → -0.0543 | after_grade XLY 2026-09-29 @09-30 20:30 |
| 452 | 0.2.0 AVGO 2026-08-31 SHORT | sector_benchmark_return_4w: 0.059 → 0.0588 | after_grade XLK 2026-09-29 @09-30 20:30 |
| 457 | 0.2.0 CRM 2026-08-31 LONG | sector_benchmark_return_4w: 0.059 → 0.0588 | after_grade XLK 2026-09-29 @09-30 20:30 |
| 520 | 0.2.0 PANW 2026-09-07 LONG | fwd_return_2w: 0.1349 → 0.1435 | hole PANW 2026-09-22 @09-25 20:30 |
| 526 | 0.2.0 TMUS 2026-09-07 LONG | fwd_return_2w: -0.0881 → -0.1039 | hole TMUS 2026-09-22 @09-25 20:30 |
| 533 | 0.2.0 GS 2026-09-07 LONG | fwd_return_2w: -0.078 → -0.0875 | hole GS 2026-09-22 @09-25 20:30 |
| 538 | 0.2.0 BRK-B 2026-09-07 LONG | fwd_return_2w: -0.002 → 0.001 | hole BRK-B 2026-09-22 @09-25 20:30 |
| 549 | 0.2.0 CVX 2026-09-07 LONG | fwd_return_2w: -0.0359 → -0.0418 | hole CVX 2026-09-22 @09-25 20:30 |
| 558 | 0.2.0 LLY 2026-09-07 LONG | fwd_return_2w: 0.0281 → 0.0328 | hole LLY 2026-09-22 @09-25 20:30 |
| 602 | 0.2.0 BRK-B 2026-09-14 LONG | sector_benchmark_return_4w: -0.0337 → -0.0377 | after_grade XLF 2026-10-13 @10-14 20:30 |
| 614 | 0.2.0 COP 2026-09-14 LONG | sector_benchmark_return_4w: 0.0174 → 0.0358 | after_grade XLE 2026-10-13 @10-14 20:30 |
| 622 | 0.2.0 LLY 2026-09-14 LONG | sector_benchmark_return_4w: 0.025 → 0.0111 | after_grade XLV 2026-10-13 @10-14 20:30 |
| 624 | 0.2.0 MRK 2026-09-14 SHORT | sector_benchmark_return_4w: 0.025 → 0.0111 | after_grade XLV 2026-10-13 @10-14 20:30 |
| 635 | 0.2.0 ORCL 2026-09-21 LONG | sector_benchmark_return_4w: -0.0443 → -0.047 | after_grade XLK 2026-10-20 @10-21 20:30 |
| 640 | 0.2.0 ADBE 2026-09-21 SHORT | sector_benchmark_return_4w: -0.0443 → -0.047 | after_grade XLK 2026-10-20 @10-21 20:30 |
| 651 | 0.2.0 CMCSA 2026-09-21 SHORT | sector_benchmark_return_4w: -0.0423 → -0.0414 | after_grade XLC 2026-10-20 @10-21 20:30 |
| 660 | 0.2.0 WFC 2026-09-21 LONG | sector_benchmark_return_4w: -0.0168 → -0.005 | after_grade XLF 2026-10-20 @10-21 20:30 |
| 666 | 0.2.0 BRK-B 2026-09-21 LONG | sector_benchmark_return_4w: -0.0168 → -0.005 | after_grade XLF 2026-10-20 @10-21 20:30 |
| 670 | 0.2.0 NKE 2026-09-21 SHORT | sector_benchmark_return_4w: 0.0131 → 0.0223 | after_grade XLY 2026-10-20 @10-21 20:30 |
| 672 | 0.2.0 SBUX 2026-09-21 LONG | sector_benchmark_return_4w: 0.0131 → 0.0223 | after_grade XLY 2026-10-20 @10-21 20:30 |
| 678 | 0.2.0 COP 2026-09-21 LONG | sector_benchmark_return_4w: 0.1001 → 0.1029 | after_grade XLE 2026-10-20 @10-21 20:30 |
| 680 | 0.2.0 CAT 2026-09-21 LONG | sector_benchmark_return_4w: -0.0234 → -0.0227 | after_grade XLI 2026-10-20 @10-21 20:30 |
| 682 | 0.2.0 LMT 2026-09-21 LONG | sector_benchmark_return_4w: -0.0234 → -0.0227 | after_grade XLI 2026-10-20 @10-21 20:30 |
| 686 | 0.2.0 LLY 2026-09-21 LONG | sector_benchmark_return_4w: 0.0406 → 0.0653 | after_grade XLV 2026-10-20 @10-21 20:30 |
| 689 | 0.2.0 ABBV 2026-09-21 LONG | sector_benchmark_return_4w: 0.0406 → 0.0653 | after_grade XLV 2026-10-20 @10-21 20:30 |
| 714 | 0.2.0 DIS 2026-09-28 SHORT | sector_benchmark_return_4w: -0.0683 → -0.0707 | after_grade XLC 2026-10-27 @10-28 20:30 |
| 715 | 0.2.0 CMCSA 2026-09-28 SHORT | sector_benchmark_return_4w: -0.0683 → -0.0707 | after_grade XLC 2026-10-27 @10-28 20:30 |
| 716 | 0.2.0 T 2026-09-28 LONG | sector_benchmark_return_4w: -0.0683 → -0.0707 | after_grade XLC 2026-10-27 @10-28 20:30 |
| 734 | 0.2.0 NKE 2026-09-28 SHORT | sector_benchmark_return_4w: 0.0449 → 0.0558 | after_grade XLY 2026-10-27 @10-28 20:30 |
| 736 | 0.2.0 SBUX 2026-09-28 LONG | sector_benchmark_return_4w: 0.0449 → 0.0558 | after_grade XLY 2026-10-27 @10-28 20:30 |
| 737 | 0.2.0 KO 2026-09-28 LONG | sector_benchmark_return_4w: 0.0781 → 0.0681 | after_grade XLP 2026-10-27 @10-28 20:30 |
| 744 | 0.2.0 CAT 2026-09-28 LONG | sector_benchmark_return_4w: -0.0128 → -0.0071 | after_grade XLI 2026-10-27 @10-28 20:30 |
| 745 | 0.2.0 GE 2026-09-28 LONG | sector_benchmark_return_4w: -0.0128 → -0.0071 | after_grade XLI 2026-10-27 @10-28 20:30 |
| 753 | 0.2.0 ABBV 2026-09-28 LONG | sector_benchmark_return_4w: 0.0512 → 0.0506 | after_grade XLV 2026-10-27 @10-28 20:30 |

## shift_library

- by cause: legacy_convention 15, hole 7, intraday_bar 2, after_grade 7
- rows with a differing stored value: **31**; with evidence for at least one column: **31**; rows to rewrite: 31
- differing columns (with evidence / all): fwd_return_4w 31/31, benchmark_return_4w 17/17, fwd_return_2w 15/15

| id | row | column: stored → recomputed | evidence (first) |
|---|---|---|---|
| 69 | TSLA 2026-06-22 | fwd_return_4w: -0.0378 → -0.0348; benchmark_return_4w: 0.0202 → 0.0197 | intraday_bar TSLA 2026-07-21 @07-21 15:47 |
| 74 | GOOGL 2026-06-22 | fwd_return_4w: 0.0239 → 0.019; benchmark_return_4w: 0.0202 → 0.0197 | intraday_bar GOOGL 2026-07-21 @07-21 15:47 |
| 249 | ADBE 2026-06-18 | fwd_return_2w: 0.1258 → 0.1276; fwd_return_4w: 0.2057 → 0.2138; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention ADBE close 2026-06-18→+14d |
| 250 | MU 2026-06-18 | fwd_return_2w: -0.1397 → -0.1768; fwd_return_4w: -0.2476 → -0.2765; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention MU close 2026-06-18→+14d |
| 251 | COST 2026-06-18 | fwd_return_2w: 0.0002 → 0.0018; fwd_return_4w: -0.0062 → -0.0135; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention COST close 2026-06-18→+14d |
| 252 | NKE 2026-06-18 | fwd_return_2w: -0.0246 → -0.0287; fwd_return_4w: -0.0139 → -0.0258; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention NKE close 2026-06-18→+14d |
| 253 | COP 2026-06-18 | fwd_return_2w: -0.0279 → -0.0543; fwd_return_4w: 0.0473 → 0.0561; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention COP close 2026-06-18→+14d |
| 254 | GE 2026-06-18 | fwd_return_2w: 0.0556 → 0.0588; fwd_return_4w: -0.0333 → -0.0457; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention GE close 2026-06-18→+14d |
| 255 | LMT 2026-06-18 | fwd_return_2w: 0.0684 → 0.058; fwd_return_4w: 0.005 → 0.002; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention LMT close 2026-06-18→+14d |
| 256 | RTX 2026-06-18 | fwd_return_2w: 0.0735 → 0.0909; fwd_return_4w: 0.0472 → 0.0534; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention RTX close 2026-06-18→+14d |
| 260 | GS 2026-06-25 | fwd_return_4w: 0.0189 → 0.0062 | after_grade GS 2026-07-24 @07-28 12:16 |
| 261 | MS 2026-06-25 | fwd_return_4w: -0.0206 → -0.0238 | after_grade MS 2026-07-24 @07-28 12:16 |
| 263 | COST 2026-06-25 | fwd_return_4w: -0.0285 → -0.0191 | after_grade COST 2026-07-24 @07-28 12:16 |
| 264 | ABBV 2026-06-25 | fwd_return_4w: 0.0475 → 0.0575 | after_grade ABBV 2026-07-24 @07-28 12:16 |
| 429 | TSLA 2026-06-18 | fwd_return_2w: -0.0176 → 0.0631; fwd_return_4w: -0.0235 → -0.064; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention TSLA close 2026-06-18→+14d |
| 430 | MU 2026-06-18 | fwd_return_2w: -0.1397 → -0.1768; fwd_return_4w: -0.2476 → -0.2765; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention MU close 2026-06-18→+14d |
| 545 | COP 2026-06-18 | fwd_return_2w: -0.0279 → -0.0543; fwd_return_4w: 0.0473 → 0.0561; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention COP close 2026-06-18→+14d |
| 546 | GE 2026-06-18 | fwd_return_2w: 0.0556 → 0.0588; fwd_return_4w: -0.0333 → -0.0457; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention GE close 2026-06-18→+14d |
| 547 | LMT 2026-06-18 | fwd_return_2w: 0.0684 → 0.058; fwd_return_4w: 0.005 → 0.002; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention LMT close 2026-06-18→+14d |
| 548 | RTX 2026-06-18 | fwd_return_2w: 0.0735 → 0.0909; fwd_return_4w: 0.0472 → 0.0534; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention RTX close 2026-06-18→+14d |
| 549 | LLY 2026-06-18 | fwd_return_2w: 0.105 → 0.079; fwd_return_4w: 0.0643 → 0.0312; benchmark_return_4w: 0.0053 → -0.0075 | legacy_convention LLY close 2026-06-18→+14d |
| 554 | HD 2026-06-25 | fwd_return_4w: -0.0667 → -0.0429 | after_grade HD 2026-07-24 @07-28 12:16 |
| 555 | COST 2026-06-25 | fwd_return_4w: -0.0285 → -0.0191 | after_grade COST 2026-07-24 @07-28 12:16 |
| 562 | ABBV 2026-06-25 | fwd_return_4w: 0.0475 → 0.0575 | after_grade ABBV 2026-07-24 @07-28 12:16 |
| 863 | CRM 2026-08-24 | fwd_return_4w: 0.1485 → 0.1333 | hole CRM 2026-09-22 @09-25 20:30 |
| 866 | BRK-B 2026-08-24 | fwd_return_4w: -0.0038 → -0.0009 | hole BRK-B 2026-09-22 @09-25 20:30 |
| 867 | HD 2026-08-24 | fwd_return_4w: -0.1243 → -0.1002 | hole HD 2026-09-22 @09-25 20:30 |
| 868 | COST 2026-08-24 | fwd_return_4w: -0.0698 → -0.0688 | hole COST 2026-09-22 @09-25 20:30 |
| 869 | SBUX 2026-08-24 | fwd_return_4w: -0.1118 → -0.1099 | hole SBUX 2026-09-22 @09-25 20:30 |
| 870 | LMT 2026-08-24 | fwd_return_4w: -0.0509 → -0.0741 | hole LMT 2026-09-22 @09-25 20:30 |
| 871 | MRK 2026-08-24 | fwd_return_4w: -0.0099 → -0.0006 | hole MRK 2026-09-22 @09-25 20:30 |

## backtest_runs

- by cause: intraday_bar 97, after_grade 330
- rows with a differing stored value: **427**; with evidence for at least one column: **427**; rows to rewrite: 427
- differing columns (with evidence / all): benchmark_return 427/427, fwd_return 425/425, excess 137/137, excess_beta 136/136, fwd_return_stopped 91/91, benchmark_return_stopped 91/91, excess_stopped 91/91, fwd_return_t2 90/90, benchmark_return_t2 90/90, exit_days 81/81, excess_t2 29/29

| backtest_id | legacy_convention | hole | intraday_bar | after_grade |
|---|---|---|---|---|
| monday_t1_pit |  |  |  | 90 |
| wf_30x_deep_pit_off |  |  |  | 60 |
| wf_30x_deep_pit_on |  |  |  | 60 |
| wf_30x_f12 |  |  |  | 60 |
| wf_30x_strong_sonnet |  |  | 29 |  |
| wf_30x_v0.2_deep |  |  |  | 60 |
| wf_extra13 |  |  | 39 |  |
| wf_fresh30_pit |  |  | 29 |  |

## Evidence but no difference

Rows whose EXIT bar landed after the 07:00 ET grade, in a hole or mid-session, yet whose stored hold values match the recomputation (the grade ran later that day, or the late write did not change the close): backtest_runs after_grade 524, backtest_runs intraday_bar 294, longterm_agent_results after_grade 1, shift_library after_grade 32

