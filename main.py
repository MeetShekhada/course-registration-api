from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from bs4 import BeautifulSoup
from pydantic import BaseModel
import re

app = FastAPI()

# Phase 1 - global catalog
catalog = {}

# Phase 2 - per-student data
students = {}


# ---------- Phase 1 models ----------

def parse_courses(raw: str) -> list[str]:
    if not raw or raw.strip().lower() == "none":
        return []
    return re.findall(r'[A-Z]{2,4}\s\d{4}', raw)


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
        catalog[code] = {
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
    course = catalog.get(course_code)
    if not course:
        normalized = re.sub(r'([A-Z]{2,4})(\d{4})', r'\1 \2', course_code)
        course = catalog.get(normalized)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


# ---------- Phase 2 endpoints ----------

def parse_transcript(content: bytes) -> list[dict]:
    soup = BeautifulSoup(content, "html.parser")
    seen = {}  # key: (course_code, term)

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if "Status" not in headers or "Course" not in headers:
            continue

        for row in table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) < 6:
                continue

            status  = cols[0].get_text(strip=True)
            code    = cols[1].get_text(strip=True)
            grade   = cols[3].get_text(strip=True)
            term    = cols[4].get_text(strip=True)
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
async def import_history(student_id: str, file: UploadFile = File(...)):
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
    students[student_id]["history"] = [c.dict() for c in body.history]
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
    students[student_id]["plan"] = [c.dict() for c in body.planned_courses]
    return {"status": "success", "planned_courses_saved": len(body.planned_courses)}


@app.put("/api/v1/students/{student_id}/plan")
def update_plan(student_id: str, body: PlanBody):
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    students[student_id]["plan"] = [c.dict() for c in body.planned_courses]
    return {"status": "success", "planned_courses_saved": len(body.planned_courses)}


@app.delete("/api/v1/students/{student_id}/plan")
def delete_plan(student_id: str):
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    students[student_id]["plan"] = []
    return {"status": "success", "message": "Plan cleared"}


@app.get("/api/v1/students/{student_id}/profile")
def get_profile(student_id: str):
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")
    s = students[student_id]
    return {
        "student_id": student_id,
        "history": s["history"],
        "plan": s["plan"]
    }
# ---------- Phase 3 helpers ----------

def normalize_code(code: str) -> str:
    """COSC-3506 = COSC 3506 = cosc3506 → COSC3506"""
    return re.sub(r'[\s\-]', '', code).upper()


SEASON_ORDER = {"W": 0, "SP": 1, "S": 2, "F": 3}


def parse_term(term: str):
    """Returns a sortable tuple (year, season_int) from e.g. '23F', '26SP'."""
    match = re.match(r'(\d{2})(W|SP|S|F)$', term)
    if not match:
        return (99, 99)
    year = int(match.group(1))
    season = SEASON_ORDER.get(match.group(2), 99)
    return (year, season)


def term_before(t1: str, t2: str) -> bool:
    """Returns True if t1 is strictly before t2."""
    return parse_term(t1) < parse_term(t2)


# ---------- Phase 3 endpoint ----------

@app.get("/api/v1/students/{student_id}/audit-report")
def audit_report(student_id: str, strict: bool = Query(default=False)):
    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")

    student = students[student_id]
    history = student["history"]
    plan = student["plan"]

    # Build completed set: normalize_code -> list of (term, credits_earned)
    completed = {}
    for course in history:
        if course["status"] == "Completed":
            nc = normalize_code(course["course_code"])
            if nc not in completed:
                completed[nc] = []
            completed[nc].append(course)

    # Deduplicate completed — keep highest credits per course (retake handling)
    total_earned = 0
    earned_per_course = {}
    for nc, entries in completed.items():
        best = max(entries, key=lambda x: x["credits_earned"])
        earned_per_course[nc] = best
        total_earned += best["credits_earned"]

    # Build catalog lookup by normalized code
    catalog_by_norm = {}
    for code, data in catalog.items():
        catalog_by_norm[normalize_code(code)] = data

    # Group plan by term
    plan_by_term = {}
    for pc in plan:
        t = pc["term"]
        if t not in plan_by_term:
            plan_by_term[t] = []
        plan_by_term[t].append(pc)

    # Sort terms chronologically
    sorted_terms = sorted(plan_by_term.keys(), key=parse_term)

    timeline_validation = []
    cross_list_violations = []
    total_planned = 0

    for term in sorted_terms:
        term_errors = []
        for pc in plan_by_term[term]:
            nc = normalize_code(pc["course_code"])
            cat_entry = catalog_by_norm.get(nc)

            # Credits for planned
            if cat_entry:
                try:
                    total_planned += int(cat_entry["credits"])
                except (ValueError, TypeError):
                    pass

            # Check prerequisites
            if cat_entry:
                for prereq in cat_entry.get("prerequisites", []):
                    np = normalize_code(prereq)
                    # Must be completed in a strictly earlier term
                    prereq_ok = False
                    if np in completed:
                        for entry in completed[np]:
                            if term_before(entry["term"], term):
                                prereq_ok = True
                                break
                    if not prereq_ok:
                        term_errors.append({
                            "course_code": pc["course_code"],
                            "type": "MISSING_PREREQUISITE",
                            "message": f"Missing prerequisite: {prereq}"
                        })

            # Check cross-listing
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

    # Status
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
