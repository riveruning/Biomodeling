# BioModel research report

Artifact: `39a0b0de28852d79331e55c22f7c91f1b38efef92367745afc2c5f56ee04c46d`
Kind: `hypothesis_study`

Study execution: **completed**

## Frozen candidate comparison

{
  "adequacy_weighted_rmse": 3,
  "note": "Specified known independent Gaussian noise; no significance test or proof of mechanism",
  "ranking": "best_compatible_candidate",
  "separation_threshold": 0.05,
  "winner": "cand_feedback"
}

| Candidate | Fit | Training weighted RMSE | Held-out scaled RMSE | Cross-backend check |
|---|---|---|---|---|
| cand_baseline | succeeded | 41.253297114445125 | 1.3044437298520668 | True |
| cand_feedback | succeeded | 2.0542668008747743e-08 | 6.906939272239231e-10 | True |
| cand_feedforward | succeeded | 4.844804766733239e-08 | 0.3178210303415662 | True |

## Conditional intervention

Intervention: `b=0`
Nominal prediction gap: 0.3333333352294061
Conditional on candidate structures and nominal fitted values; parameter uncertainty not fully propagated.

## Limits

- Synthetic, linearized candidate templates; not open mechanism discovery
- Local sensitivity and bounds are not global identifiability or confidence intervals
- Readout x is a signed deviation, not a concentration
- d/h units unknown; not fully dimensionally verified
- No holdout refitting; no biological truth claim

## Direct provenance refs

- `bfc610675b641c035ba9ff3784c32ff9f5d926d64a9cb5f2a92c54e8e6f097c0`
- `98d86eadd2077f70e2f395d3b0a6b978982f732268c02228df62a13a0248a74c`
- `16ba4582fee20c8779118b2f416a131872ec9c6499cd7237aa85784ad68d528b`
- `ec7ace0238ba64cfdbc1089770b6c47169ae372e80c52cc2eb4821a7a120502b`
- `26d4590e11109c93abf775d96787780118af71e22aa4889194ae225ac4983dce`
- `bbe63bcf67f3f251e0fcf9d22ada386dc543105c8b3c0fa22ef978e41aab2c20`
- `bb7525b6bcd8fcd2ab759768fff68e51d2508685f2e66e4638e93c4deadc1044`
- `4ee59d351bf82f23f544da69455efdef2703d397d6cabb11400eaf85bc693664`
- `35bcfeae40deb843e089a466267913e82b7a217b834df692ac4a4558bd668f38`
