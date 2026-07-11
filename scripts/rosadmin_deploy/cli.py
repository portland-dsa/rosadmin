"""The deploy specs for the shared box: the service, the frontend, and ingress.

Specs:
- `rosadmin` owns the backend's systemd units
- `ingress` owns caddy.service and the entire /etc/caddy tree, so the reverse
    proxy's routing is repeatedly provisioned config, not one-off box state
- `frontend` is declared for posterity but carries no material yet - the static tree ships
    through its own CI path.

Ingress deliberately ships *only* through this root-run tool and
never an application deploy identity: the routing config names every
environment's upstream in one place, so whoever can write it holds routing
power over all of them.
"""

from enum import StrEnum

import che_deploya
from che_deploya import (
    Check,
    Component,
    DeploySpec,
    Environment,
    FilePermissions,
    Secret,
    SharedRestart,
    Stages,
    StaticUnit,
    TemplatedUnit,
    db,
)
from che_deploya.db import Db, Role


class ServiceSecrets(StrEnum):
    """The credentials a rosadmin unit loads: the audit HMAC key, the migration
    role's scram password, the botonio SSO bearer, the Google DWD key, and the
    Solidarity Tech API token.

    Each member's value is the credential name the app reads from
    `$CREDENTIALS_DIRECTORY`, the `<name>.cred` filename, and the key in the
    encrypted secrets file - so these must match `_audit_key`, `_migrate_password`,
    and `sso_bearer` exactly. The DWD key is the raw service-account JSON; the
    unit's `CREDENTIALS_FILE` env points the app at its delivered path.

    Not every unit loads all five: the staging web `service` carries the SSO
    bearer but no Solidarity Tech token (staging reads the in-process mock, which
    ignores auth); the production `sync` sweep carries the token but no SSO bearer
    (it authenticates no users). Each component names the subset it needs.
    """

    AuditHmacKey = "audit-hmac-key"
    DbMigrationPassword = "db_migration_password"
    BotonioSsoBearer = "botonio_sso_bearer"
    GoogleDwdKey = "google-dwd-key"
    #: The real Solidarity Tech bearer token. Production-only: the staging mock
    #: needs none, so only the production sweep loads it.
    SolidarityTechToken = "solidarity_tech_token"


class ServiceEnv(StrEnum):
    """The non-secret botonio values templated into the staging override: the SSO
    verifying key and the home guild id.

    They live in the component's encrypted secrets file alongside the credentials
    but are public, so they render into the unit as `Environment=` lines rather
    than load as `.cred`s. Each member's value is both its key in that file and the
    `${...}` placeholder it fills in the override template.
    """

    BotonioSsoPubkey = "botonio_sso_pubkey"
    SsoGuildId = "sso_guild_id"
    #: The Workspace user the DWD service account impersonates - an org address,
    #: kept out of the committed template for member-privacy consistency.
    GoogleDwdSubject = "google_dwd_subject"
    #: The mock roster staging boots with. Held in the secrets file rather than
    #: the committed template because its entries name real personal emails
    #: (the testers' own accounts) - same privacy line as the subject above.
    StMockPersonas = "st_mock_personas"
    #: The org-wide Google Group the reconcile sweep syncs the whole
    #: good-standing roster into - an org address, kept out of the committed
    #: template like the subject and personas above.
    MainGroupEmail = "main_group_email"
    #: The main group's display name. The sweep needs it to name that group when
    #: it must create one; an existing group is adopted with its name untouched.
    #: Paired with MainGroupEmail in the secrets file so the two stay together.
    MainGroupName = "main_group_name"


ROSADMIN = DeploySpec(
    root="rosadmin",
    package="rosadmin_deploy",
    stages=frozenset(Stages),
    components=[
        Component(
            name="service",
            # Staging-only: the web service (with its admin socket and mock
            # Solidarity Tech source) is not on production yet. See the `sync`
            # component below for the production sweep and the stopgap note there.
            stages=frozenset({Stages.Staging}),
            secrets=Secret(
                # The web service's four: no Solidarity Tech token (staging reads
                # the mock, which ignores auth), so name the set explicitly rather
                # than sweeping in every ServiceSecrets member.
                names=frozenset(
                    {
                        ServiceSecrets.AuditHmacKey,
                        ServiceSecrets.DbMigrationPassword,
                        ServiceSecrets.BotonioSsoBearer,
                        ServiceSecrets.GoogleDwdKey,
                    }
                ),
                src="{repo_root}/secrets/rosadmin/{stage}.enc.yaml",
            ),
            units=[
                StaticUnit(
                    src="{repo_root}/deploy/systemd/rosadmin@.service",
                    dest="/etc/systemd/system/rosadmin@.service",
                ),
                StaticUnit(
                    src="{repo_root}/deploy/systemd/rosadmin@.socket",
                    dest="/etc/systemd/system/rosadmin@.socket",
                ),
                TemplatedUnit(
                    src="{repo_root}/deploy/systemd/rosadmin@{stage}.service.d/override.conf",
                    resource_loc="assets/rosadmin@{stage}.service.d/override.conf",
                    dest="/etc/systemd/system/rosadmin@{stage}.service.d/override.conf",
                    env=Environment(names=frozenset(ServiceEnv)),
                    per_stage=True,
                ),
                # The environment both the web service and the sweep read, so the
                # tenant and roster cannot drift between them. One rendered file
                # per stage, referenced by both units through EnvironmentFile=;
                # root-only, since systemd reads it as root at unit start.
                TemplatedUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-shared.env",
                    resource_loc="assets/rosadmin-shared.env",
                    dest="/etc/rosadmin/service/{stage}/shared.env",
                    env=Environment(names=frozenset(ServiceEnv)),
                    file_mode=FilePermissions.GroupConfig,
                    # This is the same directory the credentials land in; if
                    # systemd provisioning creates it before creds provisioning
                    # does, keep it off world-listable rather than the 0755 default.
                    dir_mode=FilePermissions.GroupDir,
                    per_stage=True,
                ),
                # The reconcile sweep: a oneshot fired by a 4-hourly timer. The
                # timer file installs here but is enabled on the box by the head
                # cheerleader (admin), like the web service's activation.
                StaticUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-sync@.service",
                    dest="/etc/systemd/system/rosadmin-sync@.service",
                ),
                StaticUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-sync@.timer",
                    dest="/etc/systemd/system/rosadmin-sync@.timer",
                ),
                TemplatedUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-sync@{stage}.service.d/override.conf",
                    resource_loc="assets/rosadmin-sync@{stage}.service.d/override.conf",
                    dest="/etc/systemd/system/rosadmin-sync@{stage}.service.d/override.conf",
                    env=Environment(names=frozenset(ServiceEnv)),
                    per_stage=True,
                ),
            ],
            # rosadmin's own cluster on 5433. Table grants live in the schema
            # migrations (granted to the rosadmin_app group role that each stage's
            # login role joins), so this declares only the structure: the group, the
            # migration and runtime roles, and the database the migration role owns.
            db=Db(
                port=5433,
                group_role=Role(name="rosadmin_app"),
                roles=[
                    Role(
                        name="rosadmin_{stage}_migrate",
                        login=True,
                        password=ServiceSecrets.DbMigrationPassword,
                    ),
                    Role(
                        name="rosadmin_{stage}_app",
                        login=True,
                        member_of="rosadmin_app",
                    ),
                ],
                databases=[
                    db.Database(
                        name="rosadmin_{stage}", owner="rosadmin_{stage}_migrate"
                    )
                ],
            ),
            # No restart on purpose: activation restarts the service through
            # the deploy wrapper, and a fresh box has no release to start yet.
            # Unit-file changes are applied by the head cheerleader (admin).
        ),
        # STOPGAP (2026-07-10): production runs the reconcile sweep alone - there
        # is no production web service yet. The sweep is split into its own
        # component so it can go live on production without dragging the staging
        # web units, the botonio SSO config, or the staging-only mock env onto the
        # box. This deliberately duplicates the sync unit files the staging
        # `service` component also installs; the bytes are identical, so a box
        # that runs both stages just installs them twice.
        #
        # When the production web service lands, fold this back into `service`:
        # extract the keys both units share (the Google subject, the Solidarity
        # Tech source, the database name) into a rendered shared.env like staging,
        # move migration back to the web unit's ExecStartPre, give `service` both
        # stages, and delete this component. The production sweep runbook has the
        # activation steps in the meantime.
        Component(
            name="sync",
            stages=frozenset({Stages.Production}),
            secrets=Secret(
                # The sweep writes audit rows, mirrors to Google, migrates the
                # schema (no web service does it here), and reads the real
                # Solidarity Tech roster. No SSO bearer: it authenticates no users.
                names=frozenset(
                    {
                        ServiceSecrets.AuditHmacKey,
                        ServiceSecrets.DbMigrationPassword,
                        ServiceSecrets.GoogleDwdKey,
                        ServiceSecrets.SolidarityTechToken,
                    }
                ),
                src="{repo_root}/secrets/rosadmin/{stage}.enc.yaml",
            ),
            units=[
                StaticUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-sync@.service",
                    dest="/etc/systemd/system/rosadmin-sync@.service",
                ),
                StaticUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-sync@.timer",
                    dest="/etc/systemd/system/rosadmin-sync@.timer",
                ),
                TemplatedUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-sync@{stage}.service.d/override.conf",
                    resource_loc="assets/rosadmin-sync@{stage}.service.d/override.conf",
                    dest="/etc/systemd/system/rosadmin-sync@{stage}.service.d/override.conf",
                    # Only the org-address values are rendered from the secrets;
                    # the rest of the override is stage-fixed literals (the domain,
                    # the database name, the request budget) safe to commit.
                    env=Environment(
                        names=frozenset(
                            {
                                ServiceEnv.GoogleDwdSubject,
                                ServiceEnv.MainGroupEmail,
                                ServiceEnv.MainGroupName,
                            }
                        )
                    ),
                    per_stage=True,
                ),
            ],
            # The production database on the same 5433 cluster staging shares. The
            # group role and role/database shapes mirror staging with `{stage}`
            # rendering to `production`; grants live in the schema migrations the
            # sweep applies on its first run. Idempotent: the group role already
            # exists from staging provisioning and is left untouched.
            db=Db(
                port=5433,
                group_role=Role(name="rosadmin_app"),
                roles=[
                    Role(
                        name="rosadmin_{stage}_migrate",
                        login=True,
                        password=ServiceSecrets.DbMigrationPassword,
                    ),
                    Role(
                        name="rosadmin_{stage}_app",
                        login=True,
                        member_of="rosadmin_app",
                    ),
                ],
                databases=[
                    db.Database(
                        name="rosadmin_{stage}", owner="rosadmin_{stage}_migrate"
                    )
                ],
            ),
        ),
    ],
)

FRONTEND = DeploySpec(
    root="rosadmin-frontend",
    package="rosadmin_deploy",
    stages=frozenset({Stages.Staging}),
    components=[
        # The static site needs no provisioned box material yet: its tree
        # ships through CI and its wrapper is installed with the other
        # root-owned trust anchors. The spec exists so the name and root are
        # settled before material arrives.
        Component(name="site"),
    ],
)

INGRESS = DeploySpec(
    root="caddy",
    package="rosadmin_deploy",
    stages=frozenset({Stages.Staging}),
    components=[
        Component(
            name="ingress",
            units=[
                StaticUnit(
                    src="{repo_root}/deploy/caddy/caddy.service",
                    dest="/etc/systemd/system/caddy.service",
                ),
                StaticUnit(
                    src="{repo_root}/deploy/caddy/Caddyfile",
                    dest="/etc/caddy/Caddyfile",
                ),
                StaticUnit(
                    src="{repo_root}/deploy/caddy/sites/{stage}.caddy",
                    dest="/etc/caddy/sites/{stage}.caddy",
                    per_stage=True,
                ),
            ],
            # The staged tree holds the whole /etc/caddy ensemble, so the
            # root Caddyfile's relative site import validates the exact bytes
            # about to be installed.
            check=[
                Check(
                    command=(
                        "/usr/bin/caddy",
                        "validate",
                        "--config",
                        "{file}",
                        "--adapter",
                        "caddyfile",
                    ),
                    target="/etc/caddy/Caddyfile",
                )
            ],
            # admin off means restart is the only reload path.
            restart=SharedRestart("caddy.service"),
        ),
    ],
)

main = che_deploya.build_cli(
    {"rosadmin": ROSADMIN, "frontend": FRONTEND, "ingress": INGRESS}
)
