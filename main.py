from fastapi import FastAPI, UploadFile, File, HTTPException
from bs4 import BeautifulSoup
import re

app = FastAPI()

catalog = {}


def parse_courses(raw: str) -> list[str]:
    """Extract course codes like 'COSC 2006' from a cell. Returns [] if None or empty."""
    if not raw or raw.strip().lower() == "none":
        return []
    return re.findall(r'[A-Z]{2,4}\s\d{4}', raw)


@app.post("/api/v1/admin/catalog/import")
async def import_catalog(file: UploadFile = File(...)):
    content = await file.read()
    soup = BeautifulSoup(content, "html.parser")

    rows = soup.find_all("tr")
    imported = 0

    for row in rows:
        cols = row.find_all("td")
	#cols = row.find_all("th")


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
        raise HTTPException(status_code=404, detail="Course not found")
    return course
