@mock_control @solidarity_tech
Feature: The runnable mock's persona control endpoint

  The controllable mock holds its roster in a mutable store rather than the
  read-only mock's fixed list, so an operator can cycle a single test account
  through membership states with curl while a login flow is open against it -
  no restart required. The read-only mock the other suites inject never mounts
  this surface.

  Scenario: Ralsei sets a member's persona and the next read reflects it
    Given a controllable mock with roster "kris@example.com=good_standing"
    When Ralsei sets "kris@example.com" to persona "lapsed"
    Then the control response is 200
    And a read of "kris@example.com" shows persona "lapsed"

  Scenario: Ralsei adds a brand-new member and it is assigned the next id
    Given a controllable mock with roster "kris@example.com=good_standing"
    When Ralsei sets "berdly@example.com" to persona "no_status"
    Then the control response is 200
    And the control response is member "berdly@example.com" with id 2

  Scenario: Susie deletes a member and the read no longer lists them
    Given a controllable mock with roster "kris@example.com=good_standing,susie@example.com=lapsed"
    When Susie deletes "susie@example.com"
    Then the control response is 204
    And the roster no longer lists "susie@example.com"

  Scenario: Spamton's unknown persona name is refused
    Given a controllable mock with roster "kris@example.com=good_standing"
    When Spamton sets "kris@example.com" to persona "wrecked_beyond_repair"
    Then the control response is 400
    And a read of "kris@example.com" shows persona "good_standing"

  Scenario: The read-only mock never mounts the control surface
    Given a read-only mock with roster "kris@example.com=good_standing"
    When Ralsei sets "kris@example.com" to persona "lapsed"
    Then the control response is 404
