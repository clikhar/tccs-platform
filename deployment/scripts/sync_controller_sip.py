#!/usr/bin/env python3
"""Generate Asterisk PJSIP controller endpoints from the TCCS database.

Only enabled SIP accounts assigned to enabled controllers are provisioned.
Controller extensions are restricted to 9xxx because the TCCS station dialplan
uses _9XXX for station -> controller calls.

The generated endpoints deliberately use the existing Asterisk transport-wss
configuration used by the browser WebRTC controllers. Station SIP remains on
the existing transport-tccs UDP/5060 configuration and is not generated here.

Environment:
  DATABASE_URL      SQLAlchemy-style PostgreSQL URL used by the backend.
  TCCS_PJSIP_OUTPUT Output path, default /etc/asterisk/pjsip.d/tccs-controllers.conf

The script writes atomically and does not reload Asterisk. Run:
  python3 deployment/scripts/sync_controller_sip.py
  asterisk -rx 'pjsip reload'
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

DEFAULT_DATABASE_URL = "postgresql://tccs:change-me-local@localhost:5432/tccs"
DEFAULT_OUTPUT = "/etc/asterisk/pjsip.d/tccs-controllers.conf"


def database_parts(url: str) -> tuple[str, str, str, str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise SystemExit("DATABASE_URL must be a PostgreSQL URL")
    return (
        unquote(parsed.hostname or "localhost"),
        str(parsed.port or 5432),
        unquote(parsed.username or "tccs"),
        unquote(parsed.password or ""),
        (parsed.path or "/tccs").lstrip("/") or "tccs",
    )


def ast_value(value: object) -> str:
    """Escape a value for an Asterisk config value."""
    text = str(value or "")
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace("\r", " ").replace("\n", " ")
    return text.replace('"', '\\"')


def query_accounts() -> list[dict]:
    database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    host, port, user, password, database = database_parts(database_url)
    query = """
        SELECT json_build_object(
            'controller_id', c.id,
            'controller_code', c.code,
            'controller_name', c.name,
            'extension', sa.extension,
            'username', sa.username,
            'password', sa.password
        )::text
        FROM controllers c
        JOIN sip_accounts sa ON sa.id = c.sip_account_id
        WHERE c.enabled = TRUE AND sa.enabled = TRUE
        ORDER BY sa.extension
    """
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    command = [
        "psql", "-h", host, "-p", port, "-U", user, "-d", database,
        "-At", "-c", query,
    ]
    try:
        result = subprocess.run(command, env=env, check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise SystemExit("psql is required to provision controller SIP endpoints") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Unable to query TCCS database: {exc.stderr.strip()}") from exc
    rows = []
    for line in result.stdout.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def render(accounts: list[dict]) -> str:
    seen_extensions: set[str] = set()
    seen_usernames: set[str] = set()
    lines = [
        "; GENERATED FILE - do not edit manually.",
        "; Source: TCCS controllers + sip_accounts tables.",
        "; Each controller gets an isolated conference named TCCS-CTRL-<extension>.",
        "; Browser controllers use the existing transport-wss configuration.",
        ";",
        "[controller-template](!)",
        "type=endpoint",
        "transport=transport-wss",
        "context=tccs-controller",
        "disallow=all",
        "allow=ulaw,alaw",
        "webrtc=yes",
        "use_avpf=yes",
        "media_encryption=dtls",
        "dtls_auto_generate_cert=yes",
        "dtls_verify=fingerprint",
        "dtls_setup=actpass",
        "ice_support=yes",
        "media_use_received_transport=yes",
        "rtcp_mux=yes",
        "rewrite_contact=yes",
        "force_rport=yes",
        "rtp_symmetric=yes",
        "direct_media=no",
        "",
        "[controller-auth-template](!)",
        "type=auth",
        "auth_type=userpass",
        "",
        "[controller-aor-template](!)",
        "type=aor",
        "max_contacts=1",
        "remove_existing=yes",
        "qualify_frequency=30",
        "",
    ]

    for account in accounts:
        extension = str(account["extension"]).strip()
        username = str(account["username"]).strip()
        if not re.fullmatch(r"9\d{3}", extension):
            raise SystemExit(
                f"Controller {account['controller_code']} uses SIP extension {extension!r}; "
                "controller extensions must be 9xxx."
            )
        if extension in seen_extensions:
            raise SystemExit(f"Duplicate controller SIP extension: {extension}")
        if username in seen_usernames:
            raise SystemExit(f"Duplicate controller SIP username: {username}")
        seen_extensions.add(extension)
        seen_usernames.add(username)
        controller_name = ast_value(account["controller_name"])
        lines.extend([
            f"; {account['controller_code']} / {account['controller_id']}",
            f"[{extension}](controller-template)",
            f"aors={extension}",
            f"auth={extension}-auth",
            f"callerid=\"{controller_name}\" <{extension}>",
            "",
            f"[{extension}-auth](controller-auth-template)",
            f"username={ast_value(username)}",
            f"password=\"{ast_value(account['password'])}\"",
            "",
            f"[{extension}](controller-aor-template)",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> None:
    accounts = query_accounts()
    content = render(accounts)
    output = Path(os.getenv("TCCS_PJSIP_OUTPUT", DEFAULT_OUTPUT))
    write_atomic(output, content)
    print(f"Provisioned {len(accounts)} controller SIP endpoint(s) -> {output}")
    for account in accounts:
        print(f"  {account['controller_code']}: SIP {account['extension']} -> TCCS-CTRL-{account['extension']}")


if __name__ == "__main__":
    main()
