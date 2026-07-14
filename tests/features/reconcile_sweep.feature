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

  Scenario: Google has no account for Spamton, and the sweep stops offering him
    Given a member "spamton@example.net" in standing "good_standing"
    And Google has no account for "spamton@example.net"
    When the sweep runs
    Then the group "everyone@example.net" does not contain "spamton@example.net"
    And "spamton@example.net" is recorded unmirrorable for reason "no_google_account"
    And the sweep run reports success
    When the sweep runs
    Then the sweep did not offer "spamton@example.net" to "everyone@example.net"

  Scenario: A member Google refuses is not evicted from a group that already holds them
    Given a member "spamton@example.net" in standing "good_standing"
    And Google has no account for "spamton@example.net"
    And "spamton@example.net" was refused 2 days ago
    And the group "everyone@example.net" already holds "spamton@example.net"
    When the sweep runs
    Then the group "everyone@example.net" contains "spamton@example.net"
    And the sweep run reports success

  Scenario: Ralsei makes a Google account and the lapsed refusal lets him back in
    Given a member "ralsei@example.net" in standing "good_standing"
    And "ralsei@example.net" was refused 100 days ago
    When the sweep runs
    Then the group "everyone@example.net" contains "ralsei@example.net"

  Scenario: Google is authoritative for Spamton's address and does not have it
    Given a member "spamton@example.net" in standing "good_standing"
    And the group "everyone@example.net" already exists
    And Google does not have the address "spamton@example.net"
    When the sweep runs
    Then "spamton@example.net" is recorded unmirrorable for reason "address_not_found"
    And the sweep run reports success

  Scenario: Susie deletes a group behind the sweep's back, and its members are not blamed
    Given a member "ralsei@example.net" in standing "good_standing"
    And the group "everyone@example.net" already exists
    And Susie has deleted the group "everyone@example.net" at Google
    When the sweep runs
    Then no address is recorded unmirrorable
    And the sweep run reports failure

  Scenario: The first run writes down the whole standing cohort, however large
    Given 30 members in good standing Google has no account for
    When the sweep runs
    Then 30 addresses are recorded unmirrorable
    And the sweep run reports success

  Scenario: Once armed, a sudden flood of refusals is refused wholesale
    Given refusal learning has already bootstrapped
    And 30 members in good standing Google has no account for
    When the sweep runs
    Then no address is recorded unmirrorable
    And the sweep run reports failure
    When Ralsei fixes Google and the sweep runs again
    Then the group "everyone@example.net" contains "refused0@example.net"

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

  Scenario: Ralsei's sweep provisions an unlinked body's groups and populates them
    Given an unlinked body "Cyber World" of type "Working Group"
    And a member "ralsei@example.net" in standing "good_standing"
    And "ralsei@example.net" holds a "leader" row on "Cyber World"
    When the sweep runs with provisioning
    Then the group "cyber-world-working-group-leaders@example.net" exists
    And the body "Cyber World" is linked to that leaders group
    And the group "cyber-world-working-group-leaders@example.net" contains "ralsei@example.net"

  Scenario: A second provisioning run creates nothing new
    Given an unlinked body "Cyber World" of type "Working Group"
    When the sweep runs with provisioning
    And the sweep runs with provisioning
    Then the provisioning report created 0 groups on the last run

  Scenario: A dry-run provisioning run writes nothing
    Given an unlinked body "Cyber World" of type "Working Group"
    When the sweep runs with provisioning in dry-run mode
    Then the body "Cyber World" is still unlinked
    And the group-provisioning bootstrap marker is still unset

  Scenario: Lancer's pre-existing group with slack settings is adopted, not overwritten
    Given an unlinked body "Card Castle" of type "Committee"
    And the group "card-castle-committee-leaders@example.net" already exists with slack settings
    When the sweep runs with provisioning
    Then the body "Card Castle" is linked to that leaders group
    And the provisioning report warned of divergence

  Scenario: The tripwire refuses a mass creation once armed
    Given provisioning has already bootstrapped
    And 8 unlinked bodies of type "Working Group"
    And the mass-creation tripwire is 5
    When the sweep runs with provisioning
    Then the provisioning report refused the creation
    And the sweep run reports failure
