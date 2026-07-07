Feature: botonio SSO login relay

  These scenarios exercise the SSO relay outcomes that never reach the records
  gate: a verification failure, and a denial at the standing check. The gate's
  own outcomes - a stored leader admitted, everyone else denied - live in
  login_gate.feature, which needs a real records database.

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
    Then the login is denied with no reason
