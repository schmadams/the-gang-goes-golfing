# target path: frontend/src/pages/privacy.py (new file)
"""Public privacy policy page -- exists mainly so Google's OAuth "Google
Auth Platform" Branding page has a real, reachable Privacy Policy URL to
point at (its Publish button stays disabled without one, and the domain
that URL lives on has to be registered under Authorized domains there --
see this app's own OAuth setup notes for the full walkthrough).

Deliberately not gated behind login (no session check, no redirect to
/signin) -- Google's own review has to be able to load this page while
signed out, and a real visitor should be able to read it before deciding
whether to sign in at all. No callbacks either; this is static content,
nothing here needs to react to anything.

The content below describes what this app actually collects and does,
not generic boilerplate -- update it if what the app stores/shares ever
changes (new OAuth scopes, a new kind of upload, etc.), since an
inaccurate privacy policy is worse than none for an app this size.
"""
import dash
from dash import html

dash.register_page(__name__, path="/privacy", name="Privacy Policy")

_CONTACT_EMAIL = "samadams0201@gmail.com"
_LAST_UPDATED = "18 September 2026"


def _section(title, *paragraphs):
    return html.Div(
        [html.H3(title, className="mt-4 mb-2")] + [html.P(p, className="mb-2") for p in paragraphs],
    )


def layout(**kwargs):
    return html.Div(
        html.Div(
            html.Div(
                [
                    html.H1("Privacy Policy", className="mb-1"),
                    html.P(f"Last updated: {_LAST_UPDATED}", className="t3g-signin-subtitle mb-4"),
                    html.P(
                        "The Gang Goes Golfing (\"T3G\", \"the app\", \"we\") is a small app built for a "
                        "private group of friends to organize golf rounds and tournaments, track scores "
                        "and handicaps, and share results with each other. This page explains what "
                        "information the app collects, how it's used, and who it's shared with.",
                        className="mb-2",
                    ),
                    _section(
                        "Information we collect",
                        "Account information: your name and email address, whether you sign in "
                        "with an email address directly or with a Google account.",
                        "If you sign in with Google: your Google account's email address, name, and "
                        "Google account ID (used only to recognize your account on future sign-ins). "
                        "We don't request access to your Gmail, Google Drive, Google Calendar, "
                        "contacts, or anything else in your Google account beyond your basic profile "
                        "and email -- our sign-in flow only ever asks Google for your name and email.",
                        "Golf data you or your club enter: scores, handicaps, tee times, tournament "
                        "entries and results, and round history.",
                        "Content you choose to add: photos you upload to a round or club, and any "
                        "posts or messages you write in a club's feed.",
                        "Standard technical logs kept by our hosting provider (e.g. request "
                        "timestamps, IP addresses) for security and debugging -- the same kind any "
                        "web app's hosting platform keeps automatically.",
                    ),
                    _section(
                        "How we use this information",
                        "Strictly to run the app for your golf group: creating and matching your "
                        "account, calculating and displaying handicaps, showing leaderboards and "
                        "tournament results, and posting round/tournament updates to your clubs' "
                        "activity feeds.",
                        "We do not sell your information, use it for advertising, or share it with "
                        "any third party for marketing purposes. There are no ads in this app.",
                    ),
                    _section(
                        "Who can see your information",
                        "Other members of any club you belong to can see the golf data and posts "
                        "you'd expect a club member to see -- scores, handicaps, tournament results, "
                        "feed posts and photos -- the same way they would if the club kept a shared "
                        "spreadsheet or group chat. Your email address is not shown to other members.",
                    ),
                    _section(
                        "Where your information is stored",
                        "Account and golf data is stored in a Supabase-hosted database. Uploaded "
                        "photos are stored in Supabase Storage. The app itself is hosted on Railway. "
                        "We don't operate our own servers -- these are the same kinds of third-party "
                        "infrastructure providers most small apps run on.",
                    ),
                    _section(
                        "Data retention and deletion",
                        f"If you'd like your account and data deleted, email {_CONTACT_EMAIL} and "
                        "we'll remove it.",
                    ),
                    _section(
                        "Children's privacy",
                        "This app is not directed at children and is not knowingly used by anyone "
                        "under 13.",
                    ),
                    _section(
                        "Changes to this policy",
                        "If what this app collects or how it's used changes meaningfully, this page "
                        "will be updated and the date at the top will change.",
                    ),
                    _section(
                        "Contact",
                        f"Questions about this policy or your data: {_CONTACT_EMAIL}.",
                    ),
                ],
                className="t3g-panel-body",
                style={"maxWidth": "720px", "margin": "0 auto"},
            ),
            className="t3g-panel",
            style={"maxWidth": "760px", "margin": "3rem auto"},
        ),
        className="t3g-page",
    )