"""Traitement de texte léger (FR/EN) pour les replis locaux des tâches IA.

Ces fonctions ne dépendent d'aucun modèle : elles servent quand aucun
fournisseur d'IA n'est configuré, pour que le pipeline fonctionne toujours.
"""

import re
import unicodedata
from collections import Counter

STOPWORDS = set(
    """
    a ai aie aient aies ait alors as au aucun aujourd aupres aussi autre autres aux avais avait avant avec avez
    avoir avons ayant bah beaucoup bien bon c ca car ce ceci cela celle celles celui cependant ces cet cette ceux
    chaque chez ci comme comment d dans de des deja depuis donc dont du elle elles en encore entre es est et etaient
    etait etant ete etes etre eu eux fait faire fais faut fois font hors ici il ils j je jusqu l la le les leur leurs
    lui m ma mais me meme memes mes moi mon n ne ni non nos notre nous on ont ou par parce pas peu peut plus pour
    pourquoi puis qu quand que quel quelle quelles quels qui quoi s sa sans se sera ses si sien son sont sous suis
    sur t ta tant te tes toi ton tous tout toute toutes tres tu un une unes uns va vers voici voila vos votre vous
    vu y ya the and for that this with you your are was were have has had not but all any can will just from they
    them their there what when where which who why how into out our ours about over more most some such than then
    too very also its it's i me my we he she his her him of to in on at by be is as an or if so do does did no yes
    """.split()
)

# Mots trop génériques pour faire de bons tags (verbes, adverbes, descriptions de couleurs).
GENERIC_WORDS = set(
    """
    toujours jamais souvent encore aussi deja premier premiere deuxieme troisieme dernier derniere nouveau nouvelle
    petit petite grand grande trouve trouver pense pensez penser ajoutez ajouter faire fait faites dire dit voir voila
    venir vient aller allez prendre mettre donner donne passer passe rester reste sembler semble ressemble devenir
    savoir pouvoir vouloir enregistre enregistrer ecoutez ecouter rentrent rentrer chantait chanter quelque chose choses
    gens matin soir hier demain aujourd jour jours fois rien vraiment tellement juste mieux apres avant pendant depuis
    selon facile difficile rapide image images photo format paysage portrait carre dominants dominant tons lumineuse
    sombre contrastes equilibres plutot vers chez entre cette comme ainsi alors merci bonjour bonsoir
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9]+(?:['’][a-z0-9]+)?")
_HASHTAG_RE = re.compile(r"(?<![\w&])#([\w\-]{2,50})", re.UNICODE)


def strip_accents(text):
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize(text):
    return strip_accents(text).lower()


def tokenize(text, *, keep_stopwords=False):
    words = []
    for word in _WORD_RE.findall(normalize(text).replace("’", "'")):
        if "'" in word:
            # « l'oiseau » → « oiseau »
            word = word.split("'", 1)[1]
        if len(word) < 2:
            continue
        if not keep_stopwords and word in STOPWORDS:
            continue
        words.append(word)
    return words


def stem(word):
    """Racinisation très simple FR/EN (pluriels et suffixes courants)."""
    for suffix in ("issements", "issement", "ements", "ement", "ations", "ation", "ments", "ment", "euses", "euse",
                   "ings", "ing", "ies", "ers", "er", "es", "s", "x"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def extract_hashtags(text):
    """Hashtags saisis par l'auteur, normalisés (minuscules, sans accents)."""
    seen = []
    for raw in _HASHTAG_RE.findall(text or ""):
        tag = slug_tag(raw)
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def slug_tag(value):
    value = normalize(value).strip().lstrip("#")
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value[:40]


def keywords(text, limit=5):
    """Mots-clés les plus représentatifs (fréquence × longueur)."""
    counts = Counter(
        word for word in tokenize(text) if len(word) >= 4 and not word.isdigit() and word not in GENERIC_WORDS
    )
    if not counts:
        return []
    scored = sorted(counts.items(), key=lambda item: (item[1] * (1 + min(len(item[0]), 12) / 12), len(item[0])), reverse=True)
    result = []
    stems = set()
    for word, _ in scored:
        root = stem(word)
        if root in stems:
            continue
        stems.add(root)
        result.append(word)
        if len(result) >= limit:
            break
    return result


# --- Lexiques pour le ton et la toxicité --------------------------------------

POSITIVE_WORDS = set(
    """
    merci bravo super genial geniale magnifique magnifiques adore aime aimer j'adore top excellent excellente incroyable
    beau belle beaux belles joli jolie cool chouette parfait parfaite superbe sublime touchant emouvant felicitations
    respect inspirant inspirante agreable plaisir heureux heureuse content contente love loved lovely great beautiful amazing
    awesome nice thanks thank wonderful perfect brilliant fantastic enjoy enjoyed happy sweet kind incredible gorgeous
    """.split()
)
NEGATIVE_WORDS = set(
    """
    triste dommage mauvais mauvaise decu decue decevant bof ennuyeux ennuyeuse moche horrible pire nul nulle faux fausse
    inutile ridicule pas terrible deteste fatigue fatigant penible sad bad boring awful ugly worst terrible disappointing
    hate wrong useless annoying poor sucks
    """.split()
)

# Poids de toxicité (0–1) par terme, inspiré des listes de modération FR/EN.
TOXIC_TERMS = {
    # menaces et appels à la violence
    "tuer": 0.45, "creve": 0.85, "crever": 0.75, "kill": 0.45, "die": 0.5, "suicide": 0.6, "frapper": 0.55,
    # insultes fortes
    "connard": 0.85, "connasse": 0.85, "salope": 0.9, "pute": 0.9, "encule": 0.92, "enculer": 0.92, "nique": 0.88,
    "niquer": 0.88, "batard": 0.85, "fdp": 0.9, "pd": 0.85, "bitch": 0.85, "asshole": 0.85, "bastard": 0.8,
    "fuck": 0.7, "fucking": 0.7, "motherfucker": 0.92, "cunt": 0.92, "whore": 0.9, "slut": 0.9, "retard": 0.8,
    # insultes modérées
    "con": 0.55, "conne": 0.6, "idiot": 0.55, "idiote": 0.55, "imbecile": 0.6, "debile": 0.6, "cretin": 0.6,
    "abruti": 0.6, "abrutie": 0.6, "stupide": 0.5, "moron": 0.6, "stupid": 0.5, "dumb": 0.5, "loser": 0.45,
    "minable": 0.5, "pathetique": 0.4, "degage": 0.5, "tg": 0.7,
    # vulgarités légères
    "merde": 0.3, "putain": 0.35, "shit": 0.3, "damn": 0.15, "crap": 0.25,
}
TOXIC_PHRASES = {
    "ta gueule": 0.8, "ferme la": 0.6, "ferme-la": 0.6, "je vais te tuer": 0.97, "va mourir": 0.9, "va te faire": 0.85,
    "fils de pute": 0.95, "shut up": 0.55, "kill yourself": 0.98, "i will kill you": 0.97, "go die": 0.9,
    "sale race": 0.95, "rentre chez toi": 0.6,
}


def lexicon_toxicity(*texts):
    """Score de toxicité 0–1 et termes détectés, par combinaison probabiliste des poids."""
    joined = " ".join(t for t in texts if t)
    norm = normalize(joined)
    hits = {}
    for phrase, weight in TOXIC_PHRASES.items():
        if normalize(phrase) in norm:
            hits[phrase] = weight
    for word in tokenize(joined, keep_stopwords=True):
        weight = TOXIC_TERMS.get(word) or (TOXIC_TERMS.get(word[:-1]) if word[-1:] in ("s", "x") else None)
        if weight:
            hits[word] = max(weight, hits.get(word, 0))
    remaining = 1.0
    for weight in hits.values():
        remaining *= 1 - weight
    score = 1 - remaining
    # Les CRIS EN MAJUSCULES accentuent légèrement l'agressivité.
    letters = [ch for ch in joined if ch.isalpha()]
    if hits and len(letters) > 12 and sum(ch.isupper() for ch in letters) / len(letters) > 0.7:
        score = min(1.0, score + 0.1)
    return round(min(score, 1.0), 3), sorted(hits)


def lexicon_sentiment(text):
    """Polarité simple : (positifs, négatifs) comptés sur le texte."""
    words = tokenize(text, keep_stopwords=True)
    positive = sum(1 for w in words if w in POSITIVE_WORDS)
    negative = sum(1 for w in words if w in NEGATIVE_WORDS)
    positive += sum((text or "").count(e) for e in ("❤", "😍", "👏", "🥰", "😊", "🔥", "✨", "🙏"))
    negative += sum((text or "").count(e) for e in ("😡", "🤬", "👎", "😢", "💩"))
    return positive, negative


_FRENCH_HINTS = set("le la les des une est et que pour pas avec dans sur qui ce cette nous vous je tu il elle mais du au".split())
_ENGLISH_HINTS = set("the and is are to of in that it for with on this you was be have not but my we".split())


def guess_language(text):
    """Langue probable (fr, en, ar) d'après l'alphabet et les mots outils ; '' si inconnu."""
    if not text:
        return ""
    if sum(1 for ch in text if "\u0600" <= ch <= "\u06ff") > len(text) * 0.2:
        return "ar"
    words = [w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ']+", text)]
    french = sum(1 for w in words if w in _FRENCH_HINTS)
    english = sum(1 for w in words if w in _ENGLISH_HINTS)
    if not french and not english:
        return ""
    return "fr" if french >= english else "en"
