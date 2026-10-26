"""
Configuration Django du projet Sensa.

Tout est piloté par des variables d'environnement (fichier `.env` à la racine).
Sans configuration, le projet tourne en local avec SQLite, le stockage disque,
Celery en mode « eager » et une couche Channels en mémoire.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    """Charge un fichier .env minimal (CLE=valeur) sans dépendance externe."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    value = os.environ.get(name)
    return default if value in (None, "") else value


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "oui"}


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me-sensa-2026")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ["*"]
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    # Daphne remplace `runserver` par un serveur ASGI (HTTP + WebSocket).
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "channels",
    # Socle commun
    "core",
    "accounts",
    # Modules de l'équipe
    "publications",  # 1. Hiba Chaabani
    "social",  # 2. Med Nour Romdhani
    "enrichment",  # 3. Haythem Djebbi
    "moderation",  # 4. Chedly Rebai
    "feed",  # 5. Taieb Bel Hadj Ali
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "sensa.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.sensa",
            ],
        },
    },
]

WSGI_APPLICATION = "sensa.wsgi.application"
ASGI_APPLICATION = "sensa.asgi.application"

# --- Base de données -------------------------------------------------------
# PostgreSQL 16 en production (recherche plein texte), SQLite par défaut en dev.
if env("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER", "sensa"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "localhost"),
            "PORT": env("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentification (module commun) -------------------------------------
AUTH_USER_MODEL = "accounts.Membre"
AUTHENTICATION_BACKENDS = ["accounts.backends.EmailOrUsernameBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "feed:home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalisation ---------------------------------------------------
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = env("TIME_ZONE", "Africa/Tunis")
USE_I18N = True
USE_TZ = True

# --- Fichiers statiques et médias ------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Stockage S3 / MinIO des images et audios (module 1) : USE_MINIO=1
if env_bool("USE_MINIO"):
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("MINIO_BUCKET", "sensa-media"),
            "endpoint_url": env("MINIO_ENDPOINT", "http://localhost:9000"),
            "access_key": env("MINIO_ACCESS_KEY", "minioadmin"),
            "secret_key": env("MINIO_SECRET_KEY", "minioadmin"),
            "addressing_style": "path",
            "file_overwrite": False,
            "querystring_auth": True,
        },
    }

# --- E-mails ------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "25"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Sensa <no-reply@sensa.local>")

# --- Celery + Redis (module 3, partagé par tous) -----------------------------
REDIS_URL = env("REDIS_URL")
CELERY_BROKER_URL = REDIS_URL or "memory://localhost/"
CELERY_RESULT_BACKEND = None
# Sans Redis, les tâches s'exécutent immédiatement dans le processus (mode dev).
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", not REDIS_URL)
CELERY_TASK_EAGER_PROPAGATES = False
# En mode local, les tâches « eager » tournent dans un thread : la publication ne bloque jamais.
SENSA_ASYNC_DEV = env_bool("SENSA_ASYNC_DEV", True)
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = TIME_ZONE

# Files Celery : analyses lourdes (audio, image) séparées des analyses de texte rapides.
CELERY_TASK_DEFAULT_QUEUE = "ia_rapide"
CELERY_TASK_ROUTES = {
    "enrichment.transcribe_audio": {"queue": "ia_lourde"},
    "enrichment.translate_transcription": {"queue": "ia_lourde"},
    "publications.describe_image": {"queue": "ia_lourde"},
}
# Celery Beat : le « chien de garde » relance les analyses bloquées toutes les 5 minutes.
CELERY_BEAT_SCHEDULE = {
    "sensa-pipeline-watchdog": {"task": "enrichment.pipeline_watchdog", "schedule": 300.0},
}

# --- Django Channels (notifications temps réel, module 2) --------------------
if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        }
    }
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# --- Règles métier Sensa -----------------------------------------------------
SENSA_MAX_TEXT_LENGTH = 2000
SENSA_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 Mo
SENSA_MAX_IMAGES = 10  # images par publication (carrousel)
SENSA_ALT_SUGGESTIONS_PER_HOUR = int(env("SENSA_ALT_SUGGESTIONS_PER_HOUR", "30"))  # appels à l'IA de vision
SENSA_MAX_IMAGE_PIXELS = 50_000_000  # 50 mégapixels : une image décompressée occupe ~3 octets par pixel
SENSA_MAX_AUDIO_SECONDS = 180  # 3 minutes
SENSA_POSTS_PER_HOUR = int(env("SENSA_POSTS_PER_HOUR", "10"))
SENSA_RETRY_BACKOFF = [10, 60, 300]  # relances : 10 s, 1 min, 5 min
SENSA_FEED_PAGE_SIZE = 20
SENSA_REPORTS_BEFORE_REVIEW = 3  # signalements avant mise en revue automatique

# --- Services d'IA -----------------------------------------------------------
# Ordre de préférence des fournisseurs (les non configurés sont ignorés).
SENSA_AI_PROVIDERS = env_list("SENSA_AI_PROVIDERS", "groq,claude")
# Groq : Whisper (transcription), gpt-oss (texte), Qwen (vision) — clé GROQ_API_KEY.
GROQ_API_KEY = env("GROQ_API_KEY")
SENSA_GROQ_TEXT_MODEL = env("SENSA_GROQ_TEXT_MODEL", "openai/gpt-oss-120b")
SENSA_GROQ_VISION_MODEL = env("SENSA_GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
SENSA_GROQ_WHISPER_MODEL = env("SENSA_GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
# Claude (vision, ton, tags, toxicité) — clé ANTHROPIC_API_KEY.
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
SENSA_CLAUDE_MODEL = env("SENSA_CLAUDE_MODEL", "claude-opus-5-5")
# Whisper (transcription audio) — API OpenAI, puis Whisper local en repli.
OPENAI_API_KEY = env("OPENAI_API_KEY")
SENSA_WHISPER_API_MODEL = env("SENSA_WHISPER_API_MODEL", "whisper-1")
SENSA_WHISPER_LOCAL_MODEL = env("SENSA_WHISPER_LOCAL_MODEL", "base")
# Modèles open source locaux optionnels (voir requirements-ai-local.txt).
SENSA_USE_LOCAL_MODELS = env_bool("SENSA_USE_LOCAL_MODELS", True)
SENSA_EMBEDDING_MODEL = env("SENSA_EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
SENSA_CAPTION_MODEL = env("SENSA_CAPTION_MODEL", "Salesforce/blip-image-captioning-base")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "[{levelname}] {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "sensa": {"handlers": ["console"], "level": env("SENSA_LOG_LEVEL", "INFO"), "propagate": False},
    },
}

# --- Tests : jamais d'appel réseau aux API d'IA, médias dans un dossier temporaire ---
import sys  # noqa: E402
import tempfile  # noqa: E402

TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
if TESTING:
    ANTHROPIC_API_KEY = None
    OPENAI_API_KEY = None
    GROQ_API_KEY = None
    SENSA_USE_LOCAL_MODELS = False
    CELERY_TASK_ALWAYS_EAGER = True
    SENSA_ASYNC_DEV = False
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="sensa-tests-"))
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    LOGGING["loggers"]["sensa"]["level"] = "ERROR"
    LOGGING["loggers"]["django.request"] = {"handlers": ["console"], "level": "CRITICAL", "propagate": False}
