@records_directory @db
Feature: Records-backed reads over live HTTP

  The leader-facing read routes served by `RecordsDirectory`, exercised end
  to end against a real Postgres seeded by a roster pull. Login here is a
  session minted directly for a seeded member's real Discord id, not
  fake-login - the real login gate lands separately.

  Background:
    Given the persona roster "ralsei@example.com=leader,kris@example.com=good_standing,susie@example.com=lapsed"
    When Ralsei pulls the roster
    And Ralsei is logged in against records as "ralsei@example.com"

  Scenario: A leader's groups carry only the bodies they lead, each with its leaders
    When Ralsei fetches his groups from records
    Then records lists 1 group
    And one of the returned groups is "Steering" led by "Ralsei Fluffington"

  Scenario Outline: Searching an email against records names its membership state
    When Ralsei searches records for "<email>"
    Then the records search status is "<status>"

    Examples:
      | email               | status        |
      | kris@example.com    | good_standing |
      | susie@example.com   | dues_expired  |
      | nobody@example.com  | not_found     |

  Scenario: A mutation route answers 501 in the deployed shape while mutations are unwired
    When a client attempts to add a member without mutations wired
    Then the mutation is refused because mutations are not available
