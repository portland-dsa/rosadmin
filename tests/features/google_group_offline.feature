@google @offline
Feature: Ralsei mends and Susie breaks Google groups against mocked APIs

  The three Google APIs are mocked with a single HttpMockSequence, so the
  eventual-consistency polling in GoogleGroup is exercised offline. A
  not-found-then-found sequence drives the tenacity retry without a live API.

  Scenario: Building a group waits out eventual consistency
    Given the Google APIs accept a new group after one consistency retry
    When Ralsei builds the "kris-test@example.com" group
    Then the built group has id "g1"

  Scenario: Hydrating an existing group from the remote
    Given the Google APIs describe an existing group with id "g2" and name "Existing"
    When Ralsei hydrates "existing@example.com"
    Then the hydrated group has id "g2" and name "Existing"

  Scenario: Adding a member waits until membership is visible
    Given the Google APIs confirm a member after one visibility retry
    When Ralsei adds "noelle@example.com" to the group
    Then the operation succeeds

  Scenario: Deleting a group waits until it is gone
    Given the Google APIs report the group gone after one deletion retry
    When Susie deletes the group
    Then the operation succeeds
