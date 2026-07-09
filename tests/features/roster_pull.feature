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

  Scenario: A manually added member survives a leadership reshape
    Given the persona roster "ralsei@example.com=leader,kris@example.com=good_standing"
    When Ralsei pulls the roster
    And Ralsei manually adds "kris@example.com" to "Steering"
    Given the persona roster "ralsei@example.com=good_standing,kris@example.com=good_standing,noelle@example.com=leader"
    When Ralsei pulls the roster
    Then "kris@example.com" is still a member of "Steering"

  Scenario: Deleting the adding leader's record clears attribution but the manual add survives
    Given the persona roster "ralsei@example.com=leader,kris@example.com=good_standing"
    When Ralsei pulls the roster
    And Ralsei manually adds "kris@example.com" to "Steering"
    When Susie deletes "ralsei@example.com"'s member record outright
    Then "kris@example.com" is still a member of "Steering"
    And the manual-add attribution for "kris@example.com" in "Steering" is cleared but the timestamp remains

  Scenario: The records naming a manual member a leader promotes the row
    Given the persona roster "ralsei@example.com=leader,kris@example.com=good_standing"
    When Ralsei pulls the roster
    And Ralsei manually adds "kris@example.com" to "Steering"
    Given the persona roster "ralsei@example.com=leader,kris@example.com=leader"
    When Ralsei pulls the roster
    Then "kris@example.com" leads "Steering"
    And the promoted row for "kris@example.com" in "Steering" carries no manual-add provenance

  Scenario: A member who vanishes from the records lapses
    Given the persona roster "ralsei@example.com=leader,kris@example.com=good_standing"
    When Ralsei pulls the roster
    Given the persona roster "ralsei@example.com=leader"
    When Ralsei pulls the roster
    Then "kris@example.com" is stored with standing "lapsed"
    And the pull lapsed 1 absent member

  Scenario: A vanished member who returns is restored to good standing
    Given the persona roster "ralsei@example.com=leader,kris@example.com=good_standing"
    When Ralsei pulls the roster
    Given the persona roster "ralsei@example.com=leader"
    When Ralsei pulls the roster
    Given the persona roster "ralsei@example.com=leader,kris@example.com=good_standing"
    When Ralsei pulls the roster
    Then "kris@example.com" is stored with standing "good_standing"

  Scenario: An empty upstream refuses to lapse the whole roster
    Given the persona roster "ralsei@example.com=leader,kris@example.com=good_standing,susie@example.com=good_standing,noelle@example.com=good_standing,berdly@example.com=good_standing,spamton@example.com=good_standing"
    When Ralsei pulls the roster
    Given an empty persona roster
    When Ralsei pulls the roster
    Then every member is still in good standing
    And the pull refused to lapse 6 members
