from __future__ import annotations

import json
import logging
import math
import threading
from pathlib import Path

from app.core.config import get_settings

log = logging.getLogger("perinatal.shadow")
_lock = threading.Lock()
_cache: dict[str, object] = {}
MATERNAL_V2 = "maternal_shadow_v2"
CORE6 = ["age_years", "systolic_bp", "diastolic_bp", "glucose_mmol_l", "body_temp_f", "heart_rate"]


def _load(name: str):
    with _lock:
        if name not in _cache:
            path = Path(get_settings().MODEL_DIR) / f"{name}.joblib"
            try:
                import joblib
                _cache[name] = joblib.load(path) if path.exists() else None
            except Exception as e:  # noqa: BLE001 - incompatible pickle etc.
                log.warning("shadow model %s could not be loaded (%s)", name, type(e).__name__)
                _cache[name] = None
        return _cache[name]


def maternal_v2(*, age_years, systolic_bp, diastolic_bp, glucose_mmol_l, temperature_c, heart_rate,
                bmi=None, hba1c_mmol_mol=None) -> dict | None:
    """Feature order: [age_years, systolic_bp, diastolic_bp, glucose_mmol_l, body_temp_f, heart_rate, bmi, hba1c_mmol_mol];
    the first six are required, the last two may be NaN."""
    try:
        bundle = _load(MATERNAL_V2)
        if not isinstance(bundle, dict) or "model" not in bundle:
            return None
        core = {"age_years": age_years, "systolic_bp": systolic_bp, "diastolic_bp": diastolic_bp,
                "glucose_mmol_l": glucose_mmol_l,
                "body_temp_f": temperature_c * 9 / 5 + 32 if temperature_c is not None else None, "heart_rate": heart_rate}
        missing = [k for k in CORE6 if core[k] is None]
        if missing:
            return {"status": "insufficient_inputs", "missing": missing, "version": bundle.get("version")}
        row = [float(core[k]) for k in CORE6] + [float(v) if v is not None else math.nan for v in (bmi, hba1c_mmol_mol)]
        proba = bundle["model"].predict_proba([row])[0]
        return {"status": "ok", "mode": "shadow", "model": MATERNAL_V2, "classes": list(bundle["classes"]),
                "probabilities": [round(float(p), 3) for p in proba], "version": bundle.get("version"),
                "optional_missing": [n for n, v in (("bmi", bmi), ("hba1c_mmol_mol", hba1c_mmol_mol)) if v is None]}
    except Exception as e:  # noqa: BLE001 - never break the patient card
        log.warning("maternal shadow v2 failed: %s", type(e).__name__)
        return None


def _pick(d: dict | None, keys: tuple[str, ...]) -> dict:
    return {k: d[k] for k in keys if d and k in d}


_KEEP = ("n", "accuracy", "macro_f1", "recall", "roc_auc_ovr_macro", "pr_auc_macro", "brier_multiclass")
_CAVEAT_UZ = {
    "maternal_shadow_v2": "Halol baho: model koʻrmagan manbada (leave-one-source-out) macro-F1 atigi 0,34 / 0,71 / 0,48 — "
                          "yangi aholi (Oʻzbekiston) uchun real kutilma shu. Jamlangan test natijasi (0,97) bitta manbaning "
                          "qoidaga oʻxshash belgilaridan shishgan. Mahalliy validatsiya yoʻq.",
    "fetal_ctg_shadow_v1": "Bitta Portugaliya markazi (1995–1998), SisPorto hisoblagan 21 belgi kerak — xom KTG yozuvini oʻqiy "
                           "olmaydi; tashqi va mahalliy validatsiya yoʻq, test natijasi optimistik.",
    "maternal_baseline_uci": "Eski bazaviy model (UCI, 452 qator): macro-F1 0,62. Faqat taqqoslash uchun saqlangan.",
}


def model_cards() -> list[dict]:
    """Compact model cards for the UI, read from models_store/*.model_card.json (cached)."""
    if "cards" in _cache:
        return _cache["cards"]  # type: ignore[return-value]
    out = []
    for f in sorted(Path(get_settings().MODEL_DIR).glob("*.model_card.json")):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        name = c.get("name") or f.name.replace(".model_card.json", "")
        m = c.get("metrics") or {}
        cleaning = c.get("cleaning") or {}
        if "rows_clean" in cleaning:
            rows = cleaning["rows_clean"]
        elif cleaning:
            rows = sum(v.get("rows_clean", 0) for v in cleaning.values() if isinstance(v, dict))
        else:
            rows = m.get("rows_deduplicated")
        metrics: dict = {}
        if "test" in m:
            metrics["test"] = _pick(m["test"], _KEEP)
        if "test_pooled_all_inputs" in m:
            metrics["test_all_inputs"] = _pick(m["test_pooled_all_inputs"], _KEEP)
            metrics["test_core6_only"] = _pick(m.get("test_pooled_core6_only_bmi_hba1c_missing"), _KEEP)
        if "leave_one_source_out_core6_uncalibrated" in m:
            metrics["leave_one_source_out"] = {src: _pick(v, ("n", "macro_f1", "recall", "high_roc_auc"))
                                               for src, v in m["leave_one_source_out_core6_uncalibrated"].items()}
        if not metrics:
            metrics["test"] = _pick(m, ("test_rows", "macro_f1", "high_risk_recall", "roc_auc_ovr"))
        out.append({"name": name, "version": c.get("version"), "status": "shadow",
                    "status_label": "Sinov rejimi — qarorga, zonalarga va signallarga taʼsir qilmaydi",
                    "dataset": "; ".join(f"{d.get('title')} ({d.get('licence')}, DOI {d.get('doi')})" for d in c.get("datasets", []))
                               or ("UCI Maternal Health Risk (CC BY 4.0, DOI 10.24432/C5DP5D)" if "uci" in name else None),
                    "datasets": c.get("datasets", []), "rows": rows, "trained_on": c.get("trained_on"),
                    "algorithm": c.get("algorithm"), "classes": c.get("classes"), "features": c.get("features"),
                    "metrics": metrics, "caveat": _CAVEAT_UZ.get(name, "Sinov rejimidagi model; mahalliy validatsiya yoʻq."),
                    "limitations": c.get("limitations", []),
                    "wired": name == MATERNAL_V2 and "Bemor kartasidagi ml_shadow yorligʻi" or
                             ("Ulanmagan: ilovada SisPorto belgilarini hisoblovchi modul yoʻq" if "ctg" in name else "Zaxira")})
    _cache["cards"] = out
    return out
