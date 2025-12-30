# Project Chimera Phase 3: The State Replay Manifesto

## The Paradigm Shift
We are pivoting from **"Solving Split-MNIST"** to **"Defining AI Memory"**.
Our experiments showed that for *data classification*, MLPs win.
But for *cognitive continuity*, current AI has no solution. RAG is storage; Context is buffer; Fine-tuning is destructive.

**Chimera is State Replay.**
It stores the *internal configuration* (`h`) required to maintain coherence, not the external data (`x`).

## Core Hypothesis
> "Current AI stores information. Chimera stores the conditions under which information remains stable."

## Research Roadmap
1.  **The "Cognitive State" Experiment**:
    -   Instead of classifying digits, can Chimera maintain a "Context Vector" over long conversations that an LSTM/Transformer loses?
    -   **Goal**: Demonstrate that re-injecting `h_prev` allows a model to "remember where it was" instantly, without re-reading the entire chat history.

2.  **State Space Mapping**:
    -   Analyze the topology of `h`. Do "Angry" states cluster? Do "Confused" states look different from "Certain" states?
    -   Use this to Trigger Replay only when coherence drops (The "Alpha Drift" mechanism restored).

3.  **Scaling to Transformers**:
    -   Apply Chimera principles to LLMs. Instead of KV-Cache (which grows linearly), can we compress the "Thought" into a fixed-size Reservoir State?

## Immediate Action
-   **Archive V1-V11**: Mark as "Foundational Negative Results on Classification".
-   **init**: Create `chimera_v12_cognitive.py` to test maintaining "Reasoning State" vs "Context Window".
