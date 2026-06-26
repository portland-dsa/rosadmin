@contract @solidarity_tech
Feature: Ralsei reads members through the Solidarity Tech API

  These scenarios pin the wire contract against a stubbed API: the bearer token
  on every request, offset paging through the collection, and the split between a
  lenient roster sweep and a targeted lookup when a record fails to decode.

  Scenario: Every request carries the bearer token
    Given a stubbed Solidarity Tech API with one good-standing member
    When Ralsei lists the members
    Then the request carried the bearer token
    And one member in good standing is returned

  Scenario: Listing pages through the whole collection by offset
    Given a stubbed Solidarity Tech API with 150 good-standing members across two pages
    When Ralsei lists the members
    Then 150 members are returned
    And two pages were fetched

  Scenario: A roster sweep skips a record that fails to decode
    Given a stubbed Solidarity Tech API with one good-standing member and one retired-tier record
    When Ralsei lists the members
    Then one member in good standing is returned

  Scenario: A targeted lookup surfaces a record that fails to decode
    Given a stubbed Solidarity Tech API whose email lookup returns a retired-tier record
    When Ralsei looks up "noelle@example.com"
    Then the lookup fails with a decode error
