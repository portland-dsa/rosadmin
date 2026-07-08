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

  Scenario: Susie expires a member and the served record decodes as lapsed
    Given a controllable mock with roster "kris@example.com=good_standing"
    When Susie expires "kris@example.com"
    Then the control response is 200
    And "kris@example.com" decodes with standing "lapsed"

  Scenario: Ralsei unexpires a lapsed member back to good standing
    Given a controllable mock with roster "kris@example.com=lapsed"
    When Ralsei restores "kris@example.com" to good standing
    Then the control response is 200
    And "kris@example.com" decodes with standing "good_standing"

  Scenario: An unrecognized standing is refused
    Given a controllable mock with roster "kris@example.com=good_standing"
    When Spamton sets "kris@example.com" to an unrecognized standing
    Then the control response is 400

  Scenario: Ralsei grants a leadership body and the flag comes with it
    Given a controllable mock with roster "kris@example.com=good_standing"
    When Ralsei grants "kris@example.com" the "committee-leadership" body "Steering"
    Then the control response is 200
    And "kris@example.com" decodes as a leader of "Steering"
    And "kris@example.com" decodes with the chapter-leader flag set

  Scenario: Susie revokes the last body and the flag clears with it
    Given a controllable mock with roster "kris@example.com=good_standing"
    And Ralsei grants "kris@example.com" the "committee-leadership" body "Steering"
    When Susie revokes "kris@example.com" from the "committee-leadership" body "Steering"
    Then the control response is 200
    And "kris@example.com" decodes as leading no bodies
    And "kris@example.com" decodes with the chapter-leader flag clear

  Scenario: An unrecognized leadership field is refused
    Given a controllable mock with roster "kris@example.com=good_standing"
    When Spamton grants "kris@example.com" an unrecognized leadership field
    Then the control response is 400

  Scenario: Granting a body to an unknown member is refused
    Given a controllable mock with roster "kris@example.com=good_standing"
    When Ralsei grants "berdly@example.com" the "committee-leadership" body "Steering"
    Then the control response is 404

  Scenario: A persona swap keeps a mapped Discord id
    Given a controllable mock with roster "zoopgoop@example.com=leader:123456789012345678"
    When Susie sets "zoopgoop@example.com" to persona "lapsed"
    Then the control response is 200
    And "zoopgoop@example.com" decodes with the discord id 123456789012345678

  Scenario: Ralsei stamps a real Discord id through the live upsert
    Given a controllable mock with roster "kris@example.com=good_standing"
    When Ralsei sets "kris@example.com" to persona "leader" with the discord id 123456789012345678
    Then the control response is 200
    And "kris@example.com" decodes with the discord id 123456789012345678

  Scenario: Spamton's non-numeric Discord id is refused
    Given a controllable mock with roster "kris@example.com=good_standing"
    When Ralsei sets "kris@example.com" to persona "leader" with the discord id kromer
    Then the control response is 400
