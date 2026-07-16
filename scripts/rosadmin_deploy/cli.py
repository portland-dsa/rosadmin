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

    Not every unit loads all five: staging skips the Solidarity Tech token (it
    reads the in-process mock, which ignores auth), so the `service` component
    excludes it there; the web override loads the SSO bearer but the sweep override
    does not (the sweep authenticates no users), while the sweep loads the token and
    the web override does not. Each unit's override names the subset it loads.
    """

    AuditHmacKey = "audit-hmac-key"
    DbMigrationPassword = "db_migration_password"
    BotonioSsoBearer = "botonio_sso_bearer"
    GoogleDwdKey = "google-dwd-key"
    #: The real Solidarity Tech bearer token. Production-only: the staging mock
    #: needs none, so only the production sweep loads it.
    SolidarityTechToken = "solidarity_tech_token"
    #: The Backblaze B2 application key for the write-once backup bucket.
    #: Production-only, and loaded only by the backup unit.
    B2BackupApplicationKey = "b2_backup_application_key"


class ServiceEnv(StrEnum):
    """The non-secret values rendered into the unit overrides and shared.env as
    `Environment=` lines, filled from the encrypted secrets at render time.

    They live in the component's encrypted secrets file alongside the credentials
    but are public, so they render as `Environment=` lines rather than load as
    `.cred`s. Each member's value is both its key in that file and the `${...}`
    placeholder it fills in a template.
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
    #: The box's age recipient (public key) the backup encrypts each dump to before
    #: it leaves the box. Public, but box-specific, so rendered rather than committed.
    BoxAgePubkey = "box_age_pubkey"
    #: The write-once B2 bucket the backup uploads to, and the application key's id.
    #: Non-secret on their own - the application key credential is what authorizes.
    B2BucketName = "b2_bucket_name"
    B2BackupKeyId = "b2_backup_key_id"


ROSADMIN = DeploySpec(
    root="rosadmin",
    package="rosadmin_deploy",
    stages=frozenset(Stages),
    components=[
        Component(
            name="service",
            # Both stages, one component: the web panel and the reconcile sweep.
            # Staging and production install the same units and differ only in
            # rendered values and the one production-only credential - the real
            # Solidarity Tech token, which staging (reading the in-process mock)
            # does not carry.
            stages=frozenset(Stages),
            secrets=Secret(
                names=frozenset(
                    {
                        ServiceSecrets.AuditHmacKey,
                        ServiceSecrets.DbMigrationPassword,
                        ServiceSecrets.BotonioSsoBearer,
                        ServiceSecrets.GoogleDwdKey,
                        ServiceSecrets.SolidarityTechToken,
                    }
                ),
                # Staging reads the in-process mock, which needs no real token; only
                # the production sweep loads it.
                exclude={
                    Stages.Staging: frozenset({ServiceSecrets.SolidarityTechToken})
                },
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
                # The web override renders only the botonio verifying key and guild
                # id; the Google subject and roster source come from shared.env.
                TemplatedUnit(
                    src="{repo_root}/deploy/systemd/rosadmin@{stage}.service.d/override.conf",
                    resource_loc="assets/rosadmin@{stage}.service.d/override.conf",
                    dest="/etc/systemd/system/rosadmin@{stage}.service.d/override.conf",
                    env=Environment(
                        names=frozenset(
                            {ServiceEnv.BotonioSsoPubkey, ServiceEnv.SsoGuildId}
                        )
                    ),
                    per_stage=True,
                ),
                # The environment both the panel and the sweep read, so the tenant
                # and roster cannot drift between them. One template per stage
                # because staging reads the in-process mock and production the real
                # API - a structural difference, not just different values.
                # Referenced by both units through EnvironmentFile=; root-only,
                # since systemd reads it as root at unit start.
                TemplatedUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-shared.{stage}.env",
                    resource_loc="assets/rosadmin-shared.{stage}.env",
                    dest="/etc/rosadmin/service/{stage}/shared.env",
                    env=Environment(
                        names=frozenset(ServiceEnv),
                        # Production carries no mock persona map.
                        exclude={
                            Stages.Production: frozenset({ServiceEnv.StMockPersonas})
                        },
                    ),
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
                # The sweep override renders the main group's address and name; the
                # subject and roster source come from shared.env.
                TemplatedUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-sync@{stage}.service.d/override.conf",
                    resource_loc="assets/rosadmin-sync@{stage}.service.d/override.conf",
                    dest="/etc/systemd/system/rosadmin-sync@{stage}.service.d/override.conf",
                    env=Environment(
                        names=frozenset(
                            {ServiceEnv.MainGroupEmail, ServiceEnv.MainGroupName}
                        )
                    ),
                    per_stage=True,
                ),
            ],
            # rosadmin's own cluster on 5433. Table grants live in the schema
            # migrations (granted to the rosadmin_app group role that each stage's
            # login role joins), so this declares only the structure: the group, the
            # migration and runtime roles, and the database the migration role owns.
            # Idempotent per stage: production's roles and database already exist
            # from the sweep launch and are left untouched.
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
        Component(
            name="backup",
            # Production only: staging's database is the mock roster, so there is
            # nothing worth backing up there.
            stages=frozenset({Stages.Production}),
            secrets=Secret(
                names=frozenset({ServiceSecrets.B2BackupApplicationKey}),
                src="{repo_root}/secrets/rosadmin/{stage}.enc.yaml",
            ),
            units=[
                StaticUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-db-backup.timer",
                    dest="/etc/systemd/system/rosadmin-db-backup.timer",
                ),
                # The backup script itself is installed by hand at
                # /usr/local/sbin/rosadmin-db-backup, not by this tool.
                TemplatedUnit(
                    src="{repo_root}/deploy/systemd/rosadmin-db-backup.service.tmpl",
                    resource_loc="assets/rosadmin-db-backup.service",
                    dest="/etc/systemd/system/rosadmin-db-backup.service",
                    env=Environment(
                        names=frozenset(
                            {
                                ServiceEnv.BoxAgePubkey,
                                ServiceEnv.B2BucketName,
                                ServiceEnv.B2BackupKeyId,
                            }
                        )
                    ),
                ),
            ],
        ),
    ],
)

FRONTEND = DeploySpec(
    root="rosadmin-frontend",
    package="rosadmin_deploy",
    stages=frozenset(Stages),
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
    stages=frozenset(Stages),
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
