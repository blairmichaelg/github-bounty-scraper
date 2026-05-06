# Walkthrough - Optimizing Bounty Ranking Signals

We have successfully optimized the GitHub Bounty Scraper to prioritize high-quality, on-chain, and no-KYC bounty opportunities.

## Changes Made

### 1. Gemini Vibe-Check Optimization (`vibe.py`)
- Updated the `SYSTEM_PROMPT` to mandate the extraction of payout structure details (KYC, escrow, wallet type) in the reasoning field.
- Refined score ranges to better distinguish between explicit rewards and ambiguous "help wanted" noise.

### 2. Signal Enrichment (`signals.py`, `signals_config.json`)
- Added new keyword lists for **no-KYC** phrases and **wallet payout** indicators.
- Extended `SignalResult` to track `has_onchain_escrow`, `mentions_no_kyc`, and `mentions_wallet_payout`.
- Plumbed these features through `core.py` to the SQLite database.

### 3. Scoring Model Calibration (`config.py`, `scoring.py`, `signals.py`)
- Adjusted composite weights to prioritize **escrow strength** (0.25) and **vibe score** (0.25).
- Implemented extra bonuses in `escrow_weight_sum` for clear on-chain signals (vaults, multisigs) and no-KYC mentions.
- Ensured the escrow normalization stays bounded in [0, 1].

### 4. Training Pipeline & ML Model (`db.py`, `tools/`)
- Updated `dump-dataset` to include the new payout signals as numeric features in the CSV.
- Created `tools/balance_dataset.py` to handle undersampling and orphan filtering.
- Created `tools/train_bounty_model.py` to perform 5-fold CV and threshold calibration.
- Successfully trained a model on the v3 dataset with **F1 = 1.0** (on current labeling rules) and identified `vibe_score` and `log_amount` as primary predictors.

### 5. Regression & Validation
- Verified that `is_bounty` labeling correctly handles the `amt=-1.0` sentinel for confirmed-but-unknown bounties.
- Fixed a regression in `tests/test_signals.py` regarding `CLOSED` issue handling.
- All 37 tests passing.

## Verification Results

### Vibe Smoke Test
The updated Gemini prompt now explicitly calls out payout structures:
- **Positive Example**: "Direct crypto payout to wallet address with no KYC required."
- **Ambiguous Example**: "Mentions a reward via Gitcoin, which implies a centralized platform with KYC requirements."

### Dataset Audit (v3)
- **Total rows**: 405 labeled (81 pos / 324 neg)
- **Imbalance**: 1:4 (balanced via undersampling)
- **Feature Coverage**: 96% body, 100% vibe.

### Model Performance
- **ROC-AUC**: 1.0000
- **Best Threshold**: 0.7012
- **Key Predictors**: `vibe_score`, `log_amount`, `merges_last_45d`.

## Next Steps
- Monitor the `has_onchain_escrow` importance as more data is scraped.
- Calibrate the `label_threshold` further if noise persists in the 15-24 USD range.
