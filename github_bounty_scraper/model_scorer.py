import os

import joblib
import numpy as np

from .log import get_logger

log = get_logger()


class ModelScorer:
    """Loads and runs the trained Random Forest model for bounty classification."""

    def __init__(self, model_path: str = "bounty_model.pkl"):
        self.model_path = model_path
        self.model = None
        self.features = [
            "vibe_score",
            "positive_escrow_count",
            "escrow_weight_sum",
            "has_onchain_escrow",
            "mentions_no_kyc",
            "mentions_wallet_payout",
            "merges_last_45d",
            "is_closed",
        ]

    def load(self) -> bool:
        """Load the model from disk."""
        if not os.path.exists(self.model_path):
            log.debug("Model file %s not found. ML scoring disabled.", self.model_path)
            return False
        try:
            self.model = joblib.load(self.model_path)
            log.info("Loaded ML model from %s", self.model_path)
            return True
        except Exception as e:
            log.error("Failed to load model from %s: %s", self.model_path, e)
            return False

    def predict_score(self, data: dict) -> float:
        """Predict the probability of an issue being a bounty [0, 100]."""
        if self.model is None:
            return 0.0

        try:
            X = self._prepare_input(data)
            # RF predict_proba returns [prob_neg, prob_pos]
            probabilities = self.model.predict_proba(X)
            if probabilities.shape[1] < 2:
                # Edge case: model only trained on one class
                return float(probabilities[0][0] * 100.0) if self.model.classes_[0] == 1 else 0.0

            prob = probabilities[0][1]
            return float(prob * 100.0)
        except Exception as e:
            log.warning("ML prediction failed: %s", e)
            return 0.0

    def explain_prediction(self, data: dict) -> str:
        """Provide a dynamic natural language explanation using feature importances."""
        if self.model is None:
            return "Model not loaded."
        
        try:
            # Get feature importances from the Random Forest model
            importances = self.model.feature_importances_
            # Pair them with feature names and sort
            top_indices = np.argsort(importances)[-3:][::-1]
            
            reasons = []
            for i in top_indices:
                feat = self.features[i]
                val = data.get(feat, 0)
                if val:
                    # Map feature names to user-friendly reasons
                    mapping = {
                        "vibe_score": f"vibe check ({val})",
                        "positive_escrow_count": f"escrow signals ({val})",
                        "escrow_weight_sum": "strong escrow weight",
                        "has_onchain_escrow": "on-chain escrow",
                        "mentions_no_kyc": "mentions No-KYC",
                        "mentions_wallet_payout": "wallet payout details",
                        "merges_last_45d": "repo activity",
                        "is_closed": "historical context",
                    }
                    reasons.append(mapping.get(feat, feat))

            if not reasons:
                return "Neutral or combined weak signals."
            return "Key factors: " + ", ".join(reasons)
        except Exception as e:
            log.debug("Explanation generation failed: %s", e)
            return "Composite signal score."

    def _prepare_input(self, data: dict) -> np.ndarray:
        vals = []
        for f in self.features:
            val = data.get(f, 0)
            if val is None:
                val = 0
            if isinstance(val, bool):
                val = int(val)
            vals.append(val)
        return np.array([vals])
