-- =============================================================================
-- HABIBI (حَبِيبِي) — ANDROID & WEB — RELATIONAL DATABASE SCHEMA
-- Compatible with SQLite 3.38+ and PostgreSQL 15+
-- Grounded Data Sources:
--   1. Arramooz Alwaseet Morphological Dictionary: https://github.com/linuxscout/arramooz
--   2. Quranic Arabic Corpus Lemma Database: https://corpus.quran.com/lemmas.jsp
--   3. CAMeL Lab Arabic Frequency Lists (MSA, DA, CA): https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists
--   4. Mishkal Vocalization Engine: https://github.com/linuxscout/mishkal
--   5. Tatoeba Parallel Sentences (CC-BY): https://www.manythings.org/bilingual/ara/
--   6. Mozilla Common Voice 18 Arabic: https://huggingface.co/datasets/MohamedRashad/common-voice-18-arabic
--   7. Free Spaced Repetition Scheduler (FSRS): https://github.com/open-spaced-repetition/free-spaced-repetition-scheduler
--   8. Unified ACTFL/CEFR Arabic Proficiency Model: https://f1000research.com/articles/15-325
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- 1. SCRIPT & ARTICULATORY PHONETICS (STAGE 1 PRE-A1 FOUNDATION)
-- Source: DLI Headstart2 (https://hs2.dliflc.edu/) & FSI Written Arabic
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS arabic_letters (
    letter_id           TEXT PRIMARY KEY,               -- e.g., 'ba', 'ha_phar', 'sad', 'qaf'
    isolated_glyph      TEXT NOT NULL UNIQUE,           -- e.g., 'ب'
    initial_glyph       TEXT NOT NULL,                  -- e.g., 'بـ'
    medial_glyph        TEXT NOT NULL,                  -- e.g., 'ـبـ'
    final_glyph         TEXT NOT NULL,                  -- e.g., 'ـب'
    name_arabic         TEXT NOT NULL,                  -- e.g., 'بَاء'
    name_translit       TEXT NOT NULL,                  -- e.g., 'Bāʾ'
    ipa_symbol          TEXT NOT NULL,                  -- e.g., '/b/', '/ħ/', '/sˤ/'
    shape_group         TEXT NOT NULL,                  -- 'boat' | 'hook' | 'emphatic' | 'throat' | 'nonconn'
    articulation_zone   TEXT NOT NULL,                  -- 'bilabial' | 'pharyngeal' | 'uvular' | 'velarized' | 'glottal'
    is_dual_joining     INTEGER NOT NULL DEFAULT 1,     -- 1 = joins both sides, 0 = right-joining only (ا د ذ ر ز و)
    is_sun_letter       INTEGER NOT NULL DEFAULT 0,     -- 1 = assimilates 'al-' definite article
    visual_mnemonic     TEXT NOT NULL,
    contrast_pair_id    TEXT                            -- Links minimal pairs (e.g., 'ha_phar' <-> 'ha_glottal')
);

-- -----------------------------------------------------------------------------
-- 2. TRILATERAL & QUADRILATERAL ROOTS (JATHR / جَذْر)
-- Source: Arramooz Alwaseet (https://github.com/linuxscout/arramooz)
--         & Quranic Arabic Corpus (https://corpus.quran.com/lemmas.jsp)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trilateral_roots (
    root_id             TEXT PRIMARY KEY,               -- e.g., 'ktb', 'drs', 'alm', 'aml', 'fhm', 'sfr'
    radicals_arabic     TEXT NOT NULL UNIQUE,           -- e.g., 'ك-ت-ب'
    c1_radical          TEXT NOT NULL,                  -- First radical (ف position): 'ك'
    c2_radical          TEXT NOT NULL,                  -- Second radical (ع position): 'ت'
    c3_radical          TEXT NOT NULL,                  -- Third radical (ل position): 'ب'
    c4_radical          TEXT,                           -- Optional 4th radical for quadrilateral roots
    root_type           TEXT NOT NULL DEFAULT 'sound',  -- 'sound' | 'hollow' | 'defective' | 'assimilated' | 'doubled'
    core_concept_en     TEXT NOT NULL,                  -- e.g., 'Writing / Recording'
    corpus_freq_rank    INTEGER NOT NULL,               -- Rank across CAMeL + Quranic Corpus lemmas
    cefr_unlock_stage   INTEGER NOT NULL DEFAULT 1      -- 1=Pre-A1, 2=A1, 3=A2, 4=B1, 5=B2
);

-- -----------------------------------------------------------------------------
-- 3. MORPHOLOGICAL PATTERNS (WAZN / وَزْن — MEEZAAN SARFII)
-- Source: Arramooz Alwaseet & Al-Kitaab / FSI Written Arabic Grammar
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS morphological_patterns (
    pattern_id          TEXT PRIMARY KEY,               -- e.g., 'fa3ala', 'fa3il', 'maf3ul', 'maf3al', 'form_ii'
    wazn_vocalized      TEXT NOT NULL,                  -- e.g., 'فَاعِل'
    wazn_unvocalized    TEXT NOT NULL,                  -- e.g., 'فاعل'
    wazn_translit       TEXT NOT NULL,                  -- e.g., 'fāʿil'
    verb_form_roman     TEXT,                           -- 'I' through 'X' (NULL for purely nominal place/instrument patterns)
    grammatical_role    TEXT NOT NULL,                  -- e.g., 'Active Participle (Doer)'
    semantic_rule_en    TEXT NOT NULL                   -- How this pattern transforms the root's meaning
);

-- -----------------------------------------------------------------------------
-- 4. LEXICAL ENTRIES (LEMMAS) & MISHKAL VOWEL SCAFFOLDING
-- Source: CAMeL Arabic Frequency Lists (https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists)
--         & Mishkal Vocalization Engine (https://github.com/linuxscout/mishkal)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lexical_entries (
    lemma_id            TEXT PRIMARY KEY,               -- e.g., 'lem_kataba', 'lem_katib', 'lem_maktab', 'lem_qahwa'
    root_id             TEXT REFERENCES trilateral_roots(root_id) ON DELETE SET NULL,
    pattern_id          TEXT REFERENCES morphological_patterns(pattern_id) ON DELETE SET NULL,
    part_of_speech      TEXT NOT NULL,                  -- 'verb' | 'noun' | 'adj' | 'particle' | 'phrase'
    -- Three pre-computed Mishkal vocalization tiers for O(1) UI rendering:
    msa_full_tashkeel   TEXT NOT NULL,                  -- 100% vocalized: 'كَاتِبٌ'
    msa_scaffold_50     TEXT NOT NULL,                  -- 50% faded stem vowels: 'كَاتِب'
    msa_unvocalized     TEXT NOT NULL,                  -- Real-world stripped: 'كاتب'
    msa_translit        TEXT NOT NULL,                  -- ISO/ALA-LC: 'kātib'
    english_gloss       TEXT NOT NULL,                  -- Primary English meaning: 'writer / author'
    gender              TEXT,                           -- 'm' | 'f' | 'common'
    broken_plural_full  TEXT,                           -- e.g., 'كُتَّاب' (kuttāb)
    camel_msa_freq_rank INTEGER NOT NULL,               -- Rank in CAMeL MSA_freq_lists.tsv
    camel_mix_freq_rank INTEGER NOT NULL,               -- Rank in CAMeL MIX_freq_lists.tsv (CA+DA+MSA)
    cefr_stage          INTEGER NOT NULL DEFAULT 1,     -- 1=Pre-A1, 2=A1, 3=A2, 4=B1, 5=B2
    audio_uri_msa       TEXT                            -- Reference to Common Voice / ClArTTS audio asset
);

CREATE INDEX IF NOT EXISTS idx_lexical_root ON lexical_entries(root_id);
CREATE INDEX IF NOT EXISTS idx_lexical_freq ON lexical_entries(camel_mix_freq_rank);
CREATE INDEX IF NOT EXISTS idx_lexical_stage ON lexical_entries(cefr_stage);

-- -----------------------------------------------------------------------------
-- 5. DIGLOSSIC DIALECT VARIANTS (INTEGRATED FUSHA + AMMIYA TRACKS)
-- Source: FSI Comparative Arabic (https://www.fsi-language-courses.org/fsi-comparative-arabic-courses/)
--         & CAMeL Dialectal Arabic (DA_freq_lists.tsv)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dialect_variants (
    variant_id          TEXT PRIMARY KEY,
    lemma_id            TEXT NOT NULL REFERENCES lexical_entries(lemma_id) ON DELETE CASCADE,
    dialect_code        TEXT NOT NULL,                  -- 'levantine' | 'egyptian' | 'gulf' | 'maghrebi'
    surface_vocalized   TEXT NOT NULL,                  -- e.g., 'بَشْرَب قَهْوِة' (Levantine)
    surface_unvocalized TEXT NOT NULL,                  -- e.g., 'بشرب قهوة'
    phonetic_translit   TEXT NOT NULL,                  -- e.g., 'bashrab qahwe'
    pronunciation_note  TEXT,                           -- e.g., 'ق pronounced as glottal stop /ʔ/ in urban Damascus/Cairo'
    camel_da_freq_rank  INTEGER,
    audio_uri_dialect   TEXT,
    UNIQUE(lemma_id, dialect_code)
);

-- -----------------------------------------------------------------------------
-- 6. BILINGUAL PARALLEL SENTENCES & SITUATIONAL DIALOGUES
-- Source: Tatoeba English-Arabic (https://www.manythings.org/bilingual/ara/)
--         & Peace Corps Communicative Courses (https://www.fsi-language-courses.org/peace-corps-courses/)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bilingual_sentences (
    sentence_id         TEXT PRIMARY KEY,
    primary_lemma_id    TEXT NOT NULL REFERENCES lexical_entries(lemma_id) ON DELETE CASCADE,
    source_corpus       TEXT NOT NULL DEFAULT 'tatoeba_cc_by',
    msa_vocalized       TEXT NOT NULL,
    msa_scaffold_50     TEXT NOT NULL,
    msa_unvocalized     TEXT NOT NULL,
    levantine_vocalized TEXT NOT NULL,
    egyptian_vocalized  TEXT NOT NULL,
    translit_msa        TEXT NOT NULL,
    english_translation TEXT NOT NULL,
    cloze_target_word   TEXT NOT NULL,                  -- Target word masked during Cloze reading drills
    cefr_stage          INTEGER NOT NULL DEFAULT 1
);

-- -----------------------------------------------------------------------------
-- 7. LEARNER PROFILES & ADAPTIVE SCAFFOLDING STATE
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS learner_profiles (
    user_id             TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL,
    preferred_dialect   TEXT NOT NULL DEFAULT 'levantine', -- 'msa' | 'levantine' | 'egyptian' | 'gulf'
    tashkeel_mode       TEXT NOT NULL DEFAULT 'full',      -- 'full' | 'scaffolded' | 'unvocalized'
    show_translit       INTEGER NOT NULL DEFAULT 1,
    current_cefr_stage  INTEGER NOT NULL DEFAULT 1,
    total_xp            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- 8. FSRS (FREE SPACED REPETITION SCHEDULER) MEMORY CARDS & REVIEW LOGS
-- Source: Open Spaced Repetition FSRS v4/v5 DSR Model
--         https://github.com/open-spaced-repetition/free-spaced-repetition-scheduler
--         https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fsrs_cards (
    card_id             TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES learner_profiles(user_id) ON DELETE CASCADE,
    target_type         TEXT NOT NULL,                  -- 'letter' | 'root' | 'lemma' | 'sentence_cloze' | 'minimal_pair'
    target_ref_id       TEXT NOT NULL,                  -- ID in arabic_letters, trilateral_roots, or lexical_entries
    card_modality       TEXT NOT NULL,                  -- 'visual_to_ar' | 'audio_to_meaning' | 'root_deduction' | 'speaking'
    -- Core DSR (Difficulty, Stability, Retrievability) State Variables:
    state_code          INTEGER NOT NULL DEFAULT 0,     -- 0=New, 1=Learning, 2=Review, 3=Relearning
    difficulty          REAL NOT NULL DEFAULT 5.0,      -- D in [1.0, 10.0]
    stability           REAL NOT NULL DEFAULT 0.0,      -- S in days (time for R to decay from 100% to 90%)
    retrievability      REAL NOT NULL DEFAULT 1.0,      -- R in [0.0, 1.0]
    elapsed_days        REAL NOT NULL DEFAULT 0.0,
    scheduled_days      REAL NOT NULL DEFAULT 0.0,
    reps_count          INTEGER NOT NULL DEFAULT 0,
    lapses_count        INTEGER NOT NULL DEFAULT 0,
    last_review_ts      TEXT,
    due_ts              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, target_type, target_ref_id, card_modality)
);

CREATE INDEX IF NOT EXISTS idx_fsrs_due ON fsrs_cards(user_id, due_ts);

CREATE TABLE IF NOT EXISTS fsrs_review_logs (
    log_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id             TEXT NOT NULL REFERENCES fsrs_cards(card_id) ON DELETE CASCADE,
    user_id             TEXT NOT NULL REFERENCES learner_profiles(user_id) ON DELETE CASCADE,
    rating              INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4), -- 1=Again, 2=Hard, 3=Good, 4=Easy
    state_before        INTEGER NOT NULL,
    difficulty_before   REAL NOT NULL,
    stability_before    REAL NOT NULL,
    difficulty_after    REAL NOT NULL,
    stability_after     REAL NOT NULL,
    retrievability_at   REAL NOT NULL,
    scheduled_days      REAL NOT NULL,
    response_latency_ms INTEGER NOT NULL,
    dialect_used        TEXT NOT NULL,
    tashkeel_mode_used  TEXT NOT NULL,
    reviewed_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- 9. STAGE GATE MASTERY EXAMS (ACTFL / CEFR LEVEL-UP AUDITS)
-- Source: Unified Arabic Assessment Model (https://f1000research.com/articles/15-325)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stage_gate_attempts (
    attempt_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL REFERENCES learner_profiles(user_id) ON DELETE CASCADE,
    stage_tested        INTEGER NOT NULL,
    phonetics_score     REAL NOT NULL,                  -- Minimal-pair discrimination accuracy (0.0 - 1.0)
    script_join_score   REAL NOT NULL,                  -- Positional joining & dictation accuracy
    morphology_score    REAL NOT NULL,                  -- Unseen trilateral root pattern deduction
    unvocalized_score   REAL NOT NULL,                  -- Reading accuracy with short vowels stripped
    speaking_score      REAL NOT NULL,                  -- Back-and-forth pragmatic dialogue score
    composite_score     REAL NOT NULL,
    passed              INTEGER NOT NULL CHECK (passed IN (0, 1)),
    attempted_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- 10. ANALYTICAL VIEW: ROOT-LEVEL MASTERY & DIGLOSSIA READINESS
-- Aggregates FSRS Stability across all words sharing a trilateral root so the
-- app knows when to strip short vowels (Mishkal fade) for an entire root family.
-- -----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_root_family_mastery AS
SELECT
    r.root_id,
    r.radicals_arabic,
    r.core_concept_en,
    COUNT(DISTINCT le.lemma_id) AS total_family_lemmas,
    COUNT(fc.card_id) AS active_fsrs_cards,
    ROUND(COALESCE(AVG(fc.stability), 0.0), 2) AS avg_root_stability_days,
    ROUND(COALESCE(AVG(fc.difficulty), 5.0), 2) AS avg_root_difficulty,
    CASE
        WHEN COALESCE(AVG(fc.stability), 0.0) >= 14.0 THEN 'unvocalized'
        WHEN COALESCE(AVG(fc.stability), 0.0) >= 5.0  THEN 'scaffolded'
        ELSE 'full'
    END AS recommended_tashkeel_mode
FROM trilateral_roots r
LEFT JOIN lexical_entries le ON le.root_id = r.root_id
LEFT JOIN fsrs_cards fc ON fc.target_type = 'lemma' AND fc.target_ref_id = le.lemma_id
GROUP BY r.root_id, r.radicals_arabic, r.core_concept_en;
