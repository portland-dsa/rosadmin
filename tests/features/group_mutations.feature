@group_mutations @db
Feature: Leader add/remove member endpoints with audit and Google mirror

  Adding or removing a member from a leadership body: a leader-scoped claim
  transaction against Postgres, then a best-effort Google mirror call outside
  it, then an audit row. Ralsei leads the linked body "Steering" (and the
  unlinked "Fun Gang Reunion"); Susie, Noelle, and Spamton also lead "Steering", so
  Susie can remove members there and Spamton can post a request against a
  body he really leads.

  Background:
    Given Ralsei leads the linked body "Steering"
    And Ralsei leads the unlinked body "Fun Gang Reunion"
    And Susie leads "Steering"
    And Noelle is a leader of "Steering"
    And Spamton leads "Steering"
    And a good-standing member "kris@example.org"
    And a lapsed member "berdly@example.org"

  Scenario: Ralsei adds a good-standing member to his linked body
    When Ralsei adds "kris@example.org" to the body "Steering"
    Then the response is 201
    And "kris@example.org" is a member of "Steering" added by Ralsei
    And the sync recorded an "add" on "Steering"'s member group for "kris@example.org" with outcome "skipped_dry_run"

  Scenario: The Google mirror targets the alternate address when the primary is not a gmail
    Given "kris@example.org" has the alternate email "kris@gmail.com"
    When Ralsei adds "kris@example.org" to the body "Steering"
    Then the sync recorded an "add" on "Steering"'s member group for "kris@gmail.com" with outcome "skipped_dry_run"

  Scenario: Adding to an unlinked body still creates the row
    When Ralsei adds "kris@example.org" to the body "Fun Gang Reunion"
    Then the response is 201
    And "kris@example.org" is a member of "Fun Gang Reunion" added by Ralsei
    And the sync recorded an "add" on "Fun Gang Reunion"'s member group for "kris@example.org" with outcome "skipped_unlinked"
    And the mutation audit for "group.member_added" records "skipped_unlinked"

  Scenario: An unusable example-domain address is skipped and recorded
    Given a good-standing member "spamton@example.com"
    When Ralsei adds "spamton@example.com" to the body "Steering"
    Then the response is 201
    And the mutation audit for "group.member_added" records "skipped_example_email"

  Scenario: An example-domain record never redirects to its gmail alternate
    Given a good-standing member "spamton@example.com"
    And "spamton@example.com" has the alternate email "spamton.big.shot@gmail.com"
    When Ralsei adds "spamton@example.com" to the body "Steering"
    Then the response is 201
    And the mutation audit for "group.member_added" records "skipped_example_email"

  Scenario: Adding the same member twice is refused the second time
    When Ralsei adds "kris@example.org" to the body "Steering"
    And Ralsei adds "kris@example.org" to the body "Steering"
    Then the response is 409 "already_member"
    And the sync recorded exactly 1 call

  Scenario: Susie removes a member
    Given "kris@example.org" is a member of "Steering"
    When Susie removes "kris@example.org" from the body "Steering"
    Then the response is 204
    And "kris@example.org" is not a member of "Steering"
    And the mutation audit records "group.member_removed"

  Scenario Outline: A refused write changes nothing
    When <actor> tries to <operation> "<target>" in the body "<body>"
    Then the response is <status> "<problem>"
    And that body's membership rows are unchanged
    And the sync recorded exactly 0 calls

    Examples:
      | actor   | operation | target             | body             | status | problem              |
      | Ralsei  | add       | berdly@example.org | Steering         | 409    | member_not_eligible  |
      | Ralsei  | add       | Noelle             | Steering         | 409    | already_leader       |
      | Susie   | remove    | Noelle             | Steering         | 403    | leader_not_removable |
      | Susie   | remove    | kris@example.org   | Steering         | 404    | not_a_member         |
      | Susie   | add       | kris@example.org   | Fun Gang Reunion | 404    | not_found            |
      | Susie   | remove    | kris@example.org   | Fun Gang Reunion | 404    | not_found            |
      | Spamton | add       | not-a-uuid         | Steering         | 422    | invalid_request      |
