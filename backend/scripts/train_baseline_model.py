from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import classification_report, f1_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings  # noqa: E402

VERSION = "uci-gb-0.1"
FEATURES = ["Age", "SystolicBP", "DiastolicBP", "BS", "BodyTemp", "HeartRate"]


def main() -> None:
    s = get_settings()
    df = pd.read_csv(Path(s.DATA_DIR) / "uci_maternal_health_risk.csv", encoding="utf-8-sig")
    n_raw = len(df)
    df = df.drop_duplicates()  # duplicates would leak between train and test
    y = df["RiskLevel"].map({"low risk": 0, "mid risk": 1, "high risk": 2}).values
    X = df[FEATURES].values
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, stratify=y, random_state=42)
    model = GradientBoostingClassifier(random_state=42).fit(Xtr, ytr)
    proba = model.predict_proba(Xte)
    pred = proba.argmax(1)
    metrics = {
        "rows_raw": n_raw, "rows_deduplicated": len(df), "test_rows": int(len(yte)),
        "macro_f1": round(float(f1_score(yte, pred, average="macro")), 3),
        "high_risk_recall": round(float(recall_score(yte == 2, pred == 2)), 3),
        "roc_auc_ovr": round(float(roc_auc_score(yte, proba, multi_class="ovr")), 3),
        "report": classification_report(yte, pred, target_names=["low", "mid", "high"], output_dict=True),
        "limitations": ["non-Uzbek dataset", "no patient identifiers -> patient-level split impossible",
                        "no temporal/external validation", "label provenance unclear", "shadow mode only"],
    }
    out = Path(s.MODEL_DIR)
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "classes": ["low", "mid", "high"], "features": FEATURES, "version": VERSION},
                out / "maternal_baseline_uci.joblib")
    (out / "maternal_baseline_uci.model_card.json").write_text(json.dumps({"version": VERSION, "metrics": metrics,
                                                                           "status": "shadow"}, indent=1))
    print(json.dumps({k: v for k, v in metrics.items() if k != "report"}, indent=1))
    _ = np


if __name__ == "__main__":
    main()
