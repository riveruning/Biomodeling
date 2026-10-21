# BioModel research report

Artifact: `adfe7a708b23f41a1c28e2c6b0594a830d8384c0005ce7e14534c7cb7d6e74b5`
Kind: `hypothesis_study`

Study execution: **completed**

## Frozen candidate comparison

{
  "adequacy_weighted_rmse": 3,
  "note": "Specified known independent Gaussian noise; no significance test or proof of mechanism",
  "ranking": "best_compatible_candidate",
  "separation_threshold": 0.05,
  "winner": "cand_feedforward"
}

| Candidate | Fit | Training weighted RMSE | Held-out scaled RMSE | Cross-backend check |
|---|---|---|---|---|
| cand_baseline | succeeded | 41.169125213709364 | 1.5224432453456265 | True |
| cand_feedback | succeeded | 0.7893881133418222 | 0.2906149900426513 | True |
| cand_feedforward | succeeded | 0.7893881091669963 | 0.010359869360647495 | True |

## Conditional intervention

Intervention: `b=0`
Nominal prediction gap: 0.32849034374723707
Conditional on candidate structures and nominal fitted values; parameter uncertainty not fully propagated.

## Limits

- Synthetic, linearized candidate templates; not open mechanism discovery
- Local sensitivity and bounds are not global identifiability or confidence intervals
- Readout x is a signed deviation, not a concentration
- d/h units unknown; not fully dimensionally verified
- No holdout refitting; no biological truth claim

## Direct provenance refs

- `00b45a608e34683ed15b23efd89542ad19be55ee3f32c0f8b1f1d2bd4ffd06b1`
- `acbb75877f41fccdf387289deba45e82ce03b11c7efd80f29f1ade51694ddadd`
- `195c8b5bce50e4c4eab31c9e1a65e51b3c3fb2c7a7713319e0adfb5d611bc7f7`
- `717462145561f211c28484dabee8c7b465aed89d56a317dc9667dec1d18d366c`
- `d034a414aead25c0722389ce3ca4e0e4a039cad024092171cf355a175ec11887`
- `376b378994dbc0678f6926cf487fa8f2ecde53f5bb76004d791615bc7d132863`
- `73d5ac0ce1d36e9513c62e64a3db5878cffb5162650e21c000dbdb654da221df`
- `ff16aa3bd010312c8e8077a37e34d9a381ea2a152b92def8f254a61180f8f724`
