sudo apt-get update
sudo apt-get install -y python3.9 python3.9-venv python3.9-dev

python3.9 -m venv ~/resume-env
source ~/resume-env/bin/activate

pip install --upgrade pip
pip install flask pdfplumber python-docx reportlab vertexai google-cloud-documentai


# From inside ~/jdr, grab the full path:
# export GOOGLE_APPLICATION_CREDENTIALS="$HOME/jdr/key.json"

cat << 'EOF' >> ~/.bashrc

# Google service account & project for Resume Optimizer
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/jdr/key.json"
export PROJECT_ID="dmjone"
EOF

# 2. Reload your ~/.bashrc right now
source ~/.bashrc

ls -l "$GOOGLE_APPLICATION_CREDENTIALS"
# you should see your key.json listed


# ← replace as needed, my project ID is "dmjone"
PROJECT_ID="dmjone"
SA_NAME="resume-sa"
KEY_PATH="resume-sa-key.json"

# 1. Create the SA
gcloud iam service-accounts create "$SA_NAME" \
  --project="$PROJECT_ID" \
  --display-name="Resume Optimizer Service Account"

# 2. Give it the Vertex AI User role
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# 3. Download its JSON key
gcloud iam service-accounts keys create "$KEY_PATH" \
  --iam-account="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Key saved to: $KEY_PATH"

pip install --upgrade google-cloud-aiplatform


