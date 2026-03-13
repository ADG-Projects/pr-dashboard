"""Azure DevOps API client for work item operations.

All functions accept token, org_url, and project as parameters
(resolved from the authenticated user's AdoAccount).
"""

import base64
import re
from urllib.parse import quote

import httpx
from loguru import logger

API_VERSION = "7.1"


def _auth_header(token: str) -> dict[str, str]:
    """Basic auth with empty username + PAT (ADO standard)."""
    encoded = base64.b64encode(f":{token}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


def _work_item_url(org_url: str, project: str, item_id: int) -> str:
    """Build a clickable URL to a work item in the ADO web UI."""
    return f"{org_url}/{project}/_workitems/edit/{item_id}"


def _parse_work_item(item: dict, org_url: str, project: str) -> dict:
    """Extract relevant fields from an ADO work item response."""
    fields = item.get("fields", {})
    item_id = item["id"]
    return {
        "work_item_id": item_id,
        "title": fields.get("System.Title", ""),
        "state": fields.get("System.State", ""),
        "work_item_type": fields.get("System.WorkItemType", ""),
        "url": _work_item_url(org_url, project, item_id),
        "assigned_to": (fields.get("System.AssignedTo") or {}).get("displayName"),
    }


async def list_work_items(token: str, org_url: str, project: str, limit: int = 100) -> list[dict]:
    """Fetch recent work items ordered by ChangedDate DESC."""
    base_url = f"{org_url}/{quote(project)}/_apis"
    wiql = (
        "SELECT [System.Id] FROM WorkItems "
        "WHERE [System.TeamProject] = @project "
        "ORDER BY [System.ChangedDate] DESC"
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        wiql_resp = await client.post(
            f"{base_url}/wit/wiql?api-version={API_VERSION}&$top={limit}",
            headers={**_auth_header(token), "Content-Type": "application/json"},
            json={"query": wiql},
        )
        wiql_resp.raise_for_status()
        work_items = wiql_resp.json().get("workItems", [])

        if not work_items:
            return []

        ids = [str(wi["id"]) for wi in work_items[:limit]]

        fields = "System.Id,System.Title,System.State,System.WorkItemType,System.AssignedTo"
        detail_resp = await client.get(
            f"{base_url}/wit/workitems?ids={','.join(ids)}&fields={fields}&api-version={API_VERSION}",
            headers=_auth_header(token),
        )
        detail_resp.raise_for_status()

        return [
            _parse_work_item(item, org_url, project) for item in detail_resp.json().get("value", [])
        ]


async def search_work_items(token: str, org_url: str, project: str, query: str) -> list[dict]:
    """Search ADO work items by ID or title substring using WIQL."""
    base_url = f"{org_url}/{quote(project)}/_apis"

    # If query is a pure number, search by ID; otherwise by title
    if query.strip().isdigit():
        wiql = f"SELECT [System.Id] FROM WorkItems WHERE [System.Id] = {query.strip()}"
    else:
        escaped = query.replace("'", "''")
        wiql = (
            f"SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.Title] CONTAINS '{escaped}' "
            f"ORDER BY [System.ChangedDate] DESC"
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        wiql_resp = await client.post(
            f"{base_url}/wit/wiql?api-version={API_VERSION}",
            headers={**_auth_header(token), "Content-Type": "application/json"},
            json={"query": wiql},
        )
        wiql_resp.raise_for_status()
        work_items = wiql_resp.json().get("workItems", [])

        if not work_items:
            return []

        ids = [str(wi["id"]) for wi in work_items[:20]]

        fields = "System.Id,System.Title,System.State,System.WorkItemType,System.AssignedTo"
        detail_resp = await client.get(
            f"{base_url}/wit/workitems?ids={','.join(ids)}&fields={fields}&api-version={API_VERSION}",
            headers=_auth_header(token),
        )
        detail_resp.raise_for_status()

        return [
            _parse_work_item(item, org_url, project) for item in detail_resp.json().get("value", [])
        ]


def _pr_tag(pr_number: int) -> str:
    return f"[PR #{pr_number}]"


def _pr_desc_html(url: str, pr_number: int) -> str:
    return f'<div><a href="{url}">PR #{pr_number}</a></div>'


async def add_hyperlink(
    token: str,
    org_url: str,
    project: str,
    work_item_id: int,
    url: str,
    comment: str,
    pr_number: int,
) -> bool:
    """Add hyperlink, PR tag in title, and PR link in description."""
    base_url = f"{org_url}/{quote(project)}/_apis"
    api_url = f"{base_url}/wit/workitems/{work_item_id}"
    headers = {**_auth_header(token), "Content-Type": "application/json-patch+json"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{api_url}?api-version={API_VERSION}",
                headers=_auth_header(token),
            )
            resp.raise_for_status()
            fields = resp.json().get("fields", {})
            cur_title = fields.get("System.Title", "")
            cur_desc = fields.get("System.Description") or ""

            tag = _pr_tag(pr_number)
            link_html = _pr_desc_html(url, pr_number)

            patch_body: list[dict] = [
                {
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "Hyperlink",
                        "url": url,
                        "attributes": {"comment": comment},
                    },
                },
            ]

            if tag not in cur_title:
                patch_body.append(
                    {
                        "op": "replace",
                        "path": "/fields/System.Title",
                        "value": f"{cur_title} {tag}",
                    }
                )

            if link_html not in cur_desc:
                patch_body.append(
                    {
                        "op": "add",
                        "path": "/fields/System.Description",
                        "value": f"{link_html}{cur_desc}",
                    }
                )

            resp = await client.patch(
                f"{api_url}?api-version={API_VERSION}",
                headers=headers,
                json=patch_body,
            )
            resp.raise_for_status()
            logger.info(f"Added PR #{pr_number} link to ADO work item {work_item_id}")
            return True
    except Exception as exc:
        logger.warning(f"Failed to update ADO work item {work_item_id}: {exc}")
        return False


async def remove_hyperlink(
    token: str,
    org_url: str,
    project: str,
    work_item_id: int,
    url: str,
    pr_number: int,
) -> bool:
    """Remove hyperlink, PR tag from title, and PR link from description."""
    base_url = f"{org_url}/{quote(project)}/_apis"
    api_url = f"{base_url}/wit/workitems/{work_item_id}"
    headers = {**_auth_header(token), "Content-Type": "application/json-patch+json"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{api_url}?$expand=Relations&api-version={API_VERSION}",
                headers=_auth_header(token),
            )
            resp.raise_for_status()
            data = resp.json()
            fields = data.get("fields", {})
            relations = data.get("relations") or []
            cur_title = fields.get("System.Title", "")
            cur_desc = fields.get("System.Description") or ""

            tag = _pr_tag(pr_number)

            patch_body: list[dict] = []

            for idx, rel in enumerate(relations):
                if rel.get("rel") == "Hyperlink" and rel.get("url") == url:
                    patch_body.append(
                        {
                            "op": "remove",
                            "path": f"/relations/{idx}",
                        }
                    )
                    break

            if tag in cur_title:
                new_title = cur_title.replace(f" {tag}", "").replace(tag, "")
                patch_body.append(
                    {
                        "op": "replace",
                        "path": "/fields/System.Title",
                        "value": new_title,
                    }
                )

            escaped_url = re.escape(url)
            pattern = rf"<div>\s*<a\s+href=\"{escaped_url}\"\s*>" rf"PR\s*#{pr_number}</a>\s*</div>"
            new_desc = re.sub(pattern, "", cur_desc)
            if new_desc != cur_desc:
                patch_body.append(
                    {
                        "op": "replace",
                        "path": "/fields/System.Description",
                        "value": new_desc,
                    }
                )

            if not patch_body:
                logger.info(f"Nothing to remove on ADO work item {work_item_id}")
                return True

            patch_resp = await client.patch(
                f"{api_url}?api-version={API_VERSION}",
                headers=headers,
                json=patch_body,
            )
            patch_resp.raise_for_status()
            logger.info(f"Removed PR #{pr_number} link from ADO work item {work_item_id}")
            return True
    except Exception as exc:
        logger.warning(f"Failed to update ADO work item {work_item_id}: {exc}")
        return False


async def get_work_item(token: str, org_url: str, project: str, item_id: int) -> dict | None:
    """Fetch a single work item's current details."""
    base_url = f"{org_url}/{quote(project)}/_apis"
    fields = "System.Id,System.Title,System.State,System.WorkItemType,System.AssignedTo"

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                f"{base_url}/wit/workitems/{item_id}?fields={fields}&api-version={API_VERSION}",
                headers=_auth_header(token),
            )
            resp.raise_for_status()
            return _parse_work_item(resp.json(), org_url, project)
        except httpx.HTTPStatusError as exc:
            logger.warning(f"Failed to fetch ADO work item {item_id}: {exc}")
            return None
