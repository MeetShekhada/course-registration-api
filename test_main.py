from fastapi.testclient import TestClient
from main import app, catalog, students

client = TestClient(app)


def setup_function():
    catalog.clear()
    students.clear()


SAMPLE_HTML = "<html><body><table><tr><th>Status</th><th>Course</th><th>Title</th><th>Grade</th><th>Term</th><th>Credits</th></tr><tr><td>Completed</td><td>COSC-1046</td><td>Programming I</td><td>85</td><td>23F</td><td>3</td></tr><tr><td>Completed</td><td>COSC-1047</td><td>Programming II</td><td>78</td><td>24W</td><td>3</td></tr><tr><td>In-Progress</td><td>COSC-3506</td><td>Software Systems</td><td></td><td>25F</td><td>3</td></tr><tr><td>Attempted</td><td>COSC-2007</td><td>Failed Course</td><td>F</td><td>23F</td><td>0</td></tr></table></body></html>"

CATALOG_HTML = "<html><body><table><tr><th>Course Code</th><th>Title</th><th>Credits</th><th>Prerequisites</th><th>Cross-listed</th></tr><tr><td>COSC 1046</td><td>Programming I</td><td>3</td><td>None</td><td></td></tr><tr><td>COSC 1047</td><td>Programming II</td><td>3</td><td>COSC 1046</td><td></td></tr><tr><td>COSC 3506</td><td>Software Systems</td><td>3</td><td>COSC 1047</td><td>ITEC 3506</td></tr><tr><td>ITEC 3506</td><td>Software Systems</td><td>3</td><td>COSC 1047</td><td>COSC 3506</td></tr></table></body></html>"

MINIMAL_HTML = "<html><body><table><tr><th>Status</th><th>Course</th><th>Title</th><th>Grade</th><th>Term</th><th>Credits</th></tr><tr><td>Completed</td><td>COSC-1046</td><td>Prog I</td><td>80</td><td>23F</td><td>3</td></tr></table></body></html>"


def test_import_catalog():
    response = client.post("/api/v1/admin/catalog/import", files={"file": ("catalog.html", CATALOG_HTML, "text/html")})
    assert response.status_code == 200
    assert "Imported" in response.json()["message"]


def test_get_course_not_found():
    response = client.get("/api/v1/catalog/courses/FAKE9999")
    assert response.status_code == 404


def test_get_course_found():
    client.post("/api/v1/admin/catalog/import", files={"file": ("catalog.html", CATALOG_HTML, "text/html")})
    response = client.get("/api/v1/catalog/courses/COSC1046")
    assert response.status_code == 200


def test_import_history():
    response = client.post("/api/v1/students/111/history/import", files={"file": ("t.html", SAMPLE_HTML, "text/html")})
    assert response.status_code == 201
    assert response.json()["past_courses_imported"] == 4


def test_get_profile():
    client.post("/api/v1/students/111/history/import", files={"file": ("t.html", SAMPLE_HTML, "text/html")})
    response = client.get("/api/v1/students/111/profile")
    assert response.status_code == 200
    data = response.json()
    assert data["student_id"] == "111"
    assert "history" in data
    assert "plan" in data


def test_post_plan():
    client.post("/api/v1/students/111/history/import", files={"file": ("t.html", SAMPLE_HTML, "text/html")})
    response = client.post("/api/v1/students/111/plan", json={"planned_courses": [{"course_code": "COSC-3506", "term": "26F"}]})
    assert response.status_code == 200
    assert response.json()["planned_courses_saved"] == 1


def test_student_not_found():
    response = client.get("/api/v1/students/999/profile")
    assert response.status_code == 404


def test_delete_history():
    client.post("/api/v1/students/111/history/import", files={"file": ("t.html", SAMPLE_HTML, "text/html")})
    response = client.delete("/api/v1/students/111/history")
    assert response.status_code == 200


def test_audit_report():
    client.post("/api/v1/admin/catalog/import", files={"file": ("catalog.html", CATALOG_HTML, "text/html")})
    client.post("/api/v1/students/111/history/import", files={"file": ("t.html", SAMPLE_HTML, "text/html")})
    response = client.get("/api/v1/students/111/audit-report")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "timeline_validation" in data
    assert "credit_summary" in data


def test_audit_strict():
    client.post("/api/v1/students/111/history/import", files={"file": ("t.html", SAMPLE_HTML, "text/html")})
    response = client.get("/api/v1/students/111/audit-report?strict=true")
    assert response.status_code == 200


def test_put_history():
    client.post("/api/v1/students/111/history/import", files={"file": ("t.html", SAMPLE_HTML, "text/html")})
    response = client.put("/api/v1/students/111/history", json={"history": [{"course_code": "COSC-1046", "term": "23F", "credits_earned": 3, "status": "Completed"}]})
    assert response.status_code == 200


def test_put_history_not_found():
    response = client.put("/api/v1/students/999/history", json={"history": []})
    assert response.status_code == 404


def test_delete_history_not_found():
    response = client.delete("/api/v1/students/999/history")
    assert response.status_code == 404


def test_put_plan():
    client.post("/api/v1/students/111/history/import", files={"file": ("t.html", SAMPLE_HTML, "text/html")})
    response = client.put("/api/v1/students/111/plan", json={"planned_courses": [{"course_code": "COSC-3506", "term": "26F"}]})
    assert response.status_code == 200


def test_put_plan_not_found():
    response = client.put("/api/v1/students/999/plan", json={"planned_courses": []})
    assert response.status_code == 404


def test_delete_plan():
    client.post("/api/v1/students/111/history/import", files={"file": ("t.html", SAMPLE_HTML, "text/html")})
    response = client.delete("/api/v1/students/111/plan")
    assert response.status_code == 200


def test_delete_plan_not_found():
    response = client.delete("/api/v1/students/999/plan")
    assert response.status_code == 404


def test_audit_not_found():
    response = client.get("/api/v1/students/999/audit-report")
    assert response.status_code == 404


def test_audit_with_missing_prereq():
    catalog.clear()
    students.clear()
    client.post("/api/v1/admin/catalog/import", files={"file": ("catalog.html", CATALOG_HTML, "text/html")})
    client.post("/api/v1/students/222/history/import", files={"file": ("t.html", MINIMAL_HTML, "text/html")})
    client.post("/api/v1/students/222/plan", json={"planned_courses": [{"course_code": "COSC-3506", "term": "26F"}]})
    response = client.get("/api/v1/students/222/audit-report?strict=true")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert len(data["timeline_validation"]) > 0
