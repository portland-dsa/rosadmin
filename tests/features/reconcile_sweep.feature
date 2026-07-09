@reconcile_sweep @db
Feature: The reconcile sweep converges Google Groups to the records

  `run_sweep` diffs each linked group and the main group against a fake
  Workspace and applies adds before removes through the real gate logic.
  Nothing here touches Google: the Workspace is an in-memory fake whose
  state the scenarios inspect afterward.

  Background:
    Given the main group is "everyone@example.net"
    And the body "Steering" is linked to "steering-leaders@example.net" and "steering-members@example.net"

  Scenario: Ralsei renews a lapsed member and the sweep restores them
    Given a member "kris@example.net" in standing "lapsed"
    When the sweep runs
    Then the group "everyone@example.net" does not contain "kris@example.net"
    When Ralsei sets "kris@example.net" to standing "good_standing"
    And the sweep runs
    Then the group "everyone@example.net" contains "kris@example.net"
    And an audit row records action "sweep_member_added"

  Scenario: Susie lapses and is removed from every group, leader rows and manual adds included
    Given a member "susie@example.net" in standing "good_standing"
    And "susie@example.net" holds a "leader" row on "Steering"
    And a member "kris@example.net" in standing "good_standing"
    And "kris@example.net" holds a "member" row on "Steering"
    When the sweep runs
    Then the group "steering-leaders@example.net" contains "susie@example.net"
    And the group "steering-members@example.net" contains "kris@example.net"
    When Susie sets "susie@example.net" to standing "lapsed"
    And Susie sets "kris@example.net" to standing "lapsed"
    And the sweep runs
    Then the group "steering-leaders@example.net" does not contain "susie@example.net"
    And the group "steering-members@example.net" does not contain "kris@example.net"
    And the group "everyone@example.net" does not contain "susie@example.net"
    And an audit row records action "sweep_member_removed"

  Scenario: A stranded old address is cleared and the current one takes its place
    Given a member "kris@example.net" in standing "good_standing"
    And the group "everyone@example.net" already holds "old-kris@example.net"
    When the sweep runs
    Then the group "everyone@example.net" contains "kris@example.net"
    And the group "everyone@example.net" does not contain "old-kris@example.net"

  Scenario: Spamton's unusable record never reaches the Workspace
    Given a member "spamton@example.com" in standing "good_standing"
    When the sweep runs
    Then the group "everyone@example.net" does not contain "spamton@example.com"

  Scenario: A group owner and a nested group survive the sweep
    Given the group "everyone@example.net" already holds "noelle@example.net" as a "OWNER"
    And the group "everyone@example.net" already holds "lightners@example.net" as a nested group
    When the sweep runs
    Then the group "everyone@example.net" contains "noelle@example.net"
    And the group "everyone@example.net" contains "lightners@example.net"

  Scenario: A dry-run computes the diff and applies nothing
    Given a member "kris@example.net" in standing "good_standing"
    When the sweep runs in dry-run mode
    Then the group "everyone@example.net" does not contain "kris@example.net"
    And the dry-run recorded a planned add to "everyone@example.net"

  Scenario: The fuse refuses a mass removal but still applies adds
    Given a member "kris@example.net" in standing "good_standing"
    And the group "everyone@example.net" already holds 20 seeded members
    When the sweep runs
    Then the sweep report marks "everyone@example.net" as refused
    And the group "everyone@example.net" still holds 20 seeded members
    And the group "everyone@example.net" contains "kris@example.net"
    And the sweep run reports failure
