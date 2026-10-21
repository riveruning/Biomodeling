# BioModel research report

Artifact: `256798504d3d787d8e8f8c6b63bd6cf1673b5aa36ac6e1b78b1b7adc999c7106`
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
- `bbf1916604fa6942f58723cb74c830cf50c1d77ca1e06ef0620dbe2c3f81dd55`
- `1abc3aaff6bd447b1f14372858ec40296360446a32a33848c9e0302aa6029210`
- `ef1ad47545eafd0017f29b03f3c79da9139451747062b8881e360127124de364`
- `a932bce77b1b93cfdb2c01651c62c3ca27c06cf41ae888613efe6fb7cbfd7034`
- `ade409ec1c3db37254c64c3973b528201763c55dd68bdaee3460b278bc5fcf14`
- `453d877f1f124c1b34f2dfe4d214f4ce7b96e6aeafaf0fca35f8d58e48cbc757`
- `35bcfeae40deb843e089a466267913e82b7a217b834df692ac4a4558bd668f38`
