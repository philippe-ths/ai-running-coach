"""Mint the Garmin token dump for the owner-only recovery sync (#555).

Run this ONCE on your own machine, never on the server:

    pip install garminconnect       # or use the backend venv once the PR is merged
    python scripts/garmin_login.py

It asks for your Garmin email and password (typed here, held in memory for this
login only, never written to disk or printed) and for the MFA code if Garmin asks
for one. It then prints ONE line, the token dump. Copy it into the worker's
Railway variable `GARMIN_TOKENS`:

    railway variables --set GARMIN_TOKENS='<the printed line>' --service worker

The dump is a bearer secret for your Garmin account: treat it like a password,
do not commit it, paste it into chat, or put it in a ticket. It stays valid for
roughly a year; when it expires the sync logs an authentication failure and
stops, and you re-run this script and set the variable again.
"""

import getpass
import sys


def main() -> int:
    try:
        from garminconnect import Garmin
    except ImportError:
        print("garminconnect is not installed: pip install garminconnect", file=sys.stderr)
        return 2

    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (hidden): ")
    api = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("Garmin MFA code: ").strip(),
    )
    api.login()
    dump = api.client.dumps()
    if len(dump) < 513:
        print("Unexpectedly short token dump; not printing it.", file=sys.stderr)
        return 1
    print("\nSet this as GARMIN_TOKENS on the worker (one line, a secret):\n", file=sys.stderr)
    print(dump)
    return 0


if __name__ == "__main__":
    sys.exit(main())
