"""
Document embedder — lightweight hash-based vectors.
No model download needed. Works on Railway without extra deps.
"""
import hashlib
import math

def embed_text(text: str) -> list[float]:
    """
    Convert text to a 384-dim vector using character n-gram hashing.
    Semantically similar texts produce similar vectors.
    No model download required.
    """
    text = text.lower().strip()
    vec = [0.0] * 384
    
    # Character n-grams (3,4,5) hashed into vector
    for n in [3, 4, 5]:
        for i in range(len(text) - n + 1):
            gram = text[i:i+n]
            h = int(hashlib.md5(gram.encode()).hexdigest(), 16)
            idx = h % 384
            vec[idx] += 1.0
    
    # Word unigrams
    words = text.split()
    for word in words:
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        idx = h % 384
        vec[idx] += 2.0
    
    # Word bigrams
    for i in range(len(words) - 1):
        bigram = words[i] + "_" + words[i+1]
        h = int(hashlib.md5(bigram.encode()).hexdigest(), 16)
        idx = h % 384
        vec[idx] += 1.5
    
    # L2 normalize
    magnitude = math.sqrt(sum(x*x for x in vec))
    if magnitude > 0:
        vec = [x / magnitude for x in vec]
    
    return vec


def chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks for better retrieval."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return [c for c in chunks if len(c) > 50]
