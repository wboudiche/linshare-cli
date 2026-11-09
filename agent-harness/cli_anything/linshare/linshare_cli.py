"""linshare — share documents through a LinShare server (CLI-Anything harness).

Usage:
    linshare login                      # once, in your own terminal
    linshare --json share report.pdf --to marc@acme.com --expires 7d --dry-run
    linshare                            # interactive REPL
"""

from __future__ import annotations

import functools
import json
import os
import shlex
import sys
from datetime import date
from pathlib import Path

import click

from cli_anything.linshare import __version__
from cli_anything.linshare.core import auth, documents, received, shares, spaces, upload_requests
from cli_anything.linshare.core.errors import AuthError, LinShareError, ServerError, UsageError

_state = {"json": False, "repl": False}


# -- output -----------------------------------------------------------------


def output(data, message: str = "") -> None:
    if _state["json"]:
        click.echo(json.dumps(data, indent=2, default=str, ensure_ascii=False))
        return
    if message:
        click.echo(message)
    if isinstance(data, list):
        if not data:
            click.echo("(none)")
        for item in data:
            _print_item(item)
            click.echo("")
    elif isinstance(data, dict):
        _print_item(data)


def _print_item(item, indent: int = 0) -> None:
    pad = "  " * indent
    if not isinstance(item, dict):
        click.echo(f"{pad}{item}")
        return
    for key, value in item.items():
        if value is None or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            click.echo(f"{pad}{key}:")
            _print_item(value, indent + 1)
        elif isinstance(value, list):
            click.echo(f"{pad}{key}:")
            for sub in value:
                if isinstance(sub, dict):
                    click.echo(f"{pad}  -")
                    _print_item(sub, indent + 2)
                else:
                    click.echo(f"{pad}  - {sub}")
        else:
            click.echo(f"{pad}{key}: {value}")


def handle_error(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except LinShareError as err:
            _report(err)
        except (click.ClickException, click.exceptions.Exit, click.Abort, BrokenPipeError):
            raise
        except Exception as exc:  # noqa: BLE001 — last-resort mapping to the JSON error shape
            _report(ServerError(f"Unexpected error: {exc.__class__.__name__}: {exc}"))

    return wrapper


def _report(err: LinShareError) -> None:
    if _state["json"]:
        click.echo(json.dumps(err.to_dict(), indent=2, ensure_ascii=False))
    else:
        click.echo(f"Error: {err.message}", err=True)
    if _state["repl"]:
        return
    sys.exit(err.exit_code)


class LinShareGroup(click.Group):
    """Click usage errors (missing option, unknown command) become our exit 1 + JSON error,
    so they are never confused with exit 2 (not logged in)."""

    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        if not standalone_mode:
            return super().main(args, prog_name, complete_var, standalone_mode=False, **extra)
        argv = list(sys.argv[1:] if args is None else args)
        try:
            rv = super().main(argv, prog_name, complete_var, standalone_mode=False, **extra)
        except click.UsageError as exc:
            _state["json"] = _state["json"] or "--json" in argv
            message = exc.format_message()
            if exc.ctx is not None:
                message += f" (see `{exc.ctx.command_path} --help`)"
            _report(UsageError(message))
            return None
        except click.Abort:
            click.echo("Aborted.", err=True)
            sys.exit(1)
        sys.exit(rv if isinstance(rv, int) else 0)


# -- root -------------------------------------------------------------------


@click.group(cls=LinShareGroup, invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Machine-readable JSON output.")
@click.version_option(__version__, prog_name="linshare")
@click.pass_context
def cli(ctx, use_json):
    """Share documents through LinShare (6.5 and later)."""
    _state["json"] = use_json
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# -- auth -------------------------------------------------------------------


@cli.command()
@click.option("--server", help="LinShare server URL, e.g. https://linshare.example.com")
@click.option("--email", help="Your LinShare email (password login).")
@click.option("--token", "use_token", is_flag=True, help="Paste a permanent token created in the LinShare web UI.")
@handle_error
def login(server, email, use_token):
    """Log in once; stores a long-lived token (never your password)."""
    server = server or click.prompt("LinShare server URL", err=True)
    if use_token:
        token = click.prompt("Permanent token", hide_input=True, err=True)
        cfg = auth.login_token(server, token)
    else:
        email = email or click.prompt("Email", err=True)
        password = click.prompt("Password", hide_input=True, err=True)
        try:
            cfg = auth.login_password(server, email, password, None)
        except AuthError as err:
            if err.code != "totp_required":
                raise
            totp = click.prompt("2FA code", err=True)
            cfg = auth.login_password(server, email, password, totp)
    output(
        {"server": cfg.server, "email": cfg.email, "tokenUuid": cfg.token_uuid},
        f"Logged in to {cfg.server} as {cfg.email}.",
    )


@cli.command()
@handle_error
def logout():
    """Forget the stored token (and revoke it on the server when possible)."""
    result = auth.logout()
    output(result, "Logged out." + (f" {result['note']}" if result["note"] else ""))


@cli.command()
@handle_error
def whoami():
    """Show the server and account in use."""
    output(auth.whoami())


# -- documents --------------------------------------------------------------


@cli.group("documents")
def documents_group():
    """Your documents in LinShare."""


@documents_group.command("list")
@handle_error
def documents_list():
    """List your documents."""
    client, _ = auth.client_from_config()
    output(documents.list_documents(client))


@documents_group.command("delete")
@click.argument("ids", nargs=-1, required=True)
@handle_error
def documents_delete(ids):
    """Delete documents (ask the user first)."""
    client, _ = auth.client_from_config()
    output(documents.delete_documents(client, list(ids)), f"Deleted {len(ids)} document(s).")


# -- share -------------------------------------------------------------------


@cli.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(path_type=Path))
@click.option("--to", "to", multiple=True, required=True, help="Recipient email (repeat for several).")
@click.option("--expires", help="Expiry: 7d, 2w, 1m or YYYY-MM-DD (server default if omitted).")
@click.option("--subject", help="Email subject.")
@click.option("--message", help="Message shown to recipients.")
@click.option("--password-link", is_flag=True, help="Password-protected link for outside recipients.")
@click.option(
    "--alert-if-not-downloaded",
    "alert",
    metavar="WHEN",
    help="Email me on this day if nobody downloaded the share: YYYY-MM-DD, 3d, 1w, or 'off'"
    " (server default if omitted).",
)
@click.option("--send-me-a-copy", is_flag=True, help="Email me a copy of the share notification.")
@click.option("--dry-run", is_flag=True, help="Only show what would be sent; changes nothing.")
@handle_error
def share(files, to, expires, subject, message, password_link, alert, send_me_a_copy, dry_run):
    """Upload FILES and share them by email (confirm with the user first)."""
    client, cfg = auth.client_from_config()
    today = date.today()
    plan = shares.plan_share(
        list(files), list(to), expires, subject, message, password_link, cfg.email or "", today,
        alert_if_not_downloaded=alert, send_me_a_copy=send_me_a_copy,
    )
    effects = shares.server_effects(client, plan, today)
    if dry_run:
        output({"dryRun": True, **plan, **effects}, "Dry run — nothing was sent. This would be shared:")
        return
    result = {**shares.execute_share(client, plan), **effects}
    output(result, f"Shared {len(plan['files'])} file(s) with {len(plan['recipients'])} recipient(s).")


@cli.group("shares")
def shares_group():
    """Documents you have shared."""


@shares_group.command("list")
@handle_error
def shares_list():
    """List your sent shares."""
    client, _ = auth.client_from_config()
    output(shares.list_shares(client))


@shares_group.command("revoke")
@click.argument("ids", nargs=-1, required=True)
@handle_error
def shares_revoke(ids):
    """Revoke sent shares (ask the user first)."""
    client, _ = auth.client_from_config()
    output(shares.revoke_shares(client, list(ids)), f"Revoked {len(ids)} share(s).")


# -- received ----------------------------------------------------------------


@cli.group("received")
def received_group():
    """Documents others shared with you."""


@received_group.command("list")
@handle_error
def received_list():
    """List received shares."""
    client, _ = auth.client_from_config()
    output(received.list_received(client))


@received_group.command("download")
@click.argument("share_id")
@click.option("--out", "out_dir", type=click.Path(path_type=Path), default=Path("."), help="Target directory.")
@handle_error
def received_download(share_id, out_dir):
    """Download a received file (never overwrites)."""
    client, _ = auth.client_from_config()
    result = received.download_received(client, share_id, out_dir)
    output(result, f"Saved to {result['path']}")


# -- upload requests ---------------------------------------------------------


@cli.command("request-files")
@click.option("--from", "from_", multiple=True, required=True, help="Who should send files (repeat for several).")
@click.option("--label", help="Title of the request.")
@click.option("--expires", help="Expiry: 7d, 2w, 1m or YYYY-MM-DD (server default if omitted).")
@click.option("--message", help="Message shown to the people asked (required by LinShare).")
@click.option("--dry-run", is_flag=True, help="Only show what would be requested; changes nothing.")
@handle_error
def request_files(from_, label, expires, message, dry_run):
    """Ask people to send you files, one private request each (confirm first)."""
    client, cfg = auth.client_from_config()
    plan = upload_requests.plan_request(list(from_), label, expires, message, cfg.email or "", date.today())
    if dry_run:
        output({"dryRun": True, **plan}, "Dry run — nothing was sent. This would be requested:")
        return
    result = upload_requests.create_request(client, plan)
    output(result, f"Request sent to {len(result['recipients'])} person(s).")


@cli.group("requests")
def requests_group():
    """Your upload requests (files people send you)."""


@requests_group.command("list")
@handle_error
def requests_list():
    """List your upload requests and how many files came in."""
    client, _ = auth.client_from_config()
    output(upload_requests.list_requests(client))


@requests_group.command("download")
@click.argument("request_id")
@click.option("--out", "out_dir", type=click.Path(path_type=Path), default=Path("."), help="Target directory.")
@handle_error
def requests_download(request_id, out_dir):
    """Download the files received for a request, as a zip."""
    client, _ = auth.client_from_config()
    result = upload_requests.download_request(client, request_id, out_dir)
    output(result, f"Saved to {result['path']}")


@requests_group.command("close")
@click.argument("ids", nargs=-1, required=True)
@handle_error
def requests_close(ids):
    """Close upload requests (ask the user first)."""
    client, _ = auth.client_from_config()
    output(upload_requests.close_requests(client, list(ids)), f"Closed {len(ids)} request(s).")


# -- shared spaces (workgroups and workspaces) -----------------------------------


@cli.group("spaces")
def spaces_group():
    """Workgroups (team files) and workspaces (groups of workgroups)."""


@spaces_group.command("list")
@click.option("--in", "parent", metavar="WORKSPACE", help="List the workgroups inside this workspace.")
@handle_error
def spaces_list(parent):
    """List the workgroups and workspaces you belong to, with your role."""
    client, _ = auth.client_from_config()
    output(spaces.list_spaces(client, parent))


@spaces_group.command("create")
@click.argument("name")
@click.option("--workspace", is_flag=True, help="Create a workspace instead of a workgroup.")
@click.option("--in", "parent", metavar="WORKSPACE", help="Create the workgroup inside this workspace.")
@handle_error
def spaces_create(name, workspace, parent):
    """Create a workgroup (or a workspace); you are its only member."""
    client, _ = auth.client_from_config()
    result = spaces.create_space(client, name, workspace, parent)
    output(result, f"Created {result['type']} '{result['name']}'.")


@spaces_group.command("delete")
@click.argument("ids", nargs=-1, required=True)
@handle_error
def spaces_delete(ids):
    """Delete shared spaces and all their files, for every member (ask the user first)."""
    client, _ = auth.client_from_config()
    output(spaces.delete_spaces(client, list(ids)), f"Deleted {len(ids)} shared space(s).")


@spaces_group.command("files")
@click.argument("space")
@click.option("--folder", help="Folder id (default: the top of the space).")
@handle_error
def spaces_files(space, folder):
    """List the folders and files of a workgroup."""
    client, _ = auth.client_from_config()
    output(spaces.list_files(client, space, folder))


@spaces_group.command("mkdir")
@click.argument("space")
@click.argument("name")
@click.option("--folder", help="Parent folder id (default: the top of the space).")
@handle_error
def spaces_mkdir(space, name, folder):
    """Create a folder in a workgroup."""
    client, _ = auth.client_from_config()
    result = spaces.make_folder(client, space, name, folder)
    output(result, f"Created folder '{result['name']}'.")


@spaces_group.command("upload")
@click.argument("space")
@click.argument("files", nargs=-1, required=True, type=click.Path(path_type=Path))
@click.option("--folder", help="Folder id (default: the top of the space).")
@click.option("--dry-run", is_flag=True, help="Only show what would be uploaded and who will see it.")
@handle_error
def spaces_upload(space, files, folder, dry_run):
    """Upload FILES into a workgroup; every member will see them (confirm first)."""
    client, _ = auth.client_from_config()
    if dry_run:
        plan = spaces.plan_upload(client, space, list(files), folder)
        output({"dryRun": True, **plan}, "Dry run — nothing was uploaded. This would be uploaded:")
        return
    result = spaces.upload_files(client, space, list(files), folder)
    output({"uploaded": result}, f"Uploaded {len(result)} file(s).")


@spaces_group.command("download")
@click.argument("space")
@click.argument("file_id")
@click.option("--out", "out_dir", type=click.Path(path_type=Path), default=Path("."), help="Target directory.")
@handle_error
def spaces_download(space, file_id, out_dir):
    """Download a file from a workgroup (never overwrites)."""
    client, _ = auth.client_from_config()
    result = spaces.download_file(client, space, file_id, out_dir)
    output(result, f"Saved to {result['path']}")


@spaces_group.command("delete-files")
@click.argument("space")
@click.argument("ids", nargs=-1, required=True)
@handle_error
def spaces_delete_files(space, ids):
    """Delete files or folders from a workgroup, for every member (ask the user first)."""
    client, _ = auth.client_from_config()
    output(spaces.delete_files(client, space, list(ids)), f"Deleted {len(ids)} item(s).")


@spaces_group.command("members")
@click.argument("space")
@handle_error
def spaces_members(space):
    """List the members of a shared space and their roles."""
    client, _ = auth.client_from_config()
    output(spaces.list_members(client, space))


@spaces_group.command("add-member")
@click.argument("space")
@click.argument("email")
@click.option("--role", required=True, help="READER, CONTRIBUTOR, WRITER or ADMIN (workspaces: READER, WRITER, ADMIN).")
@click.option("--dry-run", is_flag=True, help="Only show who would be added; changes nothing.")
@handle_error
def spaces_add_member(space, email, role, dry_run):
    """Give someone access to a shared space (confirm first)."""
    client, _ = auth.client_from_config()
    plan = spaces.plan_add_member(client, space, email, role)
    if dry_run:
        output({"dryRun": True, **plan}, "Dry run — nobody was added. This would be done:")
        return
    result = spaces.add_member(client, plan)
    output(result, f"Added {result['email']} as {result['role']}.")


@spaces_group.command("set-role")
@click.argument("space")
@click.argument("member")
@click.argument("role")
@handle_error
def spaces_set_role(space, member, role):
    """Change a member's role; MEMBER is an email or a member id (ask the user first)."""
    client, _ = auth.client_from_config()
    result = spaces.set_role(client, space, member, role)
    output(result, f"{result['email']} is now {result['role']}.")


@spaces_group.command("remove-member")
@click.argument("space")
@click.argument("member")
@handle_error
def spaces_remove_member(space, member):
    """Remove someone from a shared space; MEMBER is an email or a member id (ask the user first)."""
    client, _ = auth.client_from_config()
    result = spaces.remove_member(client, space, member)
    output(result, f"Removed {result['email']}.")


# -- REPL -------------------------------------------------------------------


@cli.command(hidden=True)
def repl():
    """Interactive mode."""
    from cli_anything.linshare.utils.repl_skin import ReplSkin

    skin = ReplSkin("linshare", version=__version__)
    skin.print_banner()
    session = skin.create_prompt_session()
    _state["repl"] = True
    while True:
        try:
            line = skin.get_input(session)
        except (EOFError, KeyboardInterrupt):
            break
        line = (line or "").strip()
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        try:
            args = shlex.split(line)
            if args and args[0] == "linshare":
                args = args[1:]
            cli.main(args=args, prog_name="linshare", standalone_mode=False)
        except click.ClickException as exc:
            exc.show()
        except (click.exceptions.Exit, click.Abort, SystemExit):
            pass
    skin.print_goodbye()


def main():
    try:
        cli(prog_name="linshare")
    except BrokenPipeError:
        # the reader stopped early (`linshare ... | head`): not an error worth a traceback.
        # Point stdout at /dev/null so the final flush at exit does not fail again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)


if __name__ == "__main__":
    main()
