"""Where allauth is told what this project means by signing in.

Decision 0013 point 5, in code: signing in establishes identity and never
authority. A person arriving from Google, from Slack, or from whatever OpenID
Connect provider a deployment configures gets an ordinary ``User`` row with no
flags set, and an existing administrator grants the staff flag afterwards.

The rule is enforced here rather than relied upon as Django's default,
because the default is a property of ``User.__init__`` that nothing states and
nothing would notice losing. A provider controls every value in the payload
these adapters are handed, so the two fields that decide what a session may do
are set from this side of the boundary, on every save, whatever arrived.
"""

from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.contrib.auth.models import User
from django.http import HttpRequest

# The two flags that turn an identity into an authority, and the only ones
# this project reads: ``StaffWrites`` and ``is_administrator`` in
# inventory/permissions.py ask about ``is_staff``, and ``is_superuser``
# implies it everywhere Django checks a permission.
AUTHORITY_FLAGS = ("is_staff", "is_superuser")


class AccountAdapter(DefaultAccountAdapter):
    """The local username-and-password path of decision 0013 point 2."""

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        """Local accounts are issued, not registered.

        Point 2 keeps the local path available whatever else is configured,
        and docs/deployment.md says what it is there for. It is a password an
        administrator handed over: self-service registration would only
        manufacture accounts that hold nothing, and the administrator would
        still have to make the real one.
        """
        return False


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Every provider path of decision 0013 point 1."""

    def is_open_for_signup(self, request: HttpRequest, sociallogin: SocialLogin) -> bool:
        """Arriving from a provider for the first time makes an account.

        Deliberately open where the local path above is closed, and it costs
        nothing: with ``SOCIALACCOUNT_AUTO_SIGNUP`` off the visitor confirms
        the account rather than acquiring one silently, and what they get is
        an account that may read nothing until somebody grants it.
        """
        return True

    def save_user(self, request: HttpRequest, sociallogin: SocialLogin, form: Any = None) -> User:
        """Save the new account, with the authority flags cleared.

        The provider decided the name, the email address and everything else
        in ``extra_data``; it does not get to decide this. Cleared on every
        save rather than only on creation, so a later change of provider
        payload cannot raise an account that an administrator has already
        seen.
        """
        # CLEARED BEFORE THE ROW IS WRITTEN, not only after. Both, and the
        # order is the point: clearing afterwards leaves a moment where the
        # database holds an account the provider promoted, and migration 0020
        # refuses the write that would take the last administrator away -- so
        # in a system with no other administrator, the UPDATE that strips this
        # one was refused and the account KEPT what the payload claimed.
        #
        # Which way that has to resolve is not close. A system with no
        # administrator is recoverable: re-run the deploy-time Job, or reach
        # the database. A system where an unvetted account holds one is not
        # recoverable by anything automatic. So the flags never reach the
        # table, and the clearing below becomes the belt rather than the
        # braces. inventory-tng-s8dk.
        for flag in AUTHORITY_FLAGS:
            setattr(sociallogin.user, flag, False)

        user: User = super().save_user(request, sociallogin, form)
        granted = [flag for flag in AUTHORITY_FLAGS if getattr(user, flag)]
        if granted:
            for flag in granted:
                setattr(user, flag, False)
            user.save(update_fields=granted)
        return user
