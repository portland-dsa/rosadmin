@login_gate @db
Feature: The records gate on the botonio login callback

  botonio's assertion proves Discord membership in good standing; it says
  nothing about chapter leadership. The callback's second half looks the
  verified member up in the records cache and admits only a stored `Leader`
  verdict - every other outcome, including absence from the cache, is
  refused the same reasonless way, and every outcome is audited.

  Scenario: Ralsei, a stored leader, logs in and reaches the app
    Given a member stored as Leader
    When Ralsei begins and returns from the login against records
    Then a session cookie is set
    And the login audit records the login

  Scenario: Susie, a good-standing non-leader, is denied
    Given a member stored as NonLeader
    When Susie begins and returns from the login against records
    Then the login is denied with no reason
    And a denial audit row is recorded

  Scenario: Spamton, a stored anomaly, is denied and warned about
    Given a member stored as UnmarkedLeader
    When Spamton begins and returns from the login against records
    Then the login is denied with no reason
    And a denial audit row is recorded
    And the operator warning is logged

  Scenario: Noelle, absent from the records cache, is denied
    Given no member is stored for the login
    When Noelle begins and returns from the login against records
    Then the login is denied with no reason
    And a denial audit row is recorded

  Scenario: Susie's spent assertion cannot be replayed
    Given a member stored as Leader
    When Susie logs in against records then replays the spent callback
    Then a session cookie is set
    And the replay is refused with no new session
    And a denial audit row is recorded
