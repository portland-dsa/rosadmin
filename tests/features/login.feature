Feature: botonio SSO login relay

  Scenario: Ralsei completes a login and gets a session
    Given botonio will complete with a member assertion for Ralsei
    When Ralsei begins and returns from the login
    Then a session cookie is set
    And the login audit records the Discord id

  Scenario: Susie replays a spent assertion and is refused
    Given botonio will complete with a member assertion for Ralsei
    When Ralsei begins and returns from the login
    And the same callback is replayed
    Then the replay is refused with no new session

  Scenario: Spamton returns a malformed assertion
    Given botonio will complete with a malformed assertion
    When Ralsei begins and returns from the login
    Then the login fails with no session

  Scenario: botonio signs under a key rosadmin does not pin
    Given botonio will complete with an assertion signed under an unpinned key
    When Ralsei begins and returns from the login
    Then the login fails with no session

  Scenario: a returning member with lapsed dues is denied by standing
    Given botonio will complete with a dues_expired assertion for Ralsei
    When Ralsei begins and returns from the login
    Then the login is denied with reason "dues_expired"
