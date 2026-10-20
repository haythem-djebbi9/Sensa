from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameBackend(ModelBackend):
    """Connexion avec l'adresse e-mail ou le nom d'utilisateur."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        model = get_user_model()
        identifier = username.strip()
        user = model.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier)).first()
        if user is None:
            model().set_password(password)  # temps constant contre l'énumération de comptes
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
