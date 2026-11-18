"""What each mail to administrators says: password reset links and
notices of changes to their account (docs/decisions/0012). Sending them
is core/mail.py's.
"""

from inventory_tng.core.mail import Message, Told
from inventory_tng.core.phrases import BRAND, Words, gettext, lazy_gettext, ngettext
from inventory_tng.models.admins import running_low
from inventory_tng.models.site_settings import SignInSettings

# What each says, through the catalogue (core/phrases.py), in the locale active
# when the mail is made. Plain text, so nothing is escaped: a value goes in
# as it is. Nothing to render, nothing to track. Each mail's own words are
# one message; a paragraph several mails end with is a phrase of its own.

IF_NOT_YOU = lazy_gettext(
    "If it wasn't you, ask another administrator to reset your account at once."
)
NOT_EXPECTING = lazy_gettext("If you weren't expecting this, ignore this mail.")
NOT_AGREED = lazy_gettext(
    "If this was not agreed, check with the other administrators."
)
LAST_CODE = lazy_gettext(
    "That was your last one: make new ones from your account page now, with a "
    "code from your authenticator."
)
# The sign-in settings' TOTP policies (models.site_settings.TOTP_POLICIES), as
# the mail says them.
TOTP_POLICY_WORDS = {
    # Translators: as in "TOTP is now required".
    "required": lazy_gettext("required"),
    # Translators: as in "TOTP is now optional".
    "optional": lazy_gettext("optional"),
}


def branded(message: str, **values: object) -> str:
    """`message`, as gettext says it, naming the app where it says
    %(brand)s. A keyword the extractor knows (pyproject.toml)."""
    return gettext(message, brand=BRAND, **values)


def _paragraphs(*said: Words) -> str:
    return "\n\n".join(str(each) for each in said)


def _sign_in_reset() -> str:
    return branded("Your %(brand)s sign-in was reset")


def _invited() -> str:
    return branded("You were invited to administer %(brand)s")


def codes_running_low(left: int) -> str:
    return ngettext(
        "Only %(num)d is left: make new ones from your account page soon, with "
        "a code from your authenticator.",
        "Only %(num)d are left: make new ones from your account page soon, with "
        "a code from your authenticator.",
        left,
    )


def codes_left(left: int) -> str:
    return ngettext("%(num)d is left.", "%(num)d are left.", left)


def days(num: int) -> str:
    return ngettext("%(num)d day", "%(num)d days", num)


def hours(num: int) -> str:
    return ngettext("%(num)d hour", "%(num)d hours", num)


def minutes(num: int) -> str:
    return ngettext("%(num)d minute", "%(num)d minutes", num)


def reset_link(to: str, admin_id: int, link: str) -> Message:
    return Message(
        to,
        branded("Reset your %(brand)s password"),
        branded(
            "Someone asked to reset the password of your administrator account "
            "on %(brand)s. If it was you, choose a new one here, within the "
            "hour:\n\n%(link)s\n\nThe link works once. If you use an "
            "authenticator app, signing in still asks for its code.\n\nIf it "
            "wasn't you, ignore this mail: your password stays as it is.",
            link=link,
        ),
        "reset_link",
        admin_id,
    )


def reset_link_made(to: str, admin_id: int) -> Message:
    return Message(
        to,
        _sign_in_reset(),
        _paragraphs(
            branded(
                "Another administrator just reset your sign-in on %(brand)s: you "
                "were signed out everywhere, and your password, authenticators "
                "and recovery codes no longer work. They will pass on a link for "
                "you to choose a new password, which works once, within a day.",
            ),
            IF_NOT_YOU,
        ),
        "reset_link_made",
        admin_id,
    )


def invited(to: str, admin_id: int) -> Message:
    return Message(
        to,
        _invited(),
        _paragraphs(
            branded(
                "An administrator of %(brand)s just invited you to be one too, "
                "and will pass you a link to choose your password with. It works "
                "once, within a day.",
            ),
            NOT_EXPECTING,
        ),
        "invited",
        admin_id,
    )


def restored(to: str, admin_id: int) -> Message:
    return Message(
        to,
        branded("Your %(brand)s account was brought back"),
        _paragraphs(
            branded(
                "Another administrator just brought back your administrator "
                "account on %(brand)s. Anything you signed in with was removed "
                "when it was retired. They will pass on a link for you to choose "
                "a password with, which works once, within a day.",
            ),
            IF_NOT_YOU,
        ),
        "restored",
        admin_id,
    )


def recovery_link_made(to: str, admin_id: int) -> Message:
    return Message(
        to,
        branded("Your %(brand)s account is being recovered"),
        _paragraphs(
            branded(
                "Someone with the recovery token that whoever runs %(brand)s set "
                "just made a link that resets your password and authenticators. "
                "It works once, within the hour.",
            ),
            IF_NOT_YOU,
        ),
        "recovery_link_made",
        admin_id,
    )


def invited_by_operator(to: str, admin_id: int) -> Message:
    return Message(
        to,
        _invited(),
        _paragraphs(
            branded(
                "Whoever runs %(brand)s just invited you to be one of its "
                "administrators, and will pass you a link to choose your "
                "password with. It works once, within the hour.",
            ),
            NOT_EXPECTING,
        ),
        "invited_by_operator",
        admin_id,
    )


def let_in(to: str, admin_id: int) -> Message:
    return Message(
        to,
        branded("You are being let back in to %(brand)s"),
        branded(
            "Whoever runs %(brand)s just made a link that lets you back in to "
            "your administrator account: anything you signed in with no longer "
            "works. With the link they pass on, you choose a new password and "
            "set up TOTP again. It works once, within the hour.\n\nIf you "
            "weren't expecting this, tell whoever runs %(brand)s at once.",
        ),
        "let_in",
        admin_id,
    )


def password_changed(to: str, admin_id: int) -> Message:
    return Message(
        to,
        branded("Your %(brand)s password was changed"),
        _paragraphs(
            branded(
                "The password of your administrator account on %(brand)s was "
                "just changed, and your other sessions were signed out.",
            ),
            IF_NOT_YOU,
        ),
        "password_changed",
        admin_id,
    )


def email_changed(to: str, admin_id: int) -> Message:
    return Message(
        to,
        branded("Your %(brand)s email was changed"),
        _paragraphs(
            branded(
                "The email address of your administrator account on %(brand)s "
                "was just changed from this one. Password reset links now go to "
                "the new address.",
            ),
            IF_NOT_YOU,
        ),
        "email_changed",
        admin_id,
    )


def recovery_code_used(left: int) -> Told:
    def build(to: str, admin_id: int) -> Message:
        if left == 0:
            then: Words = LAST_CODE
        elif running_low(left):
            then = codes_running_low(left)
        else:
            then = codes_left(left)
        return Message(
            to,
            branded("A recovery code was used on your %(brand)s account"),
            _paragraphs(
                # Translators: %(left)s is a sentence saying how many recovery
                # codes are left, and what to do about it.
                branded(
                    "One of your recovery codes was just used on your "
                    "administrator account on %(brand)s. %(left)s If you lost "
                    "your phone, set up a new authenticator there first.",
                    left=then,
                ),
                IF_NOT_YOU,
            ),
            "recovery_code_used",
            admin_id,
        )

    return build


def authenticators_changed(*, added: bool) -> Told:
    """Told to the owner: one who signed in with a stolen recovery code could
    otherwise add their own authenticator, then make new recovery codes."""

    def build(to: str, admin_id: int) -> Message:
        if added:
            subject = branded(
                "An authenticator was added to your %(brand)s account",
            )
            body = branded(
                "An authenticator was just added to your administrator account "
                "on %(brand)s.",
            )
        else:
            subject = branded(
                "An authenticator was removed from your %(brand)s account",
            )
            body = branded(
                "An authenticator was just removed from your administrator "
                "account on %(brand)s.",
            )
        return Message(
            to,
            subject,
            _paragraphs(body, IF_NOT_YOU),
            "authenticators_changed",
            admin_id,
        )

    return build


def authenticators_retired(to: str, admin_id: int) -> Message:
    return Message(
        to,
        branded("Set up your authenticator again on %(brand)s"),
        _paragraphs(
            branded(
                "Whoever runs %(brand)s retired your authenticator, as the key "
                "it was kept with was lost or leaked, and signed you out. Sign "
                "in with your password and one of your recovery codes, then set "
                "up an authenticator again.",
            ),
            IF_NOT_YOU,
        ),
        "authenticators_retired",
        admin_id,
    )


def left_without_a_factor(to: str, admin_id: int) -> Message:
    return Message(
        to,
        _sign_in_reset(),
        branded(
            "Whoever runs %(brand)s retired your authenticator, as the key it "
            "was kept with was lost or leaked. You had no recovery code left, so "
            "your password was removed too: ask them to let you back in.",
        ),
        "left_without_a_factor",
        admin_id,
    )


def retired(to: str, admin_id: int) -> Message:
    return Message(
        to,
        branded("Your %(brand)s account was retired"),
        branded(
            "Another administrator just retired your administrator account on "
            "%(brand)s: you can no longer sign in, and anything you signed in "
            "with was removed. Another administrator can bring you back.\n\nIf "
            "this was not agreed, tell the other administrators, or whoever "
            "runs %(brand)s, at once.",
        ),
        "retired",
        admin_id,
    )


def recovery_codes_made(to: str, admin_id: int) -> Message:
    return Message(
        to,
        branded("New recovery codes for your %(brand)s account"),
        _paragraphs(
            branded(
                "New recovery codes were just made for your administrator "
                "account on %(brand)s. The ones you had no longer work.",
            ),
            IF_NOT_YOU,
        ),
        "recovery_codes_made",
        admin_id,
    )


def activity(words: str) -> Told:
    """Told to every administrator (admins/activity.py): one who could change who
    may sign in could otherwise do it unseen."""

    def build(to: str, admin_id: int) -> Message:
        return Message(
            to,
            branded("An administrator account on %(brand)s changed"),
            _paragraphs(
                # Translators: %(words)s says what changed, such as "ada
                # reset ben's sign-in".
                branded("On %(brand)s: %(words)s.", words=words),
                NOT_AGREED,
            ),
            "activity",
            admin_id,
        )

    return build


def sign_in_settings_changed(settings: SignInSettings) -> Told:
    """Told to every administrator: one who could weaken these could
    otherwise do it unseen. The values are read now, not when it is sent."""
    totp, session_days = settings.totp_policy, settings.session_days
    confirm_hours = settings.confirm_hours
    confirm_idle_minutes = settings.confirm_idle_minutes
    account_change_minutes = settings.account_change_minutes

    def build(to: str, admin_id: int) -> Message:
        values = {
            "totp": TOTP_POLICY_WORDS[totp],
            "days": days(session_days),
            "hours": hours(confirm_hours),
            "idle": minutes(confirm_idle_minutes),
        }
        if account_change_minutes:
            said = branded(
                "An administrator just changed how administrators sign in to "
                "%(brand)s. TOTP is now %(totp)s; sessions last %(days)s; a code "
                "typed for a sensitive change lasts %(hours)s, or %(idle)s "
                "unused; changing another administrator needs a code typed "
                "within %(minutes)s.",
                minutes=minutes(account_change_minutes),
                **values,
            )
        else:
            said = branded(
                "An administrator just changed how administrators sign in to "
                "%(brand)s. TOTP is now %(totp)s; sessions last %(days)s; a code "
                "typed for a sensitive change lasts %(hours)s, or %(idle)s "
                "unused; changing another administrator needs a code typed each "
                "time.",
                **values,
            )
        return Message(
            to,
            branded("%(brand)s's sign-in settings were changed"),
            _paragraphs(said, NOT_AGREED),
            "sign_in_settings_changed",
            admin_id,
        )

    return build
