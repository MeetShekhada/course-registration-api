from fastapi import FastAPI, UploadFile, File, HTTPException
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
