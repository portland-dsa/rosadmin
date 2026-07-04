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

import che_deploya
from che_deploya import (
    Check,
    Component,
    DeploySpec,
    SharedRestart,
    Stages,
    StaticUnit,
)

ROSADMIN = DeploySpec(
    root="rosadmin",
    package="rosadmin_deploy",
    stages=frozenset({Stages.Staging}),
    components=[
        Component(
            name="service",
            units=[
                StaticUnit(
                    src="{repo_root}/deploy/systemd/rosadmin@.service",
                    dest="/etc/systemd/system/rosadmin@.service",
                ),
                StaticUnit(
                    src="{repo_root}/deploy/systemd/rosadmin@.socket",
                    dest="/etc/systemd/system/rosadmin@.socket",
                ),
                StaticUnit(
                    src="{repo_root}/deploy/systemd/rosadmin@{stage}.service.d/override.conf",
                    resource_loc="assets/rosadmin@{stage}.service.d/override.conf",
                    dest="/etc/systemd/system/rosadmin@{stage}.service.d/override.conf",
                    per_stage=True,
                ),
            ],
            # No restart on purpose: activation restarts the service through
            # the deploy wrapper, and a fresh box has no release to start yet.
            # Unit-file changes are applied by the head cheerleader (admin).
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
