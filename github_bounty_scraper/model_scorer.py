import os
import joblib
import numpy as np
import pandas as pd
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
            # Feature validation & alignment
            vals = []
            for f in self.features:
                if f not in data:
                    log.debug("Missing feature '%s' for model prediction, defaulting to 0", f)
                val = data.get(f, 0)
                if val is None:
                    val = 0
                # Handle booleans
                if isinstance(val, bool):
                    val = int(val)
                vals.append(val)
                
            X = np.array([vals])
            
            # RF predict_proba returns [prob_neg, prob_pos]
            probabilities = self.model.predict_proba(X)
            if probabilities.shape[1] < 2:
                # Edge case: model only trained on one class
                return float(probabilities[0][0] * 100.0) if self.model.classes_[0] == 1 else 0.0
                
            prob = probabilities[0][1]
            return float(prob * 100.0)
        except Exception as e:
            log.warning("ML prediction failed for input %s: %s", data, e)
            return 0.0
