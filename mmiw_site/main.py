from __future__ import annotations
import os, uuid, hashlib, json, zipfile, tempfile, asyncio
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Depends
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from typing import Optional, List
from .db import init_db, connect, now_ts
from .schemas import CaseIn, CaseOut, TipOut, LERequestIn, LERequestOut
from .storage import save_and_hash, load_and_decrypt, scrub_exif_if_image
from .audit import log
from .access import optional_user, require_user, require_role, can_view_case, can_edit_case
from .panic import process_expired_countdowns, purge_expired_location_data

app = FastAPI(title="MMIW Site Scaffold", version="0.4.0")
from .panic import router as panic_router
app.include_router(panic_router)
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

_background_task: Optional[asyncio.Task] = None


async def _background_sweep_loop():
    """Runs forever in the background once the app starts: checks for
    expired panic countdowns every few seconds (so a person who doesn't
    check in actually gets their alert sent, not just a client-side clock
    running out), and purges expired location trails once an hour.

    This makes the app self-contained — no external cron job or separate
    worker process is required for the countdown/retention system to
    actually work. If you deploy this behind a process manager that only
    runs one worker, this is sufficient. If you ever run multiple server
    processes behind a load balancer, this should be moved to a single
    dedicated worker to avoid duplicate sends — noting that here so it's
    not a surprise later."""
    last_purge = 0
    while True:
        try:
            process_expired_countdowns()
        except Exception as e:
            log("system", "background.sweep_error", str(e))
        try:
            now = now_ts()
            if now - last_purge > 3600:
                purge_expired_location_data()
                last_purge = now
        except Exception as e:
            log("system", "background.purge_error", str(e))
        await asyncio.sleep(5)


@app.on_event("startup")
def _startup():
    init_db()
    global _background_task
    _background_task = asyncio.create_task(_background_sweep_loop())


@app.get("/health")
def health(): return {"ok": True}


@app.get("/cases", response_model=List[CaseOut])
def list_cases(
    level: Optional[str] = Query(None, description="Filter by public_level"),
    status: Optional[str] = Query(None, description="Filter by case status"),
    state: Optional[str] = Query(None, alias="last_seen_state"),
    city: Optional[str] = Query(None, alias="last_seen_city"),
    tribe: Optional[str] = Query(None, alias="tribal_affiliation"),
    q: Optional[str] = Query(None, description="Search name, city, state, or tribe"),
    user: Optional[dict] = Depends(optional_user),
):
    query = "SELECT * FROM cases"
    clauses = []
    params = []

    if level:
        clauses.append("public_level = ?")
        params.append(level)
    if status:
        clauses.append("LOWER(status) = LOWER(?)")
        params.append(status)
    if state:
        clauses.append("LOWER(last_seen_state) = LOWER(?)")
        params.append(state)
    if city:
        clauses.append("LOWER(last_seen_city) = LOWER(?)")
        params.append(city)
    if tribe:
        clauses.append("LOWER(tribal_affiliation) = LOWER(?)")
        params.append(tribe)
    if q:
        like = f"%{q}%"
        clauses.append(
            "(name LIKE ? OR last_seen_city LIKE ? OR last_seen_state LIKE ? OR tribal_affiliation LIKE ?)"
        )
        params.extend([like, like, like, like])

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC"

    conn = connect()
    rows = conn.execute(query, params).fetchall()
    conn.close()

    visible = [dict(r) for r in rows if can_view_case(user, dict(r))]
    return [CaseOut(**r) for r in visible]


@app.post("/cases", response_model=CaseOut, status_code=201)
def create_case(case: CaseIn, user: dict = Depends(require_user)):
    effective_level = case.public_level
    if user.get("role") not in ("moderator", "admin") and effective_level == "public":
        effective_level = "partners"

    case_id = str(uuid.uuid4())
    conn = connect()
    cur = conn.cursor()
    cur.execute('''INSERT INTO cases (id,status,name,dob,age_at_disappearance,gender,tribal_affiliation,last_seen_date,last_seen_city,last_seen_state,geo_precision,public_level,family_consent,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (case_id, case.status, case.name, case.dob, case.age_at_disappearance, case.gender, case.tribal_affiliation,
                 case.last_seen_date, case.last_seen_city, case.last_seen_state, case.geo_precision, effective_level,
                 1 if case.family_consent else 0, now_ts(), now_ts()))

    access_id = str(uuid.uuid4())
    cur.execute(
        """INSERT INTO case_access (id, case_id, user_id, access_role, granted_by, created_at)
           VALUES (?,?,?,?,?,?)""",
        (access_id, case_id, user["id"], "family_editor", user["id"], now_ts()),
    )

    conn.commit()
    conn.close()
    log(user["id"], "case.create", case_id)

    out = case.model_dump()
    out["public_level"] = effective_level
    return CaseOut(id=case_id, **out)


@app.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: str, user: Optional[dict] = Depends(optional_user)):
    conn = connect()
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "Case not found")
    case = dict(row)
    if not can_view_case(user, case):
        raise HTTPException(404, "Case not found")

    return CaseOut(**case)


@app.put("/cases/{case_id}", response_model=CaseOut)
def update_case(case_id: str, case: CaseIn, user: dict = Depends(require_user)):
    conn = connect()
    cur = conn.cursor()

    existing = cur.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(404, "Case not found")

    existing_case = dict(existing)
    if not can_edit_case(user, existing_case):
        conn.close()
        raise HTTPException(403, "You don't have permission to edit this case")

    new_level = case.public_level
    if user.get("role") not in ("moderator", "admin"):
        new_level = existing_case["public_level"]

    cur.execute(
        """
        UPDATE cases SET
            status = ?, name = ?, dob = ?, age_at_disappearance = ?, gender = ?,
            tribal_affiliation = ?, last_seen_date = ?, last_seen_city = ?,
            last_seen_state = ?, geo_precision = ?, public_level = ?,
            family_consent = ?, updated_at = ?
        WHERE id = ?
        """,
        (case.status, case.name, case.dob, case.age_at_disappearance, case.gender,
         case.tribal_affiliation, case.last_seen_date, case.last_seen_city,
         case.last_seen_state, case.geo_precision, new_level,
         1 if case.family_consent else 0, now_ts(), case_id),
    )
    conn.commit()
    row = cur.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()
    log(user["id"], "case.update", case_id)

    return CaseOut(**dict(row))


@app.get("/cases/{case_id}/tips", response_model=List[TipOut])
def list_case_tips(case_id: str, user: Optional[dict] = Depends(optional_user)):
    conn = connect()
    case_row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not case_row:
        conn.close()
        raise HTTPException(404, "Case not found")

    case = dict(case_row)
    if not can_view_case(user, case):
        conn.close()
        raise HTTPException(404, "Case not found")

    if not can_edit_case(user, case):
        conn.close()
        raise HTTPException(403, "Only case managers can view submitted tips")

    rows = conn.execute(
        "SELECT id, case_id, created_at FROM tips WHERE case_id = ? ORDER BY created_at DESC",
        (case_id,),
    ).fetchall()
    conn.close()
    return [TipOut(**dict(r)) for r in rows]


@app.post("/tips", response_model=TipOut, status_code=201)
async def submit_tip(
    message: str = Form(...),
    case_id: Optional[str] = Form(None),
    named: bool = Form(False),
    contact: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    if case_id:
        conn = connect()
        case_row = conn.execute("SELECT id FROM cases WHERE id = ?", (case_id,)).fetchone()
        conn.close()
        if not case_row:
            raise HTTPException(404, "Case not found")

    file_hash = None
    if file is not None:
        data = await file.read()
        data = scrub_exif_if_image(data)
        _, file_hash, _, _ = save_and_hash(file.filename, data)

    tip_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO tips (id,case_id,named,contact,message,file_hash,created_at) VALUES (?,?,?,?,?,?,?)",
        (tip_id, case_id, 1 if named else 0, contact, message, file_hash, now_ts()),
    )
    conn.commit()
    conn.close()
    log("public", "tip.create", tip_id)
    return TipOut(id=tip_id, case_id=case_id, created_at=now_ts())


@app.post("/evidence/upload", status_code=201)
async def upload_evidence(
    case_id: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
):
    conn = connect()
    case_row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not case_row:
        conn.close()
        raise HTTPException(404, "Case not found")

    if not can_edit_case(user, dict(case_row)):
        conn.close()
        raise HTTPException(403, "Only case managers can upload evidence")

    data = await file.read()
    data = scrub_exif_if_image(data)
    stored_path, file_hash, was_encrypted, nonce_hex = save_and_hash(file.filename, data)

    evidence_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO evidence (id, case_id, filename, stored_path, sha256, encrypted, nonce_hex, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (evidence_id, case_id, file.filename, stored_path, file_hash, 1 if was_encrypted else 0, nonce_hex, now_ts()),
    )
    conn.commit()
    conn.close()
    log(user["id"], "evidence.upload", evidence_id)

    return {
        "id": evidence_id, "case_id": case_id, "filename": file.filename,
        "sha256": file_hash, "created_at": now_ts(),
    }


@app.get("/cases/{case_id}/evidence")
def list_case_evidence(case_id: str, user: dict = Depends(require_user)):
    conn = connect()
    case_row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not case_row:
        conn.close()
        raise HTTPException(404, "Case not found")

    if not can_edit_case(user, dict(case_row)):
        conn.close()
        raise HTTPException(403, "Only case managers can view evidence")

    rows = conn.execute(
        "SELECT id, case_id, filename, stored_path, sha256, created_at FROM evidence WHERE case_id = ? ORDER BY created_at DESC",
        (case_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/evidence/{evidence_id}/download")
def download_evidence(evidence_id: str, user: dict = Depends(require_user)):
    conn = connect()
    row = conn.execute(
        "SELECT e.id, e.filename, e.stored_path, e.case_id, e.encrypted, e.nonce_hex FROM evidence e WHERE e.id = ?",
        (evidence_id,),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Evidence not found")

    case_row = conn.execute("SELECT * FROM cases WHERE id = ?", (row["case_id"],)).fetchone()
    conn.close()
    if not case_row or not can_edit_case(user, dict(case_row)):
        raise HTTPException(403, "You don't have permission to access this evidence")

    if not os.path.exists(row["stored_path"]):
        raise HTTPException(404, "Stored file not found")

    try:
        plaintext = load_and_decrypt(
            row["stored_path"], encrypted=bool(row["encrypted"]), nonce_hex=row["nonce_hex"],
        )
    except RuntimeError as e:
        # The file is encrypted but the vault key isn't available in this
        # environment — this is a real, actionable error, not a 404.
        raise HTTPException(500, str(e))

    return Response(
        content=plaintext,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
    )


@app.get("/evidence/{evidence_id}/verify")
def verify_evidence(evidence_id: str, user: dict = Depends(require_user)):
    conn = connect()
    row = conn.execute(
        "SELECT e.id, e.filename, e.stored_path, e.sha256, e.case_id, e.encrypted, e.nonce_hex FROM evidence e WHERE e.id = ?",
        (evidence_id,),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Evidence not found")

    case_row = conn.execute("SELECT * FROM cases WHERE id = ?", (row["case_id"],)).fetchone()
    conn.close()
    if not case_row or not can_edit_case(user, dict(case_row)):
        raise HTTPException(403, "You don't have permission to access this evidence")

    if not os.path.exists(row["stored_path"]):
        raise HTTPException(404, "Stored file not found")

    try:
        plaintext = load_and_decrypt(
            row["stored_path"], encrypted=bool(row["encrypted"]), nonce_hex=row["nonce_hex"],
        )
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    computed_hash = hashlib.sha256(plaintext).hexdigest()

    return {
        "evidence_id": row["id"], "filename": row["filename"],
        "valid": computed_hash == row["sha256"],
        "stored_hash": row["sha256"], "computed_hash": computed_hash,
    }


@app.get("/cases/{case_id}/export")
def export_case(case_id: str, user: dict = Depends(require_user)):
    conn = connect()
    case_row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not case_row:
        conn.close()
        raise HTTPException(404, "Case not found")

    if not can_edit_case(user, dict(case_row)):
        conn.close()
        raise HTTPException(403, "Only case managers can export this case")

    tip_rows = conn.execute(
        "SELECT id, case_id, created_at FROM tips WHERE case_id = ? ORDER BY created_at DESC", (case_id,)
    ).fetchall()
    evidence_rows = conn.execute(
        "SELECT id, case_id, filename, stored_path, sha256, encrypted, nonce_hex, created_at FROM evidence WHERE case_id = ? ORDER BY created_at DESC",
        (case_id,),
    ).fetchall()
    conn.close()

    case_data = dict(case_row)
    tips_data = [dict(r) for r in tip_rows]
    evidence_data = [dict(r) for r in evidence_rows]

    temp_dir = tempfile.mkdtemp()
    export_dir = os.path.join(temp_dir, f"case_{case_id}")
    evidence_dir = os.path.join(export_dir, "evidence")
    os.makedirs(evidence_dir, exist_ok=True)

    with open(os.path.join(export_dir, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case_data, f, indent=2)
    with open(os.path.join(export_dir, "tips.json"), "w", encoding="utf-8") as f:
        json.dump(tips_data, f, indent=2)
    with open(os.path.join(export_dir, "evidence.json"), "w", encoding="utf-8") as f:
        json.dump(evidence_data, f, indent=2)

    manifest_data = {
        "case_id": case_id,
        "exported_at": now_ts(),
        "summary": {"tip_count": len(tips_data), "evidence_count": len(evidence_data)},
        "files": [
            {"type": "case", "filename": "case.json"},
            {"type": "tips", "filename": "tips.json"},
            {"type": "evidence_index", "filename": "evidence.json"},
        ],
        "evidence_files": [
            {"evidence_id": ev["id"], "filename": ev["filename"], "sha256": ev["sha256"]}
            for ev in evidence_data
        ],
    }
    with open(os.path.join(export_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    for ev in evidence_data:
        src = ev["stored_path"]
        if os.path.exists(src):
            dst = os.path.join(evidence_dir, ev["filename"])
            try:
                plaintext = load_and_decrypt(
                    src, encrypted=bool(ev.get("encrypted")), nonce_hex=ev.get("nonce_hex"),
                )
            except RuntimeError:
                # Vault key unavailable for this file — skip it rather than
                # exporting undecryptable ciphertext silently mislabeled as
                # the real evidence file. The manifest still lists it so the
                # gap is visible, not hidden.
                continue
            with open(dst, "wb") as wf:
                wf.write(plaintext)

    zip_path = os.path.join(temp_dir, f"case_{case_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(export_dir):
            for file_name in files:
                if file_name.startswith(".") or "__MACOSX" in root:
                    continue
                full_path = os.path.join(root, file_name)
                zf.write(full_path, os.path.relpath(full_path, export_dir))

    log(user["id"], "case.export", case_id)
    return FileResponse(path=zip_path, filename=f"case_{case_id}.zip", media_type="application/zip")


@app.post("/le/requests", response_model=LERequestOut, status_code=201)
def create_le_request(req: LERequestIn, user: dict = Depends(require_user)):
    req_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO le_requests (id,agency,contact_email,case_id,statutory_basis,scope,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (req_id, req.agency, req.contact_email, req.case_id, req.statutory_basis, req.scope, "received", now_ts()),
    )
    conn.commit()
    conn.close()
    log(user["id"], "le.request.create", req_id)
    return LERequestOut(id=req_id, status="received", created_at=now_ts())


@app.get("/posters/{case_id}")
def poster_stub(case_id: str, user: Optional[dict] = Depends(optional_user)):
    conn = connect()
    case_row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()
    if not case_row or not can_view_case(user, dict(case_row)):
        raise HTTPException(404, "Case not found")

    pdf = minimal_pdf(f"MMIW Poster — Case {case_id}\nVisit /static/index.html")
    return StreamingResponse(iter([pdf]), media_type="application/pdf",
                              headers={"Content-Disposition": f"inline; filename=poster_{case_id}.pdf"})


def minimal_pdf(text: str) -> bytes:
    text = text.replace("(", "[").replace(")", "]")
    pdf = f"""%PDF-1.4
1 0 obj<<>>endobj
2 0 obj<< /Length 44 >>stream
BT /F1 18 Tf 72 720 Td ({text}) Tj ET
endstream
endobj
3 0 obj<< /Type /Page /Parent 4 0 R /MediaBox [0 0 612 792] /Contents 2 0 R /Resources<< /Font<< /F1 5 0 R>>>>>
endobj
4 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1>>
endobj
5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica>>
endobj
6 0 obj<< /Type /Catalog /Pages 4 0 R>>
endobj
xref
0 7
0000000000 65535 f 
0000000010 00000 n 
0000000051 00000 n 
0000000179 00000 n 
0000000399 00000 n 
0000000463 00000 n 
0000000535 00000 n 
trailer<< /Size 7 /Root 6 0 R>>
startxref
610
%%EOF"""
    return pdf.encode("latin-1", "ignore")


@app.get("/", response_class=HTMLResponse)
def home():
    with open(os.path.join(static_dir, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())
