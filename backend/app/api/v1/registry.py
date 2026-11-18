from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.deps import current_user
from app.core.errors import AppError, forbidden, not_found
from app.core.permissions import (assert_lab_write, assert_mother_access, assert_newborn_access, assert_pregnancy_access,
                                  facility_in_scope, require, scope_of)
from app.core.security import decrypt, encrypt, keyed_hash, utcnow
from app.db import get_db
from app.models import (CareAssignment, Consent, Facility, LabResult, MaternalHistory, MaternalObservation, Mother,
                        Newborn, NewbornObservation, Pregnancy, Referral, RiskAssessment, Role, User)
from app.schemas import (LabResultIn, LabResultOut, MaternalObservationIn, MaternalObservationOut, MotherCreate, MotherUpdate,
                         NewbornCreate, NewbornObservationIn, NewbornOut, PregnancyCreate, PregnancyOut, RiskAssessmentOut)
from app.services import risk_engine

router = APIRouter(tags=["registry"])


def _code(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4).upper()}"


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _mother_out(m: Mother, include_contact: bool) -> dict:
    preg = sorted(m.pregnancies, key=lambda p: p.created_at)[-1] if m.pregnancies else None
    d = {"id": m.id, "local_code": m.local_code, "display_name": decrypt(m.full_name_enc) or "—", "birth_year": m.birth_year,
         "region_soato": m.region_soato, "district_code": m.district_code, "facility_id": m.facility_id,
         "legal_basis": m.legal_basis, "is_synthetic": m.is_synthetic,
         "current_risk_level": preg.current_risk_level if preg else "grey"}
    if include_contact:
        d["phone"] = decrypt(m.phone_enc)
        d["address"] = decrypt(m.address_enc)
        d["birth_date"] = decrypt(m.birth_date_enc)
    return d


# ------------------------------------------------------------------ mothers
@router.post("/mothers", status_code=201)
def create_mother(body: MotherCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "mother:create")
    fac = db.get(Facility, body.facility_id)
    if not fac or not facility_in_scope(db, user, fac.id):
        raise forbidden("Facility outside your scope")
    if body.pinfl:
        h = keyed_hash(body.pinfl)
        if db.execute(select(Mother.id).where(Mother.pinfl_hash == h)).first():
            raise AppError(409, "duplicate_patient", "A patient with this identifier is already registered")
    else:
        h = None
    if not body.registry_consent and body.legal_basis == "consent":
        raise AppError(422, "consent_required", "Registry consent is required for this legal basis")
    m = Mother(local_code=_code("M"), full_name_enc=encrypt(body.full_name), phone_enc=encrypt(body.phone),
               address_enc=encrypt(body.address), pinfl_enc=encrypt(body.pinfl), pinfl_hash=h,
               birth_date_enc=encrypt(body.birth_date.isoformat() if body.birth_date else None),
               birth_year=body.birth_date.year if body.birth_date else None, region_soato=fac.region_soato,
               district_code=body.district_code or fac.district_code, facility_id=fac.id, legal_basis=body.legal_basis,
               is_synthetic=body.is_synthetic, created_by=user.id)
    db.add(m)
    db.flush()
    db.add(CareAssignment(user_id=user.id, mother_id=m.id))
    db.add(Consent(mother_id=m.id, purpose="registry", status="granted" if body.registry_consent else "refused",
                   legal_basis=body.legal_basis, recorded_by=user.id))
    write_audit(db, actor=user, action="mother.create", resource_type="mother", resource_id=m.id, request_id=_rid(request),
                purpose="care_registration", detail={"synthetic": body.is_synthetic})
    return _mother_out(m, include_contact=False)


@router.get("/mothers")
def list_mothers(risk_level: str | None = Query(None, pattern="^(red|yellow|green|grey)$"), limit: int = Query(50, le=200),
                 offset: int = 0, request: Request = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Patients the caller is responsible for (assignment) or that were referred to the caller's facility."""
    require(user, "mother:read")
    ids: set[str] = set(db.execute(select(CareAssignment.mother_id).where(CareAssignment.user_id == user.id,
                                                                          CareAssignment.active.is_(True),
                                                                          CareAssignment.mother_id.is_not(None))).scalars())
    facs = scope_of(user).facilities
    if facs:
        ids |= set(db.execute(select(Referral.mother_id).where(Referral.to_facility_id.in_(facs),
                                                               Referral.status.in_(["sent", "accepted", "consulted"]),
                                                               Referral.mother_id.is_not(None))).scalars())
    rows = [m for m in db.execute(select(Mother).where(Mother.id.in_(ids)).order_by(Mother.created_at.desc())).scalars()]
    out = [_mother_out(m, False) for m in rows]
    if risk_level:
        out = [o for o in out if o["current_risk_level"] == risk_level]
    write_audit(db, actor=user, action="mother.list", resource_type="mother", request_id=_rid(request), purpose="care",
                detail={"count": len(out)})
    return {"items": out[offset:offset + limit], "total": len(out), "limit": limit, "offset": offset}


@router.get("/mothers/{mother_id}")
def get_mother(mother_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = assert_mother_access(db, user, db.get(Mother, mother_id))
    write_audit(db, actor=user, action="mother.read", resource_type="mother", resource_id=m.id, request_id=_rid(request), purpose="care")
    out = _mother_out(m, include_contact=user.role == Role.OBGYN)
    out["pregnancies"] = [PregnancyOut.model_validate(p).model_dump() for p in sorted(m.pregnancies, key=lambda p: p.created_at)]
    out["history"] = [{"factor_code": h.factor_code, "present": h.present} for h in
                      db.execute(select(MaternalHistory).where(MaternalHistory.mother_id == m.id)).scalars()]
    return out


@router.patch("/mothers/{mother_id}")
def update_mother(mother_id: str, body: MotherUpdate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = assert_mother_access(db, user, db.get(Mother, mother_id), "update")
    changed = []
    for f in ("phone", "address"):
        v = getattr(body, f)
        if v is not None:
            setattr(m, f"{f}_enc", encrypt(v))
            changed.append(f)
    if body.district_code:
        m.district_code = body.district_code
        changed.append("district_code")
    write_audit(db, actor=user, action="mother.update", resource_type="mother", resource_id=m.id, request_id=_rid(request),
                detail={"fields": changed})
    return _mother_out(m, include_contact=True)


# ------------------------------------------------------------------ pregnancies
@router.post("/mothers/{mother_id}/pregnancies", status_code=201, response_model=PregnancyOut)
def create_pregnancy(mother_id: str, body: PregnancyCreate, request: Request, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    m = assert_mother_access(db, user, db.get(Mother, mother_id), "update")
    if any(p.status == "active" for p in m.pregnancies):
        raise AppError(409, "active_pregnancy_exists", "Mother already has an active pregnancy")
    from datetime import timedelta
    edd = body.edd or (body.lmp_date + timedelta(days=280) if body.lmp_date else None)
    p = Pregnancy(mother_id=m.id, lmp_date=body.lmp_date, edd=edd, gravida=body.gravida, parity=body.parity,
                  plurality=body.plurality, facility_id=m.facility_id, assigned_clinician_id=user.id, is_synthetic=m.is_synthetic)
    db.add(p)
    for f in body.risk_factors:
        db.add(MaternalHistory(mother_id=m.id, factor_code=f, recorded_by=user.id))
    db.flush()
    write_audit(db, actor=user, action="pregnancy.create", resource_type="pregnancy", resource_id=p.id, request_id=_rid(request))
    return p


@router.get("/pregnancies/{pregnancy_id}/timeline")
def pregnancy_timeline(pregnancy_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = assert_pregnancy_access(db, user, db.get(Pregnancy, pregnancy_id))
    obs = db.execute(select(MaternalObservation).where(MaternalObservation.pregnancy_id == p.id)
                     .order_by(MaternalObservation.observed_at)).scalars()
    labs = db.execute(select(LabResult).where(LabResult.pregnancy_id == p.id).order_by(LabResult.collected_at)).scalars()
    ras = db.execute(select(RiskAssessment).where(RiskAssessment.subject_type == "pregnancy", RiskAssessment.subject_id == p.id)
                     .order_by(RiskAssessment.created_at)).scalars()
    write_audit(db, actor=user, action="pregnancy.read", resource_type="pregnancy", resource_id=p.id, request_id=_rid(request), purpose="care")
    return {"pregnancy": PregnancyOut.model_validate(p).model_dump(),
            "observations": [MaternalObservationOut.model_validate(o).model_dump() for o in obs],
            "lab_results": [LabResultOut.model_validate(l).model_dump() for l in labs],
            "risk_assessments": [RiskAssessmentOut.model_validate(r).model_dump() for r in ras]}


def save_maternal_observation(db: Session, user: User, p: Pregnancy, data: dict, source: str = "manual",
                              voice_draft_id: str | None = None) -> tuple[MaternalObservation, RiskAssessment]:
    idem = data.pop("idempotency_key", None)
    if idem:
        prev = db.execute(select(MaternalObservation).where(MaternalObservation.idempotency_key == idem)).scalar_one_or_none()
        if prev:
            ra = db.execute(select(RiskAssessment).where(RiskAssessment.trigger_source_id == prev.id)).scalars().first()
            return prev, ra
    data["observed_at"] = data.get("observed_at") or utcnow()
    o = MaternalObservation(pregnancy_id=p.id, source=source, voice_draft_id=voice_draft_id, idempotency_key=idem,
                            created_by=user.id, **data)
    db.add(o)
    db.flush()
    ra = risk_engine.assess_pregnancy(db, p, actor_id=user.id)
    return o, ra


@router.post("/pregnancies/{pregnancy_id}/observations", status_code=201)
def add_observation(pregnancy_id: str, body: MaternalObservationIn, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    require(user, "observation:create")
    p = assert_pregnancy_access(db, user, db.get(Pregnancy, pregnancy_id), "update")
    o, ra = save_maternal_observation(db, user, p, body.model_dump(exclude_none=True))
    write_audit(db, actor=user, action="observation.create", resource_type="maternal_observation", resource_id=o.id,
                request_id=_rid(request), detail={"risk_level": ra.level if ra else None})
    return {"observation": MaternalObservationOut.model_validate(o).model_dump(),
            "risk_assessment": RiskAssessmentOut.model_validate(ra).model_dump() if ra else None}


@router.post("/pregnancies/{pregnancy_id}/lab-results", status_code=201)
def add_lab(pregnancy_id: str, body: LabResultIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = assert_lab_write(db, user, db.get(Pregnancy, pregnancy_id))
    if body.idempotency_key:
        prev = db.execute(select(LabResult).where(LabResult.idempotency_key == body.idempotency_key)).scalar_one_or_none()
        if prev:
            return {"lab_result": LabResultOut.model_validate(prev).model_dump(), "duplicate": True}
    lab = LabResult(pregnancy_id=p.id, test_code=body.test_code, value=body.value, unit=body.unit,
                    collected_at=body.collected_at, facility_id=p.facility_id, idempotency_key=body.idempotency_key,
                    created_by=user.id)
    db.add(lab)
    db.flush()
    ra = risk_engine.assess_pregnancy(db, p, actor_id=user.id)
    write_audit(db, actor=user, action="lab.create", resource_type="lab_result", resource_id=lab.id, request_id=_rid(request),
                detail={"test_code": body.test_code})
    out = {"lab_result": LabResultOut.model_validate(lab).model_dump()}
    if user.role != Role.LAB_OPERATOR:  # lab staff do not receive clinical risk details
        out["risk_assessment"] = RiskAssessmentOut.model_validate(ra).model_dump()
    return out


@router.post("/pregnancies/{pregnancy_id}/risk-assessments", status_code=201, response_model=RiskAssessmentOut)
def reassess(pregnancy_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = assert_pregnancy_access(db, user, db.get(Pregnancy, pregnancy_id), "update")
    ra = risk_engine.assess_pregnancy(db, p, actor_id=user.id)
    write_audit(db, actor=user, action="risk.assess", resource_type="pregnancy", resource_id=p.id, request_id=_rid(request))
    return ra


@router.get("/high-risk")
def high_risk_registry(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Red/yellow pregnancies and newborns within the caller's clinical reach."""
    require(user, "mother:read")
    mother_ids = set(db.execute(select(CareAssignment.mother_id).where(CareAssignment.user_id == user.id,
                                                                       CareAssignment.active.is_(True))).scalars())
    facs = scope_of(user).facilities
    if facs:
        mother_ids |= set(db.execute(select(Referral.mother_id).where(Referral.to_facility_id.in_(facs),
                                                                      Referral.status != "closed")).scalars())
    pregs = db.execute(select(Pregnancy).where(Pregnancy.mother_id.in_(mother_ids), Pregnancy.status == "active",
                                               Pregnancy.current_risk_level.in_(["red", "yellow"]))).scalars()
    nbq = select(Newborn).where(Newborn.current_risk_level.in_(["red", "yellow"]))
    nbq = nbq.where(Newborn.facility_id.in_(facs or {"-"})) if user.role == Role.NEONATOLOGIST else nbq.where(Newborn.mother_id.in_(mother_ids))
    order = {"red": 0, "yellow": 1}
    return {"pregnancies": sorted([PregnancyOut.model_validate(p).model_dump() for p in pregs], key=lambda x: order[x["current_risk_level"]]),
            "newborns": sorted([NewbornOut.model_validate(n).model_dump() for n in db.execute(nbq).scalars()],
                               key=lambda x: order[x["current_risk_level"]])}


# ------------------------------------------------------------------ newborns
@router.post("/newborns", status_code=201, response_model=NewbornOut)
def create_newborn(body: NewbornCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(user, "newborn:create")
    fac = db.get(Facility, body.facility_id)
    if not fac or not facility_in_scope(db, user, fac.id):
        raise forbidden("Facility outside your scope")
    if body.mother_id:
        mother = db.get(Mother, body.mother_id)
        if mother is None:
            raise not_found("mother")
        if user.role == Role.OBGYN:
            assert_mother_access(db, user, mother, "update")
        elif mother.region_soato != fac.region_soato:  # neonatologist may link only mothers delivering in-region
            raise not_found("mother")
    if body.pregnancy_id:
        p = db.get(Pregnancy, body.pregnancy_id)
        if not p or p.mother_id != body.mother_id:
            raise AppError(422, "invalid_link", "pregnancy_id must belong to mother_id")
        p.status = "delivered"
    nb = Newborn(local_code=_code("NB"), mother_id=body.mother_id, pregnancy_id=body.pregnancy_id,
                 birth_datetime=body.birth_datetime, sex=body.sex, gestational_age_weeks=body.gestational_age_weeks,
                 birth_weight_g=body.birth_weight_g, apgar_1=body.apgar_1, apgar_5=body.apgar_5, facility_id=fac.id,
                 region_soato=fac.region_soato, is_synthetic=body.is_synthetic, created_by=user.id)
    db.add(nb)
    db.flush()
    db.add(CareAssignment(user_id=user.id, newborn_id=nb.id))
    risk_engine.assess_newborn(db, nb, actor_id=user.id)
    write_audit(db, actor=user, action="newborn.create", resource_type="newborn", resource_id=nb.id, request_id=_rid(request))
    return nb


@router.get("/newborns/{newborn_id}")
def get_newborn(newborn_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    nb = assert_newborn_access(db, user, db.get(Newborn, newborn_id))
    obs = db.execute(select(NewbornObservation).where(NewbornObservation.newborn_id == nb.id)
                     .order_by(NewbornObservation.observed_at)).scalars()
    ras = db.execute(select(RiskAssessment).where(RiskAssessment.subject_type == "newborn", RiskAssessment.subject_id == nb.id)
                     .order_by(RiskAssessment.created_at)).scalars()
    write_audit(db, actor=user, action="newborn.read", resource_type="newborn", resource_id=nb.id, request_id=_rid(request), purpose="care")
    return {"newborn": NewbornOut.model_validate(nb).model_dump(),
            "observations": [{c: getattr(o, c) for c in ("id", "observed_at", "temperature_c", "respiratory_rate", "heart_rate",
                                                          "spo2", "weight_g", "jaundice", "poor_feeding", "convulsions",
                                                          "severe_chest_indrawing", "source")} for o in obs],
            "risk_assessments": [RiskAssessmentOut.model_validate(r).model_dump() for r in ras]}


def save_newborn_observation(db: Session, user: User, nb: Newborn, data: dict, source: str = "manual",
                             voice_draft_id: str | None = None) -> tuple[NewbornObservation, RiskAssessment]:
    idem = data.pop("idempotency_key", None)
    if idem:
        prev = db.execute(select(NewbornObservation).where(NewbornObservation.idempotency_key == idem)).scalar_one_or_none()
        if prev:
            return prev, db.execute(select(RiskAssessment).where(RiskAssessment.trigger_source_id == prev.id)).scalars().first()
    data["observed_at"] = data.get("observed_at") or utcnow()
    o = NewbornObservation(newborn_id=nb.id, source=source, voice_draft_id=voice_draft_id, idempotency_key=idem,
                           created_by=user.id, **data)
    db.add(o)
    db.flush()
    return o, risk_engine.assess_newborn(db, nb, actor_id=user.id)


@router.post("/newborns/{newborn_id}/observations", status_code=201)
def add_newborn_observation(newborn_id: str, body: NewbornObservationIn, request: Request, user: User = Depends(current_user),
                            db: Session = Depends(get_db)):
    require(user, "newborn_observation:create")
    nb = assert_newborn_access(db, user, db.get(Newborn, newborn_id), "update")
    o, ra = save_newborn_observation(db, user, nb, body.model_dump(exclude_none=True))
    write_audit(db, actor=user, action="newborn_observation.create", resource_type="newborn_observation", resource_id=o.id,
                request_id=_rid(request), detail={"risk_level": ra.level if ra else None})
    return {"observation_id": o.id, "risk_assessment": RiskAssessmentOut.model_validate(ra).model_dump() if ra else None}
