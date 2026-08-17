CREATE TABLE activity_taxonomy (
    activity_type      TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL,
    measurement_type   TEXT NOT NULL,
    valid_units        TEXT[] NOT NULL,
    default_unit       TEXT NOT NULL,
    beginner_baseline  NUMERIC NOT NULL,
    plausible_max      NUMERIC NOT NULL,
    rounding_step      NUMERIC NOT NULL,
    synonyms           TEXT[] NOT NULL,
    source_exercise_id TEXT,
    youtube_url        TEXT,
    reviewer           TEXT NOT NULL,
    reviewed_at        TIMESTAMPTZ,
    version            INT NOT NULL DEFAULT 1,
    CONSTRAINT at_measurement_known CHECK (measurement_type IN ('distance','reps','duration')),
    CONSTRAINT at_default_unit_is_valid CHECK (default_unit = ANY (valid_units)),
    CONSTRAINT at_units_nonempty CHECK (array_length(valid_units,1) >= 1),
    CONSTRAINT at_synonyms_nonempty CHECK (array_length(synonyms,1) >= 1),
    CONSTRAINT at_step_positive CHECK (rounding_step > 0),
    CONSTRAINT at_baseline_positive CHECK (beginner_baseline > 0),
    CONSTRAINT at_max_above_baseline CHECK (plausible_max > beginner_baseline),
    CONSTRAINT at_baseline_on_grid CHECK ((beginner_baseline / rounding_step) = trunc(beginner_baseline / rounding_step)),
    CONSTRAINT at_code_is_snake CHECK (activity_type ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT at_signature_complete CHECK (
        (reviewer = 'UNREVIEWED-PLACEHOLDER' AND reviewed_at IS NULL)
        OR (reviewer <> 'UNREVIEWED-PLACEHOLDER' AND reviewed_at IS NOT NULL))
);
CREATE INDEX at_synonyms_gin ON activity_taxonomy USING gin (synonyms);
CREATE OR REPLACE FUNCTION assert_synonyms_disjoint() RETURNS trigger AS $$
DECLARE clash TEXT;
BEGIN
    SELECT string_agg(t.activity_type || ' <- ' ||
                      array_to_string(ARRAY(SELECT unnest(t.synonyms) INTERSECT SELECT unnest(NEW.synonyms)), ', '), '; ')
      INTO clash FROM activity_taxonomy t
     WHERE t.activity_type <> NEW.activity_type AND t.synonyms && NEW.synonyms;
    IF clash IS NOT NULL THEN
        RAISE EXCEPTION 'synonym collision for %: %', NEW.activity_type, clash USING ERRCODE='raise_exception';
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER at_synonyms_disjoint BEFORE INSERT OR UPDATE OF synonyms ON activity_taxonomy
    FOR EACH ROW EXECUTE FUNCTION assert_synonyms_disjoint();
INSERT INTO activity_taxonomy (activity_type, display_name, measurement_type, valid_units, default_unit,
    beginner_baseline, plausible_max, rounding_step, synonyms, source_exercise_id, youtube_url, reviewer, reviewed_at) VALUES
  ('walk','Walking','distance',ARRAY['km','mi']::TEXT[],'km',1.0,15.0,0.1,ARRAY['walk','walking','went for a walk','walked','stroll']::TEXT[],NULL,NULL,'UNREVIEWED-PLACEHOLDER',NULL),
  ('neck_isometric','Neck Hold','duration',ARRAY['sec','min']::TEXT[],'sec',5,60,5,ARRAY['neck hold','neck isometric','neck exercise','isometric neck']::TEXT[],'Isometric_Neck_Exercise_-_Front_And_Back',NULL,'UNREVIEWED-PLACEHOLDER',NULL),
  ('shoulder_circles','Shoulder Circles','reps',ARRAY['count']::TEXT[],'count',5,50,1,ARRAY['shoulder circles','shoulder rolls','rolled my shoulders','shoulder roll']::TEXT[],'Shoulder_Circles',NULL,'UNREVIEWED-PLACEHOLDER',NULL),
  ('lower_back_side_stretch','Seated Side Stretch','duration',ARRAY['sec','min']::TEXT[],'sec',10,120,5,ARRAY['side stretch','chair stretch','lower back stretch','seated side bend']::TEXT[],'Chair_Lower_Back_Stretch',NULL,'UNREVIEWED-PLACEHOLDER',NULL),
  ('glute_bridge','Glute Bridge','reps',ARRAY['count']::TEXT[],'count',5,50,1,ARRAY['glute bridge','bridge','bridges','hip bridge','butt lift']::TEXT[],'Butt_Lift_Bridge',NULL,'UNREVIEWED-PLACEHOLDER',NULL),
  ('dead_bug','Dead Bug','reps',ARRAY['count']::TEXT[],'count',4,40,1,ARRAY['dead bug','dead bugs','deadbug']::TEXT[],'Dead_Bug',NULL,'UNREVIEWED-PLACEHOLDER',NULL),
  ('hamstring_stretch','Seated Hamstring Stretch','duration',ARRAY['sec','min']::TEXT[],'sec',15,120,5,ARRAY['hamstring stretch','hamstrings','seated hamstring','leg stretch']::TEXT[],'Seated_Floor_Hamstring_Stretch',NULL,'UNREVIEWED-PLACEHOLDER',NULL),
  ('quad_stretch','Side-Lying Thigh Stretch','duration',ARRAY['sec','min']::TEXT[],'sec',15,120,5,ARRAY['quad stretch','thigh stretch','quads','quadriceps stretch']::TEXT[],'On_Your_Side_Quad_Stretch',NULL,'UNREVIEWED-PLACEHOLDER',NULL),
  ('calf_stretch','Wall Calf Stretch','duration',ARRAY['sec','min']::TEXT[],'sec',10,120,5,ARRAY['calf stretch','calves','wall stretch','calf']::TEXT[],'Calf_Stretch_Hands_Against_Wall',NULL,'UNREVIEWED-PLACEHOLDER',NULL),
  ('ankle_circles','Ankle Circles','reps',ARRAY['count']::TEXT[],'count',5,50,1,ARRAY['ankle circles','ankle rotations','ankle circle','rotated my ankle']::TEXT[],'Ankle_Circles',NULL,'UNREVIEWED-PLACEHOLDER',NULL),
  ('wrist_circles','Wrist Circles','reps',ARRAY['count']::TEXT[],'count',5,50,1,ARRAY['wrist circles','wrist rotations','wrist circle','wrists']::TEXT[],'Wrist_Circles',NULL,'UNREVIEWED-PLACEHOLDER',NULL);
CREATE OR REPLACE FUNCTION assert_taxonomy_reviewed() RETURNS void AS $$
DECLARE bad TEXT; n INT;
BEGIN
    SELECT count(*), string_agg(activity_type, ', ' ORDER BY activity_type) INTO n, bad
      FROM activity_taxonomy WHERE reviewer = 'UNREVIEWED-PLACEHOLDER';
    IF n > 0 THEN
        RAISE EXCEPTION 'activity_taxonomy has % unreviewed row(s): %. Refusing to boot.', n, bad
            USING ERRCODE='raise_exception';
    END IF;
END; $$ LANGUAGE plpgsql;
