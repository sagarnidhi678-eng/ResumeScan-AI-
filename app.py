from flask import Flask, render_template, request, redirect, url_for, jsonify, Response, session, flash
import os, re, csv, io, sqlite3
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

UPLOAD_FOLDER = "uploads"
DB_PATH = "resume_scanner.db"

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# =====================================================
# DATABASE
# =====================================================
def get_db():
    conn = sqlite3.connect(DB_PATH)
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

        except sqlite3.IntegrityError:

            flash("Email already exists")
            return redirect(url_for("signup"))

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

    return render_template(
        "dashboard.html",
        total_users=total_users,
        total_jds=total_jds
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

        session["results"] = make_json_safe(results)

        return render_template(
            "results.html",
            results=results
        )

    return render_template(
        "upload.html",
        saved_jds=saved_jds
    )

# =====================================================
# CANDIDATE
# =====================================================
@app.route("/candidate/<int:index>")
@login_required
def candidate(index):

    results = session.get("results", [])

    if index < 0 or index >= len(results):
        return redirect(url_for("upload"))

    return render_template(
        "candidate.html",
        c=results[index],
        index=index,
        total=len(results)
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
@app.route("/export_csv", methods=["POST"])
@login_required
def export_csv():

    results = session.get("results", [])

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Rank",
        "Name",
        "Score",
        "Email",
        "Phone",
        "Skills"
    ])

    for r in results:

        writer.writerow([
            r["rank"],
            r["name"],
            r["score"],
            r["email"],
            r["phone"],
            ", ".join(r["skills"])
        ])

    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            "attachment; filename=results.csv"
        }
    )

# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    app.run(debug=True)

