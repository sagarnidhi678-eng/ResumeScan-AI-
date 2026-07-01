from flask import Flask, render_template, request, redirect, url_for, jsonify, Response, session, flash
import os, re, csv, io, sqlite3, json
from functools import wraps

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import pdfplumber
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

# =====================================================
# LOAD MODEL
# =====================================================
model = None

def get_model():
    global model

    if model is None:
        model = SentenceTransformer('all-MiniLM-L6-v2')

    return model

# =====================================================ss
# APP CONFIG
# =====================================================
app = Flask(__name__)
app.secret_key = "resume_scanner_secret_2024"

#UPLOAD_FOLDER = "uploads"
#DB_PATH = "resume_scanner.db"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.path.join(BASE_DIR, "resume_scanner.db")

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# =====================================================
# DATABASE
# =====================================================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:

        # USERS TABLE
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT DEFAULT 'hr',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # JOB DESCRIPTIONS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_descriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                content TEXT,
                user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # SCANS TABLE
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                jd_title TEXT,
                jd_content TEXT,
                user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        """)

        # CANDIDATES TABLE
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER,
                rank INTEGER,
                name TEXT NOT NULL,
                score REAL,
                label TEXT,
                color TEXT,
                email TEXT,
                phone TEXT,
                linkedin TEXT,
                github TEXT,
                skills TEXT,
                education TEXT,
                experience TEXT,
                matched TEXT,
                missed TEXT,
                filename TEXT,
                status TEXT DEFAULT 'Screened',
                FOREIGN KEY (scan_id) REFERENCES scans (id) ON DELETE CASCADE
            )
        """)

        # Add column dynamically if table exists but doesn't have it
        try:
            conn.execute("ALTER TABLE candidates ADD COLUMN status TEXT DEFAULT 'Screened'")
        except sqlite3.OperationalError:
            pass

        conn.commit()

init_db()

# =====================================================
# LOGIN REQUIRED
# =====================================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):

        if "user_id" not in session:
            return redirect(url_for("login"))

        return f(*args, **kwargs)

    return decorated_function

# =====================================================
# JSON SAFE
# =====================================================
def make_json_safe(obj):

    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}

    elif isinstance(obj, list):
        return [make_json_safe(i) for i in obj]

    elif hasattr(obj, "item"):
        return obj.item()

    else:
        return obj

# =====================================================
# HELPERS
# =====================================================
def extract_text(pdf_path):

    text = ""

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()

                if t:
                    text += t + "\n"

    except:
        pass

    return text

def clean_text(text):

    text = text.lower()
    text = re.sub(r"[^a-zA-Z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text

def extract_name(text, filename):

    for line in text.split("\n")[:5]:

        line = line.strip()

        if 2 < len(line) < 40 and re.match(r"^[A-Za-z ]+$", line):
            return line.title()

    return filename.replace(".pdf", "").title()

def extract_email(text):

    m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)

    return m.group(0) if m else "Not Found"

def extract_phone(text):

    m = re.search(r'(\+?\d[\d\s\-\(\)]{8,}\d)', text)

    return m.group(0) if m else "Not Found"

def extract_linkedin(text):
    m = re.search(r'https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9\-_%]+', text, re.IGNORECASE)
    return m.group(0) if m else "Not Found"

def extract_github(text):
    m = re.search(r'https?://(?:www\.)?github\.com/[a-zA-Z0-9\-_%]+', text, re.IGNORECASE)
    return m.group(0) if m else "Not Found"

def extract_skills(text):

    skills_db = [
        "python","java","c++","javascript","html","css",
        "react","node","flask","django","sql","mysql",
        "mongodb","machine learning","deep learning",
        "pandas","numpy","excel","power bi","tableau",
        "git","github","docker","aws"
    ]

    txt = text.lower()

    return [s.title() for s in skills_db if s in txt]

def extract_education(text):

    keywords = [
        "b.tech","m.tech","bachelor","master",
        "mba","b.sc","m.sc","phd",
        "college","university"
    ]

    return list(set([
        l.strip()
        for l in text.split("\n")
        if any(k in l.lower() for k in keywords)
    ]))[:5]

def extract_experience(text):

    matches = re.findall(
        r'(\d+)\s+(years|year|months|month)',
        text.lower()
    )

    return [f"{m[0]} {m[1]}" for m in matches] or ["Fresher"]

def score_label(score):

    if score >= 80:
        return "Excellent", "green"

    elif score >= 60:
        return "Good", "blue"

    elif score >= 40:
        return "Average", "orange"

    return "Weak", "red"

# =====================================================
# HOME
# =====================================================
@app.route("/")
def home():
    return render_template("home.html")

# =====================================================
# SIGNUP
# =====================================================
@app.route("/signup", methods=["GET", "POST"])
def signup():

    if request.method == "POST":

        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")

        if not name or not email or not password:
            flash("Please fill all fields")
            return redirect(url_for("signup"))

        hashed_password = generate_password_hash(password)

        try:
            with get_db() as conn:

                conn.execute("""
                    INSERT INTO users(name, email, password)
                    VALUES (?, ?, ?)
                """, (name, email, hashed_password))

                conn.commit()

            flash("Account created successfully")
            return redirect(url_for("login"))
        
        except Exception as e:
            print("SIGNUP ERROR:", e)
            raise

        #except sqlite3.IntegrityError:

         #  flash("Email already exists")
          # return redirect(url_for("signup"))

    return render_template("signup.html")

# =====================================================
# LOGIN
# =====================================================
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email")
        password = request.form.get("password")

        with get_db() as conn:

            user = conn.execute("""
                SELECT * FROM users
                WHERE email=?
            """, (email,)).fetchone()

        if user and check_password_hash(user["password"], password):

            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]

            return redirect(url_for("dashboard"))

        flash("Invalid email or password")

    return render_template("login.html")

# =====================================================
# LOGOUT
# =====================================================
@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))

# =====================================================
# DASHBOARD
# =====================================================
@app.route("/dashboard")
@login_required
def dashboard():

    with get_db() as conn:

        total_users = conn.execute("""
            SELECT COUNT(*) FROM users
        """).fetchone()[0]

        total_jds = conn.execute("""
            SELECT COUNT(*) FROM job_descriptions
        """).fetchone()[0]

        # Query scans for the currently logged in user
        scans = conn.execute("""
            SELECT s.id, s.jd_title, s.created_at, 
                   (SELECT COUNT(*) FROM candidates WHERE scan_id = s.id) as candidate_count, 
                   (SELECT MAX(score) FROM candidates WHERE scan_id = s.id) as max_score
            FROM scans s
            WHERE s.user_id = ?
            ORDER BY s.created_at DESC
        """, (session["user_id"],)).fetchall()

    return render_template(
        "dashboard.html",
        total_users=total_users,
        total_jds=total_jds,
        scans=scans
    )

# =====================================================
# LOAD JD
# =====================================================
@app.route("/load_jd/<int:jd_id>")
@login_required
def load_jd(jd_id):

    with get_db() as conn:

        jd = conn.execute("""
            SELECT * FROM job_descriptions
            WHERE id=?
        """, (jd_id,)).fetchone()

    if jd:
        return jsonify({"content": jd["content"]})

    return jsonify({"content": ""})

# =====================================================
# UPLOAD
# =====================================================
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():

    with get_db() as conn:

        saved_jds = conn.execute("""
            SELECT * FROM job_descriptions
            ORDER BY id DESC
        """).fetchall()

    if request.method == "POST":

        jd_raw = request.form.get("jd", "")
        jd = clean_text(jd_raw)

        files = request.files.getlist("resumes")
        save_title = request.form.get("save_title", "").strip()

        if save_title:

            with get_db() as conn:

                conn.execute("""
                    INSERT INTO job_descriptions(title, content, user_id)
                    VALUES (?, ?, ?)
                """, (
                    save_title,
                    jd_raw,
                    session["user_id"]
                ))

                conn.commit()

        resume_texts = []
        raw_texts = []
        names = []
        filenames = []

        for file in files:

            if file.filename:

                filename = secure_filename(file.filename)

                path = os.path.join(
                    UPLOAD_FOLDER,
                    filename
                )

                file.save(path)

                raw = extract_text(path)
                cleaned = clean_text(raw)

                raw_texts.append(raw)
                resume_texts.append(cleaned)
                names.append(extract_name(raw, filename))
                filenames.append(filename)

        if not resume_texts:

            return render_template(
                "upload.html",
                error="No resumes uploaded",
                saved_jds=saved_jds
            )

        # =================================================
        # AI MATCHING
        # =================================================
        embeddings = get_model().encode([jd] + resume_texts)

        scores = cosine_similarity(
            [embeddings[0]],
            embeddings[1:]
        ).flatten()

        scores = [float(s) for s in scores]

        max_score = max(scores) if max(scores) > 0 else 1

        results = []

        for i in range(len(names)):

            percent = float(
                round((scores[i] / max_score) * 100, 2)
            )

            label, color = score_label(percent)

            jd_words = set(jd.split())
            resume_words = set(resume_texts[i].split())

            matched = list(jd_words & resume_words)
            missed = list(jd_words - resume_words)

            results.append({
                "rank": 0,
                "name": str(names[i]),
                "score": percent,
                "label": str(label),
                "color": str(color),
                "email": str(extract_email(raw_texts[i])),
                "phone": str(extract_phone(raw_texts[i])),
                "linkedin": str(extract_linkedin(raw_texts[i])),
                "github": str(extract_github(raw_texts[i])),
                "skills": list(extract_skills(raw_texts[i])),
                "education": list(extract_education(raw_texts[i])),
                "experience": list(extract_experience(raw_texts[i])),
                "matched": [str(x) for x in matched[:10]],
                "missed": [str(x) for x in missed[:10]],
                "filename": str(filenames[i])
            })

        results.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        for i, r in enumerate(results):
            r["rank"] = i + 1

        # Save to SQLite Database instead of session cookie to prevent overflow
        scan_title = save_title if save_title else "Scan: " + (jd_raw[:40].strip() + "..." if len(jd_raw) > 40 else jd_raw.strip())
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO scans(jd_title, jd_content, user_id)
                VALUES (?, ?, ?)
            """, (scan_title, jd_raw, session["user_id"]))
            scan_id = cursor.lastrowid

            for r in results:
                conn.execute("""
                    INSERT INTO candidates(
                        scan_id, rank, name, score, label, color, email, phone, linkedin, github,
                        skills, education, experience, matched, missed, filename
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    scan_id,
                    r["rank"],
                    r["name"],
                    r["score"],
                    r["label"],
                    r["color"],
                    r["email"],
                    r["phone"],
                    r["linkedin"],
                    r["github"],
                    json.dumps(r["skills"]),
                    json.dumps(r["education"]),
                    json.dumps(r["experience"]),
                    json.dumps(r["matched"]),
                    json.dumps(r["missed"]),
                    r["filename"]
                ))
            conn.commit()

        return redirect(url_for("view_results", scan_id=scan_id))

    return render_template(
        "upload.html",
        saved_jds=saved_jds
    )

# =====================================================
# RESULTS VIEW
# =====================================================
@app.route("/results/<int:scan_id>")
@login_required
def view_results(scan_id):
    with get_db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if not scan:
            flash("Scan results not found.")
            return redirect(url_for("dashboard"))

        db_results = conn.execute("""
            SELECT * FROM candidates 
            WHERE scan_id=? 
            ORDER BY rank ASC
        """, (scan_id,)).fetchall()

    results = []
    for r in db_results:
        r_dict = dict(r)
        r_dict["skills"] = json.loads(r_dict["skills"])
        r_dict["education"] = json.loads(r_dict["education"])
        r_dict["experience"] = json.loads(r_dict["experience"])
        r_dict["matched"] = json.loads(r_dict["matched"])
        r_dict["missed"] = json.loads(r_dict["missed"])
        results.append(r_dict)

    return render_template(
        "results.html",
        results=results,
        scan_id=scan_id,
        jd_title=scan["jd_title"]
    )

# =====================================================
# DELETE SCAN
# =====================================================
@app.route("/scan/delete/<int:scan_id>", methods=["POST"])
@login_required
def delete_scan(scan_id):
    with get_db() as conn:
        conn.execute("DELETE FROM scans WHERE id=?", (scan_id,))
        conn.commit()
    flash("Scan deleted successfully.")
    return redirect(url_for("dashboard"))

# =====================================================
# CANDIDATE
# =====================================================
@app.route("/candidate/<int:candidate_id>")
@login_required
def candidate(candidate_id):
    with get_db() as conn:
        c = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if not c:
            return redirect(url_for("dashboard"))

        # Get all candidates in the same scan, sorted by rank
        all_candidates = conn.execute(
            "SELECT id FROM candidates WHERE scan_id=? ORDER BY rank ASC",
            (c["scan_id"],)
        ).fetchall()

    candidate_ids = [r["id"] for r in all_candidates]
    index = candidate_ids.index(candidate_id)
    total = len(candidate_ids)

    prev_id = candidate_ids[index - 1] if index > 0 else None
    next_id = candidate_ids[index + 1] if index < total - 1 else None

    # Format candidate record as a dict and deserialize JSON fields
    c_dict = dict(c)
    c_dict["skills"] = json.loads(c_dict["skills"])
    c_dict["education"] = json.loads(c_dict["education"])
    c_dict["experience"] = json.loads(c_dict["experience"])
    c_dict["matched"] = json.loads(c_dict["matched"])
    c_dict["missed"] = json.loads(c_dict["missed"])

    return render_template(
        "candidate.html",
        c=c_dict,
        index=index,
        total=total,
        prev_id=prev_id,
        next_id=next_id,
        scan_id=c["scan_id"]
    )

# =====================================================
# VIEW RESUME
# =====================================================
@app.route("/resume/<filename>")
@login_required
def serve_resume(filename):

    path = os.path.join(
        UPLOAD_FOLDER,
        filename
    )

    if not os.path.exists(path):
        return "File not found", 404

    with open(path, "rb") as f:
        return Response(
            f.read(),
            mimetype="application/pdf"
        )

# =====================================================
# EXPORT CSV
# =====================================================
@app.route("/export_csv/<int:scan_id>", methods=["POST"])
@login_required
def export_csv(scan_id):
    with get_db() as conn:
        db_results = conn.execute("""
            SELECT * FROM candidates 
            WHERE scan_id=? 
            ORDER BY rank ASC
        """, (scan_id,)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Rank",
        "Name",
        "Score",
        "Email",
        "Phone",
        "LinkedIn",
        "GitHub",
        "Skills"
    ])

    for r in db_results:
        skills = json.loads(r["skills"])
        writer.writerow([
            r["rank"],
            r["name"],
            r["score"],
            r["email"],
            r["phone"],
            r["linkedin"],
            r["github"],
            ", ".join(skills)
        ])

    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            f"attachment; filename=results_scan_{scan_id}.csv"
        }
    )

# =====================================================
# CANDIDATE STATUS UPDATE
# =====================================================
@app.route("/candidate/<int:candidate_id>/status", methods=["POST"])
@login_required
def update_candidate_status(candidate_id):
    data = request.get_json() or {}
    new_status = data.get("status", "Screened")

    if new_status not in ["Screened", "Shortlisted", "Rejected"]:
        return jsonify({"success": False, "error": "Invalid status"}), 400

    with get_db() as conn:
        conn.execute("UPDATE candidates SET status=? WHERE id=?", (new_status, candidate_id))
        conn.commit()

    return jsonify({"success": True, "status": new_status})

# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    app.run(debug=True)

