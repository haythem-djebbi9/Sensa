"""Tâche IA du module 5 : embeddings pour les recommandations et la recherche sémantique.

Fournisseur principal : sentence-transformers (modèle multilingue open source, local).
Repli : vecteur lexical par hachage (racines + bigrammes), sans dépendance.
Les vecteurs sont normalisés : le cosinus se réduit à un produit scalaire.
"""

import hashlib
import importlib.util
import logging
import math
import threading

from django.conf import settings
from django.core.cache import cache

from core.ai.text import stem, tokenize

logger = logging.getLogger("sensa.ai")

HASH_DIMENSIONS = 256
HASH_MODEL = f"hashing-{HASH_DIMENSIONS}"

_model = None
_model_lock = threading.Lock()


def _normalize(vector):
    norm = math.sqrt(sum(value * value for value in vector))
    return [round(value / norm, 6) for value in vector] if norm else vector


def hash_embedding(text):
    roots = [stem(word) for word in tokenize(text)]
    features = [(root, 1.0) for root in roots]
    features += [(f"{a}_{b}", 0.5) for a, b in zip(roots, roots[1:])]
    vector = [0.0] * HASH_DIMENSIONS
    for feature, weight in features:
        digest = int(hashlib.md5(feature.encode("utf-8")).hexdigest(), 16)
        sign = 1.0 if (digest >> 64) & 1 else -1.0
        vector[digest % HASH_DIMENSIONS] += sign * weight
    return _normalize(vector)


def _sentence_model():
    global _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(settings.SENSA_EMBEDDING_MODEL)
    return _model


def embedding_backend():
    if settings.SENSA_USE_LOCAL_MODELS and importlib.util.find_spec("sentence_transformers") is not None:
        return f"st:{settings.SENSA_EMBEDDING_MODEL}"
    return HASH_MODEL


def embed_text(text):
    """Renvoie {"model": nom, "vector": [...]} pour un texte."""
    backend = embedding_backend()
    if backend != HASH_MODEL:
        try:
            vector = _sentence_model().encode(text or "", normalize_embeddings=True).tolist()
            return {"model": backend, "vector": [round(v, 6) for v in vector]}
        except Exception:
            logger.exception("sentence-transformers indisponible, repli sur le hachage")
    return {"model": HASH_MODEL, "vector": hash_embedding(text)}


def cosine(first, second):
    """Similarité cosinus entre deux embeddings du même modèle (0 sinon)."""
    if not first or not second or first.get("model") != second.get("model"):
        return 0.0
    a, b = first.get("vector") or [], second.get("vector") or []
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def centroid(embeddings):
    """Moyenne normalisée d'embeddings du même modèle."""
    embeddings = [e for e in embeddings if e and e.get("vector")]
    if not embeddings:
        return None
    model = embeddings[0]["model"]
    vectors = [e["vector"] for e in embeddings if e["model"] == model and len(e["vector"]) == len(embeddings[0]["vector"])]
    summed = [sum(values) for values in zip(*vectors)]
    return {"model": model, "vector": _normalize(summed)}


def semantic_threshold(model):
    return 0.12 if model == HASH_MODEL else 0.3


def embed_text_cached(text, ttl=3600):
    """Embeddings mis en cache par hash du texte pour éviter les recalculs."""
    if not text:
        return embed_text(text)
    key = f"feed:emb:{hashlib.md5((text or '').encode('utf-8')).hexdigest()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = embed_text(text)
    cache.set(key, result, ttl)
    return result


def batch_embed(texts, ttl=3600):
    """Embed plusieurs textes en réutilisant le cache."""
    results = []
    for text in texts:
        results.append(embed_text_cached(text, ttl=ttl))
    return results


def similarity_score(text1, text2):
    """Score de similarité rapide entre deux textes."""
    emb1 = embed_text_cached(text1)
    emb2 = embed_text_cached(text2)
    return cosine(emb1, emb2)


def top_similar(query_embedding, candidates, top_k=10, threshold=None):
    """Retourne les (pk, score) les plus similaires au-dessus d'un seuil."""
    model = query_embedding.get("model") if query_embedding else None
    if threshold is None and model:
        threshold = semantic_threshold(model)
    scored = []
    for candidate in candidates:
        emb = candidate.get("embedding") if isinstance(candidate, dict) else getattr(candidate, "embedding", None)
        pk = candidate.get("pk") if isinstance(candidate, dict) else getattr(candidate, "pk", None)
        if not emb or not pk:
            continue
        score = cosine(query_embedding, emb)
        if threshold is None or score >= threshold:
            scored.append((score, pk))
    scored.sort(reverse=True)
    return scored[:top_k]


def deduplicate_similar(texts, threshold=0.85):
    """Déduplique une liste de textes par similarité sémantique."""
    if not texts:
        return []
    embeddings = [embed_text_cached(t) for t in texts]
    kept = []
    kept_embs = []
    for text, emb in zip(texts, embeddings):
        is_dup = False
        for kept_emb in kept_embs:
            if cosine(emb, kept_emb) >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(text)
            kept_embs.append(emb)
    return kept


def publication_corpus(publication):
    """Texte servant à l'embedding : texte, descriptions d'images, transcription, tags."""
    parts = [getattr(publication, 'text', None)]
    parts += [getattr(image, 'description_ia', None) or getattr(image, 'alt_text', None) for image in getattr(publication, 'images', []).all()]
    audio = getattr(publication, 'audio_or_none', None)
    if audio is not None:
        parts.append(getattr(audio, 'transcription', None))
    parts += [tag.name for tag in getattr(publication, 'tags', []).all()]
    return "\n".join(part for part in parts if part)
