@roster_pull @db
Feature: The roster pull materializes Solidarity Tech into Postgres

  `pull_roster` reads a persona roster served in-process and writes it into
  the real members, leadership_bodies, and body_memberships tables. A re-pull
  converges rather than duplicates, and a leader who steps down loses only
  their own leader row - a co-leader on the same body keeps theirs.

  Scenario: A fresh pull is reflected in the members, bodies, and leader rows
    Given the persona roster "ralsei@example.com=leader,kris@example.com=good_standing"
    When Ralsei pulls the roster
    Then the members table holds 2 members
    And the leadership_bodies table holds 1 body
    And "ralsei@example.com" leads "Steering"
    And "kris@example.com" leads no bodies
    And the pull touched 2 members
    And the pull touched 1 leadership body
    And the pull touched 1 leader row

  Scenario: A re-pull changes nothing
    Given the persona roster "ralsei@example.com=leader,kris@example.com=good_standing"
    When Ralsei pulls the roster
    And Ralsei pulls the roster
    Then the members table holds 2 members
    And the leadership_bodies table holds 1 body
    And "ralsei@example.com" leads "Steering"

  Scenario: A leader who steps down loses that leader row; a co-leader's is untouched
    Given the persona roster "ralsei@example.com=leader,noelle@example.com=co_leader"
    When Ralsei pulls the roster
    Given the persona roster "ralsei@example.com=good_standing,noelle@example.com=co_leader"
    When Ralsei pulls the roster
    Then "ralsei@example.com" leads no bodies
    And "noelle@example.com" leads "Steering"
    And the leadership_bodies table holds 1 body

  Scenario: A leader absent from a later pull keeps their leader row
    Given the persona roster "noelle@example.com=co_leader,kris@example.com=leader"
    When Ralsei pulls the roster
    Given the persona roster "noelle@example.com=co_leader"
    When Ralsei pulls the roster
    Then "kris@example.com" leads "Steering"
    And "noelle@example.com" leads "Steering"

  Scenario: A marked-no-body persona is stored EmptyLeader and surfaces as an anomaly
    Given the persona roster "spamton@example.com=marked_no_body"
    When Ralsei pulls the roster
    Then "spamton@example.com" is stored as EmptyLeader
    And the pull flags "spamton@example.com" as an anomaly
