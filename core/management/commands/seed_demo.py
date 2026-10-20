"""Données de démonstration : comptes, publications multimodales, abonnements, likes, commentaires.

    python manage.py seed_demo           # crée les données (idempotent)
    python manage.py seed_demo --reset   # supprime d'abord les comptes de démo

Les images sont générées localement (Pillow). Les audios sont de vrais enregistrements
vocaux en français (core/demo_assets) : si Groq (ou une autre source Whisper) est configuré,
ils sont transcrits pour de vrai par le pipeline ; sinon une transcription d'exemple est utilisée.
"""

import io
import random
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test.utils import override_settings

from accounts.models import Membre
from enrichment import pipeline
from enrichment.models import AnalyseIA, Tag
from publications.models import Audio, Image, Publication
from social.models import Abonnement, Commentaire, Reaction

DEMO_PASSWORD = "SensaDemo2026!"

USERS = [
    # username, nom affiché, e-mail, rôle, bio, ville, intérêts
    ("admin", "Avery Admin", "admin@sensa.local", Membre.Role.ADMIN, "J'administre Sensa.", "Tunis", "accessibilité, IA"),
    ("camille", "Camille Owens", "camille@sensa.local", Membre.Role.MODERATEUR, "Modératrice bienveillante.", "Sousse", "communauté, lecture"),
    ("lina", "Lina Brooks", "lina@sensa.local", Membre.Role.MEMBRE, "Designer qui collectionne les petits sons, la belle typo et les raisons de sortir.", "Portland", "design sonore, accessibilité, randonnée"),
    ("maya", "Maya Chen", "maya@sensa.local", Membre.Role.MEMBRE, "Musicienne, j'écris des chansons en plein air.", "Lyon", "musique, nature"),
    ("noah", "Noah Williams", "noah@sensa.local", Membre.Role.MEMBRE, "Field recording et marchés du matin.", "Marseille", "field recording, villes"),
    ("elena", "Elena Rossi", "elena@sensa.local", Membre.Role.MEMBRE, "Photographe · collectionneuse de sons.", "Bizerte", "photo, mer, voyages"),
    ("karim", "Karim Haddad", "karim@sensa.local", Membre.Role.MEMBRE, "Cuisine, football et podcasts.", "Sfax", "cuisine, sport"),
]

POSTS = [
    ("maya", "J'ai trouvé un coin tranquille hors de la ville et j'ai écrit quelque chose de nouveau. Écoutez les oiseaux qui se joignent à mi-chemin. #musique #nature", "meadow", "maya",
     [(0.0, "Bon, je crois que j'ai trouvé l'endroit. C'est plus calme qu'il n'y paraît."), (4.0, "Ce morceau a commencé avec trois notes, puis le vent a tourné."), (7.0, "Et voilà les oiseaux dont je vous parlais.")]),
    ("noah", "Une réflexion rapide sur les sons qui font qu'une ville ressemble à la maison. Enregistré ce matin au marché. #fieldrecording", None, "noah",
     [(0.0, "Il est sept heures, le marché se réveille."), (3.5, "On entend les cagettes, les voix, un vélo qui passe."), (6.0, "C'est ça, le son de chez moi.")]),
    ("elena", "Coucher de soleil sur le port de Bizerte, les bateaux rentrent un par un. #photo #mer", "sunset", None, None),
    ("lina", "Une matinée pluvieuse, en trois sons. Le premier est ma bouilloire 🫖", None, "lina",
     [(0.0, "Premier son : la pluie sur la verrière."), (2.5, "Deuxième : la bouilloire qui commence à chanter."), (4.5, "Troisième : le silence juste après.")]),
    ("karim", "Recette du jour : couscous au poisson comme à Sfax. Le secret, c'est le temps. #cuisine", "food", None, None),
    ("elena", "La forêt de Aïn Draham après la pluie, tout est vert et ça sent la mousse. #nature #randonnee", "forest", None, None),
    ("lina", "Petit rappel : ajoutez toujours un texte alternatif à vos images. Ici l'IA le fait pour vous, mais votre regard compte aussi ! #accessibilite", None, None, None),
    ("noah", "Vous êtes tous des idiots si vous pensez que le field recording c'est facile, bande de crétins.", None, None, None),
    ("karim", "Quel match hier soir ! Le stade chantait d'une seule voix. #football", "stadium", None, None),
]

COMMENTS = [
    (0, "noah", "L'harmonie naturelle vers la fin est parfaite. Merci de partager le processus !"),
    (0, "lina", "Magnifique, j'adore les oiseaux 😍"),
    (1, "maya", "Ça me ramène directement au marché de mon enfance."),
    (2, "karim", "Superbe photo, bravo !"),
    (4, "elena", "Je teste ce week-end, merci pour la recette."),
    (5, "noah", "Dommage que la photo soit un peu sombre, mais l'ambiance est là."),
]

PALETTES = {
    "meadow": ((255, 214, 140), (126, 168, 92), (250, 232, 180)),
    "sunset": ((255, 150, 90), (70, 50, 120), (255, 210, 120)),
    "food": ((240, 200, 140), (180, 90, 40), (255, 240, 210)),
    "forest": ((40, 90, 60), (15, 50, 30), (140, 190, 120)),
    "stadium": ((30, 40, 90), (20, 120, 60), (240, 240, 255)),
}


def make_image(kind):
    from PIL import Image as PILImage
    from PIL import ImageDraw

    top, bottom, accent = PALETTES[kind]
    width, height = 1200, 800
    img = PILImage.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / height
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    rng = random.Random(kind)
    draw.ellipse([820, 120, 1020, 320], fill=accent)
    for _ in range(14):
        x, y, r = rng.randint(0, width), rng.randint(420, height), rng.randint(30, 120)
        shade = tuple(max(0, min(255, c + rng.randint(-30, 30))) for c in bottom)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=shade)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=88)
    return ContentFile(buffer.getvalue(), name=f"demo-{kind}.jpg")


ASSETS = Path(__file__).resolve().parents[2] / "demo_assets"


def transcription_available():
    from django.conf import settings

    from core.ai import groq_provider

    return groq_provider.is_configured() or bool(settings.OPENAI_API_KEY)


def make_audio(name):
    """Enregistrement vocal de démonstration (WAV 16 kHz) et sa durée réelle."""
    import mutagen

    path = ASSETS / f"{name}.wav"
    duration = mutagen.File(path).info.length
    return ContentFile(path.read_bytes(), name=f"demo-{name}.wav"), round(duration, 2)


class Command(BaseCommand):
    help = "Crée des comptes et des publications de démonstration pour Sensa."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Supprime d'abord les comptes de démonstration.")

    def handle(self, *args, **options):
        # Exécution synchrone du pipeline pour afficher les statuts finaux.
        with override_settings(SENSA_ASYNC_DEV=False):
            self._seed(options)

    def _seed(self, options):
        usernames = [row[0] for row in USERS]
        if options["reset"]:
            deleted, _ = Membre.objects.filter(username__in=usernames).delete()
            Tag.objects.filter(publications=None).delete()
            self.stdout.write(self.style.WARNING(f"{deleted} objets de démonstration supprimés."))

        members = {}
        with transaction.atomic():
            for username, name, email, role, bio, city, interests in USERS:
                member, created = Membre.objects.get_or_create(
                    username=username, defaults={"email": email, "role": role, "email_confirmed": True}
                )
                if created:
                    member.set_password(DEMO_PASSWORD)
                    member.is_staff = role == Membre.Role.ADMIN
                    member.is_superuser = role == Membre.Role.ADMIN
                    member.save()
                    profile = member.profile
                    profile.display_name, profile.bio, profile.location, profile.interests = name, bio, city, interests
                    profile.save()
                members[username] = member

            follows = [("lina", "maya"), ("lina", "noah"), ("lina", "elena"), ("maya", "lina"), ("noah", "maya"),
                       ("elena", "lina"), ("karim", "elena"), ("maya", "elena"), ("camille", "lina")]
            for follower, followed in follows:
                Abonnement.objects.get_or_create(follower=members[follower], followed=members[followed])

        if Publication.objects.filter(author__username__in=usernames).exists():
            self.stdout.write("Publications de démonstration déjà présentes (utilisez --reset pour recommencer).")
            return

        posts = []
        real_transcription = transcription_available()
        for index, (author, text, image_kind, audio_name, segments) in enumerate(POSTS):
            with transaction.atomic():
                post = Publication.objects.create(author=members[author], text=text)
                if image_kind:
                    Image.objects.create(publication=post, file=make_image(image_kind))
                if audio_name:
                    content, duration = make_audio(audio_name)
                    audio = Audio.objects.create(publication=post, file=content, duration=duration)
                    if not real_transcription:
                        # Sans moteur Whisper configuré : transcription d'exemple.
                        audio.segments = [{"start": start, "text": line} for start, line in segments]
                        audio.transcription = " ".join(line for _, line in segments)
                        audio.language = "fr"
                        audio.save()
                        AnalyseIA.objects.create(
                            publication=post, type=AnalyseIA.Type.TRANSCRIPTION, target_id=str(audio.pk),
                            status=AnalyseIA.Status.DONE, attempts=1, provider="donnees-demo",
                            result={"language": "fr", "segments": len(segments)},
                        )
            types = None if real_transcription else [
                t for t in AnalyseIA.Type.values if t not in (AnalyseIA.Type.TRANSCRIPTION, *AnalyseIA.SIDE_TYPES)
            ]
            pipeline.launch(post, types=types)
            posts.append(post)
            self.stdout.write(f"  · publication de @{author} créée et enrichie ({Publication.objects.get(pk=post.pk).status})")

        likes = [("lina", 0), ("noah", 0), ("elena", 0), ("lina", 1), ("maya", 2), ("lina", 2), ("karim", 2),
                 ("maya", 5), ("lina", 5), ("noah", 4), ("elena", 3), ("maya", 6)]
        for username, index in likes:
            Reaction.objects.get_or_create(publication=posts[index], member=members[username])

        from social.tasks import analyse_tone

        for index, username, text in COMMENTS:
            comment = Commentaire.objects.create(publication=posts[index], author=members[username], text=text)
            analyse_tone.delay(comment.pk)

        statuses = ", ".join(
            f"{p.author.username}:{Publication.objects.get(pk=p.pk).status}" for p in posts
        )
        self.stdout.write(self.style.SUCCESS(f"Données de démonstration créées. Statuts : {statuses}"))
        self.stdout.write(f"Comptes : {', '.join(usernames)} — mot de passe : {DEMO_PASSWORD}")
