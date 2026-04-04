
import os
import re
import json
import sys

def extract_metadata(content):
    metadata = {}
    match = re.search(r'---\s*(.*?)\s*---', content, re.DOTALL)
    if match:
        yaml_block = match.group(1)
        for line in yaml_block.split('\n'):
            if ':' in line:
                key, val = line.split(':', 1)
                metadata[key.strip()] = val.strip().strip('"')
    return metadata

def extract_concepts(content):
    concepts = []
    # Known FRC keywords
    keywords = ["Lambda-field", "Universal Vector", "Witness", "Coherence", "Rigidity", "Attractor", "Resonance"]
    for kw in keywords:
        if kw.lower() in content.lower():
            concepts.append(kw)
    return concepts

def generate_engram(file_path):
    with open(file_path, 'r') as f:
        content = f.read()
        
    meta = extract_metadata(content)
    concepts = extract_concepts(content)
    
    # Simple extraction for Epistemic State
    abstract_match = re.search(r'# Abstract\s*(.*?)\s*#', content, re.DOTALL)
    abstract = abstract_match.group(1).strip() if abstract_match else "No abstract found."
    
    engram = {
        "context_id": meta.get("title", os.path.basename(file_path)),
        "timestamp": meta.get("date", "Unknown"),
        "series": meta.get("series", "Uncategorized"),
        "epistemic_state": {
            "verified_truths": [abstract],
            "core_concepts": concepts
        },
        "affective_state": {
            "collaboration_vibe": "Formal Scientific Research",
            "energy_levels": "High"
        },
        "next_attractor": meta.get("link_next", "Goal-oriented completion.")
    }
    return engram

def probe_directory(directory, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                print(f"Probing {file_path}...")
                try:
                    engram = generate_engram(file_path)
                    output_file = os.path.join(output_dir, file.replace(".md", ".json"))
                    with open(output_file, 'w') as f:
                        json.dump(engram, f, indent=2)
                except Exception as e:
                    print(f"Failed to probe {file}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 mirror_probe.py <source_dir> <output_dir>")
        sys.exit(1)
        
    probe_directory(sys.argv[1], sys.argv[2])
