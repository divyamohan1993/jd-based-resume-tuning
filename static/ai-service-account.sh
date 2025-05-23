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
