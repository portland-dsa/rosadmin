Feature: Audit durability and observability

  Every audited action is written to Postgres (the record of truth) and mirrored
  to journald (the observable stream). journald's mere absence is tolerated, but
  a journald that is present and fails is surfaced, and a database failure is
  never hidden.

  Scenario Outline: Recording an audit event as the database and journald vary
    Given the audit database is <database>
    And the journald socket is <journald>
    When Ralsei's action is recorded to the audit log
    Then the audit database write <db_write>
    And the journald mirror <journald_write>
    And the recording call <call>

    Examples: the database is healthy
      | database | journald               | db_write | journald_write | call                    |
      | up       | present                | happens  | happens        | succeeds                |
      | up       | understandably missing | happens  | is skipped     | succeeds                |
      | up       | incorrectly missing    | happens  | is refused     | raises a journald error |

    Examples: the database is down
      | database | journald               | db_write | journald_write | call                    |
      | down     | present                | fails    | happens        | raises a database error |
      | down     | understandably missing | fails    | is skipped     | raises a database error |
      | down     | incorrectly missing    | fails    | is refused     | raises a compound error |
