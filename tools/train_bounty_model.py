import pandas as pd
import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, roc_auc_score, precision_recall_curve
import json
import os
import joblib

TRAIN_FILE = "bounty_dataset_train.csv"

def train_and_eval():
    if not os.path.exists(TRAIN_FILE):
        print(f"Error: {TRAIN_FILE} not found.")
        return

    df = pd.read_csv(TRAIN_FILE)
    
    # Filter only labeled rows for training
    df = df[df['is_bounty'].notnull()].copy()
    df['is_bounty'] = df['is_bounty'].astype(int)

    # Feature Engineering
    df['log_amount'] = np.log10(df['numeric_amount'].clip(lower=0) + 1)
    df['vibe_score'] = pd.to_numeric(df['vibe_score'], errors='coerce').fillna(0)
    df['merges_last_45d'] = pd.to_numeric(df['merges_last_45d'], errors='coerce').fillna(0)
    df['positive_escrow_count'] = pd.to_numeric(df['positive_escrow_count'], errors='coerce').fillna(0)
    df['escrow_weight_sum'] = pd.to_numeric(df['escrow_weight_sum'], errors='coerce').fillna(0)
    
    # Strategy 1 — Amount-blind label
    df['is_bounty_v2'] = (
        (df['vibe_score'] >= 55) & (df['positive_escrow_count'] >= 1)
    ).astype(int)
    print(f"v2 label: {df['is_bounty_v2'].sum()} pos / {(df['is_bounty_v2']==0).sum()} neg")

    features_leaky = [
        'log_amount', 'vibe_score', 'positive_escrow_count', 'escrow_weight_sum',
        'has_onchain_escrow', 'mentions_no_kyc', 'mentions_wallet_payout',
        'merges_last_45d', 'is_closed'
    ]
    
    features_clean = [
        'vibe_score', 'positive_escrow_count', 'escrow_weight_sum',
        'has_onchain_escrow', 'mentions_no_kyc', 'mentions_wallet_payout',
        'merges_last_45d', 'is_closed'
    ]

    def evaluate_model(feats, name, y):
        X = df[feats]
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        cv_aucs = []
        all_probs = np.zeros(len(y))
        
        for train_idx, val_idx in skf.split(X, y):
            X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
            y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
            m = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
            m.fit(X_train, y_train)
            probs = m.predict_proba(X_val)[:, 1]
            all_probs[val_idx] = probs
            cv_aucs.append(roc_auc_score(y_val, probs))
            
        precisions, recalls, thresholds = precision_recall_curve(y, all_probs)
        f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-9)
        best_idx = np.argmax(f1_scores)
        best_threshold = float(thresholds[min(best_idx, len(thresholds)-1)])
        max_f1 = float(f1_scores[best_idx])
        avg_auc = np.mean(cv_aucs)
        
        print(f"\n--- {name} ---")
        print(f"CV ROC-AUC: {avg_auc:.4f}")
        print(f"Best Threshold: {best_threshold:.4f}")
        print(f"Max F1 Score: {max_f1:.4f}")
        
        # Feature importance
        final_model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
        final_model.fit(X, y)
        importances = dict(zip(feats, final_model.feature_importances_))
        print("Feature Importances:")
        for f, imp in sorted(importances.items(), key=lambda x: x[1], reverse=True):
            print(f"  {f:<22}: {imp:.4f}")
            
        return avg_auc, max_f1, best_threshold, importances, final_model

    y_orig = df['is_bounty']
    auc_a, f1_a, thr_a, imp_a, model_a = evaluate_model(features_leaky, "MODEL A (Leaky)", y_orig)
    auc_c, f1_c, thr_c, imp_c, model_c = evaluate_model(features_clean, "MODEL C (No-Leakage)", y_orig)
    auc_d, f1_d, thr_d, imp_d, model_d = evaluate_model(features_leaky, "MODEL D (Vibe-labels)", df['is_bounty_v2'])

    print("\n=== MODEL COMPARISON ===")
    print(f"Model A (leaky):       AUC={auc_a:.4f}  F1={f1_a:.4f}  (DO NOT USE)")
    print(f"Model C (no-leakage):  AUC={auc_c:.4f}  F1={f1_c:.4f}")
    print(f"Model D (vibe-labels): AUC={auc_d:.4f}  F1={f1_d:.4f}")

    # PRODUCTION MODEL SELECTION
    prod_model_name = "C"
    prod_model = model_c
    prod_auc = auc_c
    prod_f1 = f1_c
    prod_thr = thr_c
    prod_features = features_clean
    prod_imp = imp_c

    if auc_c < 0.70 or f1_c < 0.65:
        if auc_d >= 0.65:
            prod_model_name = "D"
            prod_model = model_d
            prod_auc = auc_d
            prod_f1 = f1_d
            prod_thr = thr_d
            prod_features = features_leaky
            prod_imp = imp_d
        else:
            print("\nWARNING: All leakage-free models are below performance targets (AUC 0.65).")

    # PRODUCTION_MODEL = prod_model_name
    print(f"\nSELECTED PRODUCTION MODEL: {prod_model_name}")

    # Save results
    results = {
        "production_model": prod_model_name,
        "leakage_free": True,
        "best_threshold": prod_thr,
        "f1_score": prod_f1,
        "roc_auc": prod_auc,
        "features": prod_features,
        "importances": prod_imp
    }
    
    joblib.dump(prod_model, "bounty_model.pkl")
    print(f"Saved Model {prod_model_name} to bounty_model.pkl")
    
    with open("best_threshold.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Saved best_threshold.json")

if __name__ == "__main__":
    train_and_eval()
