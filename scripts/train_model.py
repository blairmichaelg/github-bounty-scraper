#!/usr/bin/env python3
"""
Train or retrain the bounty lead scoring model.

Usage:
    python scripts/train_model.py --input bounty_dataset_train.csv
    python scripts/train_model.py --input bounty_dataset_train.csv --output models/bounty_model.pkl
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.preprocessing import LabelEncoder


# Features determined from scoring.py and CSV headers
FEATURE_COLUMNS = [
    "numeric_amount",
    "vibe_score",
    "merges_last_45d",
    "total_escrows_seen",
    "rugs_seen",
    "has_onchain_escrow",
    "mentions_no_kyc",
    "mentions_wallet_payout",
    "positive_escrow_count",
    "escrow_weight_sum",
    "is_dead_repo",
]
LABEL_COLUMN = "is_bounty"


def load_and_validate(input_path: Path) -> pd.DataFrame:
    df = pd.read_csv(input_path)
    missing = [c for c in FEATURE_COLUMNS + [LABEL_COLUMN] if c not in df.columns]
    if missing:
        print(f"ERROR: Missing columns in dataset: {missing}", file=sys.stderr)
        print(f"Available columns: {list(df.columns)}", file=sys.stderr)
        sys.exit(1)
    before = len(df)
    # Drop rows where label is missing or features are missing
    df = df.dropna(subset=FEATURE_COLUMNS + [LABEL_COLUMN])
    # Also drop rows where is_bounty is empty string (ambiguous)
    df = df[df[LABEL_COLUMN] != ""]
    df[LABEL_COLUMN] = df[LABEL_COLUMN].astype(int)
    
    dropped = before - len(df)
    if dropped:
        print(f"Dropped {dropped} rows with nulls or ambiguity in feature/label columns")
    print(f"Dataset: {len(df)} rows, {df[LABEL_COLUMN].value_counts().to_dict()}")
    return df


def save_with_checksum(model: object, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output_path)
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    output_path.with_suffix(".pkl.sha256").write_text(digest)
    print(f"Model saved to: {output_path}")
    print(f"SHA256: {digest}")


def train(input_path: Path, output_path: Path) -> None:
    df = load_and_validate(input_path)
    X = df[FEATURE_COLUMNS].values
    y = df[LABEL_COLUMN].values

    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    # Cross-validation first
    clf = GradientBoostingClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        random_state=42,
    )
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(clf, X, y_enc, cv=cv, scoring="f1_weighted")
    print(f"\n5-Fold CV F1 (weighted): {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    # Train/test split for final evaluation
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_enc, test_size=0.2, stratify=y_enc, random_state=42
    )
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)

    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=[str(c) for c in le.classes_]))
    print("Confusion Matrix:")
    print(confusion_matrix(y_test, y_pred))
    print(f"\nTest Accuracy: {(y_pred == y_test).mean():.4f}")

    # Retrain on full dataset before saving
    clf.fit(X, y_enc)
    save_with_checksum(clf, output_path)

    # Save label encoder alongside model
    le_path = output_path.with_name(output_path.stem + "_label_encoder.pkl")
    joblib.dump(le, le_path)
    print(f"Label encoder saved to: {le_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the bounty lead scoring model")
    parser.add_argument("--input", required=True, type=Path, help="Path to labeled CSV dataset")
    parser.add_argument(
        "--output",
        default=Path("bounty_model.pkl"),
        type=Path,
        help="Output path for trained model (default: bounty_model.pkl)",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    train(args.input, args.output)


if __name__ == "__main__":
    main()
