-- The leadership cross-check alongside the raw flag it is checked against: the
-- boolean Solidarity Tech serves directly, the display-name override, and the
-- decoded LeadershipAssessment comparing the flag to the derived leadership
-- bodies. See rosadmin/membership/source.py for the assessment states.
-- depends: create_membership_tables

CREATE TYPE leadership_assessment AS ENUM
    ('leader', 'non_leader', 'unmarked_leader', 'empty_leader');

ALTER TABLE members ADD COLUMN is_chapter_leader     boolean NOT NULL DEFAULT false;
ALTER TABLE members ADD COLUMN alternate_name        text;
ALTER TABLE members ADD COLUMN leadership_assessment leadership_assessment
                               NOT NULL DEFAULT 'non_leader';
