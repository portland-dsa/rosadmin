@admin_control @db
Feature: The operator admin socket

  The admin app carries no session machinery of its own - in production, only
  possession of its private unix socket authorizes a call. These scenarios
  reach it in-process, exercising the same routes a systemd-owned socket would
  serve: body linking (composing with the leader panel's write endpoints),
  the pull trigger, and - only when the configured source is the mock - the
  persona relay.

  Scenario: Linking a body through the admin socket makes the panel's mirror call target it
    Given Ralsei leads the unlinked body "Steering"
    And a good-standing member "kris@example.org"
    When Ralsei links "Steering" through the admin socket with leader group "steering-leaders@example.org" and member group "steering-members@example.org"
    Then the admin response is 204
    When Ralsei adds "kris@example.org" to the body "Steering"
    Then the response is 201
    And the sync recorded an "add" on "Steering"'s member group for "kris@example.org" with outcome "skipped_dry_run"

  Scenario: Unlinking a body through the admin socket clears both group emails
    Given Ralsei leads the linked body "Castle Town"
    When Ralsei unlinks "Castle Town" through the admin socket
    Then the admin response is 204
    And the body "Castle Town" is unlinked

  Scenario: Linking a body that does not exist is refused
    When Ralsei links a nonexistent body through the admin socket with leader group "a@example.org" and member group "b@example.org"
    Then the admin response is 404

  Scenario: Triggering a pull through the admin socket materializes the mock roster
    Given the admin socket is pointed at a mock roster "ralsei@example.com=leader,noelle@example.com=good_standing"
    When Ralsei triggers a pull through the admin socket
    Then the admin response is 200
    And the members table holds 2 members
    And "ralsei@example.com" leads "Steering"

  Scenario: Expiring a persona through the relay is reflected on the next pull
    Given the admin socket is pointed at a mock roster "noelle@example.com=good_standing"
    When Ralsei triggers a pull through the admin socket
    And Ralsei expires "noelle@example.com" through the persona relay
    And Ralsei triggers a pull through the admin socket
    Then "noelle@example.com" has standing "lapsed"

  Scenario: The persona relay is absent when the admin socket has no mock configured
    When Ralsei expires "noelle@example.com" through the persona relay
    Then the admin response is 404
