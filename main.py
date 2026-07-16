from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from bs4 import BeautifulSoup
from pydantic import BaseModel
import re
import bcrypt
import jwt
import time
from collections import deque

app = FastAPI()

catalog = {}
students = {}
users = {}
rate_limit_store = {}

SECRET_KEY = "supersecretkey123"
ALGORITHM = "HS256"
security = HTTPBearer(auto_error=False)

# Hardcode admin at startup
admin_hash = bcrypt.hashpw(b"admin", bcrypt.gensalt())
users["admin"] = {"username": "admin", "password_hash": admin_hash, "role": "admin"}


# ---------- Auth models ----------

class AuthBody(BaseModel):
    username: str
    password: str


# ---------- Phase 2 models ----------

class PlannedCourse(BaseModel):
    course_code: str
    term: str

class PlanBody(BaseModel):
    planned_courses: list[PlannedCourse]

class HistoryCourse(BaseModel):
    course_code: str
    term: str
    credits_earned: int
    status: str

class HistoryBody(BaseModel):
    history: list[HistoryCourse]


# ---------- Helpers ----------

def parse_courses(raw: str) -> list[str]:
    if not raw or raw.strip().lower() == "none":
        return []
    return re.findall(r'[A-Z]{2,4}[\s\-]?\d{4}', raw)


def normalize_code(code: str) -> str:
    return re.sub(r'[\s\-]', '', code).upper()


SEASON_ORDER = {"W": 0, "SP": 1, "S": 2, "F": 3}


def parse_term(term: str):
    match = re.match(r'(\d{2})(W|SP|S|F)$', term)
    if not match:
        return (99, 99)
    year = int(match.group(1))
    season = SEASON_ORDER.get(match.group(2), 99)
    return (year, season)


def term_before(t1: str, t2: str) -> bool:
    return parse_term(t1) < parse_term(t2)


def make_token(username: str, role: str) -> str:
    payload = {"sub": username, "role": role, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_token(credentials.credentials)


# ---------- Auth endpoints ----------

@app.post("/api/v1/auth/register", status_code=201)
def register(body: AuthBody):
    if body.username in users:
        raise HTTPException(status_code=409, detail="Username already exists")
    hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt())
    users[body.username] = {"username": body.username, "password_hash": hashed, "role": "user"}
    return {"status": "registered"}


@app.post("/api/v1/auth/login")
def login(body: AuthBody):
    user = users.get(body.username)
    if not user or not bcrypt.checkpw(body.password.encode(), user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = make_token(body.username, user["role"])
    return {"access_token": token, "token_type": "bearer"}


# ---------- Phase 1 endpoints ----------

@app.post("/api/v1/admin/catalog/import")
async def import_catalog(file: UploadFile = File(...)):
    content = await file.read()
    soup = BeautifulSoup(content, "html.parser")
    rows = soup.find_all("tr")
    imported = 0
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 4:
            continue
        code    = cols[0].get_text(strip=True)
        title   = cols[1].get_text(strip=True)
        credits = cols[2].get_text(strip=True)
        prereqs = cols[3].get_text(strip=True)
        cross   = cols[4].get_text(strip=True) if len(cols) > 4 else ""
        norm_key = normalize_code(code)
        catalog[norm_key] = {
            "course_code":   code,
            "title":         title,
            "credits":       credits,
            "prerequisites": parse_courses(prereqs),
            "cross_listed":  parse_courses(cross),
        }
        imported += 1
    return {"message": f"Imported {imported} courses successfully."}


@app.get("/api/v1/catalog/courses/{course_code}")
def get_course(course_code: str):
    norm_key = normalize_code(course_code)
    course = catalog.get(norm_key)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


# ---------- Phase 2 endpoints ----------

def parse_transcript(content: bytes) -> list[dict]:
    soup = BeautifulSoup(content, "html.parser")
    seen = {}

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if "Status" not in headers or "Course" not in headers:
            continue

        for row in table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) < 6:
                continue

            status      = cols[0].get_text(strip=True)
            code        = cols[1].get_text(strip=True)
            grade       = cols[3].get_text(strip=True)
            term        = cols[4].get_text(strip=True)
            credits_raw = cols[5].get_text(strip=True)

            if status not in ("Completed", "In-Progress", "Attempted"):
                continue
            if not term:
                continue

            try:
                credits_earned = int(credits_raw)
            except (ValueError, TypeError):
                credits_earned = 0

            key = (code, term)

            def grade_score(g):
                try:
                    float(g)
                    return 2
                except ValueError:
                    pass
                if g and g != "P":
                    return 1
                return 0

            if key not in seen or grade_score(grade) > grade_score(seen[key]["_grade"]) or \
               (grade_score(grade) == grade_score(seen[key]["_grade"]) and credits_earned > seen[key]["credits_earned"]):
                seen[key] = {
                    "course_code":    code,
                    "term":           term,
                    "credits_earned": credits_earned,
                    "status":         status,
                    "_grade":         grade,
                }

    return [
        {k: v for k, v in record.items() if k != "_grade"}
        for record in seen.values()
    ]


@app.post("/api/v1/students/{student_id}/history/import", status_code=201)
async def import_history(
    student_id: str,
    file: UploadFile = File(...),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if payload.get("sub") != student_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    content = await file.read()
    courses = parse_transcript(content)
    students[student_id] = {
        "student_id": student_id,
        "history": courses,
        "plan": []
    }
    return {"status": "success", "past_courses_imported": len(courses)}


@app.put("/api/v1/students/{student_id}/history")
def update_history(student_id: str, body: HistoryBody):
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    students[student_id]["history"] = [c.model_dump() for c in body.history]
    return {"status": "success", "message": "Academic history updated successfully"}


@app.delete("/api/v1/students/{student_id}/history")
def delete_history(student_id: str):
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    students[student_id]["history"] = []
    return {"status": "success", "message": "History cleared"}


@app.post("/api/v1/students/{student_id}/plan")
def create_plan(student_id: str, body: PlanBody):
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    students[student_id]["plan"] = [c.model_dump() for c in body.planned_courses]
    return {"status": "success", "planned_courses_saved": len(body.planned_courses)}


@app.put("/api/v1/students/{student_id}/plan")
def update_plan(student_id: str, body: PlanBody):
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    students[student_id]["plan"] = [c.model_dump() for c in body.planned_courses]
    return {"status": "success", "planned_courses_saved": len(body.planned_courses)}


@app.delete("/api/v1/students/{student_id}/plan")
def delete_plan(student_id: str):
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    students[student_id]["plan"] = []
    return {"status": "success", "message": "Plan cleared"}


@app.get("/api/v1/students/{student_id}/profile")
def get_profile(
    student_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if payload.get("sub") != student_id and payload.get("role") != "admin":
        raise HTTPException(status_code=401, detail="Unauthorized")
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    s = students[student_id]
    return {
        "student_id": student_id,
        "history": s["history"],
        "plan": s["plan"]
    }


@app.get("/api/v1/students/{student_id}/plan")
def get_plan(
    student_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if payload.get("sub") != student_id and payload.get("role") != "admin":
        raise HTTPException(status_code=401, detail="Unauthorized")
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"plan": students[student_id]["plan"]}


# ---------- Phase 3 endpoints ----------

@app.get("/api/v1/students/{student_id}/audit-report")
def audit_report(
    student_id: str,
    strict: bool = Query(default=False),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")

    # Rate limiting
    key = None
    if credentials:
        try:
            payload = decode_token(credentials.credentials)
            key = payload.get("sub")
        except Exception:
            pass
    if not key:
        key = "anonymous"

    now = time.time()
    window = 60
    if key not in rate_limit_store:
        rate_limit_store[key] = []
    rate_limit_store[key] = [t for t in rate_limit_store[key] if now - t < window]
    if len(rate_limit_store[key]) >= 10:
        raise HTTPException(status_code=429, detail="Too many requests")
    rate_limit_store[key].append(now)

    student = students[student_id]
    history = student["history"]
    plan = student["plan"]

    completed = {}
    for course in history:
        if course["status"] == "Completed":
            nc = normalize_code(course["course_code"])
            if nc not in completed:
                completed[nc] = []
            completed[nc].append(course)

    total_earned = 0
    earned_per_course = {}
    for nc, entries in completed.items():
        best = max(entries, key=lambda x: x["credits_earned"])
        earned_per_course[nc] = best
        total_earned += best["credits_earned"]

    catalog_by_norm = {}
    for code, data in catalog.items():
        catalog_by_norm[normalize_code(code)] = data

    plan_by_term = {}
    for pc in plan:
        t = pc["term"]
        if t not in plan_by_term:
            plan_by_term[t] = []
        plan_by_term[t].append(pc)

    sorted_terms = sorted(plan_by_term.keys(), key=parse_term)

    timeline_validation = []
    cross_list_violations = []
    total_planned = 0

    for term in sorted_terms:
        term_errors = []
        for pc in plan_by_term[term]:
            nc = normalize_code(pc["course_code"])
            cat_entry = catalog_by_norm.get(nc)

            if cat_entry:
                try:
                    total_planned += int(cat_entry["credits"])
                except (ValueError, TypeError):
                    pass

            if cat_entry:
                for prereq in cat_entry.get("prerequisites", []):
                    np_norm = normalize_code(prereq)
                    prereq_ok = False
                    if np_norm in completed:
                        for entry in completed[np_norm]:
                            if term_before(entry["term"], term):
                                prereq_ok = True
                                break
                    if not prereq_ok:
                        term_errors.append({
                            "course_code": pc["course_code"],
                            "type": "MISSING_PREREQUISITE",
                            "message": f"Missing prerequisite: {prereq}"
                        })

            if cat_entry:
                for cross in cat_entry.get("cross_listed", []):
                    ncross = normalize_code(cross)
                    if ncross in earned_per_course:
                        cross_list_violations.append({
                            "course_code": pc["course_code"],
                            "type": "CROSS_LIST_CONFLICT",
                            "message": f"Cross-listed with completed course {cross}"
                        })

        if term_errors:
            timeline_validation.append({
                "term": term,
                "errors": term_errors
            })

    has_issues = bool(timeline_validation or cross_list_violations)
    if not has_issues:
        status = "ok"
    elif strict:
        status = "failed"
    else:
        status = "warning"

    total_remaining = max(0, 120 - total_earned - total_planned)

    return {
        "student_id": student_id,
        "status": status,
        "timeline_validation": timeline_validation,
        "cross_list_violations": cross_list_violations,
        "credit_summary": {
            "total_earned": total_earned,
            "total_planned": total_planned,
            "total_remaining_for_graduation": total_remaining
        }
    }


# ---------- Phase 4 recommendations ----------

@app.get("/api/v1/students/{student_id}/recommendations")
def recommendations(
    student_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if payload.get("sub") != student_id and payload.get("role") != "admin":
        raise HTTPException(status_code=401, detail="Unauthorized")
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")

    history = students[student_id]["history"]
    completed_norms = set()
    for course in history:
        if course["status"] == "Completed":
            completed_norms.add(normalize_code(course["course_code"]))

    # Only recommend courses not yet completed
    remaining = {
        nc: data for nc, data in catalog.items()
        if nc not in completed_norms
    }

    # Build dependency graph
    in_degree = {nc: 0 for nc in remaining}
    graph = {nc: [] for nc in remaining}

    for nc, data in remaining.items():
        for prereq in data.get("prerequisites", []):
            pnc = normalize_code(prereq)
            if pnc in remaining:
                graph[pnc].append(nc)
                in_degree[nc] += 1

    # Kahn's algorithm — level by level
    queue = deque([nc for nc in remaining if in_degree[nc] == 0])
    pathway = []
    TERM_NAMES = ["26F", "27W", "27SP", "27F", "28W", "28SP", "28F", "29W", "29SP", "29F"]
    term_idx = 0

    while queue:
        term_courses = []
        next_queue = deque()
        level_size = len(queue)
        for _ in range(level_size):
            nc = queue.popleft()
            term_courses.append(catalog[nc]["course_code"])
            for neighbor in graph[nc]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    next_queue.append(neighbor)
        if term_courses:
            term_name = TERM_NAMES[term_idx] if term_idx < len(TERM_NAMES) else f"T{term_idx}"
            pathway.append({"term": term_name, "courses": term_courses})
            term_idx += 1
        queue = next_queue

    return {
        "student_id": student_id,
        "recommended_pathway": pathway
    }