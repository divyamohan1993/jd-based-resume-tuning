import os
import uuid
import time
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
import google.cloud.aiplatform as aiplatform
import vertexai
from vertexai.preview.language_models import ChatModel

# ── CONFIG ────────────────────────────────────────────────────────────────────
PROJECT_ID   = os.getenv("GCP_PROJECT") or "YOUR_PROJECT_ID"
REGION       = os.getenv("GCP_REGION", "us-central1")  # Chat-Bison lives in us-central1
UPLOAD_DIR   = os.path.join(os.getcwd(), "uploads")
ALLOWED_EXTS = {"pdf", "docx", "txt"}

os.makedirs(UPLOAD_DIR, exist_ok=True)

# Initialize Vertex AI SDKs
aiplatform.init(project=PROJECT_ID, location=REGION)
vertexai.init(project=PROJECT_ID, location=REGION)
# Load the chat-bison model
CHAT_MODEL = ChatModel.from_pretrained("chat-bison@001")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", str(uuid.uuid4()))

# ── UTILITIES ─────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTS


def cleanup_old_uploads():
    now = time.time()
    for fname in os.listdir(UPLOAD_DIR):
        path = os.path.join(UPLOAD_DIR, fname)
        if os.path.isfile(path) and now - os.path.getctime(path) > 2 * 3600:
            os.remove(path)


@app.before_request
def before_request():
    cleanup_old_uploads()


def extract_text(path):
    ext = path.rsplit('.', 1)[1].lower()
    text = ""
    if ext == 'pdf':
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or '') + '\n'
    elif ext == 'docx':
        doc = Document(path)
        for p in doc.paragraphs:
            text += p.text + '\n'
    else:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
    return text


def call_ai(prompt: str, temperature: float = 0.2, max_output_tokens: int = 1024) -> str:
    chat = CHAT_MODEL.start_chat(
        context="You are an expert resume optimizer. Preserve original wording and be concise."
    )
    response = chat.send_message(
        prompt,
        temperature=temperature,
        max_output_tokens=max_output_tokens
    )
    return response.text.strip()


def generate_pdf(text: str, path: str):
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter
    margin, y = 50, height - 50
    for line in text.splitlines():
        c.drawString(margin, y, line[:95])
        y -= 14
        if y < 50:
            c.showPage()
            y = height - 50
    c.save()

# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    has_resume = bool(session.get('resume_file'))
    return render_template('index.html', has_resume=has_resume)


@app.route('/upload_resume', methods=['POST'])
def upload_resume():
    file = request.files.get('resume')
    if not file or not allowed_file(file.filename):
        flash('Upload PDF, DOCX or TXT only.')
        return redirect(url_for('index'))

    filename = secure_filename(file.filename)
    rid = str(uuid.uuid4())
    ext = filename.rsplit('.', 1)[1]
    stored = f"{rid}.{ext}"
    file.save(os.path.join(UPLOAD_DIR, stored))

    session.clear()
    session['resume_id'] = rid
    session['resume_file'] = stored
    return redirect(url_for('enter_jd'))


@app.route('/delete_resume', methods=['POST'])
def delete_resume():
    stored = session.pop('resume_file', None)
    session.clear()
    if stored:
        path = os.path.join(UPLOAD_DIR, stored)
        if os.path.exists(path): os.remove(path)
    return redirect(url_for('index'))


@app.route('/enter_jd')
def enter_jd():
    if not session.get('resume_file'):
        return redirect(url_for('index'))
    return render_template('enter_jd.html')


@app.route('/submit_jd', methods=['POST'])
def submit_jd():
    jd = request.form.get('jd_text', '').strip()
    if not jd:
        flash('JD cannot be empty')
        return redirect(url_for('enter_jd'))

    # Extract keywords via chat model
    prompt_kw = (
        "Extract a comma-separated list of key skills and responsibilities "
        f"from this JD:\n\n{jd}\n\nList:"
    )
    kws = call_ai(prompt_kw, max_output_tokens=256)
    keywords = [k.strip() for k in kws.split(',') if k.strip()]
    session['keywords'] = keywords

    # Compare against resume text
    resume_path = os.path.join(UPLOAD_DIR, session['resume_file'])
    text = extract_text(resume_path).lower()
    matched = [k for k in keywords if k.lower() in text]
    missing = [k for k in keywords if k.lower() not in text]
    session['matched'] = matched
    session['missing'] = missing

    return render_template('ask_missing.html', matched=matched, missing=missing)


@app.route('/submit_missing', methods=['POST'])
def submit_missing():
    answers = {k: request.form.get(k, '').strip() for k in session.get('missing', [])}
    session['answers'] = answers

    # Rewrite resume
    resume_path = os.path.join(UPLOAD_DIR, session['resume_file'])
    original = extract_text(resume_path)
    prompt = (
        "Here is the original resume:\n\n" + original + "\n\nUser-provided details:\n"
    )
    for skill, desc in answers.items():
        prompt += f"- {skill}: {desc}\n"
    prompt += (
        "\nRewrite into a one-page ATS-friendly resume, minimal edits."
    )
    optimized = call_ai(prompt, max_output_tokens=1024, temperature=0.3)
    session['optimized_text'] = optimized

    out_file = f"optimized_{session['resume_id']}.pdf"
    out_path = os.path.join(UPLOAD_DIR, out_file)
    generate_pdf(optimized, out_path)
    session['optimized_file'] = out_file

    return redirect(url_for('download'))


@app.route('/download')
def download():
    fn = session.get('optimized_file')
    if not fn:
        return redirect(url_for('index'))
    return send_file(
        os.path.join(UPLOAD_DIR, fn),
        as_attachment=True,
        download_name='optimized_resume.pdf'
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
