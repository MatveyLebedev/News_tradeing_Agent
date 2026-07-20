# Re-grade to `open_t1` fill convention — 2026-07-19

Entry = open of the first trading bar strictly after as_of; horizon anchored
at the entry; benchmark measured with the same convention. Decisions, labels
and prompts untouched — measurement only. `mean gap→old book` is the
sign-adjusted untradable gap (last close ≤ as_of → entry open) the old
convention credited to traded rows.

| bucket | rows | reopened | mean fwd old | new | trades | mean excess old | new | win% old | new | mean gap→old book |
|---|---|---|---|---|---|---|---|---|---|---|
| backtest/wf_2026q2_v0.1/h14 | 56 | 0 | +0.0150 | +0.0126 | 18 | +0.0350 | +0.0371 | 61% | 50% | +0.0037 |
| backtest/wf_2026q2_v0.1/h28 | 40 | 0 | +0.0266 | +0.0250 | 15 | +0.0402 | +0.0422 | 47% | 47% | -0.0003 |
| backtest/wf_2026q2_v0.2/h14 | 72 | 0 | +0.0067 | +0.0093 | 13 | +0.0098 | +0.0112 | 54% | 46% | +0.0062 |
| backtest/wf_2026q2_v0.2/h28 | 64 | 0 | +0.0107 | +0.0137 | 13 | +0.0064 | +0.0068 | 46% | 46% | +0.0062 |
| backtest/wf_30x_f12/h14 | 270 | 0 | +0.0049 | +0.0062 | 49 | +0.0062 | +0.0055 | 51% | 51% | +0.0014 |
| backtest/wf_30x_f12/h28 | 240 | 0 | +0.0053 | +0.0055 | 46 | +0.0053 | +0.0085 | 57% | 52% | +0.0009 |
| backtest/wf_30x_f12_cons/h14 | 270 | 0 | +0.0049 | +0.0062 | 48 | -0.0019 | -0.0024 | 50% | 54% | -0.0011 |
| backtest/wf_30x_f12_cons/h28 | 270 | 30 | +0.0053 | +0.0055 | 46 | +0.0011 | +0.0041 | 59% | 59% | -0.0016 |
| backtest/wf_30x_f12_cons/h84 | 30 | 30 | — | — | 0 | — | — | — | — | — |
| backtest/wf_30x_f12_cons2/h14 | 270 | 0 | +0.0049 | +0.0062 | 47 | -0.0104 | -0.0095 | 43% | 47% | -0.0016 |
| backtest/wf_30x_f12_cons2/h28 | 270 | 30 | +0.0053 | +0.0055 | 45 | -0.0097 | -0.0034 | 53% | 53% | -0.0020 |
| backtest/wf_30x_f12_cons2/h84 | 30 | 30 | — | — | 0 | — | — | — | — | — |
| backtest/wf_30x_f12_deepv2/h14 | 270 | 0 | +0.0049 | +0.0062 | 40 | -0.0096 | -0.0093 | 42% | 48% | -0.0006 |
| backtest/wf_30x_f12_deepv2/h28 | 240 | 0 | +0.0053 | +0.0055 | 38 | -0.0091 | -0.0031 | 58% | 55% | -0.0013 |
| backtest/wf_30x_f12_v3/h14 | 270 | 0 | +0.0049 | +0.0062 | 47 | +0.0058 | +0.0048 | 47% | 53% | +0.0001 |
| backtest/wf_30x_f12_v3/h28 | 240 | 0 | +0.0053 | +0.0055 | 46 | +0.0055 | +0.0091 | 59% | 54% | -0.0001 |
| backtest/wf_30x_v0.1/h14 | 270 | 0 | +0.0049 | +0.0062 | 75 | -0.0110 | -0.0090 | 45% | 40% | -0.0020 |
| backtest/wf_30x_v0.1/h28 | 240 | 0 | +0.0053 | +0.0055 | 72 | -0.0116 | -0.0049 | 49% | 47% | -0.0020 |
| backtest/wf_30x_v0.2/h14 | 270 | 0 | +0.0049 | +0.0062 | 46 | -0.0088 | -0.0098 | 50% | 48% | +0.0020 |
| backtest/wf_30x_v0.2/h28 | 240 | 0 | +0.0053 | +0.0055 | 44 | -0.0054 | -0.0000 | 55% | 55% | +0.0015 |
| backtest/wf_30x_v0.2_ct/h14 | 270 | 0 | +0.0049 | +0.0062 | 47 | -0.0098 | -0.0104 | 49% | 47% | +0.0016 |
| backtest/wf_30x_v0.2_ct/h28 | 240 | 0 | +0.0053 | +0.0055 | 45 | -0.0071 | -0.0018 | 53% | 53% | +0.0011 |
| backtest/wf_30x_v0.2_deep/h14 | 270 | 0 | +0.0049 | +0.0062 | 42 | +0.0139 | +0.0135 | 55% | 52% | +0.0028 |
| backtest/wf_30x_v0.2_deep/h28 | 240 | 0 | +0.0053 | +0.0055 | 39 | +0.0178 | +0.0234 | 62% | 62% | +0.0023 |
| backtest/wf_30x_v0.2_g2/h14 | 270 | 0 | +0.0049 | +0.0062 | 22 | -0.0145 | -0.0186 | 55% | 50% | +0.0016 |
| backtest/wf_30x_v0.2_g2/h28 | 240 | 0 | +0.0053 | +0.0055 | 22 | -0.0289 | -0.0260 | 50% | 45% | +0.0016 |
| backtest/wf_30x_v0.2_g2ct/h14 | 270 | 0 | +0.0049 | +0.0062 | 20 | +0.0114 | +0.0083 | 60% | 55% | +0.0022 |
| backtest/wf_30x_v0.2_g2ct/h28 | 240 | 0 | +0.0053 | +0.0055 | 20 | -0.0063 | -0.0069 | 55% | 50% | +0.0022 |
| shift_library | 253 | 15 | +0.0463 | +0.0359 | 0 | — | — | — | — | — |

## By as_of weekday (primary horizon rows)

| bucket | rows | reopened | mean fwd old | new | trades | mean excess old | new | win% old | new | mean gap→old book |
|---|---|---|---|---|---|---|---|---|---|---|
| backtests as_of=Fri | 5842 | 0 | +0.0054 | +0.0062 | 1005 | -0.0018 | +0.0003 | 52% | 51% | +0.0003 |

Rows touched: 6215; mode: APPLY
