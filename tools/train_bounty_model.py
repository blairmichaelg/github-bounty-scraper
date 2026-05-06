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
    
    features = [
        'log_amount', 'vibe_score', 'merges_last_45d',
        'has_onchain_escrow', 'mentions_no_kyc', 'mentions_wallet_payout',
        'is_closed'
    ]
    
    X = df[features]
    y = df['is_bounty']

    print(f"Training on {len(df)} samples ({sum(y)} pos / {len(y)-sum(y)} neg)")

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    
    cv_f1s = []
    cv_aucs = []
    all_probs = np.zeros(len(y))
    
    # Train final model on all data for feature importance
    model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
    
    for train_idx, val_idx in skf.split(X, y):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
        
        m = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
        m.fit(X_train, y_train)
        
        probs = m.predict_proba(X_val)[:, 1]
        all_probs[val_idx] = probs
        
        cv_aucs.append(roc_auc_score(y_val, probs))

    # Calibrate threshold on out-of-fold probabilities
    precisions, recalls, thresholds = precision_recall_curve(y, all_probs)
    f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-9)
    best_idx = np.argmax(f1_scores)
    best_threshold = float(thresholds[min(best_idx, len(thresholds)-1)])
    max_f1 = float(f1_scores[best_idx])

    print(f"CV ROC-AUC: {np.mean(cv_aucs):.4f}")
    print(f"Best Threshold: {best_threshold:.4f}")
    print(f"Max F1 Score: {max_f1:.4f}")

    # Feature importance
    model.fit(X, y)
    importances = dict(zip(features, model.feature_importances_))
    print("\nFeature Importances:")
    for f, imp in sorted(importances.items(), key=lambda x: x[1], reverse=True):
        print(f"  {f:<22}: {imp:.4f}")

    # Save results
    results = {
        "best_threshold": best_threshold,
        "f1_score": max_f1,
        "roc_auc": np.mean(cv_aucs),
        "features": features,
        "importances": importances
    }
    
    import joblib
    joblib.dump(model, "bounty_model.pkl")
    print("Saved bounty_model.pkl")
    
    with open("best_threshold.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Saved best_threshold.json")

if __name__ == "__main__":
    train_and_eval()
