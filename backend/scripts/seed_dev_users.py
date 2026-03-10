"""Seed fake dev users with their own GitHub tokens for multi-user testing.

Usage:
    cd backend && uv run python scripts/seed_dev_users.py

Reads DEV_ALICE_TOKEN and DEV_BOB_TOKEN from .env (via settings).
Creates two dev users each with their own real GitHub token. Each user
gets a unique fake github_id so they don't collide with real OAuth users
or each other, even if the tokens belong to the same GitHub account.

Spaces are auto-discovered per token so each user gets access to whatever
orgs their token can see.

Note: These dev users are for local testing only. The cleanup migration
(2f2f348ad3af) will remove them if run. To re-seed after cleanup, just
run this script again.
"""

import asyncio
import sys

from sqlalchemy import select

# Ensure backend src is importable
sys.path.insert(0, ".")

from src.config.settings import settings
from src.db.engine import async_session_factory
from src.models.tables import GitHubAccount, User
from src.services.crypto import encrypt_token
from src.services.discovery import discover_spaces_for_account
from src.services.github_client import GitHubClient

FAKE_USERS = [
    {
        "login": "alice-dev",
        "name": "Alice Dev",
        "github_id": 900001,
        "token_attr": "dev_alice_token",
    },
    {
        "login": "bob-dev",
        "name": "Bob Dev",
        "github_id": 900002,
        "token_attr": "dev_bob_token",
    },
]


async def main() -> None:
    for fake_def in FAKE_USERS:
        token = getattr(settings, fake_def["token_attr"])
        if not token:
            print(
                f"Missing token for {fake_def['name']}. "
                f"Set DEV_ALICE_TOKEN / DEV_BOB_TOKEN in .env"
            )
            return

    async with async_session_factory() as session:
        for fake_def in FAKE_USERS:
            token = getattr(settings, fake_def["token_attr"])
            print(f"\n--- {fake_def['name']} ---")

            # Validate token by fetching the GitHub user it belongs to
            gh = GitHubClient(token=token)
            try:
                gh_user = await gh.get_authenticated_user()
            except Exception as exc:
                print(f"  Token for {fake_def['name']} is invalid: {exc}")
                continue
            finally:
                await gh.close()

            real_login = gh_user["login"]
            real_gh_id = gh_user["id"]
            print(f"  Token belongs to GitHub user: {real_login} (id={real_gh_id})")

            # Upsert User with fake github_id to avoid collision with the real OAuth user
            existing_user = (
                await session.execute(select(User).where(User.github_id == fake_def["github_id"]))
            ).scalar_one_or_none()

            if existing_user:
                user = existing_user
                print(f"  User '{user.login}' already exists (id={user.id})")
            else:
                user = User(
                    github_id=fake_def["github_id"],
                    login=fake_def["login"],
                    name=fake_def["name"],
                    avatar_url=gh_user.get("avatar_url"),
                    is_active=True,
                )
                session.add(user)
                await session.flush()
                print(f"  Created user '{user.login}' (id={user.id})")

            # Upsert GitHubAccount linking the dev user to the real GitHub identity
            encrypted = encrypt_token(token)
            existing_acct = (
                await session.execute(
                    select(GitHubAccount).where(
                        GitHubAccount.user_id == user.id,
                        GitHubAccount.github_id == real_gh_id,
                    )
                )
            ).scalar_one_or_none()

            if existing_acct:
                existing_acct.encrypted_token = encrypted
                existing_acct.login = real_login
                existing_acct.avatar_url = gh_user.get("avatar_url")
                existing_acct.is_active = True
                acct = existing_acct
                print(f"  Updated GitHubAccount (id={acct.id})")
            else:
                acct = GitHubAccount(
                    user_id=user.id,
                    github_id=real_gh_id,
                    login=real_login,
                    avatar_url=gh_user.get("avatar_url"),
                    encrypted_token=encrypted,
                    base_url="https://api.github.com",
                    is_active=True,
                )
                session.add(acct)
                await session.flush()
                print(f"  Created GitHubAccount (id={acct.id})")

            # Auto-discover spaces for this account's token
            spaces = await discover_spaces_for_account(session, acct)
            print(f"  Discovered {len(spaces)} space(s): {[s.slug for s in spaces]}")

            await session.commit()

        print("\nDone! Set DEV_MODE=true in .env and use the DEV switcher in the UI.")
        print(
            "WARNING: If multiple dev users share the same GitHub token, "
            "they will track repos with duplicate tokens. "
            "Use different tokens for realistic multi-user testing."
        )


if __name__ == "__main__":
    asyncio.run(main())
