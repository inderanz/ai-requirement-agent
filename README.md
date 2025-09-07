AI Requirement Agent

## Overview

This repository automates the extraction, processing, and management of requirements from PDF documents using Python scripts and GitHub Actions workflows. It is designed for teams working with financial standards, compliance, and document automation.

---

## Table of Contents
- [Workflows](#workflows)
- [Usage](#usage)
- [Prerequisites](#prerequisites)
- [GitHub Actions Used](#github-actions-used)
- [Scripts](#scripts)
- [Parameters & Variables](#parameters--variables)
- [Google-Specific Variables](#google-specific-variables)
- [How to Set Up](#how-to-set-up)

---

## Workflows

### 1. AI PDF Agent Dispatcher (`ai_pdf_agent_dispath.yml`)
**Purpose:**
- Listens for new issues, comments, or manual triggers.
- Checks if the issue/comment contains a PDF path or `@gemini` keyword.
- If so, triggers the main agent workflow (`requirement_agent.yml`).

**How it works:**
- **Trigger:** On issue creation/edit, comment creation/edit, or manual dispatch.
- **Gate Job:**
	- Parses the issue/comment body for `upload-pdf/*.pdf` or `@gemini`.
	- Sets `should_run` output to true if found.
- **Call-Agent Job:**
	- If `should_run` is true, calls the reusable workflow `requirement_agent.yml` with GCP and bucket parameters.
	- Passes Google Workload Identity Federation secrets for authentication.

**Usage:**
- Create or edit an issue/comment with a PDF path or `@gemini` to trigger extraction.

---

### 2. PDF Issue → Gemini → Markdown (`requirement_agent.yml`)
**Purpose:**
- Main workflow for extracting requirements from PDFs, running Gemini AI, and generating Markdown reports.
- Containerized and reusable.

**How it works:**
- **Trigger:** Called by dispatcher workflow or other workflows via `workflow_call`.
- **Inputs:**
	- `gcp_project_id`, `gcp_location`, `gcs_bucket` (required)
	- Prompt customization options (optional)
- **Secrets:**
	- `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT_EMAIL` (required for Google authentication)
- **Jobs:**
	- **prep:**
		- Checks out code and central scripts/prompts.
		- Determines issue metadata and feature branch.
		- Collects issue context and finds PDFs.
		- Prepares PDFs (fetch, OCR, chunk, extract images).
		- Authenticates to Google Cloud using Workload Identity Federation (no JSON key needed).
		- Uploads PDFs to GCS bucket.
		- Builds prompt and runs Gemini AI via Vertex AI SDK.
		- Uploads outputs and assets as workflow artifacts.
		- Comments on the issue with status and results.
	- **finalize:**
		- Downloads artifacts.
		- Writes/updates Markdown report.
		- Gathers images and validates Markdown.
		- Commits and pushes report to feature branch.
		- Comments with report link and PR starter.

**Usage:**
- Triggered automatically by dispatcher workflow when a PDF is referenced in an issue/comment.
- Can be called from other workflows using `workflow_call`.

---

## Prerequisites
- **Python 3.11+**
- **Docker** (for image publishing)
- **GitHub repository** with Actions enabled
- **Google Cloud project** with Workload Identity Federation configured
- **Required Python packages:**
	- Listed in `requirements.txt` (e.g., PyPDF2, requests, etc.)

---

## GitHub Actions Used
| Action | Vendor | Purpose |
|--------|--------|---------|
| `actions/checkout@v4` | GitHub | Checks out repository code |
| `google-github-actions/auth@v2` | Google | Authenticates to Google Cloud (WIF) |
| `google-github-actions/setup-gcloud@v2` | Google | Sets up gcloud CLI |
| `actions/upload-artifact@v4` | GitHub | Uploads workflow artifacts |

---


## Prompts

### How Prompts Are Used

- **Prompt Selection:**
  The workflow uses `prompts/routing.yaml` to select which prompt(s) to use for a given issue, based on keywords in the issue/comment or PDF signals (e.g., table presence, page count).
- **Persona & Task Prompts:**
  - `system/analyst_system.md`: Defines the system persona (senior AU payments analyst) and output style.
  - `tasks/*.md`: Each file describes a specific extraction or mapping task (e.g., NPP requirements, ISO 20022 mapping, risk/compliance, table extraction).
- **Regulatory Snippets:**
  - `standards/au_snippets.yaml`: Contains micro-RAG snippets for AU compliance, privacy, and AI ethics, which are appended to the prompt when relevant.

**Prompt Flow:**
1. The workflow runs `select_prompt.py` to choose the right prompt IDs using `routing.yaml` and issue context.
2. The selected persona and task prompts are loaded and combined by `build_prompt.py`.
3. If the task involves compliance, relevant snippets from `au_snippets.yaml` are appended.
4. The final prompt is sent to Gemini via Vertex AI.

---

### Prompt Files

- **routing.yaml:**
  Contains rules for mapping issue/comment keywords and PDF signals to prompt IDs.  
  Example: If the issue mentions "NPP" or "ISO 20022", it selects both `npp_requirements` and `iso20022_mapping`.

- **standards/au_snippets.yaml:**
  YAML list of compliance, privacy, and AI ethics requirements, used for micro-RAG enrichment.

- **system/analyst_system.md:**
  Markdown template for the system persona and output requirements.

- **tasks/*.md:**
  - `iso20022_mapping.md`: ISO 20022 message mapping and validation.
  - `npp_requirements.md`: AU NPP business and technical requirements.
  - `risk_compliance_au.md`: AU risk, security, and compliance mapping.
  - `table_extraction.md`: High-fidelity table extraction.

---

## Scripts

### How Scripts Work Together

1. **collect_issue_context.py**  
	- Collects issue metadata, body, comments, and detects PDF references (URLs or repo paths).
	- Outputs a context JSON for downstream steps.

2. **select_prompt.py**  
	- Uses `routing.yaml` and issue context to select prompt IDs.
	- Writes selection to `prompt_selection.json`.

3. **plan_sections.py**  
	- Plans required report sections based on issue text or defaults.
	- Outputs `required_sections.json`.

4. **fetch_and_prepare_pdf.py**  
	- Downloads or loads referenced PDFs.
	- Runs OCR if needed, extracts images, splits large PDFs, and prepares for upload.
	- Updates context JSON with final PDF paths, GCS URIs, and extraction policy.

5. **build_prompt.py**  
	- Loads persona and selected task prompts.
	- Appends regulatory snippets if relevant.
	- Builds the final prompt and system instruction for Gemini.
	- Outputs prompt text and settings JSON.

6. **run_gemini_sdk.py**  
	- Runs Gemini via Vertex AI SDK using the prompt and PDFs.
	- Handles output truncation, retries, and appends figures if images exist.
	- Writes Markdown output and raw response for debugging.

7. **embed_images_if_missing.py**  
	- Ensures all extracted images are embedded in the Markdown report.

8. **validate_and_fix_md.py**  
	- Validates the generated Markdown for required sections, image references, and table structure.

9. **util.py**  
	- Provides utility functions for file operations, HTTP downloads, and JSON helpers.

---

### Workflow Integration

- The workflow orchestrates these scripts in sequence:
  1. Collects issue context.
  2. Selects prompts and plans report sections.
  3. Prepares PDFs and images.
  4. Builds the prompt and runs Gemini.
  5. Embeds images and validates the final report.
  6. Uploads results and comments on the issue.

---

## Example Flow

1. **Issue Created:**  
	User creates an issue referencing a PDF or using keywords.
2. **Prompt Selection:**  
	`select_prompt.py` chooses the right extraction task(s).
3. **PDF Processing:**  
	`fetch_and_prepare_pdf.py` downloads, OCRs, and extracts images.
4. **Prompt Building:**  
	`build_prompt.py` assembles the system and user prompt.
5. **AI Extraction:**  
	`run_gemini_sdk.py` runs Gemini to generate the Markdown report.
6. **Image Embedding & Validation:**  
	`embed_images_if_missing.py` and `validate_and_fix_md.py` ensure report quality.
7. **Result:**  
	Workflow uploads the report, images, and comments with links and status.

---

This update provides a comprehensive view of how prompts and scripts work together in your workflow. If you need more details on any workflow, action, or script, let me know!

---

## Parameters & Variables
### Workflow Inputs
- `gcp_project_id`: Google Cloud project ID
- `gcp_location`: GCP region (e.g., australia-southeast1)
- `gcs_bucket`: GCS bucket for storing PDFs and outputs
- `force_prompt_ids`, `prompts_dir`, `routing_path`, `inline_task_prompt`: Prompt customization

### Secrets
- `GCP_WORKLOAD_IDENTITY_PROVIDER`: Workload Identity Federation provider string
- `GCP_SERVICE_ACCOUNT_EMAIL`: Service account email for WIF

### Script Arguments
- Most scripts accept file paths, output directories, and configuration options via command-line arguments or environment variables.

---

## Google-Specific Variables
- **Workload Identity Federation is used for authentication.**
- No need to set `GOOGLE_APPLICATION_CREDENTIALS` with a JSON key file.
- Instead, set the following secrets in your GitHub repository:
	- `GCP_WORKLOAD_IDENTITY_PROVIDER`
	- `GCP_SERVICE_ACCOUNT_EMAIL`

---

## How to Set Up
1. **Clone the repository:**
	 ```sh
	 git clone https://github.com/inderanz/ai-requirement-agent.git
	 cd ai-requirement-agent
	 ```
2. **Install Python dependencies:**
	 ```sh
	 pip install -r requirements.txt
	 ```
3. **Configure Google Cloud Workload Identity Federation:**
	 - Set up a Workload Identity Pool and Provider in GCP.
	 - Create a service account and grant necessary permissions.
	 - Add the provider string and service account email as GitHub secrets.
4. **Configure workflows:**
	 - Edit workflow YAML files to set your project, bucket, and other variables.
5. **Run workflows:**
	 - Create/edit issues or comments with PDF references to trigger extraction.
	 - Monitor workflow runs in the GitHub Actions tab.

---

## Example Workflow Usage
**Trigger PDF extraction:**
- Upload a PDF to `upload-pdf/`
- Create or edit an issue/comment referencing the PDF or with `@gemini`
- Workflow will extract requirements and update issues

---

## Troubleshooting
- Ensure all secrets are set in GitHub.
- Check workflow logs for errors.
- Validate Python environment and dependencies.

---

## Contributing
- Fork the repo and create a pull request.
- Add new scripts or workflows as needed.
- Update documentation for new features.

---

## License
MIT License

---

If you need more details on any workflow, action, or script, let me know!