"""The rosadmin deploy spec: what the shared box installs for the service.

There are no secrets or database yet - staging answers
`/healthz` and needs neither - so the component is just the two systemd unit
files.
"""

from che_deploya import (
    DeploySpec,
    Component,
    StaticUnit,
    Stages,
)
import che_deploya

SPEC = DeploySpec(
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
                    src="{repo_root}/deploy/systemd/rosadmin@{stage}.service.d/override.conf",
                    resource_loc="assets/rosadmin@{stage}.service.d/override.conf",
                    dest="/etc/systemd/system/rosadmin@{stage}.service.d/override.conf",
                    per_stage=True,
                ),
            ],
        ),
    ],
)

main = che_deploya.build_cli(SPEC)
