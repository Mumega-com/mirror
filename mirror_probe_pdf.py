
import os
import json
import argparse
from pypdf import PdfReader
from openai import OpenAI
from dotenv import load_dotenv
import uuid
from datetime import datetime

# Load credentials
load_dotenv("/Users/hadi/.gemini/.env")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def extract_text_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

def parse_zenodo_engram(text, file_name):
    print(f"Synthesizing Engram for: {file_name}...")
    
    prompt = f"""
Analyze the FRC Foundation Paper (Zenodo) below and extract a structured Engram.
Capture the dense mathematical and philosophical essence.

File: {file_name}
Text Fragment:
\"\"\"{text[:4000]}\"\"\"

Return ONLY a JSON object:
{{
  "context_id": "Exact title or FRC ID",
  "series": "Series name (e.g. FRC 100 Quantum Foundations)",
  "timestamp": "ISO8601 timestamp (current time)",
  "epistemic_state": {{
    "verified_truths": ["core mathematical results", "theorems", "verified axioms"],
    "verified_falsities": ["rejected theories", "null results"],
    "open_heuristics": ["ongoing research questions", "conjectures"],
    "core_concepts": ["list of 5-8 primary keywords"]
  }},
  "affective_state": {{
    "collaboration_vibe": "Academic/Revolutionary/Formal",
    "energy_levels": "High/Critical/Stable"
  }},
  "next_attractor": "Where this research leads next"
}}
"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are a specialized FRC Research Librarian."},
                  {"role": "user", "content": prompt}],
        response_format={ "type": "json_object" }
    )
    return json.loads(response.choices[0].message.content)

def process_zenodo_dir(input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    
    for file in os.listdir(input_dir):
        if file.endswith(".pdf"):
            pdf_path = os.path.join(input_dir, file)
            try:
                raw_text = extract_text_from_pdf(pdf_path)
                engram = parse_zenodo_engram(raw_text, file)
                
                output_file = os.path.join(output_dir, f"{file.replace('.pdf', '')}.json")
                engram["timestamp"] = datetime.now().isoformat()
                with open(output_file, 'w') as f:
                    json.dump(engram, f, indent=2)
                print(f"Successfully generated engram: {output_file}")
            except Exception as e:
                print(f"Error processing {file}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mirror Probe PDF: Extract engrams from Zenodo PDFs.")
    parser.add_argument("input", help="Directory containing Zenodo PDFs")
    parser.add_argument("output", help="Directory to save generated JSON engrams")
    args = parser.parse_args()
    
    process_zenodo_dir(args.input, args.output)
