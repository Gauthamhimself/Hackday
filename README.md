ProofLens 🔎
Turn confusing mathematical proofs into visual explanations.

ProofLens is an AI-powered learning tool designed to help students
understand mathematical proofs one logical step at a time. Instead of
getting stuck when a professor skips a step, students can submit a proof
and explore its reasoning, assumptions, and logical structure in plain
English.

Built as a hackathon MVP with Python and Streamlit.
✨ Features
- Multiple input methods: Paste a proof, upload an image of
  handwritten work, or capture an image with your camera.
- PDF support: Upload a PDF and extract its text for analysis when
  the document contains selectable text. Scanned PDFs may require OCR
  or image-based processing.
- Step-by-step explanations: Break a proof into logical steps and
  explain the reasoning in accessible language.
- Assumptions and logical gaps: Surface assumptions, unsupported
  transitions, and potential errors when identified by the analysis.
- Visual proof tree: Represent relationships between statements in
  a proof as a tree.
- Problem history: Revisit analyses from the current app session
  without needing to run the analysis again.
Note: Available features depend on the current implementation and
model configuration. AI-generated explanations can be incorrect; use
ProofLens as a learning aid, not as a formal proof verifier.

🧠 How it works
1. Submit a proof by pasting text, uploading an image, using the
   camera, or uploading a supported PDF.
2. Extract the content. Text input is passed through directly;
   images and PDFs are processed through the application's supported
   parsing workflow.
3. Analyze the proof. The parser uses the configured model
   integration to produce structured proof information.
4. Explore the explanation. Review the logical steps, explanations,
   assumptions, and potential gaps returned by the analysis.
5. View the proof tree. The visualization module displays the
   relationships between proof steps.
6. Revisit past work. Use the session history to return to earlier
   analyses.
🛠️ Tech stack
- Python --- application logic
- Streamlit --- interactive web interface
- Gemma 4 resources / configured model integration --- proof
  analysis, depending on project configuration
- Graphviz --- proof-tree visualization, if used by the
  visualization implementation
- PDF extraction library --- text extraction from text-based PDFs,
  depending on installed dependencies
- SymPy --- optional symbolic checks for supported mathematical
  expressions
The exact model setup and available dependencies are defined by the
project's code and requirements.txt.
📁 Project structure
.
├── app.py             # Streamlit UI, input workflows, results, and history
├── parser.py          # Parses and analyzes submitted proofs
├── visuals.py         # Builds the visual proof tree
├── requirements.txt   # Python dependencies
└── README.md          # Project documentation
Additional files may be present depending on the model setup and
deployment configuration.
🚀 Getting started
1. Clone the repository
git clone <repository-url>
cd <repository-folder>
Replace the placeholders with your repository URL and folder name.
2. Create a virtual environment
macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
Windows PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1
3. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
If PDF extraction requires a library that is not listed in
requirements.txt, install and add the library selected by the project
maintainers.
4. Configure the model
Check the existing parser implementation and project instructions for
model configuration requirements. If an API key or other secret is
required, provide it through the expected environment variable or
Streamlit secrets configuration.

Do not commit API keys, credentials, or private configuration files to
version control.

If the configured model runs locally, follow the setup instructions for
that model instead.
5. Run the application
streamlit run app.py
Streamlit will provide a local URL in the terminal, usually
http://localhost:8501.
📖 Example workflow
Try a simple proof such as:
Claim: If n is an even integer, then n² is even.

Proof:
Assume n is even.
Then n = 2k for some integer k.
Therefore, n² = (2k)² = 4k² = 2(2k²).
Since 2k² is an integer, n² is even.
Submit the proof and use the analysis to follow the reasoning from the
assumption to the conclusion. The exact output depends on the parser and
configured model.
🧩 Module responsibilities
app.py
Owns the user-facing experience: - Proof text input and supported file
inputs - Upload and camera workflows - Loading states, validation, and
error messages - Displaying the parser's analysis - Session-based
problem history - Integration with the proof-tree visualization
parser.py
Provides the proof parsing and analysis interface. The intended
interface is:
parse_proof(image_bytes, text) -> dict
The returned dictionary should contain the structured information needed
by the UI and visualization. Check the current implementation for the
exact schema.
visuals.py
Provides the proof visualization interface:
draw_proof(proof: dict)
The function's actual rendering behavior depends on the implementation.
The UI should pass it the structured proof data returned by the parser.
⚠️ Limitations
- AI-generated mathematical explanations may contain mistakes or miss
  subtle logical gaps.
- Symbolic verification is only possible for expressions and cases
  explicitly supported by the implementation.
- Text extraction works best for PDFs with selectable text. Scanned
  documents and handwritten proofs may need OCR or image-based
  processing.
- Complex notation, diagrams, and multi-proof documents may require
  the user to review or edit extracted content.
- Session-based history may not survive a browser refresh, server
  restart, or a new session unless persistent storage is implemented.
- Camera input depends on browser permissions and the environment in
  which Streamlit is running.
- Model availability and performance depend on the configured runtime,
  resources, and credentials.
🔒 Privacy
Avoid uploading documents containing sensitive personal information. Do
not store uploaded files or model credentials in the repository. Review
the configured model provider's data-handling policies before submitting
private coursework or documents.
🧪 Development checklist
Before a demo or release, verify:
- [ ] The app starts with streamlit run app.py.
- [ ] Text input can be submitted and analyzed.
- [ ] Image upload and camera input work in the target environment.
- [ ] PDF text extraction works for supported PDFs.
- [ ] Scanned PDFs are handled with a clear message or a supported OCR
  workflow.
- [ ] Parser errors are shown clearly.
- [ ] Results display without assuming optional dictionary fields
  always exist.
- [ ] The proof tree renders when valid structured data is available.
- [ ] Previous analyses can be revisited without rerunning the model.
- [ ] Missing model configuration is reported clearly.
🎯 Project goal
ProofLens aims to make proof-based mathematics more approachable by
helping students see not just what a proof says, but why each step
follows.
Built as a hackathon project for AI + Education.
