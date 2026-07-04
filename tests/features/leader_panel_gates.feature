Feature: Leader panel gates
  The session and deployment-flag gates: no session is refused, bad personas
  cannot log in, and the fake-login surface is absent unless its flag is set.

  Scenario: An unauthenticated request is refused
    Given the service is running with fake-login enabled
    When an unauthenticated client requests the session overview
    Then the request is refused as unauthenticated

  Scenario: An unknown persona cannot log in
    Given the service is running with fake-login enabled
    When a client attempts fake-login as "nobody"
    Then the login is refused as an unknown persona

  Scenario: A non-leader persona cannot log in
    Given the service is running with fake-login enabled
    When a client attempts fake-login as "good_standing"
    Then the login is refused because the persona is not a chapter leader

  Scenario: Fake-login is absent when its flag is off
    Given the service is running with fake-login disabled
    When a client attempts fake-login as "leader"
    Then the fake-login route is absent
