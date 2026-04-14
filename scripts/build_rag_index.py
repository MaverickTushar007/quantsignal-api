"""
scripts/build_rag_index.py
Embeds the foundational quantitative research corpus using sentence-transformers
and saves them into a local FAISS index (ml/rag_index.faiss).
"""
import json
import os
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from pathlib import Path

# Setup paths
DATA_DIR = Path("data")
ML_DIR = Path("ml")
CORPUS_FILE = DATA_DIR / "quant_corpus.json"
INDEX_FILE = ML_DIR / "rag_index.faiss"
CHUNKS_FILE = ML_DIR / "rag_chunks.json"

def build_index():
    print("Loading embedding model (all-MiniLM-L6-v2)...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    print(f"Loading corpus from {CORPUS_FILE}...")
    with open(CORPUS_FILE, 'r') as f:
        corpus = json.load(f)

    # We will embed the actual text of each research paper chunk
    chunks = []
    texts_to_embed = []
    for item in corpus:
        text = f"[{item['authors']}] {item['title']}: {item['text']}"
        texts_to_embed.append(text)
        chunks.append({
            "id": item["id"],
            "title": item["title"],
            "authors": item["authors"],
            "text": item["text"],
            "full_context": text
        })

    print(f"Embedding {len(texts_to_embed)} chunks...")
    embeddings = model.encode(texts_to_embed, show_progress_bar=True)
    embeddings = np.array(embeddings).astype("float32")

    print("Building FAISS index...")
    # all-MiniLM-L6-v2 dimensions = 384
    dim = embeddings.shape[1]
    index = faiss.IndexFlatL2(dim)
    index.add(embeddings)

    ML_DIR.mkdir(exist_ok=True)

    print(f"Saving index to {INDEX_FILE}...")
    faiss.write_index(index, str(INDEX_FILE))

    print(f"Saving chunks mapping to {CHUNKS_FILE}...")
    with open(CHUNKS_FILE, 'w') as f:
        json.dump(chunks, f, indent=2)

    print("Done! The RAG index is ready for deployment.")

if __name__ == "__main__":
    build_index()
