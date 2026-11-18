from __future__ import annotations

import hashlib
import json
import warnings
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix, f1_score,
                             recall_score, roc_auc_score)
from sklearn.model_selection import StratifiedGroupKFold

warnings.filterwarnings("ignore", category=UserWarning)

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "ml"
PROC = ROOT / "data" / "processed" / "ml"
MODELS = ROOT / "backend" / "models_store"
SEED = 42

MATERNAL_VERSION = "maternal-shadow-hgb-2.0"
CTG_VERSION = "fetal-ctg-shadow-hgb-1.0"

# Exact column order expected by model.predict_proba. The first six are identical (order + units)
# to maternal_baseline_uci; the last two are optional -> pass float("nan") when unknown.
MATERNAL_FEATURES = ["age_years", "systolic_bp", "diastolic_bp", "glucose_mmol_l", "body_temp_f",
                     "heart_rate", "bmi", "hba1c_mmol_mol"]
OPTIONAL = ["bmi", "hba1c_mmol_mol"]
MATERNAL_CLASSES = ["low", "mid", "high"]

CTG_FEATURES = ["LB", "AC", "FM", "UC", "DL", "DS", "DP", "ASTV", "MSTV", "ALTV", "MLTV", "Width", "Min",
                "Max", "Nmax", "Nzeros", "Mode", "Mean", "Median", "Variance", "Tendency"]
CTG_CLASSES = ["normal", "suspect", "pathological"]


# ------------------------------------------------------------------ data
def _clean_maternal(df: pd.DataFrame, source: str) -> tuple[pd.DataFrame, dict]:
    """Harmonised cleaning; returns the clean frame and an audit of what was removed."""
    audit = {"rows_raw": int(len(df))}
    df = df.copy()
    for c in MATERNAL_FEATURES:
        if c not in df:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["label"].notna()]
    audit["dropped_missing_label"] = audit["rows_raw"] - len(df)
    # a few temperatures were typed in Celsius
    c_mask = df["body_temp_f"].between(34, 43)
    df.loc[c_mask, "body_temp_f"] = df.loc[c_mask, "body_temp_f"] * 9 / 5 + 32
    audit["temp_celsius_converted"] = int(c_mask.sum())
    df.loc[~df["bmi"].between(10, 60), "bmi"] = np.nan  # BMI == 0 etc. -> missing, row kept
    ok = (df["age_years"].between(10, 60) & df["systolic_bp"].between(60, 260) & df["diastolic_bp"].between(30, 160)
          & (df["diastolic_bp"] < df["systolic_bp"]) & df["glucose_mmol_l"].between(1.5, 35)
          & df["body_temp_f"].between(93, 108) & df["heart_rate"].between(30, 220))
    audit["dropped_implausible_or_missing_core"] = int((~ok).sum())
    df = df[ok]
    n = len(df)
    df = df.drop_duplicates(subset=MATERNAL_FEATURES + ["label"])
    audit["dropped_exact_duplicates"] = n - len(df)
    # identical vitals with conflicting labels: keep the highest-risk label (conservative)
    n = len(df)
    df = df.sort_values("label").drop_duplicates(subset=MATERNAL_FEATURES, keep="last")
    audit["dropped_conflicting_label_duplicates"] = n - len(df)
    df["source"] = source
    audit["rows_clean"] = int(len(df))
    audit["class_counts"] = {MATERNAL_CLASSES[int(k)]: int(v) for k, v in df["label"].value_counts().sort_index().items()}
    return df[MATERNAL_FEATURES + ["label", "source"]], audit


def load_maternal() -> tuple[pd.DataFrame, dict]:
    frames, audit = [], {}
    three = {"low risk": 0, "mid risk": 1, "high risk": 2}

    # A) Mendeley ckhgtgdg4f - 6,103 records, Bangladesh, CC BY 4.0 (Patient ID / Name are dropped here)
    a = pd.read_csv(RAW / "mendeley" / "ckhgtgdg4f" / "Mathernal_Risk.csv", encoding="utf-8-sig")
    a = a.rename(columns=lambda c: c.strip())
    a = pd.DataFrame({
        "age_years": a["Age"], "systolic_bp": a["Systolic Blood Pressure(mm Hg)"],
        "diastolic_bp": a["Diastolic Blood Pressure(mm Hg)"],
        # header units are swapped in the source file: the "Fasting ... mg/dl" column holds 3.5-8.9
        # (mmol/L) and the "HbA1c" column holds 30-50 (IFCC mmol/mol)
        "glucose_mmol_l": a["Blood Glucose(Fasting hour-mg/dl)"], "body_temp_f": a["Body Temperature(F)"],
        "heart_rate": a["Heart rate(bpm)"], "bmi": a["BMI(kg/m 2)"], "hba1c_mmol_mol": a["Blood Glucose(HbA1c)"],
        "label": a["Status"].str.strip().str.lower().map(three)})
    f, audit["mendeley_ckhgtgdg4f"] = _clean_maternal(a, "mendeley_ckhgtgdg4f")
    frames.append(f)

    # B) Mendeley p5w98dvbbk - 1,205 records, Bangladesh, CC BY 4.0, binary label (High / Low)
    b = pd.read_csv(RAW / "mendeley" / "p5w98dvbbk" / "Dataset_-_Updated.csv", encoding="utf-8-sig")
    b = pd.DataFrame({
        "age_years": b["Age"], "systolic_bp": b["Systolic BP"], "diastolic_bp": b["Diastolic"],
        "glucose_mmol_l": b["BS"], "body_temp_f": b["Body Temp"], "heart_rate": b["Heart Rate"], "bmi": b["BMI"],
        "label": b["Risk Level"].str.strip().str.lower().map({"low": 0, "high": 2})})
    f, audit["mendeley_p5w98dvbbk"] = _clean_maternal(b, "mendeley_p5w98dvbbk")
    frames.append(f)

    # C) UCI Maternal Health Risk (id 863) - 1,014 records, Bangladesh, CC BY 4.0
    u = pd.read_csv(ROOT / "data" / "processed" / "uci_maternal_health_risk.csv", encoding="utf-8-sig")
    u = pd.DataFrame({
        "age_years": u["Age"], "systolic_bp": u["SystolicBP"], "diastolic_bp": u["DiastolicBP"],
        "glucose_mmol_l": u["BS"], "body_temp_f": u["BodyTemp"], "heart_rate": u["HeartRate"],
        "label": u["RiskLevel"].str.strip().str.lower().map(three)})
    f, audit["uci_863"] = _clean_maternal(u, "uci_863")
    frames.append(f)

    df = pd.concat(frames, ignore_index=True)
    df["label"] = df["label"].astype(int)
    return df, audit


def load_ctg() -> tuple[pd.DataFrame, dict]:
    d = pd.read_csv(RAW / "ctg" / "uci193_data.csv")
    audit = {"rows_raw": int(len(d))}
    d = d.dropna(subset=CTG_FEATURES + ["NSP"])
    d["label"] = d["NSP"].astype(int) - 1  # NSP 1/2/3 -> 0/1/2. CLASS (10 FHR patterns) is dropped: label leak
    n = len(d)
    d = d.drop_duplicates(subset=CTG_FEATURES + ["label"])
    audit["dropped_exact_duplicates"] = n - len(d)
    n = len(d)
    d = d.sort_values("label").drop_duplicates(subset=CTG_FEATURES, keep="last")
    audit["dropped_conflicting_label_duplicates"] = n - len(d)
    d["source"] = "uci_193"
    audit["rows_clean"] = int(len(d))
    audit["class_counts"] = {CTG_CLASSES[k]: int(v) for k, v in d["label"].value_counts().sort_index().items()}
    return d[CTG_FEATURES + ["label", "source"]].reset_index(drop=True), audit


# ------------------------------------------------------------------ modelling helpers
def _groups(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    """No dataset ships a reusable patient id (the one that does has exactly one row per id), so the
    leak-safe unit is the clinical measurement vector: rows identical on the always-present columns can
    never be split across train / test / calibration folds."""
    key = df[cols].round(3).astype(str).agg("|".join, axis=1)
    return key.map(lambda s: int(hashlib.md5(s.encode()).hexdigest()[:12], 16)).to_numpy()


def _hgb() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(learning_rate=0.06, max_iter=400, max_leaf_nodes=15, min_samples_leaf=10,
                                          l2_regularization=1.0, class_weight="balanced", early_stopping=True,
                                          validation_fraction=0.15, n_iter_no_change=25, random_state=SEED)


def _fit_calibrated(X: np.ndarray, y: np.ndarray, groups: np.ndarray, strat: np.ndarray,
                    sample_weight: np.ndarray | None = None) -> CalibratedClassifierCV:
    folds = list(StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SEED).split(X, strat, groups))
    return CalibratedClassifierCV(_hgb(), method="isotonic", cv=folds, ensemble=True).fit(X, y, sample_weight=sample_weight)


def _source_weights(source: pd.Series) -> np.ndarray:
    """Weight ~ 1/sqrt(source size), mean 1: stops the largest dataset from drowning the small ones."""
    w = 1.0 / np.sqrt(source.map(source.value_counts()).to_numpy(float))
    return w / w.mean()


def _ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    return float(sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).mean() for b in range(bins) if (idx == b).any()))


def evaluate(proba: np.ndarray, y: np.ndarray, classes: list[str]) -> dict:
    k = len(classes)
    pred = proba.argmax(1)
    present = [c for c in range(k) if (y == c).any()]
    onehot = np.eye(k)[y]
    out = {
        "n": int(len(y)),
        "accuracy": round(float((pred == y).mean()), 4),
        "macro_f1": round(float(f1_score(y, pred, labels=present, average="macro")), 4),
        "recall": {classes[c]: round(float(recall_score(y == c, pred == c)), 4) for c in present},
        "precision": {classes[c]: round(float(((pred == c) & (y == c)).sum() / max((pred == c).sum(), 1)), 4) for c in present},
        "brier_multiclass": round(float(((proba - onehot) ** 2).sum(1).mean()), 4),
        "confusion_matrix_rows_true": confusion_matrix(y, pred, labels=list(range(k))).tolist(),
    }
    if len(present) == k and k > 2:
        out["roc_auc_ovr_macro"] = round(float(roc_auc_score(y, proba, multi_class="ovr", average="macro")), 4)
        out["pr_auc_macro"] = round(float(np.mean([average_precision_score(y == c, proba[:, c]) for c in range(k)])), 4)
    top = k - 1  # highest-risk class
    yt = (y == top).astype(int)
    if 0 < yt.sum() < len(yt):
        out[f"{classes[top]}_roc_auc"] = round(float(roc_auc_score(yt, proba[:, top])), 4)
        out[f"{classes[top]}_pr_auc"] = round(float(average_precision_score(yt, proba[:, top])), 4)
        out[f"{classes[top]}_brier"] = round(float(brier_score_loss(yt, proba[:, top])), 4)
        out[f"{classes[top]}_ece_10bin"] = round(_ece(proba[:, top], yt), 4)
        out[f"{classes[top]}_prevalence"] = round(float(yt.mean()), 4)
    return out


def _perm_importance(model, X, y, features) -> dict:
    r = permutation_importance(model, X, y, scoring="f1_macro", n_repeats=15, random_state=SEED)
    order = np.argsort(-r.importances_mean)
    return {features[i]: {"mean_drop_macro_f1": round(float(r.importances_mean[i]), 4),
                          "std": round(float(r.importances_std[i]), 4)} for i in order}


def _split(df: pd.DataFrame, group_cols: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    groups = _groups(df, group_cols)
    strat = (df["source"].astype("category").cat.codes * 10 + df["label"]).to_numpy()
    tr, te = next(StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SEED).split(df, strat, groups))
    assert not set(groups[tr]) & set(groups[te]), "group leakage between train and test"
    return tr, te, groups


def _write_card(name: str, card: dict) -> None:
    (MODELS / f"{name}.model_card.json").write_text(json.dumps(card, indent=1, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ maternal
def train_maternal() -> dict:
    df, audit = load_maternal()
    PROC.mkdir(parents=True, exist_ok=True)
    df.assign(label_name=df["label"].map(dict(enumerate(MATERNAL_CLASSES)))).to_csv(PROC / "maternal_pooled_clean.csv", index=False)

    core = MATERNAL_FEATURES[:6]
    tr, te, groups = _split(df, core)
    train, test = df.iloc[tr].reset_index(drop=True), df.iloc[te].reset_index(drop=True)
    g_tr = groups[tr]

    # Missing-input augmentation (train only): the backend often has no BMI / HbA1c, so a copy of every
    # training row that has them is added with both masked. Copies keep the group id of the original, so
    # they can never sit on the other side of a calibration fold.
    has_opt = train[OPTIONAL].notna().any(axis=1).to_numpy()
    masked = train[has_opt].copy()
    masked[OPTIONAL] = np.nan
    train_aug = pd.concat([train, masked], ignore_index=True)
    g_aug = np.concatenate([g_tr, g_tr[has_opt]])
    strat_aug = (train_aug["source"].astype("category").cat.codes * 10 + train_aug["label"]).to_numpy()

    Xtr, ytr = train_aug[MATERNAL_FEATURES].to_numpy(float), train_aug["label"].to_numpy()
    Xte, yte = test[MATERNAL_FEATURES].to_numpy(float), test["label"].to_numpy()
    w_aug = _source_weights(train_aug["source"])
    model = _fit_calibrated(Xtr, ytr, g_aug, strat_aug, w_aug)

    proba = model.predict_proba(Xte)
    Xte_core = Xte.copy()
    Xte_core[:, 6:] = np.nan
    proba_core = model.predict_proba(Xte_core)
    uncal = _hgb().fit(Xtr, ytr, sample_weight=w_aug).predict_proba(Xte)

    metrics = {
        "test_pooled_all_inputs": evaluate(proba, yte, MATERNAL_CLASSES),
        "test_pooled_core6_only_bmi_hba1c_missing": evaluate(proba_core, yte, MATERNAL_CLASSES),
        "test_by_source_all_inputs": {s: evaluate(proba[(test["source"] == s).to_numpy()], yte[(test["source"] == s).to_numpy()], MATERNAL_CLASSES)
                                      for s in sorted(test["source"].unique())},
        "test_by_source_core6_only": {s: evaluate(proba_core[(test["source"] == s).to_numpy()], yte[(test["source"] == s).to_numpy()], MATERNAL_CLASSES)
                                      for s in sorted(test["source"].unique())},
        "uncalibrated_reference": {k: v for k, v in evaluate(uncal, yte, MATERNAL_CLASSES).items()
                                   if k in ("macro_f1", "brier_multiclass", "high_brier", "high_ece_10bin")},
    }

    # Leave-one-source-out: the honest estimate of transfer to an unseen population (e.g. Uzbekistan).
    loso = {}
    for s in sorted(df["source"].unique()):
        a, b = df[df["source"] != s], df[df["source"] == s]
        m = _hgb().fit(a[core].to_numpy(float), a["label"].to_numpy())
        loso[s] = evaluate(m.predict_proba(b[core].to_numpy(float)), b["label"].to_numpy(), MATERNAL_CLASSES)
        loso[s].pop("confusion_matrix_rows_true")
    metrics["leave_one_source_out_core6_uncalibrated"] = loso

    importance = {"all_inputs": _perm_importance(model, Xte, yte, MATERNAL_FEATURES),
                  "core6_only": _perm_importance(model, Xte_core, yte, MATERNAL_FEATURES)}

    MODELS.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "classes": MATERNAL_CLASSES, "features": MATERNAL_FEATURES, "version": MATERNAL_VERSION},
                MODELS / "maternal_shadow_v2.joblib")
    card = {
        "name": "maternal_shadow_v2", "version": MATERNAL_VERSION, "status": "shadow",
        "intended_use": "SHADOW MODE ONLY - never drives alerts, never shown as a decision; logged next to the rule-based assessment for offline evaluation.",
        "trained_on": str(date.today()), "sklearn_version": sklearn.__version__,
        "algorithm": "HistGradientBoostingClassifier (class_weight=balanced, early stopping) inside CalibratedClassifierCV(isotonic, 5 group-safe folds, ensemble)",
        "classes": MATERNAL_CLASSES, "features": MATERNAL_FEATURES,
        "feature_units": {"age_years": "years", "systolic_bp": "mmHg", "diastolic_bp": "mmHg",
                          "glucose_mmol_l": "mmol/L (fasting / blood sugar)", "body_temp_f": "deg F  (= C * 9/5 + 32)",
                          "heart_rate": "beats/min", "bmi": "kg/m2, optional -> NaN",
                          "hba1c_mmol_mol": "IFCC mmol/mol, optional -> NaN  (= (HbA1c_% - 2.15) * 10.929)"},
        "missing_inputs": "First six features are required. bmi and hba1c_mmol_mol may be float('nan') (never None).",
        "datasets": [
            {"id": "mendeley_ckhgtgdg4f", "title": "Maternal health risk data", "doi": "10.17632/ckhgtgdg4f.1", "licence": "CC BY 4.0"},
            {"id": "mendeley_p5w98dvbbk", "title": "Maternal Health Risk Assessment Dataset", "doi": "10.17632/p5w98dvbbk.1", "licence": "CC BY 4.0",
             "note": "binary label: High -> high, Low -> low (no mid class)"},
            {"id": "uci_863", "title": "UCI Maternal Health Risk", "doi": "10.24432/C5DP5D", "licence": "CC BY 4.0"}],
        "cleaning": audit,
        "split": {"method": "StratifiedGroupKFold(5) -> 1 fold held out (20%); groups = identical core-6 measurement vector; stratified by source x label",
                  "train_rows": int(len(train)), "train_rows_after_missing_input_augmentation": int(len(train_aug)), "test_rows": int(len(test))},
        "metrics": metrics, "permutation_importance_test": importance,
        "limitations": [
            "shadow mode only - never drives alerts",
            "all three sources are from Bangladesh; no Uzbek / Central Asian data, no local validation",
            "labels differ by source and are partly rule-derived: on mendeley_ckhgtgdg4f the label is almost a deterministic function of BMI + HbA1c, so pooled metrics are inflated by that source",
            "leave-one-source-out scores (see metrics) are far lower than in-distribution scores: expect poor transfer to a new population",
            "the model can exploit which inputs are missing as a proxy for the source dataset",
            "no patient identifiers that repeat -> true patient-level split impossible; grouping by identical measurement vector is used instead",
            "no gestational age, parity, proteinuria, haemoglobin or obstetric history among the inputs",
            "no temporal or external validation; calibration is valid only for the pooled source mix",
        ],
    }
    _write_card("maternal_shadow_v2", card)
    return card


# ------------------------------------------------------------------ fetal CTG
def train_ctg() -> dict | None:
    if not (RAW / "ctg" / "uci193_data.csv").exists():
        print("CTG data not found - skipping fetal model")
        return None
    df, audit = load_ctg()
    df.assign(label_name=df["label"].map(dict(enumerate(CTG_CLASSES)))).to_csv(PROC / "fetal_ctg_clean.csv", index=False)
    tr, te, groups = _split(df, CTG_FEATURES)
    Xtr, ytr = df.iloc[tr][CTG_FEATURES].to_numpy(float), df.iloc[tr]["label"].to_numpy()
    Xte, yte = df.iloc[te][CTG_FEATURES].to_numpy(float), df.iloc[te]["label"].to_numpy()
    model = _fit_calibrated(Xtr, ytr, groups[tr], ytr)
    proba = model.predict_proba(Xte)
    uncal = _hgb().fit(Xtr, ytr).predict_proba(Xte)
    # recall for "not normal" (suspect or pathological) - the screening question
    abn = evaluate(np.c_[proba[:, 0], proba[:, 1:].sum(1)], (yte > 0).astype(int), ["normal", "abnormal"])
    metrics = {"test": evaluate(proba, yte, CTG_CLASSES),
               "test_binary_normal_vs_abnormal": {k: abn[k] for k in ("recall", "precision", "abnormal_roc_auc", "abnormal_pr_auc", "abnormal_brier")},
               "uncalibrated_reference": {k: v for k, v in evaluate(uncal, yte, CTG_CLASSES).items()
                                          if k in ("macro_f1", "brier_multiclass", "pathological_brier", "pathological_ece_10bin")}}
    joblib.dump({"model": model, "classes": CTG_CLASSES, "features": CTG_FEATURES, "version": CTG_VERSION},
                MODELS / "fetal_ctg_shadow_v1.joblib")
    card = {
        "name": "fetal_ctg_shadow_v1", "version": CTG_VERSION, "status": "shadow",
        "intended_use": "SHADOW MODE ONLY - never drives alerts, never shown as a decision.",
        "trained_on": str(date.today()), "sklearn_version": sklearn.__version__,
        "algorithm": "HistGradientBoostingClassifier (class_weight=balanced, early stopping) inside CalibratedClassifierCV(isotonic, 5 group-safe folds, ensemble)",
        "classes": CTG_CLASSES, "features": CTG_FEATURES,
        "feature_notes": "UCI names. LB = FHR baseline (bpm); AC, FM, UC, DL, DS, DP = accelerations, fetal movements, uterine contractions, light / severe / prolonged decelerations PER SECOND; ASTV / ALTV = % of time with abnormal short / long term variability; MSTV / MLTV = mean short / long term variability; Width..Tendency = FHR histogram descriptors.",
        "datasets": [{"id": "uci_193", "title": "UCI Cardiotocography", "doi": "10.24432/C51S4N", "licence": "CC BY 4.0",
                      "url": "https://archive.ics.uci.edu/dataset/193/cardiotocography"}],
        "cleaning": audit,
        "split": {"method": "StratifiedGroupKFold(5) -> 1 fold held out (20%); groups = identical feature vector", "train_rows": int(len(tr)), "test_rows": int(len(te))},
        "metrics": metrics, "permutation_importance_test": _perm_importance(model, Xte, yte, CTG_FEATURES),
        "limitations": [
            "shadow mode only - never drives alerts",
            "inputs are 21 features pre-computed by the SisPorto 2.0 CTG analyser; the model cannot read a raw CTG trace and is unusable without an equivalent feature extractor",
            "single Portuguese centre, recordings from 1995-1998, labels are the consensus of three obstetricians (NSP), not neonatal outcomes",
            "no patient / pregnancy identifier: several recordings of one fetus may sit on both sides of the split, so test scores are optimistic",
            "no external or temporal validation; never validated in Uzbekistan",
        ],
    }
    _write_card("fetal_ctg_shadow_v1", card)
    return card


# ------------------------------------------------------------------ SIAT district table (context data, not used for training)
SIAT_DISTRICT_IDS = [223, 224, 225, 2768, 2769, 576, 616, 628, 659, 2844, 2845, 503, 2842, 2843, 662, 663, 664, 2776, 246, 244, 2846, 302, 305]


def build_siat_district_table() -> int:
    src = RAW / "siat_district"
    if not src.exists():
        return 0
    rows = []
    for sid in SIAT_DISTRICT_IDS:
        f = src / f"{sid}.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        d = d[0] if isinstance(d, list) else d
        meta = {m.get("name_en"): m.get("value_en") for m in d.get("metadata", []) if isinstance(m, dict)}
        for r in d.get("data", []):
            code = str(r.get("Code", ""))
            level = "country" if code == "1700" else "region" if len(code) == 4 else "district"
            for k, v in r.items():
                if k.isdigit() and v is not None:
                    rows.append({"siat_id": sid, "indicator_en": meta.get("Indicator name") or meta.get("Name of indicator") or "",
                                 "code": code, "region_code": code[:4], "level": level, "name_uz": r.get("Klassifikator"),
                                 "name_en": r.get("Klassifikator_en"), "year": int(k), "value": v})
    if rows:
        pd.DataFrame(rows).to_csv(PROC / "siat_district_perinatal.csv", index=False)
    return len(rows)


def main() -> None:
    PROC.mkdir(parents=True, exist_ok=True)
    m = train_maternal()
    print("== maternal_shadow_v2")
    print(json.dumps({k: m["metrics"][k] for k in ("test_pooled_all_inputs", "test_pooled_core6_only_bmi_hba1c_missing")}, indent=1))
    print("by source (all inputs):", json.dumps({s: {k: v[k] for k in ("n", "macro_f1", "recall")} for s, v in m["metrics"]["test_by_source_all_inputs"].items()}))
    print("by source (core6):", json.dumps({s: {k: v[k] for k in ("n", "macro_f1", "recall")} for s, v in m["metrics"]["test_by_source_core6_only"].items()}))
    print("leave-one-source-out:", json.dumps({s: {k: v[k] for k in ("n", "macro_f1", "recall")} for s, v in m["metrics"]["leave_one_source_out_core6_uncalibrated"].items()}))
    print("uncalibrated:", m["metrics"]["uncalibrated_reference"])
    print("importance:", json.dumps(m["permutation_importance_test"]))
    c = train_ctg()
    if c:
        print("== fetal_ctg_shadow_v1")
        print(json.dumps(c["metrics"], indent=1))
        print("importance:", json.dumps(c["permutation_importance_test"]))
    print("siat district rows:", build_siat_district_table())


if __name__ == "__main__":
    main()
