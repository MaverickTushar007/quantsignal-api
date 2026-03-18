import json
import numpy as np
import os
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Paths
CORPUS_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/quant_corpus.json"))

class LiteRAG:
    def __init__(self):
        self.vectorizer = TfidfVectorizer(stop_words='english')
        self.chunks = []
        self.matrix = None
        self.load_corpus()

    def load_corpus(self):
        if not os.path.exists(CORPUS_PATH):
            return

        with open(CORPUS_PATH, 'r') as f:
            data = json.load(f)

        # Flatten into search chunks
        for paper in data["papers"]:
            for chunk in paper["content"]:
                self.chunks.append({
                    "title": paper["title"],
                    "year": paper["year"],
                    "text": chunk
                })

        # Fit TF-IDF matrix
        corpus_texts = [f"{c['title']} {c['text']}" for c in self.chunks]
        self.matrix = self.vectorizer.fit_transform(corpus_texts)

    def search(self, query: str, top_k: int = 2):
        if not self.matrix or not self.chunks:
            return []

        query_vec = self.vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, self.matrix).flatten()
        top_indices = np.argsort(similarities)[::-1][:top_k]

        results = []
        for idx in top_indices:
            if similarities[idx] > 0.1: # relevance threshold
                results.append(self.chunks[idx])
        
        return results

# Singleton
rag_engine = LiteRAG()

def search_research(query: str, top_k: int = 2):
    return rag_engine.search(query, top_k)
