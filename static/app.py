import os
import uuid
import time
from datetime import datetime, timedelta

from flask import (
    Flask, request, session, redirect,
    url_for, render_template, send_file, flash
)
from werkzeug.utils import secure_filename

# PDF/Text parsing
import pdfplumber
from docx import Document

# PDF generation
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# Vertex AI
import vertexai
from vertexai.preview.language_models import TextGenerationModel

# ── CONFIG ────────────────────────────────────────────────────────────────────
PROJECT_ID = os.getenv("GCP_PROJECT") or "dmjone"
REGION     = os.getenv("GCP_REGION")  or "us-central1"
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
ALLOWED_EXTS = {"pdf", "docx", "txt"}

os.makedirs(UPLOAD_DIR, exist_ok=True)

# init Vertex AI
vertexai.init(project=PROJECT_ID, location=REGION)
MODEL = TextGenerationModel.from_pretrained("text-bison@001")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", str(uuid.uuid4()))


# ── UTILITIES ─────────────────────────────────────────────────────────────────

def allowed_file(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXTS

def cleanup_old_uploads():
    now = time.time()
    for fname in os.listdir(UPLOAD_DIR):
        path = os.path.join(UPLOAD_DIR, fname)
        if os.path.isfile(path):
            if now - os.path.getctime(path) > 2 * 3600:
                os.remove(path)

@app.before_request
def _before():
    cleanup_old_uploads()

def extract_text(path):
    ext = path.rsplit(".",1)[1].lower()
    txt = ""
    if ext == "pdf":
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                txt += page.extract_text() or "" + "\n"
    elif ext == "docx":
        doc = Document(path)
        for p in doc.paragraphs:
            txt += p.text + "\n"
    else:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
    return txt

def call_vertex(prompt: str, max_tokens=512, temp=0.2) -> str:
    res = MODEL.predict(prompt, temperature=temp, max_output_tokens=max_tokens)
    return res.text.strip()

def generate_pdf(text: str, out_path: str):
    c = canvas.Canvas(out_path, pagesize=letter)
    w, h = letter
    margin, y = 50, h - 50
    for line in text.splitlines():
        c.drawString(margin, y, line[:95])
        y -= 14
        if y < 50:
            c.showPage()
            y = h - 50
    c.save()


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    has_resume = bool(session.get("resume_file"))
    return render_template("index.html", has_resume=has_resume)

@app.route("/upload_resume", methods=["POST"])
def upload_resume():
    f = request.files.get("resume")
    if not f or not allowed_file(f.filename):
        flash("Please upload a PDF, DOCX or TXT.")
        return redirect(url_for("index"))

    filename = secure_filename(f.filename)
    rid = str(uuid.uuid4())
    ext = filename.rsplit(".",1)[1]
    stored = f"{rid}.{ext}"
    f.save(os.path.join(UPLOAD_DIR, stored))

    session.clear()
    session["resume_id"]   = rid
    session["resume_file"] = stored
    return redirect(url_for("enter_jd"))

@app.route("/delete_resume", methods=["POST"])
def delete_resume():
    stored = session.pop("resume_file", None)
    session.clear()
    if stored:
        path = os.path.join(UPLOAD_DIR, stored)
        if os.path.exists(path):
            os.remove(path)
    return redirect(url_for("index"))

@app.route("/enter_jd")
def enter_jd():
    if not session.get("resume_file"):
        return redirect(url_for("index"))
    return render_template("enter_jd.html")

@app.route("/submit_jd", methods=["POST"])
def submit_jd():
    jd = request.form.get("jd_text","").strip()
    if not jd:
        flash("JD cannot be empty")
        return redirect(url_for("enter_jd"))

    session["jd_text"] = jd
    # 1) extract keywords from JD
    prompt_kw = (
        "Extract a comma-separated list of key skills and responsibilities "
        "from this job description:\n\n" + jd + "\n\nList:"
    )
    kws = call_vertex(prompt_kw, max_tokens=256)
    keywords = [k.strip() for k in kws.split(",") if k.strip()]
    session["keywords"] = keywords

    # 2) parse resume text and find missing
    resume_path = os.path.join(UPLOAD_DIR, session["resume_file"])
    resume_text = extract_text(resume_path).lower()
    matched = [k for k in keywords if k.lower() in resume_text]
    missing = [k for k in keywords if k.lower() not in resume_text]
    session["matched"] = matched
    session["missing"] = missing

    return render_template(
      "ask_missing.html",
      matched=matched,
      missing=missing
    )

@app.route("/submit_missing", methods=["POST"])
def submit_missing():
    answers = {}
    for k in session.get("missing", []):
        answers[k] = request.form.get(k, "").strip()
    session["answers"] = answers

    # 3) rewrite resume via Vertex
    resume_path = os.path.join(UPLOAD_DIR, session["resume_file"])
    original = extract_text(resume_path)
    prompt = (
      "You are an expert resume optimizer. "
      "Here is the original resume text:\n\n" + original +
      "\n\nAdditional user details:\n"
    )
    for skill, desc in answers.items():
        prompt += f"- {skill}: {desc}\n"
    prompt += (
      "\nRewrite the resume into a one-page ATS-friendly PDF text, "
      "making minimal changes to original wording. "
      "Return the full resume content."
    )

    optimized = call_vertex(prompt, max_tokens=1024, temp=0.3)
    session["optimized_text"] = optimized

    # 4) generate PDF
    out_pdf = f"optimized_{session['resume_id']}.pdf"
    out_path = os.path.join(UPLOAD_DIR, out_pdf)
    generate_pdf(optimized, out_path)
    session["optimized_file"] = out_pdf

    return redirect(url_for("download"))

@app.route("/download")
def download():
    fn = session.get("optimized_file")
    if not fn:
        return redirect(url_for("index"))
    return send_file(
      os.path.join(UPLOAD_DIR, fn),
      as_attachment=True,
      download_name="optimized_resume.pdf"
    )


if __name__ == "__main__":
    # for local testing
    app.run(host="0.0.0.0", port=8080, debug=True)
