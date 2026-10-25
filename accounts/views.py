from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils.encoding import force_bytes, force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST

from .forms import AccountForm, LoginForm, ProfileForm, SignupForm
from .models import Membre
from .tokens import email_confirmation_token


def safe_next(request, fallback):
    """URL de retour sûre (même hôte) passée dans `next`."""
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return fallback


def send_confirmation_email(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_confirmation_token.make_token(user)
    url = request.build_absolute_uri(reverse("accounts:confirm_email", args=[uid, token]))
    body = render_to_string("accounts/emails/confirm_email.txt", {"user": user, "url": url})
    send_mail("Confirmez votre adresse e-mail Sensa", body, None, [user.email])


def signup(request):
    if request.user.is_authenticated:
        return redirect("feed:home")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
        login(request, user, backend="accounts.backends.EmailOrUsernameBackend")
        send_confirmation_email(request, user)
        messages.success(request, "Bienvenue sur Sensa ! Un e-mail de confirmation vient de vous être envoyé.")
        return redirect("feed:home")
    return render(request, "accounts/signup.html", {"form": form})


class SensaLoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


def confirm_email(request, uidb64, token):
    try:
        user = Membre.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (Membre.DoesNotExist, ValueError, TypeError, OverflowError):
        user = None
    if user is not None and email_confirmation_token.check_token(user, token):
        user.email_confirmed = True
        user.save(update_fields=["email_confirmed"])
        messages.success(request, "Votre adresse e-mail est confirmée. Merci !")
    else:
        messages.error(request, "Ce lien de confirmation est invalide ou a expiré.")
    return redirect("feed:home" if request.user.is_authenticated else "accounts:login")


@login_required
@require_POST
def resend_confirmation(request):
    if request.user.email_confirmed:
        messages.info(request, "Votre adresse e-mail est déjà confirmée.")
    else:
        send_confirmation_email(request, request.user)
        messages.success(request, f"Un nouvel e-mail de confirmation a été envoyé à {request.user.email}.")
    return redirect(safe_next(request, reverse("feed:home")))


@login_required
def profile(request, username):
    from django.db.models import OuterRef, Subquery

    from publications.models import Enregistrement, Publication, Republication
    from social.models import Abonnement
    from social.services import annotate_social

    member = get_object_or_404(Membre.objects.select_related("profile"), username__iexact=username)
    visible = Publication.objects.visible_to(request.user)
    own_posts = visible.filter(author=member)
    member_reposts = Republication.objects.filter(member=member)
    reposted = visible.filter(pk__in=member_reposts.values("publication_id"))
    is_me = member == request.user
    tab = request.GET.get("onglet")
    if tab not in ("republications", "enregistres") or (tab == "enregistres" and not is_me):
        tab = "publications"  # les enregistrements sont privés
    if tab == "republications":
        reposted_at = member_reposts.filter(publication=OuterRef("pk")).values("created_at")[:1]
        posts = annotate_social(reposted.for_cards(), request.user).annotate(reposted_at=Subquery(reposted_at))
        posts = posts.order_by("-reposted_at")
    elif tab == "enregistres":
        my_saves = Enregistrement.objects.filter(member=member)
        saved_at = my_saves.filter(publication=OuterRef("pk")).values("created_at")[:1]
        saved = visible.filter(pk__in=my_saves.values("publication_id"))
        posts = annotate_social(saved.for_cards(), request.user).annotate(saved_at=Subquery(saved_at))
        posts = posts.order_by("-saved_at")
    else:
        posts = annotate_social(own_posts.for_cards(), request.user)
    view = "liste" if request.GET.get("vue") == "liste" else "grille"
    context = {
        "member": member,
        "posts": posts[:60],
        "tab": tab,
        "view": view,
        "posts_count": own_posts.count(),
        "republications_count": reposted.count(),
        "followers_count": Abonnement.objects.filter(followed=member).count(),
        "following_count": Abonnement.objects.filter(follower=member).count(),
        "is_following": Abonnement.objects.filter(follower=request.user, followed=member).exists(),
        "is_me": is_me,
    }
    return render(request, "accounts/profile.html", context)


@login_required
def profile_edit(request):
    user = request.user
    account_form = AccountForm(request.POST or None, instance=user, prefix="compte")
    profile_form = ProfileForm(request.POST or None, request.FILES or None, instance=user.profile, prefix="profil")
    if request.method == "POST" and account_form.is_valid() and profile_form.is_valid():
        email_changed = "email" in account_form.changed_data
        account = account_form.save(commit=False)
        if email_changed:
            account.email_confirmed = False
        account.save()
        profile_form.save()
        if email_changed:
            send_confirmation_email(request, account)
            messages.info(request, "Adresse e-mail modifiée : confirmez-la grâce au lien reçu.")
        messages.success(request, "Profil mis à jour.")
        return redirect("accounts:profile", username=user.username)
    return render(request, "accounts/profile_edit.html", {"account_form": account_form, "profile_form": profile_form})


password_reset = auth_views.PasswordResetView.as_view(
    template_name="accounts/password_reset_form.html",
    email_template_name="accounts/emails/password_reset_email.txt",
    subject_template_name="accounts/emails/password_reset_subject.txt",
    success_url=reverse_lazy("accounts:password_reset_done"),
)
password_reset_done = auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html")
password_reset_confirm = auth_views.PasswordResetConfirmView.as_view(
    template_name="accounts/password_reset_confirm.html",
    success_url=reverse_lazy("accounts:password_reset_complete"),
)
password_reset_complete = auth_views.PasswordResetCompleteView.as_view(
    template_name="accounts/password_reset_complete.html"
)
