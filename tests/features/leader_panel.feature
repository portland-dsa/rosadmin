Feature: Leader panel walking skeleton
  The leader-facing surface end to end - sessions, search, membership changes,
  and error rendering - against the persona-backed stub behind fake-login.

  Background:
    Given the service is running with fake-login enabled
    And Ralsei is logged in as the leader

  Scenario: Logging in returns the overview and a session cookie
    Then the login response shows the display name "Ralsei Fluffington"
    And the login response lists 3 groups
    And the client holds a session cookie

  Scenario: Ralsei sees the groups he manages
    When Ralsei fetches his groups
    Then he manages 3 groups
    And one of them has the body type "Campaign"

  Scenario Outline: Searching an email names its membership state
    When Ralsei searches for "<email>"
    Then the search status is "<status>"

    Examples:
      | email               | status               |
      | ralsei@example.com  | good_standing        |
      | kris@example.com    | good_standing        |
      | susie@example.com   | dues_expired         |
      | berdly@example.com  | no_membership_status |
      | noelle@example.com  | malformed            |
      | spamton@example.com | malformed            |
      | nobody@example.com  | not_found            |

  Scenario: Only a good-standing search discloses a member record
    When Ralsei searches for "susie@example.com"
    Then the response carries no member record

  Scenario: Ralsei adds Kris to Dark World Research
    When Ralsei searches for "kris@example.com"
    And Ralsei adds the found member to "Dark World Research"
    Then "Dark World Research" lists "kris@example.com" as a member

  Scenario: Adding the same member twice is a conflict
    When Ralsei searches for "kris@example.com"
    And Ralsei adds the found member to "Dark World Research"
    And Ralsei adds the found member to "Dark World Research"
    Then the change is refused as a conflict

  Scenario: Ralsei removes Kris from a group
    When Ralsei searches for "kris@example.com"
    And Ralsei adds the found member to "Dark World Research"
    And Ralsei removes "kris@example.com" from "Dark World Research"
    Then "Dark World Research" no longer lists "kris@example.com"

  Scenario: Removing someone who is not in the group is refused
    When Ralsei removes "kris@example.com" from "Dark World Research"
    Then the removal is refused because they are not a member

  Scenario: Adding an unknown member is refused
    When Ralsei tries to add an unknown member to "Dark World Research"
    Then the add is refused because there is no such member
