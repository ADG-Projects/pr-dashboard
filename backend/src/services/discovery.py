"""Auto-discover spaces (orgs + personal account) for a GitHubAccount."""

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.tables import GitHubAccount, Space
from src.services.crypto import decrypt_token
from src.services.github_client import GitHubClient


async def discover_spaces_for_account(session: AsyncSession, account: GitHubAccount) -> list[Space]:
    """Discover orgs + personal account and upsert Space rows.

    Returns the list of spaces that were created or updated.
    """
    if not account.encrypted_token:
        logger.warning(f"Account {account.login} has no token, skipping discovery")
        return []

    token = decrypt_token(account.encrypted_token)
    gh = GitHubClient(token=token, base_url=account.base_url)
    spaces: list[Space] = []

    try:
        # Discover orgs via /user/orgs (requires read:org scope)
        discovered_slugs: set[str] = set()
        try:
            orgs = await gh.list_user_orgs()
        except Exception:
            logger.warning(f"Cannot list orgs for {account.login} (token may lack read:org scope)")
            orgs = []

        for org in orgs:
            slug = org["login"]
            discovered_slugs.add(slug)
            name = org.get("description") or slug
            space = await _upsert_space(
                session,
                account_id=account.id,
                slug=slug,
                name=name,
                space_type="org",
            )
            spaces.append(space)

        # Fallback: discover orgs from accessible repos when /user/orgs returns nothing.
        # Fine-grained PATs and SSO-restricted tokens often can't list orgs but CAN
        # access repos, so we extract unique org owners from the repo list.
        if not orgs:
            try:
                repos = await gh.list_all_repos()
                org_owners: dict[str, str] = {}
                for repo in repos:
                    owner = repo["owner"]
                    owner_login = owner["login"]
                    owner_type = owner.get("type", "").lower()
                    if owner_type == "organization" and owner_login not in discovered_slugs:
                        org_owners[owner_login] = owner_login
                        discovered_slugs.add(owner_login)

                for slug in org_owners:
                    space = await _upsert_space(
                        session,
                        account_id=account.id,
                        slug=slug,
                        name=slug,
                        space_type="org",
                    )
                    spaces.append(space)

                if org_owners:
                    slugs = list(org_owners.keys())
                    logger.info(
                        f"Discovered {len(slugs)} org(s) via repo owners "
                        f"for {account.login}: {slugs}"
                    )
            except Exception:
                logger.warning(f"Fallback org discovery from repos failed for {account.login}")

        # Discover personal account
        try:
            gh_user = await gh.get_authenticated_user()
            space = await _upsert_space(
                session,
                account_id=account.id,
                slug=gh_user["login"],
                name=gh_user.get("name") or gh_user["login"],
                space_type="user",
            )
            spaces.append(space)
        except Exception:
            logger.exception(f"Failed to discover personal space for {account.login}")

    finally:
        await gh.close()

    logger.info(
        f"Discovered {len(spaces)} space(s) for account {account.login}: {[s.slug for s in spaces]}"
    )
    return spaces


async def _upsert_space(
    session: AsyncSession,
    account_id: int,
    slug: str,
    name: str,
    space_type: str,
) -> Space:
    """Create or update a Space for a given account + slug."""
    result = await session.execute(
        select(Space).where(
            Space.github_account_id == account_id,
            Space.slug == slug,
        )
    )
    space = result.scalar_one_or_none()

    if space is None:
        space = Space(
            name=name,
            slug=slug,
            space_type=space_type,
            github_account_id=account_id,
            is_active=False,  # User toggles on which orgs to track
        )
        session.add(space)
        await session.flush()
    else:
        space.name = name
        space.space_type = space_type

    return space
