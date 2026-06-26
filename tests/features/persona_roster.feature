@persona_roster @solidarity_tech
Feature: Ralsei reads the persona roster through the adapter

  The persona mock serves a roster built from named personas; the real Solidarity
  Tech adapter reads it in-process over an ASGI transport, so every membership
  state is exercised end to end without a live API. The step wording is distinct
  from the contract suite's so the two suites share no step definitions.

  Scenario: The whole roster decodes
    Given a persona roster "kris@example.com=good_standing,susie@example.com=lapsed"
    When Ralsei reads the roster
    Then the roster has 2 members

  Scenario: The lenient sweep skips a retired-tier persona
    Given a persona roster "kris@example.com=good_standing,noelle@example.com=retired_tier"
    When Ralsei reads the roster
    Then the roster has 1 member

  Scenario: The lenient sweep skips a malformed persona
    Given a persona roster "kris@example.com=good_standing,spamton@example.com=malformed"
    When Ralsei reads the roster
    Then the roster has 1 member

  Scenario: An empty roster yields no members
    Given a persona roster ""
    When Ralsei reads the roster
    Then the roster has 0 members

  Scenario: A targeted lookup finds a member by email
    Given a persona roster "kris@example.com=good_standing"
    When Ralsei looks up the member "kris@example.com"
    Then the lookup returns a member in good standing

  Scenario: A targeted lookup returns nothing for an unknown email
    Given a persona roster "kris@example.com=good_standing"
    When Ralsei looks up the member "nobody@example.com"
    Then the lookup returns no member
