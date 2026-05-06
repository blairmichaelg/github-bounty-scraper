import pandas as pd
import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, roc_auc_score, precision_recall_curve
import json
import os

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
    df['vibe_score'] = df['vibe_score'].fillna(0)
    df['merges_last_45d'] = df['merges_last_45d'].fillna(0)
    df['positive_escrow_count'] = pd.to_numeric(df['positive_escrow_count'], errors='coerce').fillna(0)
    df['escrow_weight_sum'] = pd.to_numeric(df['escrow_weight_sum'], errors='coerce').fillna(0)
    
    features_a = [
        'log_amount', 'positive_escrow_count', 'escrow_weight_sum',
        'has_onchain_escrow', 'mentions_no_kyc', 'mentions_wallet_payout',
        'merges_last_45d', 'is_closed'
    ]
    features_b = features_a + ['vibe_score']
    
    y = df['is_bounty']

    print(f"Training on {len(df)} samples ({sum(y)} pos / {len(y)-sum(y)} neg)")

    def evaluate_model(feats, name):
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

    auc_a, f1_a, thr_a, imp_a, model_a = evaluate_model(features_a, "MODEL A (Signal-only)")
    
    # WARNING: Model B uses vibe_score as a feature. If is_bounty
    # labels were derived from composite score which includes vibe_score,
    # this model is overfit by circular leakage. Use Model A in production.
    auc_b, f1_b, thr_b, imp_b, model_b = evaluate_model(features_b, "MODEL B (Full Model)")

    if auc_a < 0.60:
        print("\nWARNING: Signal-only model AUC is low. Payout features need more real-world data before they carry meaningful weight.")

    # Save results
    results = {
        "production_model": "A",
        "best_threshold": thr_a,
        "f1_score": f1_a,
        "roc_auc": auc_a,
        "signal_only_auc": auc_a,
        "signal_only_f1": f1_a,
        "model_b_metrics": {
            "f1_score": f1_b,
            "roc_auc": auc_b,
            "best_threshold": thr_b,
            "model_b_circular_leak": True
        },
        "features": features_a,
        "importances": imp_a
    }
    
    import joblib
    joblib.dump(model_a, "bounty_model.pkl")
    print("\nSaved Model A to bounty_model.pkl (Production)")
    
    with open("best_threshold.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Saved best_threshold.json")

if __name__ == "__main__":
    train_and_eval()
