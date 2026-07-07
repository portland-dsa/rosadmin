ALTER TABLE members
    DROP COLUMN leadership_assessment,
    DROP COLUMN alternate_name,
    DROP COLUMN is_chapter_leader;

DROP TYPE leadership_assessment;
