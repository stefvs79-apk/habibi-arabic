#!/usr/bin/env python3
"""
Habibi (حَبِيبِي) — Data Ingestion Pipeline, Unicode Vowel Scaffolder & FSRS v5 Engine
=======================================================================================
This module provides:
  1. HabibiVowelScaffolder: Pre-computes Full Tashkeel, 50% Scaffolded Fade, and
     Real-World Unvocalized strings using standard Unicode Arabic diacritic ranges (U+064B..U+0652).
  2. HabibiCurriculumSeeder: Seeds original Habibi trilateral roots, 320 everyday
     tridialectal vocabulary items, and original bilingual example sentences.
  3. FSRSScheduler: Pure-Python implementation of the open-source Free Spaced Repetition
     Scheduler DSR (Difficulty, Stability, Retrievability) equations
     (https://github.com/open-spaced-repetition/free-spaced-repetition-scheduler)
     with an Arabic root-synergy bonus and automatic vowel-fading progression.
  4. End-to-End SQLite Verification Suite: Initializes `habibi_schema.sql`,
     ingests grounded linguistic records, simulates a learner review session, and prints
     the resulting database state and root-family mastery view.
"""

from __future__ import annotations

import datetime as dt
import math
import pathlib
import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# =============================================================================
# 1. HABIBI UNICODE VOWEL SCAFFOLDING ENGINE (TASHKEEL PROCESSOR)
# =============================================================================
class HabibiVowelScaffolder:
    """
    Processes vocalized Arabic text into three pedagogical scaffolding tiers:
      - Tier 1 (Full): 100% short vowel diacritics preserved for Pre-A1 decoding.
      - Tier 2 (Scaffolded 50%): Strips predictable stem vowels while preserving
        shadda (gemination \u0651) and final case/mood markers so learners infer
        internal root patterns without losing grammatical endings.
      - Tier 3 (Unvocalized): Strips all short vowel diacritics for real-world
        street/media literacy while preserving base letters and hamza seats.
    """

    # Unicode range for Arabic tashkeel (Fathatan through Sukun + Superscript Alif)
    ALL_TASHKEEL_RE = re.compile(r"[\u064B-\u0652\u0670]")
    # Short vowels only (Fatha \u064E, Damma \u064F, Kasra \u0650, Sukun \u0652)
    SHORT_VOWELS_ONLY_RE = re.compile(r"[\u064E\u064F\u0650\u0652]")

    @classmethod
    def strip_all_tashkeel(cls, text: str) -> str:
        """Returns completely unvocalized real-world Arabic script."""
        return cls.ALL_TASHKEEL_RE.sub("", text)

    @classmethod
    def generate_scaffolded_50(cls, vocalized_text: str) -> str:
        """
        Fades internal short vowels on alternating syllables while keeping
        gemination (shadda \u0651) and word-final inflectional vowels intact.
        """
        words = vocalized_text.split(" ")
        scaffolded_words: List[str] = []
        for word in words:
            if len(word) <= 2:
                scaffolded_words.append(word)
                continue
            # Split word into stem (all but last 2 chars) and ending (last 2 chars)
            stem, ending = word[:-2], word[-2:]
            counter = 0

            def _fade_match(match: re.Match) -> str:
                nonlocal counter
                counter += 1
                return "" if (counter % 2 == 1) else match.group(0)

            faded_stem = cls.SHORT_VOWELS_ONLY_RE.sub(_fade_match, stem)
            scaffolded_words.append(faded_stem + ending)
        return " ".join(scaffolded_words)

    @classmethod
    def build_three_tiers(cls, vocalized_text: str) -> Tuple[str, str, str]:
        """Returns (full_tashkeel, scaffolded_50, unvocalized)."""
        return (
            vocalized_text,
            cls.generate_scaffolded_50(vocalized_text),
            cls.strip_all_tashkeel(vocalized_text),
        )


# =============================================================================
# 2. FREE SPACED REPETITION SCHEDULER (FSRS DSR MATHEMATICAL ENGINE)
# Grounded in: https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm
# =============================================================================
@dataclass
class FSRSCardState:
    card_id: str
    state_code: int       # 0=New, 1=Learning, 2=Review, 3=Relearning
    difficulty: float     # D in [1.0, 10.0]
    stability: float      # S in days
    retrievability: float # R in [0.0, 1.0]
    elapsed_days: float
    scheduled_days: float
    reps_count: int
    lapses_count: int


class FSRSScheduler:
    """
    Implements the DSR (Difficulty, Stability, Retrievability) memory model from
    Open Spaced Repetition (FSRS v4.5 / v5) with an optional Arabic Root-Family
    Synergy modifier (when a learner already knows a sibling word from the same
    Arabic trilateral root, initial stability is boosted by 15%).
    """

    # Default FSRS v4/v5 parameters (w0..w3 = initial stability for Again, Hard, Good, Easy)
    W_INIT_STABILITY = {1: 0.40, 2: 1.20, 3: 3.10, 4: 7.40}
    W_INIT_DIFFICULTY = 5.0
    REQUEST_RETENTION = 0.90
    DECAY = -0.5
    FACTOR = 19.0 / 81.0  # Ensures R(S, S) = (1 + 19/81)^(-0.5) = 0.90

    @classmethod
    def compute_retrievability(cls, elapsed_days: float, stability: float) -> float:
        """
        Power-law forgetting curve:
            R(t, S) = (1 + FACTOR * (t / S)) ^ DECAY
        At t == S, R(S, S) = 0.90 (90% probability of recall).
        """
        if stability <= 0.0:
            return 1.0
        return math.pow(1.0 + cls.FACTOR * (max(0.0, elapsed_days) / stability), cls.DECAY)

    @classmethod
    def next_interval_days(cls, stability: float) -> float:
        """
        Inverts the forgetting curve for target retention r = 0.90:
            I(r, S) = (S / FACTOR) * (r^(1 / DECAY) - 1)
        When REQUEST_RETENTION == 0.90, I(0.90, S) == S.
        """
        interval = (stability / cls.FACTOR) * (math.pow(cls.REQUEST_RETENTION, 1.0 / cls.DECAY) - 1.0)
        return round(max(0.5, interval), 2)

    @classmethod
    def review_card(
        cls,
        card: FSRSCardState,
        rating: int,
        elapsed_days: float,
        root_family_known: bool = False,
    ) -> FSRSCardState:
        """
        Updates (D, S, R) given a user rating in {1: Again, 2: Hard, 3: Good, 4: Easy}.
        """
        if rating not in (1, 2, 3, 4):
            raise ValueError(f"FSRS rating must be 1..4, got {rating}")

        root_synergy_mult = 1.15 if root_family_known else 1.00

        # Case 1: First review of a brand-new card
        if card.state_code == 0 or card.reps_count == 0 or card.stability <= 0.0:
            new_s = round(cls.W_INIT_STABILITY[rating] * root_synergy_mult, 2)
            new_d = round(min(10.0, max(1.0, cls.W_INIT_DIFFICULTY - 0.8 * (rating - 3))), 2)
            new_state = 1 if rating == 1 else 2
            sched = cls.next_interval_days(new_s) if rating > 1 else 0.04  # ~1 hour relearn
            return FSRSCardState(
                card_id=card.card_id,
                state_code=new_state,
                difficulty=new_d,
                stability=new_s,
                retrievability=1.0,
                elapsed_days=elapsed_days,
                scheduled_days=sched,
                reps_count=card.reps_count + 1,
                lapses_count=card.lapses_count + (1 if rating == 1 else 0),
            )

        # Case 2: Subsequent review of an active card
        r_now = cls.compute_retrievability(elapsed_days, card.stability)

        # Update Difficulty D' with mean reversion toward W_INIT_DIFFICULTY
        delta_d = -0.55 * (rating - 3)
        raw_d = card.difficulty + delta_d
        mean_reverted_d = 0.90 * raw_d + 0.10 * cls.W_INIT_DIFFICULTY
        new_d = round(min(10.0, max(1.0, mean_reverted_d)), 2)

        if rating == 1:
            # Post-lapse stability S'_f (card forgotten)
            new_s = round(
                max(
                    0.40,
                    0.32
                    * math.pow(new_d, -0.25)
                    * (math.pow(card.stability + 1.0, 0.20) - 1.0)
                    * math.exp(0.25 * (1.0 - r_now)),
                ),
                2,
            )
            return FSRSCardState(
                card_id=card.card_id,
                state_code=3,  # Relearning
                difficulty=new_d,
                stability=new_s,
                retrievability=round(r_now, 4),
                elapsed_days=elapsed_days,
                scheduled_days=0.05,
                reps_count=card.reps_count + 1,
                lapses_count=card.lapses_count + 1,
            )

        # Successful recall (rating in 2=Hard, 3=Good, 4=Easy): Stability increase S'_r
        hard_penalty = 0.65 if rating == 2 else 1.00
        easy_bonus = 1.35 if rating == 4 else 1.00

        s_inc = (
            math.exp(1.05)
            * (11.0 - new_d)
            * math.pow(card.stability, -0.18)
            * (math.exp(0.75 * (1.0 - r_now)) - 1.0)
            * hard_penalty
            * easy_bonus
            * root_synergy_mult
        )
        new_s = round(card.stability * (1.0 + max(0.15, s_inc)), 2)
        sched = cls.next_interval_days(new_s)

        return FSRSCardState(
            card_id=card.card_id,
            state_code=2,  # Review
            difficulty=new_d,
            stability=new_s,
            retrievability=round(r_now, 4),
            elapsed_days=elapsed_days,
            scheduled_days=sched,
            reps_count=card.reps_count + 1,
            lapses_count=card.lapses_count,
        )


# =============================================================================
# 3. HABIBI CURRICULUM SEEDING PIPELINE (SQLITE)
# =============================================================================
class ArabicCurriculumPipeline:
    """
    Loads the relational schema and ingests structured records from:
      - DLI Headstart2 & FSI Written Arabic (Letters & Articulation)
      - Original Habibi Trilateral Roots & Morphological Patterns
      - Original 320-Word Tridialectal Everyday Vocabulary
      - Pre-computed 3-Tier Unicode Vowel Scaffolding
      - Original Bilingual Sentences & MIT-Licensed FSRS Scheduling
    """

    def __init__(self, db_path: str, schema_sql_path: str) -> None:
        self.db_path = db_path
        self.schema_sql_path = schema_sql_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def initialize_schema(self) -> None:
        sql_script = pathlib.Path(self.schema_sql_path).read_text(encoding="utf-8")
        self.conn.executescript(sql_script)
        self.conn.commit()

    def seed_reference_curriculum(self) -> None:
        cur = self.conn.cursor()

        # 1. Seed All 28 Arabic Letters (with Shape Groups & Sun/Moon Assimilation Rules)
        letters = [('ba', 'ب', 'بـ', 'ـبـ', 'ـب', 'بَاء', "Bā'", '/b/', 'boat', 'non_coronal_moon', 1, 0, 'Shallow boat hull with 1 drop of water underneath (/b/ = bottom dot). | Example: البَاب (al-bāb (the door))', None), ('ta', 'ت', 'تـ', 'ـتـ', 'ـت', 'تَاء', "Tā'", '/t/', 'boat', 'coronal_sun', 1, 1, 'Boat hull with 2 eyes looking up at the top (/t/ = two top dots). | Example: التُّفَّاح (at-tuffāḥ (the apples))', None), ('tha', 'ث', 'ثـ', 'ـثـ', 'ـث', 'ثَاء', "Thā'", '/θ/ as in think', 'boat', 'coronal_sun', 1, 1, 'Boat hull with 3 dots in a triangle on top (/th/ = three dots). | Example: الثَّلْج (ath-thalj (the snow/ice))', None), ('nun', 'ن', 'نـ', 'ـنـ', 'ـن', 'نُون', 'Nūn', '/n/', 'boat', 'coronal_sun', 1, 1, 'Deeper bowl shape with 1 dot floating in the center (/n/ = north dot). | Example: النُّور (an-nūr (the light))', None), ('ya', 'ي', 'يـ', 'ـيـ', 'ـي', 'يَاء', "Yā'", '/j/ or /iː/', 'boat', 'non_coronal_moon', 1, 0, 'Swan neck in isolated/final form, transforms into a boat with 2 bottom dots in initial/medial. | Example: اليَوْم (al-yawm (the day))', None), ('jim', 'ج', 'جـ', 'ـجـ', 'ـج', 'جِيم', 'Jīm', '/dʒ/ or /g/ (Egypt)', 'hook', 'non_coronal_moon', 1, 0, 'Wave crest with 1 dot tucked inside the belly (pronounced /g/ in Cairo, /j/ in MSA/Levant). | Example: الجَمَل (al-jamal (the camel))', None), ('ha_phar', 'ح', 'حـ', 'ـحـ', 'ـح', 'حَاء', "Ḥā'", '/ħ/ Pharyngeal', 'hook', 'non_coronal_moon', 1, 0, 'Clean wave hook with ZERO dots — pure constricted throat whisper. | Example: الحِسَاب (al-ḥisāb (the bill))', None), ('kha', 'خ', 'خـ', 'ـخـ', 'ـخ', 'خَاء', "Khā'", '/x/ Velar Scrape', 'hook', 'non_coronal_moon', 1, 0, 'Wave hook with 1 dot on top of the head — like Scottish loch. | Example: الخُبْز (al-khubz (the bread))', None), ('sin', 'س', 'سـ', 'ـسـ', 'ـس', 'سِين', 'Sīn', '/s/ Plain', 'emphatic', 'coronal_sun', 1, 1, 'Three front teeth with a smooth tail — light, smiling English s. | Example: السَّلَام (as-salām (the peace))', None), ('shin', 'ش', 'شـ', 'ـشـ', 'ـش', 'شِين', 'Shīn', '/ʃ/ as in shine', 'emphatic', 'coronal_sun', 1, 1, 'Three teeth topped with 3 rising sparks (/sh/ = shine). | Example: الشَّمْس (ash-shams (the sun))', None), ('sad', 'ص', 'صـ', 'ـصـ', 'ـص', 'صَاد', 'Ṣād', '/sˤ/ Emphatic', 'emphatic', 'coronal_sun', 1, 1, 'Closed teardrop loop — pull the back of your tongue up for a resonant heavy S. | Example: الصَّبَاح (aṣ-ṣabāḥ (the morning))', None), ('dad', 'ض', 'ضـ', 'ـضـ', 'ـض', 'ضَاد', 'Ḍād', '/dˤ/ Emphatic D', 'emphatic', 'coronal_sun', 1, 1, 'Ṣād loop with 1 top dot — Arabic is called Lughat aḍ-Ḍād (Language of Ḍād)! | Example: الضَّوْء (aḍ-ḍawʾ (the light))', None), ('tah', 'ط', 'طـ', 'ـطـ', 'ـط', 'طَاء', "Ṭā'", '/tˤ/ Emphatic T', 'emphatic', 'coronal_sun', 1, 1, 'Loop with a vertical mast — heavy, resonant T that deepens surrounding vowels. | Example: الطَّالِب (aṭ-ṭālib (the student))', None), ('zah', 'ظ', 'ظـ', 'ـظـ', 'ـظ', 'ظَاء', "Ẓā'", '/ðˤ/ Emphatic DH', 'emphatic', 'coronal_sun', 1, 1, "Ṭā' loop with 1 dot next to the mast — deep, resonant voiced th (as in 'this'). | Example: الظُّهْر (aẓ-ẓuhr (the noon))", None), ('ayn', 'ع', 'عـ', 'ـعـ', 'ـع', 'عَيْن', 'ʿAyn', '/ʕ/ Voiced Pharyngeal', 'throat', 'non_coronal_moon', 1, 0, 'Open hook that closes into a triangle in medial/final form — voiced pharyngeal squeeze. | Example: العَمَل (al-ʿamal (the work))', None), ('ghayn', 'غ', 'غـ', 'ـغـ', 'ـغ', 'غَيْن', 'Ghayn', '/ɣ/ Voiced Uvular', 'throat', 'non_coronal_moon', 1, 0, 'ʿAyn shape with 1 top dot — smooth gargled French R. | Example: الغُرْفَة (al-ghurfa (the room))', None), ('fa', 'ف', 'فـ', 'ـفـ', 'ـف', 'فَاء', "Fā'", '/f/', 'throat', 'non_coronal_moon', 1, 0, 'Small neck loop sitting flat on the baseline with 1 top dot. | Example: الفُنْدُق (al-funduq (the hotel))', None), ('qaf', 'ق', 'قـ', 'ـقـ', 'ـق', 'قَاف', 'Qāf', '/q/ Uvular Stop', 'throat', 'non_coronal_moon', 1, 0, 'Deeper round bowl with 2 top dots — popped back at the uvula. | Example: القَمَر (al-qamar (the moon))', None), ('kaf', 'ك', 'كـ', 'ـكـ', 'ـك', 'كَاف', 'Kāf', '/k/ Plain Velar', 'throat', 'non_coronal_moon', 1, 0, 'Tall L-frame that morphs into an S-bolt (كـ) in initial/medial position. | Example: الكِتَاب (al-kitāb (the book))', None), ('ha', 'ه', 'هـ', 'ـهـ', 'ـه', 'هَاء', "Hā'", '/h/ Glottal', 'throat', 'non_coronal_moon', 1, 0, 'Soft breathy English H; changes from circle (ه) to double loop (هـ) to figure-8 (ـهـ). | Example: الهَاتِف (al-hātif (the phone))', None), ('alif', 'ا', 'ا', 'ـا', 'ـا', 'أَلِف', 'Alif', '/aː/ or /ʔ/', 'nonconn', 'non_coronal_moon', 0, 0, 'Tall vertical pillar — connects from the right (ـا) but NEVER connects to the left. | Example: الأُسْتَاذ (al-ustādh (the professor))', None), ('dal', 'د', 'د', 'ـد', 'ـد', 'دَال', 'Dāl', '/d/', 'nonconn', 'coronal_sun', 0, 1, 'Bent hinge sitting on the line — right-joining only. | Example: الدَّرْس (ad-dars (the lesson))', None), ('dhal', 'ذ', 'ذ', 'ـذ', 'ـذ', 'ذَال', 'Dhāl', '/ð/ as in this', 'nonconn', 'coronal_sun', 0, 1, "Dāl hinge with 1 top dot — voiced English th as in 'this' or 'brother'. | Example: الذَّهَب (adh-dhahab (the gold))", None), ('ra', 'ر', 'ر', 'ـر', 'ـر', 'رَاء', "Rā'", '/r/ Alveolar Tap/Trill', 'nonconn', 'coronal_sun', 0, 1, 'Crescent slide dipping below the baseline — right-joining only. | Example: الرَّجُل (ar-rajul (the man))', None), ('zay', 'ز', 'ز', 'ـز', 'ـز', 'زَاي', 'Zāy', '/z/', 'nonconn', 'coronal_sun', 0, 1, "Rā' crescent slide with 1 dot balanced above it (/z/ = buzz). | Example: الزَّيْت (az-zayt (the oil))", None), ('waw', 'و', 'و', 'ـو', 'ـو', 'وَاو', 'Wāw', '/w/ or /uː/', 'nonconn', 'non_coronal_moon', 0, 0, "Looped head with a sliding tail — serves as both 'w' consonant and long 'ū' vowel. | Example: الوَلَد (al-walad (the boy))", None), ('lam', 'ل', 'لـ', 'ـلـ', 'ـل', 'لَام', 'Lām', '/l/', 'liquid', 'coronal_sun', 1, 1, "Tall shepherd's hook; combines with Alif to form the ligature لا (lā). | Example: اللُّغَة (al-lugha (the language))", None), ('mim', 'م', 'مـ', 'ـمـ', 'ـم', 'مِيم', 'Mīm', '/m/', 'liquid', 'non_coronal_moon', 1, 0, 'Small round bead on the line with a vertical tail in isolated/final position. | Example: المَكْتَب (al-maktab (the office))', None)]
        cur.executemany(
            """
            INSERT OR REPLACE INTO arabic_letters
            (letter_id, isolated_glyph, initial_glyph, medial_glyph, final_glyph,
             name_arabic, name_translit, ipa_symbol, shape_group, articulation_zone,
             is_dual_joining, is_sun_letter, visual_mnemonic, contrast_pair_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            letters,
        )

        # 2. Seed Core Trilateral Roots (10 High-Frequency Trilateral Roots)
        roots = [
            ("ktb", "ك-ت-ب", "ك", "ت", "ب", None, "sound", "Writing / Recording", 18, 1),
            ("drs", "د-ر-س", "د", "ر", "س", None, "sound", "Studying / Learning", 42, 1),
            ("alm", "ع-ل-م", "ع", "ل", "م", None, "sound", "Knowledge / Science", 9, 1),
            ("aml", "ع-م-ل", "ع", "م", "ل", None, "sound", "Working / Operating", 11, 1),
            ("shrb", "ش-ر-ب", "ش", "ر", "ب", None, "sound", "Drinking / Absorbing", 64, 1),
            ("fhm", "ف-ه-م", "ف", "ه", "م", None, "sound", "Understanding", 55, 1),
            ("sfr", "س-ف-ر", "س", "ف", "ر", None, "sound", "Travel / Journey", 72, 1),
            ("qra", "ق-ر-أ", "ق", "ر", "أ", None, "sound", "Reading / Reciting", 48, 1),
            ("hsb", "ح-س-ب", "ح", "س", "ب", None, "sound", "Calculating / Accounting", 59, 1),
            ("sn3", "ص-ن-ع", "ص", "ن", "ع", None, "sound", "Making / Manufacturing", 67, 1),
        ]
        cur.executemany(
            """
            INSERT OR REPLACE INTO trilateral_roots
            (root_id, radicals_arabic, c1_radical, c2_radical, c3_radical, c4_radical,
             root_type, core_concept_en, corpus_freq_rank, cefr_unlock_stage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            roots,
        )

        # 3. Seed Morphological Patterns (Wazn)
        patterns = [
            ("fa3ala", "فَعَلَ", "فعل", "faʿala", "I", "Form I Base Verb", "Past tense base action performed by the subject"),
            ("fa3il", "فَاعِل", "فاعل", "fāʿil", "I", "Active Participle (Doer)", "Person or agent performing the root action"),
            ("maf3ul", "مَفْعُول", "مفعول", "mafʿūl", "I", "Passive Participle (Object)", "Entity upon which the root action was completed"),
            ("maf3al", "مَفْعَل", "مفعل", "mafʿal", None, "Noun of Place (Ism Makān)", "Physical place where the root action occurs"),
        ]
        cur.executemany(
            """
            INSERT OR REPLACE INTO morphological_patterns
            (pattern_id, wazn_vocalized, wazn_unvocalized, wazn_translit,
             verb_form_roman, grammatical_role, semantic_rule_en)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            patterns,
        )

        # 4. Seed Top 320 Core Everyday Words across 14 Units (MSA, Levantine, Egyptian)
        core_320_words = [{'id': 'c1', 'rank': 1, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'أَنَا', 'lev': 'أَنَا', 'egy': 'أَنَا', 'trMsa': 'anā', 'trLev': 'ana', 'trEgy': 'ana', 'eng': 'I / me', 'd': 4.2, 's': 5.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c2', 'rank': 2, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'أَنْتَ', 'lev': 'إِنْتَ', 'egy': 'إِنْتَ', 'trMsa': 'anta', 'trLev': 'inta', 'trEgy': 'inta', 'eng': 'you (masculine)', 'd': 4.6, 's': 5.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c3', 'rank': 3, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'أَنْتِ', 'lev': 'إِنْتِي', 'egy': 'إِنْتِي', 'trMsa': 'anti', 'trLev': 'inti', 'trEgy': 'inti', 'eng': 'you (feminine)', 'd': 5.0, 's': 5.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c4', 'rank': 4, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'هُوَ', 'lev': 'هُوِّ', 'egy': 'هُوَ', 'trMsa': 'huwa', 'trLev': 'huwwe', 'trEgy': 'huwwa', 'eng': 'he / it (m)', 'd': 5.4, 's': 5.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c5', 'rank': 5, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'هِيَ', 'lev': 'هِيِّ', 'egy': 'هِيَّ', 'trMsa': 'hiya', 'trLev': 'hiyye', 'trEgy': 'hiyya', 'eng': 'she / it (f)', 'd': 3.8, 's': 5.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c6', 'rank': 6, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'نَحْنُ', 'lev': 'إِحْنَا', 'egy': 'إِحْنَا', 'trMsa': 'naḥnu', 'trLev': 'iḥna', 'trEgy': 'iḥna', 'eng': 'we / us', 'd': 4.2, 's': 5.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c7', 'rank': 7, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'هُمْ', 'lev': 'هُنِّ', 'egy': 'هُمَّ', 'trMsa': 'hum', 'trLev': 'hunne', 'trEgy': 'humma', 'eng': 'they / them', 'd': 4.6, 's': 5.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c8', 'rank': 8, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'هٰذَا', 'lev': 'هَادَا', 'egy': 'دَه', 'trMsa': 'hādhā', 'trLev': 'haada', 'trEgy': 'dah', 'eng': 'this (m)', 'd': 5.0, 's': 5.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c9', 'rank': 9, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'هٰذِهِ', 'lev': 'هَيْ', 'egy': 'دِي', 'trMsa': 'hādhihi', 'trLev': 'hay', 'trEgy': 'dee', 'eng': 'this (f)', 'd': 5.4, 's': 5.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c10', 'rank': 10, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'مَاذَا', 'lev': 'شُو', 'egy': 'إِيه', 'trMsa': 'mādhā', 'trLev': 'shū', 'trEgy': 'ēh', 'eng': 'what?', 'd': 3.8, 's': 5.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c11', 'rank': 11, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'مَنْ', 'lev': 'مِين', 'egy': 'مِين', 'trMsa': 'man', 'trLev': 'meen', 'trEgy': 'meen', 'eng': 'who?', 'd': 4.2, 's': 5.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c12', 'rank': 12, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'أَيْنَ', 'lev': 'وَيْن', 'egy': 'فِين', 'trMsa': 'ayna', 'trLev': 'weyn', 'trEgy': 'feen', 'eng': 'where?', 'd': 4.6, 's': 5.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c13', 'rank': 13, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'مَتَى', 'lev': 'إِيمْتَى', 'egy': 'إِمْتَى', 'trMsa': 'matā', 'trLev': 'eemta', 'trEgy': 'imta', 'eng': 'when?', 'd': 5.0, 's': 5.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c14', 'rank': 14, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'كَيْفَ', 'lev': 'كِيف', 'egy': 'إِزَّاي', 'trMsa': 'kayfa', 'trLev': 'keef', 'trEgy': 'izzaay', 'eng': 'how?', 'd': 5.4, 's': 5.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c15', 'rank': 15, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'لِمَاذَا', 'lev': 'لَيْش', 'egy': 'لِيه', 'trMsa': 'limādhā', 'trLev': 'leysh', 'trEgy': 'leeh', 'eng': 'why?', 'd': 3.8, 's': 5.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c16', 'rank': 16, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'كَمْ', 'lev': 'أَدِّيش', 'egy': 'كَام', 'trMsa': 'kam', 'trLev': 'addeesh', 'trEgy': 'kaam', 'eng': 'how much / how many?', 'd': 4.2, 's': 5.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c17', 'rank': 17, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'نَعَمْ', 'lev': 'إِي / نَعَم', 'egy': 'أَيْوَه', 'trMsa': 'naʿam', 'trLev': 'ee / naʿam', 'trEgy': 'aywa', 'eng': 'yes', 'd': 4.6, 's': 5.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c18', 'rank': 18, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'لَا', 'lev': 'لَا', 'egy': 'لَأ', 'trMsa': 'lā', 'trLev': 'laa', 'trEgy': 'laʾ', 'eng': 'no', 'd': 5.0, 's': 5.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c19', 'rank': 19, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'فِي', 'lev': 'بـ / فِي', 'egy': 'فِي', 'trMsa': 'fī', 'trLev': 'b- / fī', 'trEgy': 'fī', 'eng': 'in / inside', 'd': 5.4, 's': 5.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c20', 'rank': 20, 'category': 'Pronouns & Particles', 'icon': '🧭', 'root': 'particle', 'msa': 'مَعَ', 'lev': 'مَع', 'egy': 'مَعَ', 'trMsa': 'maʿa', 'trLev': 'maʿ', 'trEgy': 'maʿa', 'eng': 'with', 'd': 3.8, 's': 5.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c21', 'rank': 21, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ك-و-ن', 'msa': 'كَانَ', 'lev': 'كَان', 'egy': 'كَان', 'trMsa': 'kāna', 'trLev': 'kaan', 'trEgy': 'kaan', 'eng': 'he was / to be (past)', 'd': 4.2, 's': 5.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c22', 'rank': 22, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ر-و-د', 'msa': 'أَرَادَ', 'lev': 'بَدُّه', 'egy': 'عَايِز', 'trMsa': 'arāda', 'trLev': 'baddo', 'trEgy': 'ʿaayiz', 'eng': 'to want', 'd': 4.6, 's': 5.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c23', 'rank': 23, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ق-د-ر', 'msa': 'اِسْتَطَاعَ', 'lev': 'قِدِر', 'egy': 'قِدِر', 'trMsa': 'istaṭāʿa', 'trLev': 'qidir', 'trEgy': 'idir', 'eng': 'to be able / can', 'd': 5.0, 's': 5.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c24', 'rank': 24, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ذ-ه-ب', 'msa': 'ذَهَبَ', 'lev': 'رَاح', 'egy': 'رَاح', 'trMsa': 'dhahaba', 'trLev': 'raaḥ', 'trEgy': 'raaḥ', 'eng': 'to go', 'd': 5.4, 's': 4.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c25', 'rank': 25, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ج-ي-ء', 'msa': 'جَاءَ', 'lev': 'إِجَا', 'egy': 'جِه', 'trMsa': 'jāʾa', 'trLev': 'ija', 'trEgy': 'gih', 'eng': 'to come', 'd': 3.8, 's': 4.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c26', 'rank': 26, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ق-و-ل', 'msa': 'قَالَ', 'lev': 'قَال', 'egy': 'قَال', 'trMsa': 'qāla', 'trLev': 'qaal', 'trEgy': 'aal', 'eng': 'to say / tell', 'd': 4.2, 's': 4.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c27', 'rank': 27, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ك-ل-م', 'msa': 'تَكَلَّمَ', 'lev': 'حَكَى', 'egy': 'إِتْكَلِّم', 'trMsa': 'takallama', 'trLev': 'ḥaka', 'trEgy': 'itkallim', 'eng': 'to speak / talk', 'd': 4.6, 's': 4.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c28', 'rank': 28, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ع-ر-ف', 'msa': 'عَرَفَ', 'lev': 'عْرِف', 'egy': 'عِرِف', 'trMsa': 'ʿarafa', 'trLev': 'ʿrif', 'trEgy': 'ʿirif', 'eng': 'to know', 'd': 5.0, 's': 4.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c29', 'rank': 29, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ف-ه-م', 'msa': 'فَهِمَ', 'lev': 'فْهِم', 'egy': 'فِهِم', 'trMsa': 'fahima', 'trLev': 'fhim', 'trEgy': 'fihim', 'eng': 'to understand', 'd': 5.4, 's': 4.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c30', 'rank': 30, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ر-أ-ي', 'msa': 'رَأَى', 'lev': 'شَاف', 'egy': 'شَاف', 'trMsa': 'raʾā', 'trLev': 'shaaf', 'trEgy': 'shaaf', 'eng': 'to see', 'd': 3.8, 's': 4.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c31', 'rank': 31, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'س-م-ع', 'msa': 'سَمِعَ', 'lev': 'سْمِع', 'egy': 'سِمِع', 'trMsa': 'samiʿa', 'trLev': 'smiʿ', 'trEgy': 'simiʿ', 'eng': 'to hear / listen', 'd': 4.2, 's': 4.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c32', 'rank': 32, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'أ-ك-ل', 'msa': 'أَكَلَ', 'lev': 'أَكَل', 'egy': 'كَل', 'trMsa': 'akala', 'trLev': 'akal', 'trEgy': 'kal', 'eng': 'to eat', 'd': 4.6, 's': 4.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c33', 'rank': 33, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ش-ر-ب', 'msa': 'شَرِبَ', 'lev': 'شْرِب', 'egy': 'شِرِب', 'trMsa': 'shariba', 'trLev': 'shrib', 'trEgy': 'shirib', 'eng': 'to drink', 'd': 5.0, 's': 4.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c34', 'rank': 34, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ك-ت-ب', 'msa': 'كَتَبَ', 'lev': 'كَتَب', 'egy': 'كَتَب', 'trMsa': 'kataba', 'trLev': 'katab', 'trEgy': 'katab', 'eng': 'to write', 'd': 5.4, 's': 4.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c35', 'rank': 35, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ق-ر-أ', 'msa': 'قَرَأَ', 'lev': 'قَرَا', 'egy': 'قَرَا', 'trMsa': 'qaraʾa', 'trLev': 'qara', 'trEgy': 'ara', 'eng': 'to read', 'd': 3.8, 's': 4.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c36', 'rank': 36, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ع-م-ل', 'msa': 'عَمِلَ', 'lev': 'اِشْتَغَل', 'egy': 'اِشْتَغَل', 'trMsa': 'ʿamila', 'trLev': 'ishtaghal', 'trEgy': 'ishtaghal', 'eng': 'to work / do', 'd': 4.2, 's': 4.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c37', 'rank': 37, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'أ-خ-ذ', 'msa': 'أَخَذَ', 'lev': 'أَخَد', 'egy': 'خَد', 'trMsa': 'akhadha', 'trLev': 'akhad', 'trEgy': 'khad', 'eng': 'to take', 'd': 4.6, 's': 4.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c38', 'rank': 38, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ع-ط-ي', 'msa': 'أَعْطَى', 'lev': 'عَطَى', 'egy': 'إِدَّى', 'trMsa': 'aʿṭā', 'trLev': 'ʿaṭa', 'trEgy': 'idda', 'eng': 'to give', 'd': 5.0, 's': 4.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c39', 'rank': 39, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ش-ر-ي', 'msa': 'اِشْتَرَى', 'lev': 'اِشْتَرَى', 'egy': 'اِشْتَرَى', 'trMsa': 'ishtarā', 'trLev': 'ishtara', 'trEgy': 'ishtara', 'eng': 'to buy', 'd': 5.4, 's': 4.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c40', 'rank': 40, 'category': 'Core Verbs', 'icon': '⚡', 'root': 'ح-ب-ب', 'msa': 'أَحَبَّ', 'lev': 'حَبّ', 'egy': 'حَبّ', 'trMsa': 'aḥabba', 'trLev': 'ḥabb', 'trEgy': 'ḥabb', 'eng': 'to love / like', 'd': 3.8, 's': 4.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c41', 'rank': 41, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ر-ج-ل', 'msa': 'رَجُلٌ', 'lev': 'رِجَّال', 'egy': 'رَاجِل', 'trMsa': 'rajulun', 'trLev': 'rijjaal', 'trEgy': 'raagil', 'eng': 'man', 'd': 4.2, 's': 4.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c42', 'rank': 42, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'م-ر-أ', 'msa': 'اِمْرَأَةٌ', 'lev': 'مَرَة', 'egy': 'سِتّ', 'trMsa': 'imraʾatun', 'trLev': 'mara', 'trEgy': 'sitt', 'eng': 'woman', 'd': 4.6, 's': 4.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c43', 'rank': 43, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'و-ل-د', 'msa': 'وَلَدٌ', 'lev': 'وَلَد', 'egy': 'وَلَد', 'trMsa': 'waladun', 'trLev': 'walad', 'trEgy': 'walad', 'eng': 'boy / child', 'd': 5.0, 's': 4.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c44', 'rank': 44, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ب-ن-ت', 'msa': 'بِنْتٌ', 'lev': 'بِنْت', 'egy': 'بِنْت', 'trMsa': 'bintun', 'trLev': 'bint', 'trEgy': 'bint', 'eng': 'girl / daughter', 'd': 5.4, 's': 4.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c45', 'rank': 45, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'أ-ب-و', 'msa': 'أَبٌ', 'lev': 'أَب / بَابَا', 'egy': 'أَب / بَابَا', 'trMsa': 'abun', 'trLev': 'ab / baaba', 'trEgy': 'ab / baaba', 'eng': 'father', 'd': 3.8, 's': 4.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c46', 'rank': 46, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'أ-م-م', 'msa': 'أُمٌّ', 'lev': 'إِمّ / مَامَا', 'egy': 'أُمّ / مَامَا', 'trMsa': 'ummun', 'trLev': 'imm / maama', 'trEgy': 'umm / maama', 'eng': 'mother', 'd': 4.2, 's': 4.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c47', 'rank': 47, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'أ-خ-و', 'msa': 'أَخٌ', 'lev': 'أَخ', 'egy': 'أَخ', 'trMsa': 'akhun', 'trLev': 'akh', 'trEgy': 'akh', 'eng': 'brother', 'd': 4.6, 's': 4.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c48', 'rank': 48, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'أ-خ-و', 'msa': 'أُخْتٌ', 'lev': 'إِخْت', 'egy': 'أُخْت', 'trMsa': 'ukhtun', 'trLev': 'ikht', 'trEgy': 'ukht', 'eng': 'sister', 'd': 5.0, 's': 4.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c49', 'rank': 49, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ز-و-ج', 'msa': 'زَوْجٌ', 'lev': 'جَوْز', 'egy': 'جُوز', 'trMsa': 'zawjun', 'trLev': 'jawz', 'trEgy': 'gōz', 'eng': 'husband / spouse', 'd': 5.4, 's': 4.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c50', 'rank': 50, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ز-و-ج', 'msa': 'زَوْجَةٌ', 'lev': 'مَرَة', 'egy': 'مَرَات', 'trMsa': 'zawjatun', 'trLev': 'mara', 'trEgy': 'maraat', 'eng': 'wife', 'd': 3.8, 's': 4.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c51', 'rank': 51, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ب-ن-ي', 'msa': 'اِبْنٌ', 'lev': 'إِبِن', 'egy': 'إِبْن', 'trMsa': 'ibnun', 'trLev': 'ibin', 'trEgy': 'ibn', 'eng': 'son', 'd': 4.2, 's': 4.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c52', 'rank': 52, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ع-أ-ل', 'msa': 'عَائِلَةٌ', 'lev': 'عَيْلِة', 'egy': 'عِيلَة', 'trMsa': 'ʿāʾilatun', 'trLev': 'ʿayle', 'trEgy': 'ʿeela', 'eng': 'family', 'd': 4.6, 's': 4.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c53', 'rank': 53, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ص-د-ق', 'msa': 'صَدِيقٌ', 'lev': 'صَاحِب / رَفِيق', 'egy': 'صَاحِب', 'trMsa': 'ṣadīqun', 'trLev': 'ṣaaḥib / rafeeq', 'trEgy': 'ṣaaḥib', 'eng': 'friend', 'd': 5.0, 's': 4.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c54', 'rank': 54, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ج-و-ر', 'msa': 'جَارٌ', 'lev': 'جَار', 'egy': 'جَار', 'trMsa': 'jārun', 'trLev': 'jaar', 'trEgy': 'gaar', 'eng': 'neighbor', 'd': 5.4, 's': 4.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c55', 'rank': 55, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ن-و-س', 'msa': 'نَاسٌ', 'lev': 'نَاس', 'egy': 'نَاس', 'trMsa': 'nāsun', 'trLev': 'naas', 'trEgy': 'naas', 'eng': 'people', 'd': 3.8, 's': 4.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c56', 'rank': 56, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ش-خ-ص', 'msa': 'شَخْصٌ', 'lev': 'شَخْص', 'egy': 'شَخْص', 'trMsa': 'shakhṣun', 'trLev': 'shakhṣ', 'trEgy': 'shakhṣ', 'eng': 'person / individual', 'd': 4.2, 's': 4.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c57', 'rank': 57, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ض-ي-ف', 'msa': 'ضَيْفٌ', 'lev': 'ضَيْف', 'egy': 'ضِيف', 'trMsa': 'ḍayfun', 'trLev': 'ḍayf', 'trEgy': 'ḍeef', 'eng': 'guest', 'd': 4.6, 's': 4.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c58', 'rank': 58, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ط-ب-ب', 'msa': 'طَبِيبٌ', 'lev': 'دَكْتُور', 'egy': 'دُكْتُور', 'trMsa': 'ṭabībun', 'trLev': 'daktoor', 'trEgy': 'duktoor', 'eng': 'doctor / physician', 'd': 5.0, 's': 4.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c59', 'rank': 59, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ه-ن-د-س', 'msa': 'مُهَنْدِسٌ', 'lev': 'مُهَنْدِس', 'egy': 'مُهَنْدِس', 'trMsa': 'muhandisun', 'trLev': 'muhandis', 'trEgy': 'muhandis', 'eng': 'engineer', 'd': 5.4, 's': 4.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c60', 'rank': 60, 'category': 'People & Family', 'icon': '👨\u200d👩\u200d👧\u200d👦', 'root': 'ع-ل-م', 'msa': 'مُعَلِّمٌ', 'lev': 'أُسْتَاذ', 'egy': 'مُدَرِّس', 'trMsa': 'muʿallimun', 'trLev': 'ustaadh', 'trEgy': 'mudarris', 'eng': 'teacher', 'd': 3.8, 's': 4.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c61', 'rank': 61, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'ي-و-م', 'msa': 'يَوْمٌ', 'lev': 'يُوم', 'egy': 'يُوم', 'trMsa': 'yawmun', 'trLev': 'yōm', 'trEgy': 'yōm', 'eng': 'day', 'd': 4.2, 's': 4.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c62', 'rank': 62, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'ي-و-م', 'msa': 'اليَوْمَ', 'lev': 'اليُوم', 'egy': 'النَّهَارْدَه', 'trMsa': 'al-yawma', 'trLev': 'el-yōm', 'trEgy': 'en-naharda', 'eng': 'today', 'd': 4.6, 's': 4.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c63', 'rank': 63, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'غ-د-و', 'msa': 'غَدًا', 'lev': 'بُكْرَا', 'egy': 'بُكْرَة', 'trMsa': 'ghadan', 'trLev': 'bukra', 'trEgy': 'bukra', 'eng': 'tomorrow', 'd': 5.0, 's': 4.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c64', 'rank': 64, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'أ-م-س', 'msa': 'أَمْسِ', 'lev': 'مْبَارِح', 'egy': 'إِمْبَارِح', 'trMsa': 'amsi', 'trLev': 'mbaariḥ', 'trEgy': 'imbaariḥ', 'eng': 'yesterday', 'd': 5.4, 's': 4.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c65', 'rank': 65, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'أ-و-ن', 'msa': 'الآنَ', 'lev': 'هَلَّق', 'egy': 'دِلْوَقْتِي', 'trMsa': 'al-āna', 'trLev': 'hallaq', 'trEgy': 'dilwaqti', 'eng': 'now', 'd': 3.8, 's': 4.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c66', 'rank': 66, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'و-ق-ت', 'msa': 'وَقْتٌ', 'lev': 'وَقِت', 'egy': 'وَقْت', 'trMsa': 'waqtun', 'trLev': 'waqit', 'trEgy': 'waqt', 'eng': 'time', 'd': 4.2, 's': 4.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c67', 'rank': 67, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'س-و-ع', 'msa': 'سَاعَةٌ', 'lev': 'سَاعَة', 'egy': 'سَاعَة', 'trMsa': 'sāʿatun', 'trLev': 'saaʿa', 'trEgy': 'saaʿa', 'eng': 'hour / clock / watch', 'd': 4.6, 's': 4.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c68', 'rank': 68, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'د-ق-ق', 'msa': 'دَقِيقَةٌ', 'lev': 'دَقِيقَة', 'egy': 'دِقِيقَة', 'trMsa': 'daqīqatun', 'trLev': 'daqeeqa', 'trEgy': 'diʾeeʾa', 'eng': 'minute', 'd': 5.0, 's': 4.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c69', 'rank': 69, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'س-ب-ع', 'msa': 'أُسْبُوعٌ', 'lev': 'أُسْبُوع', 'egy': 'أُسْبُوع', 'trMsa': 'usbūʿun', 'trLev': 'usbooʿ', 'trEgy': 'usbooʿ', 'eng': 'week', 'd': 5.4, 's': 4.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c70', 'rank': 70, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'ش-ه-ر', 'msa': 'شَهْرٌ', 'lev': 'شَهْر', 'egy': 'شَهْر', 'trMsa': 'shahrun', 'trLev': 'shahr', 'trEgy': 'shahr', 'eng': 'month', 'd': 3.8, 's': 4.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c71', 'rank': 71, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'س-ن-و', 'msa': 'سَنَةٌ', 'lev': 'سِنِة', 'egy': 'سَنَة', 'trMsa': 'sanatun', 'trLev': 'sine', 'trEgy': 'sana', 'eng': 'year', 'd': 4.2, 's': 4.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c72', 'rank': 72, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'ص-ب-ح', 'msa': 'صَبَاحٌ', 'lev': 'صُبْح', 'egy': 'صُبْح', 'trMsa': 'ṣabāḥun', 'trLev': 'ṣubḥ', 'trEgy': 'ṣubḥ', 'eng': 'morning', 'd': 4.6, 's': 4.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c73', 'rank': 73, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'م-س-و', 'msa': 'مَسَاءٌ', 'lev': 'مَسَا', 'egy': 'مَسَا', 'trMsa': 'masāʾun', 'trLev': 'masa', 'trEgy': 'masa', 'eng': 'evening', 'd': 5.0, 's': 4.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c74', 'rank': 74, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'ل-ي-ل', 'msa': 'لَيْلٌ', 'lev': 'لَيْل', 'egy': 'لِيل', 'trMsa': 'laylun', 'trLev': 'leyl', 'trEgy': 'leel', 'eng': 'night', 'd': 5.4, 's': 4.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c75', 'rank': 75, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'و-ح-د', 'msa': 'وَاحِدٌ', 'lev': 'وَاحَد', 'egy': 'وَاحِد', 'trMsa': 'wāḥidun', 'trLev': 'waaḥad', 'trEgy': 'waaḥid', 'eng': 'one (1)', 'd': 3.8, 's': 4.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c76', 'rank': 76, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'ث-ن-ي', 'msa': 'اِثْنَانِ', 'lev': 'تْنِين', 'egy': 'اِتْنِين', 'trMsa': 'ithnāni', 'trLev': 'tneen', 'trEgy': 'itneen', 'eng': 'two (2)', 'd': 4.2, 's': 4.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c77', 'rank': 77, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'ث-ل-ث', 'msa': 'ثَلَاثَةٌ', 'lev': 'تْلَاتِة', 'egy': 'تَلَاتَة', 'trMsa': 'thalāthatun', 'trLev': 'tlaate', 'trEgy': 'talaata', 'eng': 'three (3)', 'd': 4.6, 's': 4.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c78', 'rank': 78, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'ر-ب-ع', 'msa': 'أَرْبَعَةٌ', 'lev': 'أَرْبَعَة', 'egy': 'أَرْبَعَة', 'trMsa': 'arbaʿatun', 'trLev': 'arbaʿa', 'trEgy': 'arbaʿa', 'eng': 'four (4)', 'd': 5.0, 's': 4.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c79', 'rank': 79, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'خ-م-س', 'msa': 'خَمْسَةٌ', 'lev': 'خَمْسِة', 'egy': 'خَمْسَة', 'trMsa': 'khamsatun', 'trLev': 'khamse', 'trEgy': 'khamsa', 'eng': 'five (5)', 'd': 5.4, 's': 4.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c80', 'rank': 80, 'category': 'Time & Numbers', 'icon': '⏳', 'root': 'ع-ش-ر', 'msa': 'عَشَرَةٌ', 'lev': 'عَشَرَة', 'egy': 'عَشَرَة', 'trMsa': 'ʿasharatun', 'trLev': 'ʿashara', 'trEgy': 'ʿashara', 'eng': 'ten (10)', 'd': 3.8, 's': 4.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c81', 'rank': 81, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ب-ي-ت', 'msa': 'بَيْتٌ', 'lev': 'بَيْت', 'egy': 'بِيت', 'trMsa': 'baytun', 'trLev': 'beyt', 'trEgy': 'beet', 'eng': 'house / home', 'd': 4.2, 's': 4.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c82', 'rank': 82, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'غ-ر-ف', 'msa': 'غُرْفَةٌ', 'lev': 'أُوضَة / غُرْفِة', 'egy': 'أُوضَة', 'trMsa': 'ghurfatun', 'trLev': 'ooḍa / ghurfe', 'trEgy': 'ooḍa', 'eng': 'room', 'd': 4.6, 's': 4.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c83', 'rank': 83, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ب-و-ب', 'msa': 'بَابٌ', 'lev': 'بَاب', 'egy': 'بَاب', 'trMsa': 'bābun', 'trLev': 'baab', 'trEgy': 'baab', 'eng': 'door / gate', 'd': 5.0, 's': 4.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c84', 'rank': 84, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ش-ب-ك', 'msa': 'شُبَّاكٌ', 'lev': 'شِبَّاك', 'egy': 'شُبَّاك', 'trMsa': 'shubbākun', 'trLev': 'shibbaak', 'trEgy': 'shubbaak', 'eng': 'window', 'd': 5.4, 's': 3.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c85', 'rank': 85, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'م-د-ن', 'msa': 'مَدِينَةٌ', 'lev': 'مَدِينِة', 'egy': 'مَدِينَة', 'trMsa': 'madīnatun', 'trLev': 'madeene', 'trEgy': 'madeena', 'eng': 'city', 'd': 3.8, 's': 3.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c86', 'rank': 86, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ب-ل-د', 'msa': 'بَلَدٌ', 'lev': 'بَلَد', 'egy': 'بَلَد', 'trMsa': 'baladun', 'trLev': 'balad', 'trEgy': 'balad', 'eng': 'country / town', 'd': 4.2, 's': 3.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c87', 'rank': 87, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ش-ر-ع', 'msa': 'شَارِعٌ', 'lev': 'شَارِع', 'egy': 'شَارِع', 'trMsa': 'shāriʿun', 'trLev': 'shaariʿ', 'trEgy': 'shaariʿ', 'eng': 'street', 'd': 4.6, 's': 3.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c88', 'rank': 88, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'س-و-ق', 'msa': 'سُوقٌ', 'lev': 'سُوق', 'egy': 'سُوق', 'trMsa': 'sūqun', 'trLev': 'sooq', 'trEgy': 'sooʾ', 'eng': 'market', 'd': 5.0, 's': 3.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c89', 'rank': 89, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ط-ع-م', 'msa': 'مَطْعَمٌ', 'lev': 'مَطْعَم', 'egy': 'مَطْعَم', 'trMsa': 'maṭʿamun', 'trLev': 'maṭʿam', 'trEgy': 'maṭʿam', 'eng': 'restaurant', 'd': 5.4, 's': 3.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c90', 'rank': 90, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ق-ه-و', 'msa': 'مَقْهًى', 'lev': 'قَهْوِة / كَافِيه', 'egy': 'قَهْوَة / كَافِيه', 'trMsa': 'maqhan', 'trLev': 'qahwe / kaafeeh', 'trEgy': 'ahwa / kaafeeh', 'eng': 'café / coffeehouse', 'd': 3.8, 's': 3.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c91', 'rank': 91, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ف-ن-د-ق', 'msa': 'فُنْدُقٌ', 'lev': 'أُوتِيل / فُنْدُق', 'egy': 'فُنْدُق', 'trMsa': 'funduqun', 'trLev': 'oteel / funduq', 'trEgy': 'funduʾ', 'eng': 'hotel', 'd': 4.2, 's': 3.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c92', 'rank': 92, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ط-ي-ر', 'msa': 'مَطَارٌ', 'lev': 'مَطَار', 'egy': 'مَطَار', 'trMsa': 'maṭārun', 'trLev': 'maṭaar', 'trEgy': 'maṭaar', 'eng': 'airport', 'd': 4.6, 's': 3.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c93', 'rank': 93, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ش-ف-ي', 'msa': 'مُسْتَشْفًى', 'lev': 'مُسْتَشْفَى', 'egy': 'مُسْتَشْفَى', 'trMsa': 'mustashfan', 'trLev': 'mustashfa', 'trEgy': 'mustashfa', 'eng': 'hospital', 'd': 5.0, 's': 3.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c94', 'rank': 94, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'د-ر-س', 'msa': 'مَدْرَسَةٌ', 'lev': 'مَدْرَسِة', 'egy': 'مَدْرَسَة', 'trMsa': 'madrasatun', 'trLev': 'madrase', 'trEgy': 'madrasa', 'eng': 'school', 'd': 5.4, 's': 3.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c95', 'rank': 95, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ج-م-ع', 'msa': 'جَامِعَةٌ', 'lev': 'جَامْعَة', 'egy': 'جَامْعَة', 'trMsa': 'jāmiʿatun', 'trLev': 'jaamʿa', 'trEgy': 'gamʿa', 'eng': 'university', 'd': 3.8, 's': 3.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c96', 'rank': 96, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ك-ت-ب', 'msa': 'مَكْتَبٌ', 'lev': 'مَكْتَب', 'egy': 'مَكْتَب', 'trMsa': 'maktabun', 'trLev': 'maktab', 'trEgy': 'maktab', 'eng': 'office / desk', 'd': 4.2, 's': 3.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c97', 'rank': 97, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ك-ت-ب', 'msa': 'مَكْتَبَةٌ', 'lev': 'مَكْتَبِة', 'egy': 'مَكْتَبَة', 'trMsa': 'maktabatun', 'trLev': 'maktabe', 'trEgy': 'maktaba', 'eng': 'library / bookstore', 'd': 4.6, 's': 3.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c98', 'rank': 98, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ش-ر-ك', 'msa': 'شَرِكَةٌ', 'lev': 'شَرِكَة', 'egy': 'شَرِكَة', 'trMsa': 'sharikatun', 'trLev': 'sharike', 'trEgy': 'shirka', 'eng': 'company / firm', 'd': 5.0, 's': 3.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c99', 'rank': 99, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'م-و-ق-ع', 'msa': 'مَوْقِعٌ', 'lev': 'مَوْقِع', 'egy': 'مَوْقِع', 'trMsa': 'mawqiʿun', 'trLev': 'mawqiʿ', 'trEgy': 'mawqiʿ', 'eng': 'site / location / website', 'd': 5.4, 's': 3.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c100', 'rank': 100, 'category': 'Places & Home', 'icon': '🏛️', 'root': 'ح-د-ق', 'msa': 'حَدِيقَةٌ', 'lev': 'جْنَيْنِة / حَدِيقَة', 'egy': 'جِنِينَة', 'trMsa': 'ḥadīqatun', 'trLev': 'jneyne / ḥadeeqa', 'trEgy': 'gineena', 'eng': 'park / garden', 'd': 3.8, 's': 3.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c101', 'rank': 101, 'category': 'Food & Café', 'icon': '☕', 'root': 'م-و-ه', 'msa': 'مَاءٌ', 'lev': 'مَيّ', 'egy': 'مَيَّة', 'trMsa': 'māʾun', 'trLev': 'mayy', 'trEgy': 'mayya', 'eng': 'water', 'd': 4.2, 's': 3.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c102', 'rank': 102, 'category': 'Food & Café', 'icon': '☕', 'root': 'ق-ه-و', 'msa': 'قَهْوَةٌ', 'lev': 'قَهْوِة', 'egy': 'قَهْوَة', 'trMsa': 'qahwatun', 'trLev': 'qahwe', 'trEgy': 'ahwa', 'eng': 'coffee', 'd': 4.6, 's': 3.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c103', 'rank': 103, 'category': 'Food & Café', 'icon': '☕', 'root': 'ش-و-ي', 'msa': 'شَايٌ', 'lev': 'شَاي', 'egy': 'شَاي', 'trMsa': 'shāyun', 'trLev': 'shaay', 'trEgy': 'shaay', 'eng': 'tea', 'd': 5.0, 's': 3.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c104', 'rank': 104, 'category': 'Food & Café', 'icon': '☕', 'root': 'ح-ل-ب', 'msa': 'حَلِيبٌ', 'lev': 'حَلِيب', 'egy': 'لَبَن', 'trMsa': 'ḥalībun', 'trLev': 'ḥaleeb', 'trEgy': 'laban', 'eng': 'milk', 'd': 5.4, 's': 3.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c105', 'rank': 105, 'category': 'Food & Café', 'icon': '☕', 'root': 'س-ك-ر', 'msa': 'سُكَّرٌ', 'lev': 'سُكَّر', 'egy': 'سُكَّر', 'trMsa': 'sukkarun', 'trLev': 'sukkar', 'trEgy': 'sukkar', 'eng': 'sugar', 'd': 3.8, 's': 3.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c106', 'rank': 106, 'category': 'Food & Café', 'icon': '☕', 'root': 'خ-ب-ز', 'msa': 'خُبْزٌ', 'lev': 'خِبْز', 'egy': 'عِيش', 'trMsa': 'khubzun', 'trLev': 'khibz', 'trEgy': 'ʿeesh', 'eng': 'bread', 'd': 4.2, 's': 3.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c107', 'rank': 107, 'category': 'Food & Café', 'icon': '☕', 'root': 'ر-ز-ز', 'msa': 'أُرُزٌّ', 'lev': 'رِزّ', 'egy': 'رُزّ', 'trMsa': 'uruzzun', 'trLev': 'rizz', 'trEgy': 'ruzz', 'eng': 'rice', 'd': 4.6, 's': 3.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c108', 'rank': 108, 'category': 'Food & Café', 'icon': '☕', 'root': 'د-ج-ج', 'msa': 'دَجَاجٌ', 'lev': 'جَاج', 'egy': 'فِرَاخ', 'trMsa': 'dajājun', 'trLev': 'jaaj', 'trEgy': 'firaakh', 'eng': 'chicken', 'd': 5.0, 's': 3.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c109', 'rank': 109, 'category': 'Food & Café', 'icon': '☕', 'root': 'ل-ح-م', 'msa': 'لَحْمٌ', 'lev': 'لَحْمِة', 'egy': 'لَحْمَة', 'trMsa': 'laḥmun', 'trLev': 'laḥme', 'trEgy': 'laḥma', 'eng': 'meat', 'd': 5.4, 's': 3.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c110', 'rank': 110, 'category': 'Food & Café', 'icon': '☕', 'root': 'س-م-ك', 'msa': 'سَمَكٌ', 'lev': 'سَمَك', 'egy': 'سَمَك', 'trMsa': 'samakun', 'trLev': 'samak', 'trEgy': 'samak', 'eng': 'fish', 'd': 3.8, 's': 3.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c111', 'rank': 111, 'category': 'Food & Café', 'icon': '☕', 'root': 'ف-ط-ر', 'msa': 'فَطُورٌ', 'lev': 'فْطُور', 'egy': 'فِطَار', 'trMsa': 'faṭūrun', 'trLev': 'fṭoor', 'trEgy': 'fiṭaar', 'eng': 'breakfast', 'd': 4.2, 's': 3.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c112', 'rank': 112, 'category': 'Food & Café', 'icon': '☕', 'root': 'غ-د-و', 'msa': 'غَدَاءٌ', 'lev': 'غَدَا', 'egy': 'غَدَا', 'trMsa': 'ghadāʾun', 'trLev': 'ghada', 'trEgy': 'ghada', 'eng': 'lunch', 'd': 4.6, 's': 3.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c113', 'rank': 113, 'category': 'Food & Café', 'icon': '☕', 'root': 'ع-ش-و', 'msa': 'عَشَاءٌ', 'lev': 'عَشَا', 'egy': 'عَشَا', 'trMsa': 'ʿashāʾun', 'trLev': 'ʿasha', 'trEgy': 'ʿasha', 'eng': 'dinner', 'd': 5.0, 's': 3.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c114', 'rank': 114, 'category': 'Food & Café', 'icon': '☕', 'root': 'ف-ك-ه', 'msa': 'فَاكِهَةٌ', 'lev': 'فَوَاكِه', 'egy': 'فَاكْهَة', 'trMsa': 'fākihatun', 'trLev': 'fawaakih', 'trEgy': 'fakha', 'eng': 'fruit', 'd': 5.4, 's': 3.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c115', 'rank': 115, 'category': 'Food & Café', 'icon': '☕', 'root': 'ت-ف-ح', 'msa': 'تُفَّاحٌ', 'lev': 'تِفَّاح', 'egy': 'تُفَّاح', 'trMsa': 'tuffāḥun', 'trLev': 'tiffaaḥ', 'trEgy': 'tuffaaḥ', 'eng': 'apples', 'd': 3.8, 's': 3.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c116', 'rank': 116, 'category': 'Food & Café', 'icon': '☕', 'root': 'م-ل-ح', 'msa': 'مِلْحٌ', 'lev': 'مِلِح', 'egy': 'مَلْح', 'trMsa': 'milḥun', 'trLev': 'miliḥ', 'trEgy': 'malḥ', 'eng': 'salt', 'd': 4.2, 's': 3.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c117', 'rank': 117, 'category': 'Food & Café', 'icon': '☕', 'root': 'ط-ع-م', 'msa': 'طَعَامٌ', 'lev': 'أَكِل', 'egy': 'أَكْل', 'trMsa': 'ṭaʿāmun', 'trLev': 'akil', 'trEgy': 'akl', 'eng': 'food / meal', 'd': 4.6, 's': 3.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c118', 'rank': 118, 'category': 'Food & Café', 'icon': '☕', 'root': 'ح-س-ب', 'msa': 'حِسَابٌ', 'lev': 'حْسَاب', 'egy': 'حِسَاب', 'trMsa': 'ḥisābun', 'trLev': 'ḥsaab', 'trEgy': 'ḥisaab', 'eng': 'bill / check', 'd': 5.0, 's': 3.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c119', 'rank': 119, 'category': 'Food & Café', 'icon': '☕', 'root': 'ق-و-م', 'msa': 'قَائِمَةُ الطَّعَامِ', 'lev': 'المِينْيُو', 'egy': 'المِينْيُو', 'trMsa': 'qāʾimatu al-ṭaʿāmi', 'trLev': 'el-meenyu', 'trEgy': 'el-meenyu', 'eng': 'menu', 'd': 5.4, 's': 3.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c120', 'rank': 120, 'category': 'Food & Café', 'icon': '☕', 'root': 'ل-ذ-ذ', 'msa': 'لَذِيذٌ', 'lev': 'طَيِّب', 'egy': 'لَذِيذ / حِلْو', 'trMsa': 'ladhīdhun', 'trLev': 'ṭayyib', 'trEgy': 'lazeez / ḥilw', 'eng': 'delicious / tasty', 'd': 3.8, 's': 3.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c121', 'rank': 121, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'س-ي-ر', 'msa': 'سَيَّارَةٌ', 'lev': 'سَيَّارَة', 'egy': 'عَرَبِيَّة', 'trMsa': 'sayyāratun', 'trLev': 'sayyaara', 'trEgy': 'ʿarabiyya', 'eng': 'car / vehicle', 'd': 4.2, 's': 3.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c122', 'rank': 122, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ح-ف-ل', 'msa': 'حَافِلَةٌ', 'lev': 'بَاص', 'egy': 'أُوتُوبِيس', 'trMsa': 'ḥāfilatun', 'trLev': 'baaṣ', 'trEgy': 'otobees', 'eng': 'bus', 'd': 4.6, 's': 3.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c123', 'rank': 123, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ق-ط-ر', 'msa': 'قِطَارٌ', 'lev': 'قِطَار', 'egy': 'قَطْر', 'trMsa': 'qiṭārun', 'trLev': 'qiṭaar', 'trEgy': 'aṭr', 'eng': 'train', 'd': 5.0, 's': 3.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c124', 'rank': 124, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ط-ي-ر', 'msa': 'طَائِرَةٌ', 'lev': 'طَيَّارَة', 'egy': 'طَيَّارَة', 'trMsa': 'ṭāʾiratun', 'trLev': 'ṭayyaara', 'trEgy': 'ṭayyaara', 'eng': 'airplane', 'd': 5.4, 's': 3.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c125', 'rank': 125, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'د-ر-ج', 'msa': 'دَرَّاجَةٌ', 'lev': 'بِسِكْلِيت', 'egy': 'عَجَلَة', 'trMsa': 'darrājatun', 'trLev': 'bisikleet', 'trEgy': 'ʿagala', 'eng': 'bicycle / bike', 'd': 3.8, 's': 3.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c126', 'rank': 126, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ط-ر-ق', 'msa': 'طَرِيقٌ', 'lev': 'طَرِيق', 'egy': 'طَرِيق', 'trMsa': 'ṭarīqun', 'trLev': 'ṭareeq', 'trEgy': 'ṭareeʾ', 'eng': 'road / way', 'd': 4.2, 's': 3.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c127', 'rank': 127, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ج-و-ز', 'msa': 'جَوَازُ السَّفَرِ', 'lev': 'جَوَاز السَّفَر', 'egy': 'جَوَاز السَّفَر', 'trMsa': 'jawāzu al-safari', 'trLev': 'jawaaz el-safar', 'trEgy': 'gawaaz el-safar', 'eng': 'passport', 'd': 4.6, 's': 3.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c128', 'rank': 128, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ذ-ك-ر', 'msa': 'تَذْكِرَةٌ', 'lev': 'تَذْكَرَة', 'egy': 'تَذْكَرَة', 'trMsa': 'tadhkiratun', 'trLev': 'tazkara', 'trEgy': 'tazkara', 'eng': 'ticket', 'd': 5.0, 's': 3.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c129', 'rank': 129, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ح-ق-ب', 'msa': 'حَقِيبَةٌ', 'lev': 'شَنْطَة', 'egy': 'شَنْطَة', 'trMsa': 'ḥaqībatun', 'trLev': 'shanṭa', 'trEgy': 'shanṭa', 'eng': 'bag / suitcase', 'd': 5.4, 's': 3.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c130', 'rank': 130, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ي-م-ن', 'msa': 'يَمِينٌ', 'lev': 'يَمِين', 'egy': 'يِمِين', 'trMsa': 'yamīnun', 'trLev': 'yameen', 'trEgy': 'yimeen', 'eng': 'right (direction)', 'd': 3.8, 's': 3.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c131', 'rank': 131, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ي-س-ر', 'msa': 'يَسَارٌ', 'lev': 'شْمَال', 'egy': 'شِمَال', 'trMsa': 'yasārun', 'trLev': 'shmaal', 'trEgy': 'shimaal', 'eng': 'left (direction)', 'd': 4.2, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c132', 'rank': 132, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ق-د-م', 'msa': 'إِلَى الأَمَامِ', 'lev': 'دُغْرِي', 'egy': 'عَلَى طُول', 'trMsa': 'ilā al-amāmi', 'trLev': 'dughri', 'trEgy': 'ʿala ṭool', 'eng': 'straight ahead', 'd': 4.6, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c133', 'rank': 133, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ق-ر-ب', 'msa': 'قَرِيبٌ', 'lev': 'قَرِيب', 'egy': 'قُرَيِّب', 'trMsa': 'qarībun', 'trLev': 'qareeb', 'trEgy': 'urayyib', 'eng': 'near / close', 'd': 5.0, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c134', 'rank': 134, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ب-ع-د', 'msa': 'بَعِيدٌ', 'lev': 'بْعِيد', 'egy': 'بِعِيد', 'trMsa': 'baʿīdun', 'trLev': 'bʿeed', 'trEgy': 'biʿeed', 'eng': 'far', 'd': 5.4, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c135', 'rank': 135, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ه-ن-ا', 'msa': 'هُنَا', 'lev': 'هُون', 'egy': 'هِنَا', 'trMsa': 'hunā', 'trLev': 'hōn', 'trEgy': 'hina', 'eng': 'here', 'd': 3.8, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c136', 'rank': 136, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ه-ن-ك', 'msa': 'هُنَاكَ', 'lev': 'هُنَاك', 'egy': 'هِنَاك', 'trMsa': 'hunāka', 'trLev': 'hunaak', 'trEgy': 'hinaak', 'eng': 'there', 'd': 4.2, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c137', 'rank': 137, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'د-خ-ل', 'msa': 'دَاخِلٌ', 'lev': 'جُوَّا', 'egy': 'جُوَّه', 'trMsa': 'dākhilun', 'trLev': 'juwwa', 'trEgy': 'guwwa', 'eng': 'inside', 'd': 4.6, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c138', 'rank': 138, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'خ-ر-ج', 'msa': 'خَارِجٌ', 'lev': 'بَرَّا', 'egy': 'بَرَّه', 'trMsa': 'khārijun', 'trLev': 'barra', 'trEgy': 'barra', 'eng': 'outside', 'd': 5.0, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c139', 'rank': 139, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ف-و-ق', 'msa': 'فَوْقَ', 'lev': 'فُوق', 'egy': 'فُوق', 'trMsa': 'fawqa', 'trLev': 'fōq', 'trEgy': 'fōʾ', 'eng': 'above / upstairs', 'd': 5.4, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c140', 'rank': 140, 'category': 'Travel & Directions', 'icon': '✈️', 'root': 'ت-ح-ت', 'msa': 'تَحْتَ', 'lev': 'تَحِت', 'egy': 'تَحْت', 'trMsa': 'taḥta', 'trLev': 'taḥit', 'trEgy': 'taḥt', 'eng': 'below / under', 'd': 3.8, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c141', 'rank': 141, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ك-ت-ب', 'msa': 'كِتَابٌ', 'lev': 'كْتَاب', 'egy': 'كِتَاب', 'trMsa': 'kitābun', 'trLev': 'ktaab', 'trEgy': 'kitaab', 'eng': 'book', 'd': 4.2, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c142', 'rank': 142, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ق-ل-م', 'msa': 'قَلَمٌ', 'lev': 'قَلَم', 'egy': 'قَلَم', 'trMsa': 'qalamun', 'trLev': 'qalam', 'trEgy': 'alam', 'eng': 'pen', 'd': 4.6, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c143', 'rank': 143, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'د-ر-س', 'msa': 'دَرْسٌ', 'lev': 'دَرْس', 'egy': 'دَرْس', 'trMsa': 'darsun', 'trLev': 'dars', 'trEgy': 'dars', 'eng': 'lesson', 'd': 5.0, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c144', 'rank': 144, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ل-غ-و', 'msa': 'لُغَةٌ', 'lev': 'لُغَة', 'egy': 'لُغَة', 'trMsa': 'lughatun', 'trLev': 'lugha', 'trEgy': 'lugha', 'eng': 'language', 'd': 5.4, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c145', 'rank': 145, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ك-ل-م', 'msa': 'كَلِمَةٌ', 'lev': 'كِلْمِة', 'egy': 'كِلْمَة', 'trMsa': 'kalimatun', 'trLev': 'kilme', 'trEgy': 'kilma', 'eng': 'word', 'd': 3.8, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c146', 'rank': 146, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'س-أ-ل', 'msa': 'سُؤَالٌ', 'lev': 'سُؤَال', 'egy': 'سُؤَال', 'trMsa': 'suʾālun', 'trLev': 'suʾaal', 'trEgy': 'suʾaal', 'eng': 'question', 'd': 4.2, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c147', 'rank': 147, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ج-و-ب', 'msa': 'جَوَابٌ', 'lev': 'جَوَاب', 'egy': 'جَوَاب', 'trMsa': 'jawābun', 'trLev': 'jawaab', 'trEgy': 'gawaab', 'eng': 'answer', 'd': 4.6, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c148', 'rank': 148, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ش-ك-ل', 'msa': 'مُشْكِلَةٌ', 'lev': 'مُشْكْلِة', 'egy': 'مُشْكِلَة', 'trMsa': 'mushkilatun', 'trLev': 'mushkle', 'trEgy': 'mushkila', 'eng': 'problem / issue', 'd': 5.0, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c149', 'rank': 149, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ح-ل-ل', 'msa': 'حَلٌّ', 'lev': 'حَلّ', 'egy': 'حَلّ', 'trMsa': 'ḥallun', 'trLev': 'ḥall', 'trEgy': 'ḥall', 'eng': 'solution', 'd': 5.4, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c150', 'rank': 150, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ف-ك-ر', 'msa': 'فِكْرَةٌ', 'lev': 'فِكْرَة', 'egy': 'فِكْرَة', 'trMsa': 'fikratun', 'trLev': 'fikra', 'trEgy': 'fikra', 'eng': 'idea', 'd': 3.8, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c151', 'rank': 151, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ش-ر-ع', 'msa': 'مَشْرُوعٌ', 'lev': 'مَشْرُوع', 'egy': 'مَشْرُوع', 'trMsa': 'mashrūʿun', 'trLev': 'mashrooʿ', 'trEgy': 'mashrooʿ', 'eng': 'project', 'd': 4.2, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c152', 'rank': 152, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ب-ي-ن', 'msa': 'بَيَانَاتٌ', 'lev': 'بَيَانَات', 'egy': 'بَيَانَات', 'trMsa': 'bayānātun', 'trLev': 'bayaanaat', 'trEgy': 'bayaanaat', 'eng': 'data', 'd': 4.6, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c153', 'rank': 153, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ن-ظ-م', 'msa': 'نِظَامٌ', 'lev': 'نِظَام / سِيسْتِم', 'egy': 'نِظَام', 'trMsa': 'niẓāmun', 'trLev': 'niẓaam', 'trEgy': 'niẓaam', 'eng': 'system', 'd': 5.0, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c154', 'rank': 154, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ع-د-د', 'msa': 'مُعَدَّاتٌ', 'lev': 'مُعَدَّات', 'egy': 'مُعِدَّات', 'trMsa': 'muʿaddātun', 'trLev': 'muʿaddaat', 'trEgy': 'muʿiddaat', 'eng': 'equipment / fleet', 'd': 5.4, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c155', 'rank': 155, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ه-ت-ف', 'msa': 'هَاتِفٌ', 'lev': 'تَلِفُون / مُوبَايْل', 'egy': 'مُوبَايْل', 'trMsa': 'hātifun', 'trLev': 'talifōn / mōbaayl', 'trEgy': 'mōbaayl', 'eng': 'phone / mobile', 'd': 3.8, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c156', 'rank': 156, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ح-س-ب', 'msa': 'حَاسُوبٌ', 'lev': 'كُمْبْيُوتَر', 'egy': 'كُمْبْيُوتَر', 'trMsa': 'ḥāsūbun', 'trLev': 'kumbyootar', 'trEgy': 'kumbyootar', 'eng': 'computer', 'd': 4.2, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c157', 'rank': 157, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ر-س-ل', 'msa': 'رِسَالَةٌ', 'lev': 'رِسَالِة', 'egy': 'رِسَالَة', 'trMsa': 'risālatun', 'trLev': 'risaale', 'trEgy': 'risaala', 'eng': 'message / letter', 'd': 4.6, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c158', 'rank': 158, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'م-و-ل', 'msa': 'مَالٌ', 'lev': 'مَصَارِي', 'egy': 'فُلُوس', 'trMsa': 'mālun', 'trLev': 'maṣaari', 'trEgy': 'fuloos', 'eng': 'money', 'd': 5.0, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c159', 'rank': 159, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ع-م-ل', 'msa': 'عَمَلٌ', 'lev': 'شُغُل', 'egy': 'شُغْل', 'trMsa': 'ʿamalun', 'trLev': 'shughul', 'trEgy': 'shughl', 'eng': 'work / job', 'd': 5.4, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c160', 'rank': 160, 'category': 'Work, Tech & Study', 'icon': '💻', 'root': 'ف-ر-ق', 'msa': 'فَرِيقٌ', 'lev': 'فَرِيق', 'egy': 'فَرِيق', 'trMsa': 'farīqun', 'trLev': 'fareeq', 'trEgy': 'fareeʾ', 'eng': 'team', 'd': 3.8, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c161', 'rank': 161, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ج-ي-د', 'msa': 'جَيِّدٌ', 'lev': 'مْنِيح', 'egy': 'كُوَيِّس', 'trMsa': 'jayyidun', 'trLev': 'mneeḥ', 'trEgy': 'kuwayyis', 'eng': 'good', 'd': 4.2, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c162', 'rank': 162, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'م-ت-ز', 'msa': 'مُمْتَازٌ', 'lev': 'مُمْتَاز', 'egy': 'مُمْتَاز', 'trMsa': 'mumtāzun', 'trLev': 'mumtaaz', 'trEgy': 'mumtaaz', 'eng': 'excellent', 'd': 4.6, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c163', 'rank': 163, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ك-ب-ر', 'msa': 'كَبِيرٌ', 'lev': 'كْبِير', 'egy': 'كِبِير', 'trMsa': 'kabīrun', 'trLev': 'kbeer', 'trEgy': 'kibeer', 'eng': 'big / large', 'd': 5.0, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c164', 'rank': 164, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ص-غ-ر', 'msa': 'صَغِيرٌ', 'lev': 'زْغِير', 'egy': 'صُغَيَّر', 'trMsa': 'ṣaghīrun', 'trLev': 'zgheer', 'trEgy': 'ṣughayyar', 'eng': 'small / young', 'd': 5.4, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c165', 'rank': 165, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ج-د-د', 'msa': 'جَدِيدٌ', 'lev': 'جْدِيد', 'egy': 'جِدِيد', 'trMsa': 'jadīdun', 'trLev': 'jdeed', 'trEgy': 'gideed', 'eng': 'new', 'd': 3.8, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c166', 'rank': 166, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ق-د-م', 'msa': 'قَدِيمٌ', 'lev': 'قَدِيم', 'egy': 'قَدِيم', 'trMsa': 'qadīmun', 'trLev': 'qadeem', 'trEgy': 'adeem', 'eng': 'old / ancient', 'd': 4.2, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c167', 'rank': 167, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ج-م-ل', 'msa': 'جَمِيلٌ', 'lev': 'حِلُو', 'egy': 'جَمِيل / حِلْو', 'trMsa': 'jamīlun', 'trLev': 'ḥilo', 'trEgy': 'gameel / ḥilw', 'eng': 'beautiful / nice', 'd': 4.6, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c168', 'rank': 168, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'س-ر-ع', 'msa': 'سَرِيعٌ', 'lev': 'سَرِيع', 'egy': 'سَرِيع', 'trMsa': 'sarīʿun', 'trLev': 'sareeʿ', 'trEgy': 'sareeʿ', 'eng': 'fast', 'd': 5.0, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c169', 'rank': 169, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'س-ه-ل', 'msa': 'سَهْلٌ', 'lev': 'سَهِل', 'egy': 'سَهْل', 'trMsa': 'sahlun', 'trLev': 'sahil', 'trEgy': 'sahl', 'eng': 'easy', 'd': 5.4, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c170', 'rank': 170, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ص-ع-ب', 'msa': 'صَعْبٌ', 'lev': 'صَعِب', 'egy': 'صَعْب', 'trMsa': 'ṣaʿbun', 'trLev': 'ṣaʿib', 'trEgy': 'ṣaʿb', 'eng': 'difficult / hard', 'd': 3.8, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c171', 'rank': 171, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ح-ر-ر', 'msa': 'حَارٌّ', 'lev': 'سُخُن / شُوب', 'egy': 'حَرّ / سُخْن', 'trMsa': 'ḥārrun', 'trLev': 'sukhun / shōb', 'trEgy': 'ḥarr / sukhn', 'eng': 'hot', 'd': 4.2, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c172', 'rank': 172, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ب-ر-د', 'msa': 'بَارِدٌ', 'lev': 'بَارِد', 'egy': 'بَارِد / سَاقِع', 'trMsa': 'bāridun', 'trLev': 'baarid', 'trEgy': 'baarid / saaʾiʿ', 'eng': 'cold', 'd': 4.6, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c173', 'rank': 173, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'غ-ل-و', 'msa': 'غَالٍ', 'lev': 'غَالِي', 'egy': 'غَالِي', 'trMsa': 'ghālin', 'trLev': 'ghaali', 'trEgy': 'ghaali', 'eng': 'expensive / precious', 'd': 5.0, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c174', 'rank': 174, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ر-خ-ص', 'msa': 'رَخِيصٌ', 'lev': 'رْخِيص', 'egy': 'رِخِيص', 'trMsa': 'rakhīṣun', 'trLev': 'rkheeṣ', 'trEgy': 'rikheeṣ', 'eng': 'cheap / inexpensive', 'd': 5.4, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c175', 'rank': 175, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ك-ث-ر', 'msa': 'كَثِيرٌ', 'lev': 'كْتِير', 'egy': 'كِتِير', 'trMsa': 'kathīrun', 'trLev': 'kteer', 'trEgy': 'kiteer', 'eng': 'much / many / very', 'd': 3.8, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c176', 'rank': 176, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ق-ل-ل', 'msa': 'قَلِيلٌ', 'lev': 'شْوَيّ', 'egy': 'شُوَيَّة', 'trMsa': 'qalīlun', 'trLev': 'shwayy', 'trEgy': 'shuwayya', 'eng': 'a little / few', 'd': 4.2, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c177', 'rank': 177, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ك-ل-ل', 'msa': 'كُلٌّ', 'lev': 'كِلّ', 'egy': 'كُلّ', 'trMsa': 'kullun', 'trLev': 'kill', 'trEgy': 'kull', 'eng': 'all / every', 'd': 4.6, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c178', 'rank': 178, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ب-ي-ض', 'msa': 'أَبْيَضُ', 'lev': 'أَبْيَض', 'egy': 'أَبْيَض', 'trMsa': 'abyaḍu', 'trLev': 'abyaḍ', 'trEgy': 'abyaḍ', 'eng': 'white', 'd': 5.0, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c179', 'rank': 179, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'س-و-د', 'msa': 'أَسْوَدُ', 'lev': 'أَسْوَد', 'egy': 'إِسْوِد', 'trMsa': 'aswadu', 'trLev': 'aswad', 'trEgy': 'iswid', 'eng': 'black', 'd': 5.4, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c180', 'rank': 180, 'category': 'Adjectives & Colors', 'icon': '🎨', 'root': 'ح-م-ر', 'msa': 'أَحْمَرُ', 'lev': 'أَحْمَر', 'egy': 'أَحْمَر', 'trMsa': 'aḥmaru', 'trLev': 'aḥmar', 'trEgy': 'aḥmar', 'eng': 'red', 'd': 3.8, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c181', 'rank': 181, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'س-ل-م', 'msa': 'السَّلَامُ عَلَيْكُمْ', 'lev': 'السَّلَامُ عَلَيْكُم', 'egy': 'السَّلَامُ عَلَيْكُم', 'trMsa': 'al-salāmu ʿalaykum', 'trLev': 'as-salaamu ʿalaykum', 'trEgy': 'as-salaamu ʿalaykum', 'eng': 'peace be upon you (hello)', 'd': 4.2, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c182', 'rank': 182, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'س-ل-م', 'msa': 'وَعَلَيْكُمُ السَّلَامُ', 'lev': 'وَعَلَيْكُم السَّلَام', 'egy': 'وَعَلَيْكُم السَّلَام', 'trMsa': 'wa ʿalaykumu al-salāmu', 'trLev': 'wa ʿalaykum as-salaam', 'trEgy': 'wa ʿalaykum as-salaam', 'eng': 'and upon you be peace', 'd': 4.6, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c183', 'rank': 183, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ر-ح-ب', 'msa': 'مَرْحَبًا', 'lev': 'مَرْحَبَا', 'egy': 'أَهْلًا', 'trMsa': 'marḥaban', 'trLev': 'marḥaba', 'trEgy': 'ahlan', 'eng': 'hello / welcome', 'd': 5.0, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c184', 'rank': 184, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'أ-ه-ل', 'msa': 'أَهْلًا وَسَهْلًا', 'lev': 'أَهْلًا وَسَهْلًا', 'egy': 'أَهْلًا وَسَهْلًا', 'trMsa': 'ahlan wa sahlan', 'trLev': 'ahlan wa sahlan', 'trEgy': 'ahlan wa sahlan', 'eng': 'welcome (warm greeting)', 'd': 5.4, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c185', 'rank': 185, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ص-ب-ح', 'msa': 'صَبَاحُ الخَيْرِ', 'lev': 'صَبَاح الخَيْر', 'egy': 'صَبَاح الخِير', 'trMsa': 'ṣabāḥu al-khayri', 'trLev': 'ṣabaaḥ el-kheyr', 'trEgy': 'ṣabaaḥ el-kheer', 'eng': 'good morning', 'd': 3.8, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c186', 'rank': 186, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ص-ب-ح', 'msa': 'صَبَاحُ النُّورِ', 'lev': 'صَبَاح النُّور', 'egy': 'صَبَاح النُّور', 'trMsa': 'ṣabāḥu al-nūri', 'trLev': 'ṣabaaḥ en-noor', 'trEgy': 'ṣabaaḥ en-noor', 'eng': 'morning of light (reply)', 'd': 4.2, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c187', 'rank': 187, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'م-س-و', 'msa': 'مَسَاءُ الخَيْرِ', 'lev': 'مَسَا الخَيْر', 'egy': 'مَسَاء الخِير', 'trMsa': 'masāʾu al-khayri', 'trLev': 'masa el-kheyr', 'trEgy': 'masaaʾ el-kheer', 'eng': 'good evening', 'd': 4.6, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c188', 'rank': 188, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ح-و-ل', 'msa': 'كَيْفَ حَالُكَ؟', 'lev': 'كِيفَك؟ / شْلُونَك؟', 'egy': 'إِزَّيَّك؟ / عَامِل إِيه؟', 'trMsa': 'kayfa ḥāluka?', 'trLev': 'keefak? / shlōnak?', 'trEgy': 'izzayyak? / ʿaamil ēh?', 'eng': 'how are you?', 'd': 5.0, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c189', 'rank': 189, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ح-م-د', 'msa': 'الحَمْدُ لِلّٰهِ', 'lev': 'الحَمْدُ لِلّٰه', 'egy': 'الحَمْدُ لِلّٰه', 'trMsa': 'al-ḥamdu lillāhi', 'trLev': 'el-ḥamdu lillaah', 'trEgy': 'el-ḥamdu lillaah', 'eng': 'praise be to God (I am well)', 'd': 5.4, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c190', 'rank': 190, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ش-ك-ر', 'msa': 'شُكْرًا جَزِيلًا', 'lev': 'شُكْرًا كْتِير', 'egy': 'شُكْرًا جَزِيلًا', 'trMsa': 'shukran jazīlan', 'trLev': 'shukran kteer', 'trEgy': 'shukran gazeelan', 'eng': 'thank you very much', 'd': 3.8, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c191', 'rank': 191, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ع-ف-و', 'msa': 'عَفْوًا', 'lev': 'عَفْوًا / وَلَوْ', 'egy': 'العَفْو', 'trMsa': 'ʿafwan', 'trLev': 'ʿafwan / walaw', 'trEgy': 'el-ʿafw', 'eng': "you're welcome / excuse me", 'd': 4.2, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c192', 'rank': 192, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ف-ض-ل', 'msa': 'مِنْ فَضْلِكَ', 'lev': 'لَوْ سَمَحْت', 'egy': 'لَوْ سَمَحْت', 'trMsa': 'min faḍlika', 'trLev': 'law samaḥt', 'trEgy': 'law samaḥt', 'eng': 'please (if you permit)', 'd': 4.6, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c193', 'rank': 193, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ف-ض-ل', 'msa': 'تَفَضَّلْ', 'lev': 'تْفَضَّل', 'egy': 'اِتْفَضَّل', 'trMsa': 'tafaḍḍal', 'trLev': 'tfaḍḍal', 'trEgy': 'itfaḍḍal', 'eng': 'here you go / please come in', 'd': 5.0, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c194', 'rank': 194, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'أ-س-ف', 'msa': 'أَنَا آسِفٌ', 'lev': 'آسِف / عَفْوًا', 'egy': 'أَنَا آسِف / مَعْلِشّ', 'trMsa': 'anā āsifun', 'trLev': 'aasif / ʿafwan', 'trEgy': 'ana aasif / maʿlish', 'eng': 'I am sorry', 'd': 5.4, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c195', 'rank': 195, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ش-ر-ف', 'msa': 'تَشَرَّفْنَا', 'lev': 'تْشَرَّفْنَا', 'egy': 'اِتْشَرَّفْنَا', 'trMsa': 'tasharrafnā', 'trLev': 'tsharrafna', 'trEgy': 'itsharrafna', 'eng': 'honored to meet you', 'd': 3.8, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c196', 'rank': 196, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'س-ل-م', 'msa': 'مَعَ السَّلَامَةِ', 'lev': 'مَع السَّلَامِة', 'egy': 'مَعَ السَّلَامَة', 'trMsa': 'maʿa al-salāmati', 'trLev': 'maʿ es-salaame', 'trEgy': 'maʿa es-salaama', 'eng': 'goodbye (go with safety)', 'd': 4.2, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c197', 'rank': 197, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ش-ي-ء', 'msa': 'إِنْ شَاءَ اللّٰهُ', 'lev': 'إِنْ شَاءَ اللّٰه', 'egy': 'إِنْ شَاءَ اللّٰه', 'trMsa': 'in shāʾa Allāhu', 'trLev': 'in shaaʾ Allaah', 'trEgy': 'in shaaʾ Allaah', 'eng': 'God willing / hopefully', 'd': 4.6, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c198', 'rank': 198, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'م-ك-ن', 'msa': 'مُمْكِنْ؟', 'lev': 'مُمْكِن؟', 'egy': 'مُمْكِن؟', 'trMsa': 'mumkin?', 'trLev': 'mumkin?', 'trEgy': 'mumkin?', 'eng': 'is it possible? / may I?', 'd': 5.0, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c199', 'rank': 199, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'ط-ب-ع', 'msa': 'طَبْعًا', 'lev': 'طَبْعًا / أَكِيد', 'egy': 'طَبْعًا / أَكِيد', 'trMsa': 'ṭabʿan', 'trLev': 'ṭabʿan / akeed', 'trEgy': 'ṭabʿan / akeed', 'eng': 'of course / certainly', 'd': 5.4, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c200', 'rank': 200, 'category': 'Conversational Expressions', 'icon': '🗣️', 'root': 'س-ل-م', 'msa': 'سَلِمَتْ يَدَاكَ', 'lev': 'يِسْلَمُو إِيدَيْك', 'egy': 'تِسْلَم إِيدَك', 'trMsa': 'salimat yadāka', 'trLev': 'yislamo eedeyk', 'trEgy': 'tislam eedak', 'eng': 'may your hands be blessed (thank you)', 'd': 3.8, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c201', 'rank': 201, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ر-أ-س', 'msa': 'رَأْسٌ', 'lev': 'رَاس', 'egy': 'رَاس', 'trMsa': 'raʾsun', 'trLev': 'raas', 'trEgy': 'raas', 'eng': 'head', 'd': 4.2, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c202', 'rank': 202, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ع-ي-ن', 'msa': 'عَيْنٌ', 'lev': 'عَيْن', 'egy': 'عِين', 'trMsa': 'ʿaynun', 'trLev': 'ʿayn', 'trEgy': 'ʿeen', 'eng': 'eye', 'd': 4.6, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c203', 'rank': 203, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'أ-ذ-ن', 'msa': 'أُذُنٌ', 'lev': 'دَان / إِذِن', 'egy': 'وِدْن', 'trMsa': 'udhunun', 'trLev': 'daan / idhin', 'trEgy': 'widn', 'eng': 'ear', 'd': 5.0, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c204', 'rank': 204, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'أ-ن-ف', 'msa': 'أَنْفٌ', 'lev': 'مِنْخَار', 'egy': 'مَنَاخِير', 'trMsa': 'anfun', 'trLev': 'minkhaar', 'trEgy': 'manaakheer', 'eng': 'nose', 'd': 5.4, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c205', 'rank': 205, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ف-و-ه', 'msa': 'فَمٌ', 'lev': 'تِمّ', 'egy': 'بُقّ', 'trMsa': 'famun', 'trLev': 'timm', 'trEgy': 'buʾʾ', 'eng': 'mouth', 'd': 3.8, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c206', 'rank': 206, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ي-د-ي', 'msa': 'يَدٌ', 'lev': 'إِيد', 'egy': 'إِيد', 'trMsa': 'yadun', 'trLev': 'eed', 'trEgy': 'eed', 'eng': 'hand / arm', 'd': 4.2, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c207', 'rank': 207, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ق-ل-ب', 'msa': 'قَلْبٌ', 'lev': 'قَلْب', 'egy': 'قَلْب', 'trMsa': 'qalbun', 'trLev': 'qalb', 'trEgy': 'alb', 'eng': 'heart', 'd': 4.6, 's': 3.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c208', 'rank': 208, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ظ-ه-ر', 'msa': 'ظَهْرٌ', 'lev': 'ضَهْر', 'egy': 'ضَهْر', 'trMsa': 'ẓahrun', 'trLev': 'ḍahr', 'trEgy': 'ḍahr', 'eng': 'back', 'd': 5.0, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c209', 'rank': 209, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ب-ط-ن', 'msa': 'بَطْنٌ', 'lev': 'بَطِن', 'egy': 'بَطْن', 'trMsa': 'baṭnun', 'trLev': 'baṭin', 'trEgy': 'baṭn', 'eng': 'stomach', 'd': 5.4, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c210', 'rank': 210, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ر-ج-ل', 'msa': 'قَدَمٌ', 'lev': 'إِجِر', 'egy': 'رِجْل', 'trMsa': 'qadamun', 'trLev': 'ijir', 'trEgy': 'rigl', 'eng': 'foot / leg', 'd': 3.8, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c211', 'rank': 211, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ص-ح-ح', 'msa': 'صِحَّةٌ', 'lev': 'صِحَّة', 'egy': 'صِحَّة', 'trMsa': 'ṣiḥḥatun', 'trLev': 'ṣiḥḥa', 'trEgy': 'ṣiḥḥa', 'eng': 'health', 'd': 4.2, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c212', 'rank': 212, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'م-ر-ض', 'msa': 'مَرِيضٌ', 'lev': 'مَرِيض / تَعْبَان', 'egy': 'عَيَّان / تَعْبَان', 'trMsa': 'marīḍun', 'trLev': 'mareeḍ / taʿbaan', 'trEgy': 'ʿayyaan / taʿbaan', 'eng': 'sick / patient', 'd': 4.6, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c213', 'rank': 213, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'أ-ل-م', 'msa': 'أَلَمٌ', 'lev': 'وَجَع', 'egy': 'وَجَع', 'trMsa': 'alamun', 'trLev': 'wajaʿ', 'trEgy': 'wagaʿ', 'eng': 'pain / ache', 'd': 5.0, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c214', 'rank': 214, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ص-د-ع', 'msa': 'صُدَاعٌ', 'lev': 'وَجَع رَاس', 'egy': 'صُدَاع', 'trMsa': 'ṣudāʿun', 'trLev': 'wajaʿ raas', 'trEgy': 'ṣudaaʿ', 'eng': 'headache', 'd': 5.4, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c215', 'rank': 215, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ح-ر-ر', 'msa': 'حُمَّى', 'lev': 'حَرَارَة', 'egy': 'سُخُونِيَّة', 'trMsa': 'ḥummā', 'trLev': 'ḥaraara', 'trEgy': 'sukhooniyya', 'eng': 'fever', 'd': 3.8, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c216', 'rank': 216, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'د-و-ي', 'msa': 'دَوَاءٌ', 'lev': 'دَوَا', 'egy': 'دَوَا', 'trMsa': 'dawāʾun', 'trLev': 'dawa', 'trEgy': 'dawa', 'eng': 'medicine / medication', 'd': 4.2, 's': 3.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c217', 'rank': 217, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ص-ي-د-ل', 'msa': 'صَيْدَلِيَّةٌ', 'lev': 'صَيْدَلِيِّة', 'egy': 'صَيْدَلِيَّة', 'trMsa': 'ṣaydaliyyatun', 'trLev': 'ṣaydaliyye', 'trEgy': 'ṣaydaliyya', 'eng': 'pharmacy', 'd': 4.6, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c218', 'rank': 218, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ع-ي-د', 'msa': 'عِيَادَةٌ', 'lev': 'عِيَادِة', 'egy': 'عِيَادَة', 'trMsa': 'ʿiyādatun', 'trLev': 'ʿiyaade', 'trEgy': 'ʿiyaada', 'eng': 'clinic', 'd': 5.0, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c219', 'rank': 219, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'س-ع-ف', 'msa': 'إِسْعَافٌ', 'lev': 'إِسْعَاف', 'egy': 'إِسْعَاف', 'trMsa': 'isʿāfun', 'trLev': 'isʿaaf', 'trEgy': 'isʿaaf', 'eng': 'ambulance / first aid', 'd': 5.4, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c220', 'rank': 220, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ر-و-ح', 'msa': 'اِسْتَرَاحَ', 'lev': 'اِرْتَاح', 'egy': 'اِسْتَرَيَّح', 'trMsa': 'istarāḥa', 'trLev': 'irtaaḥ', 'trEgy': 'istarayyaḥ', 'eng': 'to rest', 'd': 3.8, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c221', 'rank': 221, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'س-ل-م', 'msa': 'سَلَامَتُكَ', 'lev': 'سَلَامْتَك', 'egy': 'أَلْف سَلَامَة', 'trMsa': 'salāmatuka', 'trLev': 'salaamtak', 'trEgy': 'alf salaama', 'eng': 'get well soon!', 'd': 4.2, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c222', 'rank': 222, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ن-و-م', 'msa': 'نَوْمٌ', 'lev': 'نُوم', 'egy': 'نُوم', 'trMsa': 'nawmun', 'trLev': 'nōm', 'trEgy': 'nōm', 'eng': 'sleep', 'd': 4.6, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c223', 'rank': 223, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ي-ق-ظ', 'msa': 'اِسْتَيْقَظَ', 'lev': 'فَاق / صِحِي', 'egy': 'صِحِي', 'trMsa': 'istayqaẓa', 'trLev': 'faaq / ṣiḥi', 'trEgy': 'ṣiḥi', 'eng': 'to wake up', 'd': 5.0, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c224', 'rank': 224, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'غ-س-ل', 'msa': 'غَسَلَ', 'lev': 'غَسَل', 'egy': 'غَسَل', 'trMsa': 'ghasala', 'trLev': 'ghasal', 'trEgy': 'ghasal', 'eng': 'to wash', 'd': 5.4, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c225', 'rank': 225, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ن-ظ-ف', 'msa': 'نَظِيفٌ', 'lev': 'نْضِيف', 'egy': 'نِضِيف', 'trMsa': 'naẓīfun', 'trLev': 'nḍeef', 'trEgy': 'niḍeef', 'eng': 'clean', 'd': 3.8, 's': 2.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c226', 'rank': 226, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ق-و-ي', 'msa': 'قَوِيٌّ', 'lev': 'قَوِي', 'egy': 'قَوِي', 'trMsa': 'qawiyyun', 'trLev': 'qawi', 'trEgy': 'awi', 'eng': 'strong', 'd': 4.2, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c227', 'rank': 227, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ت-ع-ب', 'msa': 'مُتْعَبٌ', 'lev': 'تَعْبَان', 'egy': 'تَعْبَان', 'trMsa': 'mutʿabun', 'trLev': 'taʿbaan', 'trEgy': 'taʿbaan', 'eng': 'tired', 'd': 4.6, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c228', 'rank': 228, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ج-و-ع', 'msa': 'جَائِعٌ', 'lev': 'جُوعَان', 'egy': 'جَعَان', 'trMsa': 'jāʾiʿun', 'trLev': 'jooʿaan', 'trEgy': 'gaʿaan', 'eng': 'hungry', 'd': 5.0, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c229', 'rank': 229, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ع-ط-ش', 'msa': 'عَطْشَانُ', 'lev': 'عَطْشَان', 'egy': 'عَطْشَان', 'trMsa': 'ʿaṭshānu', 'trLev': 'ʿaṭshaan', 'trEgy': 'ʿaṭshaan', 'eng': 'thirsty', 'd': 5.4, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c230', 'rank': 230, 'category': 'Body, Health & Pharmacy', 'icon': '🩺', 'root': 'ح-س-س', 'msa': 'حَسَاسِيَّةٌ', 'lev': 'حَسَاسِيِّة', 'egy': 'حَسَاسِيَّة', 'trMsa': 'ḥasāsiyyatun', 'trLev': 'ḥasaasiyye', 'trEgy': 'ḥasaasiyya', 'eng': 'allergy', 'd': 3.8, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c231', 'rank': 231, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ش-م-س', 'msa': 'شَمْسٌ', 'lev': 'شَمِس', 'egy': 'شَمْس', 'trMsa': 'shamsun', 'trLev': 'shamis', 'trEgy': 'shams', 'eng': 'sun', 'd': 4.2, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c232', 'rank': 232, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ق-م-ر', 'msa': 'قَمَرٌ', 'lev': 'قَمَر', 'egy': 'قَمَر', 'trMsa': 'qamarun', 'trLev': 'qamar', 'trEgy': 'amar', 'eng': 'moon', 'd': 4.6, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c233', 'rank': 233, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'س-م-و', 'msa': 'سَمَاءٌ', 'lev': 'سَمَا', 'egy': 'سَمَا', 'trMsa': 'samāʾun', 'trLev': 'sama', 'trEgy': 'sama', 'eng': 'sky', 'd': 5.0, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c234', 'rank': 234, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ه-و-ي', 'msa': 'هَوَاءٌ', 'lev': 'هَوَا', 'egy': 'هَوَا', 'trMsa': 'hawāʾun', 'trLev': 'hawa', 'trEgy': 'hawa', 'eng': 'air / wind', 'd': 5.4, 's': 2.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c235', 'rank': 235, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'م-ط-ر', 'msa': 'مَطَرٌ', 'lev': 'مَطَر / شِتَا', 'egy': 'مَطَر', 'trMsa': 'maṭarun', 'trLev': 'maṭar / shita', 'trEgy': 'maṭar', 'eng': 'rain', 'd': 3.8, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c236', 'rank': 236, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ط-ق-س', 'msa': 'طَقْسٌ', 'lev': 'طَقِس / جَوّ', 'egy': 'جَوّ', 'trMsa': 'ṭaqsun', 'trLev': 'ṭaqis / jaww', 'trEgy': 'gaww', 'eng': 'weather', 'd': 4.2, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c237', 'rank': 237, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ب-ح-ر', 'msa': 'بَحْرٌ', 'lev': 'بَحِر', 'egy': 'بَحْر', 'trMsa': 'baḥrun', 'trLev': 'baḥir', 'trEgy': 'baḥr', 'eng': 'sea', 'd': 4.6, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c238', 'rank': 238, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ن-ه-ر', 'msa': 'نَهْرٌ', 'lev': 'نَهَر', 'egy': 'نَهْر', 'trMsa': 'nahrun', 'trLev': 'nahar', 'trEgy': 'nahr', 'eng': 'river', 'd': 5.0, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c239', 'rank': 239, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ج-ب-ل', 'msa': 'جَبَلٌ', 'lev': 'جَبَل', 'egy': 'جَبَل', 'trMsa': 'jabalun', 'trLev': 'jabal', 'trEgy': 'gabal', 'eng': 'mountain', 'd': 5.4, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c240', 'rank': 240, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ص-ح-ر', 'msa': 'صَحْرَاءُ', 'lev': 'صَحْرَا', 'egy': 'صَحْرَا', 'trMsa': 'ṣaḥrāʾu', 'trLev': 'ṣaḥra', 'trEgy': 'ṣaḥra', 'eng': 'desert', 'd': 3.8, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c241', 'rank': 241, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ش-ج-ر', 'msa': 'شَجَرَةٌ', 'lev': 'شَجَرَة', 'egy': 'شَجَرَة', 'trMsa': 'shajaratun', 'trLev': 'shajara', 'trEgy': 'shagara', 'eng': 'tree', 'd': 4.2, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c242', 'rank': 242, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'و-ر-د', 'msa': 'وَرْدٌ', 'lev': 'وَرِد', 'egy': 'وَرْد', 'trMsa': 'wardun', 'trLev': 'warid', 'trEgy': 'ward', 'eng': 'flowers / roses', 'd': 4.6, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c243', 'rank': 243, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'أ-ر-ض', 'msa': 'أَرْضٌ', 'lev': 'أَرِض', 'egy': 'أَرْض', 'trMsa': 'arḍun', 'trLev': 'ariḍ', 'trEgy': 'arḍ', 'eng': 'earth / land / ground', 'd': 5.0, 's': 2.7, 'elapsed': 1.0, 'reps': 1}, {'id': 'c244', 'rank': 244, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ن-و-ر', 'msa': 'نَارٌ', 'lev': 'نَار', 'egy': 'نَار', 'trMsa': 'nārun', 'trLev': 'naar', 'trEgy': 'naar', 'eng': 'fire', 'd': 5.4, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c245', 'rank': 245, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ث-ل-ج', 'msa': 'ثَلْجٌ', 'lev': 'تَلِج', 'egy': 'تَلْج', 'trMsa': 'thaljun', 'trLev': 'talij', 'trEgy': 'talg', 'eng': 'snow / ice', 'd': 3.8, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c246', 'rank': 246, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ص-ي-ف', 'msa': 'صَيْفٌ', 'lev': 'صَيْف', 'egy': 'صِيف', 'trMsa': 'ṣayfun', 'trLev': 'ṣayf', 'trEgy': 'ṣeef', 'eng': 'summer', 'd': 4.2, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c247', 'rank': 247, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ش-ت-و', 'msa': 'شِتَاءٌ', 'lev': 'شِتَا', 'egy': 'شِتَا', 'trMsa': 'shitāʾun', 'trLev': 'shita', 'trEgy': 'shita', 'eng': 'winter', 'd': 4.6, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c248', 'rank': 248, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ر-ب-ع', 'msa': 'رَبِيعٌ', 'lev': 'رَبِيع', 'egy': 'رَبِيع', 'trMsa': 'rabīʿun', 'trLev': 'rabeeʿ', 'trEgy': 'rabeeʿ', 'eng': 'spring', 'd': 5.0, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c249', 'rank': 249, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'خ-ر-ف', 'msa': 'خَرِيفٌ', 'lev': 'خَرِيف', 'egy': 'خَرِيف', 'trMsa': 'kharīfun', 'trLev': 'khareef', 'trEgy': 'khareef', 'eng': 'autumn / fall', 'd': 5.4, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c250', 'rank': 250, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ش-ر-ق', 'msa': 'شَرْقٌ', 'lev': 'شَرِق', 'egy': 'شَرْق', 'trMsa': 'sharqun', 'trLev': 'shariq', 'trEgy': 'sharʾ', 'eng': 'east', 'd': 3.8, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c251', 'rank': 251, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'غ-ر-ب', 'msa': 'غَرْبٌ', 'lev': 'غَرِب', 'egy': 'غَرْب', 'trMsa': 'gharbun', 'trLev': 'gharib', 'trEgy': 'gharb', 'eng': 'west', 'd': 4.2, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c252', 'rank': 252, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ش-م-ل', 'msa': 'شَمَالٌ', 'lev': 'شَمَال', 'egy': 'شَمَال', 'trMsa': 'shamālun', 'trLev': 'shamaal', 'trEgy': 'shamaal', 'eng': 'north', 'd': 4.6, 's': 2.6, 'elapsed': 1.0, 'reps': 1}, {'id': 'c253', 'rank': 253, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ج-ن-ب', 'msa': 'جَنُوبٌ', 'lev': 'جَنُوب', 'egy': 'جَنُوب', 'trMsa': 'janūbun', 'trLev': 'janoob', 'trEgy': 'ganoob', 'eng': 'south', 'd': 5.0, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c254', 'rank': 254, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ع-و-ل-م', 'msa': 'عَالَمٌ', 'lev': 'عَالَم', 'egy': 'عَالَم', 'trMsa': 'ʿālamun', 'trLev': 'ʿaalam', 'trEgy': 'ʿaalam', 'eng': 'world', 'd': 5.4, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c255', 'rank': 255, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ط-ب-ع', 'msa': 'طَبِيعَةٌ', 'lev': 'طَبِيعَة', 'egy': 'طَبِيعَة', 'trMsa': 'ṭabīʿatun', 'trLev': 'ṭabeeʿa', 'trEgy': 'ṭabeeʿa', 'eng': 'nature', 'd': 3.8, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c256', 'rank': 256, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ن-ج-م', 'msa': 'نَجْمٌ', 'lev': 'نِجْمِة', 'egy': 'نِجْمَة', 'trMsa': 'najmun', 'trLev': 'nijme', 'trEgy': 'nigma', 'eng': 'star', 'd': 4.2, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c257', 'rank': 257, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ض-و-ء', 'msa': 'ضَوْءٌ', 'lev': 'ضَوّ', 'egy': 'نُور', 'trMsa': 'ḍawʾun', 'trLev': 'ḍaww', 'trEgy': 'noor', 'eng': 'light', 'd': 4.6, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c258', 'rank': 258, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ظ-ل-ل', 'msa': 'ظِلٌّ', 'lev': 'فَيّ', 'egy': 'ضِلّ', 'trMsa': 'ẓillun', 'trLev': 'fayy', 'trEgy': 'ḍill', 'eng': 'shade / shadow', 'd': 5.0, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c259', 'rank': 259, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ح-ج-ر', 'msa': 'حَجَرٌ', 'lev': 'حَجَر', 'egy': 'حَجَر', 'trMsa': 'ḥajarun', 'trLev': 'ḥajar', 'trEgy': 'ḥagar', 'eng': 'stone / rock', 'd': 5.4, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c260', 'rank': 260, 'category': 'Weather & Nature', 'icon': '🌿', 'root': 'ر-م-ل', 'msa': 'رَمْلٌ', 'lev': 'رَمِل', 'egy': 'رَمْل', 'trMsa': 'ramlun', 'trLev': 'ramil', 'trEgy': 'raml', 'eng': 'sand', 'd': 3.8, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c261', 'rank': 261, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'particle', 'msa': 'وَ', 'lev': 'و', 'egy': 'و', 'trMsa': 'wa', 'trLev': 'w', 'trEgy': 'w', 'eng': 'and', 'd': 4.2, 's': 2.5, 'elapsed': 1.0, 'reps': 1}, {'id': 'c262', 'rank': 262, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'particle', 'msa': 'أَوْ', 'lev': 'أَوْ / وَلَّا', 'egy': 'أَوْ / وَلَّا', 'trMsa': 'aw', 'trLev': 'aw / walla', 'trEgy': 'aw / walla', 'eng': 'or', 'd': 4.6, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c263', 'rank': 263, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'particle', 'msa': 'لٰكِنْ', 'lev': 'بَسّ / لٰكِن', 'egy': 'بَسّ / لٰكِن', 'trMsa': 'lākin', 'trLev': 'bass / laakin', 'trEgy': 'bass / laakin', 'eng': 'but / however', 'd': 5.0, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c264', 'rank': 264, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'particle', 'msa': 'لِأَنَّ', 'lev': 'لِأَنُّه / عَشَان', 'egy': 'عَشَان', 'trMsa': 'li-anna', 'trLev': 'li-anno / ʿashaan', 'trEgy': 'ʿashaan', 'eng': 'because', 'd': 5.4, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c265', 'rank': 265, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'particle', 'msa': 'إِذَا', 'lev': 'إِذَا / لَوْ', 'egy': 'لَوْ', 'trMsa': 'idhā', 'trLev': 'idha / law', 'trEgy': 'law', 'eng': 'if', 'd': 3.8, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c266', 'rank': 266, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'particle', 'msa': 'عِنْدَمَا', 'lev': 'لَمَّا', 'egy': 'لَمَّا', 'trMsa': 'ʿindamā', 'trLev': 'lamma', 'trEgy': 'lamma', 'eng': 'when (conjunction)', 'd': 4.2, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c267', 'rank': 267, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ق-ب-ل', 'msa': 'قَبْلَ', 'lev': 'قَبِل', 'egy': 'قَبْل', 'trMsa': 'qabla', 'trLev': 'qabil', 'trEgy': 'abl', 'eng': 'before', 'd': 4.6, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c268', 'rank': 268, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ب-ع-د', 'msa': 'بَعْدَ', 'lev': 'بَعِد', 'egy': 'بَعْد', 'trMsa': 'baʿda', 'trLev': 'baʿid', 'trEgy': 'baʿd', 'eng': 'after', 'd': 5.0, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c269', 'rank': 269, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'م-ن', 'msa': 'مِنْ', 'lev': 'مِن', 'egy': 'مِن', 'trMsa': 'min', 'trLev': 'min', 'trEgy': 'min', 'eng': 'from', 'd': 5.4, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c270', 'rank': 270, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'إ-ل-ى', 'msa': 'إِلَى', 'lev': 'عَـ / لَـ', 'egy': 'لِـ / عَلَى', 'trMsa': 'ilā', 'trLev': 'ʿa- / la-', 'trEgy': 'li- / ʿala', 'eng': 'to / toward', 'd': 3.8, 's': 2.4, 'elapsed': 1.0, 'reps': 1}, {'id': 'c271', 'rank': 271, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ع-ل-ى', 'msa': 'عَلَى', 'lev': 'عَلَى / عَـ', 'egy': 'عَلَى', 'trMsa': 'ʿalā', 'trLev': 'ʿala / ʿa-', 'trEgy': 'ʿala', 'eng': 'on / upon', 'd': 4.2, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c272', 'rank': 272, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ع-ن', 'msa': 'عَنْ', 'lev': 'عَن', 'egy': 'عَن', 'trMsa': 'ʿan', 'trLev': 'ʿan', 'trEgy': 'ʿan', 'eng': 'about / away from', 'd': 4.6, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c273', 'rank': 273, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ب-ي-ن', 'msa': 'بَيْنَ', 'lev': 'بَيْن', 'egy': 'بِين', 'trMsa': 'bayna', 'trLev': 'beyn', 'trEgy': 'been', 'eng': 'between', 'd': 5.0, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c274', 'rank': 274, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ع-ن-د', 'msa': 'عِنْدِي', 'lev': 'عِنْدِي', 'egy': 'عِنْدِي', 'trMsa': 'ʿindī', 'trLev': 'ʿindi', 'trEgy': 'ʿindi', 'eng': 'I have (at me)', 'd': 5.4, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c275', 'rank': 275, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'م-ع', 'msa': 'مَعِي', 'lev': 'مَعِي', 'egy': 'مَعَايَا', 'trMsa': 'maʿī', 'trLev': 'maʿi', 'trEgy': 'maʿaaya', 'eng': 'I have with me', 'd': 3.8, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c276', 'rank': 276, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'و-ج-د', 'msa': 'هُنَاكَ / يُوجَدُ', 'lev': 'فِي', 'egy': 'فِيه', 'trMsa': 'yūjadu', 'trLev': 'fī', 'trEgy': 'feeh', 'eng': 'there is / there are', 'd': 4.2, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c277', 'rank': 277, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'particle', 'msa': 'لَيْسَ', 'lev': 'مُو / مِش', 'egy': 'مِش', 'trMsa': 'laysa', 'trLev': 'mū / mish', 'trEgy': 'mish', 'eng': 'is not / not', 'd': 4.6, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c278', 'rank': 278, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'أ-ي-ض', 'msa': 'أَيْضًا', 'lev': 'كَمَان', 'egy': 'كَمَان', 'trMsa': 'ayḍan', 'trLev': 'kamaan', 'trEgy': 'kamaan', 'eng': 'also / too', 'd': 5.0, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c279', 'rank': 279, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ف-ق-ط', 'msa': 'فَقَطْ', 'lev': 'بَسّ', 'egy': 'بَسّ', 'trMsa': 'faqaṭ', 'trLev': 'bass', 'trEgy': 'bass', 'eng': 'only / just', 'd': 5.4, 's': 2.3, 'elapsed': 1.0, 'reps': 1}, {'id': 'c280', 'rank': 280, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ج-د-د', 'msa': 'جِدًّا', 'lev': 'كْتِير', 'egy': 'قَوِي / جِدًّا', 'trMsa': 'jiddan', 'trLev': 'kteer', 'trEgy': 'awi / giddan', 'eng': 'very', 'd': 3.8, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c281', 'rank': 281, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ت-ق-ر-ب', 'msa': 'تَقْرِيبًا', 'lev': 'تَقْرِيبًا', 'egy': 'تَقْرِيبًا', 'trMsa': 'taqrīban', 'trLev': 'taqreeban', 'trEgy': 'taʾreeban', 'eng': 'approximately / almost', 'd': 4.2, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c282', 'rank': 282, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'د-و-م', 'msa': 'دَائِمًا', 'lev': 'دَايْمًا', 'egy': 'دَايْمًا', 'trMsa': 'dāʾiman', 'trLev': 'daayman', 'trEgy': 'daayman', 'eng': 'always', 'd': 4.6, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c283', 'rank': 283, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ح-ي-ن', 'msa': 'أَحْيَانًا', 'lev': 'أَحْيَانًا', 'egy': 'سَاعَات', 'trMsa': 'aḥyānan', 'trLev': 'aḥyaanan', 'trEgy': 'saaʿaat', 'eng': 'sometimes', 'd': 5.0, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c284', 'rank': 284, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'أ-ب-د', 'msa': 'أَبَدًا', 'lev': 'أَبَدًا / بِالمَرَّة', 'egy': 'أَبَدًا / خَالِص', 'trMsa': 'abadan', 'trLev': 'abadan / bil-marra', 'trEgy': 'abadan / khaaliṣ', 'eng': 'never / not at all', 'd': 5.4, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c285', 'rank': 285, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'س-و-ي', 'msa': 'مَعًا', 'lev': 'سَوَا', 'egy': 'سَوَا', 'trMsa': 'maʿan', 'trLev': 'sawa', 'trEgy': 'sawa', 'eng': 'together', 'd': 3.8, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c286', 'rank': 286, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'و-ح-د', 'msa': 'وَحْدِي', 'lev': 'لَحَالِي', 'egy': 'لِوَحْدِي', 'trMsa': 'waḥdī', 'trLev': 'la-ḥaali', 'trEgy': 'li-waḥdi', 'eng': 'by myself / alone', 'd': 4.2, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c287', 'rank': 287, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'أ-و-ل', 'msa': 'أَوَّلًا', 'lev': 'أَوَّل شِي', 'egy': 'أَوَّل حَاجَة', 'trMsa': 'awwalan', 'trLev': 'awwal shi', 'trEgy': 'awwal ḥaaga', 'eng': 'firstly / first of all', 'd': 4.6, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c288', 'rank': 288, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'ث-م-م', 'msa': 'ثُمَّ', 'lev': 'بَعْدِين', 'egy': 'وبَعْدِين', 'trMsa': 'thumma', 'trLev': 'baʿdeyn', 'trEgy': 'w-baʿdeen', 'eng': 'then / after that', 'd': 5.0, 's': 2.2, 'elapsed': 1.0, 'reps': 1}, {'id': 'c289', 'rank': 289, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'أ-خ-ر', 'msa': 'أَخِيرًا', 'lev': 'بِالآخِر', 'egy': 'فِي الآخِر', 'trMsa': 'akhīran', 'trLev': 'bil-aakhir', 'trEgy': 'fi el-aakhir', 'eng': 'finally', 'd': 5.4, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c290', 'rank': 290, 'category': 'Sentence Glue & Connectors', 'icon': '🔗', 'root': 'م-ث-ل', 'msa': 'مَثَلًا', 'lev': 'مَثَلًا', 'egy': 'مَثَلًا', 'trMsa': 'mathalan', 'trLev': 'mathalan', 'trEgy': 'masalan', 'eng': 'for example', 'd': 3.8, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c291', 'rank': 291, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'س-ع-د', 'msa': 'سَعِيدٌ', 'lev': 'مَبْسُوط', 'egy': 'مَبْسُوط', 'trMsa': 'saʿīdun', 'trLev': 'mabsooṭ', 'trEgy': 'mabsooṭ', 'eng': 'happy / glad', 'd': 4.2, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c292', 'rank': 292, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ح-ز-ن', 'msa': 'حَزِينٌ', 'lev': 'زَعْلَان', 'egy': 'زَعْلَان', 'trMsa': 'ḥazīnun', 'trLev': 'zaʿlaan', 'trEgy': 'zaʿlaan', 'eng': 'sad / upset', 'd': 4.6, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c293', 'rank': 293, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'غ-ض-ب', 'msa': 'غَاضِبٌ', 'lev': 'مَعَصِّب', 'egy': 'مِتْضَايِق', 'trMsa': 'ghāḍibun', 'trLev': 'mʿaṣṣib', 'trEgy': 'mitḍaayiʾ', 'eng': 'angry / annoyed', 'd': 5.0, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c294', 'rank': 294, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ه-د-أ', 'msa': 'هَادِئٌ', 'lev': 'هَادِي', 'egy': 'هَادِي', 'trMsa': 'hādiʾun', 'trLev': 'haadi', 'trEgy': 'haadi', 'eng': 'calm / quiet', 'd': 5.4, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c295', 'rank': 295, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ش-غ-ل', 'msa': 'مَشْغُولٌ', 'lev': 'مَشْغُول', 'egy': 'مَشْغُول', 'trMsa': 'mashghūlun', 'trLev': 'mashghool', 'trEgy': 'mashghool', 'eng': 'busy', 'd': 3.8, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c296', 'rank': 296, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ج-ه-ز', 'msa': 'جَاهِزٌ', 'lev': 'جَاهِز', 'egy': 'جَاهِز', 'trMsa': 'jāhizun', 'trLev': 'jaahiz', 'trEgy': 'gaahiz', 'eng': 'ready', 'd': 4.2, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c297', 'rank': 297, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'أ-ك-د', 'msa': 'مُتَأَكِّدٌ', 'lev': 'مِتْأَكِّد', 'egy': 'مِتْأَكِّد', 'trMsa': 'mutaʾakkidun', 'trLev': 'mitʾakkid', 'trEgy': 'mitʾakkid', 'eng': 'sure / certain', 'd': 4.6, 's': 2.1, 'elapsed': 1.0, 'reps': 1}, {'id': 'c298', 'rank': 298, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ه-م-م', 'msa': 'مُهِمٌّ', 'lev': 'مُهِمّ', 'egy': 'مُهِمّ', 'trMsa': 'muhimmun', 'trLev': 'muhimm', 'trEgy': 'muhimm', 'eng': 'important', 'd': 5.0, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c299', 'rank': 299, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ض-ر-ر', 'msa': 'ضَرُورِيٌّ', 'lev': 'ضَرُورِي', 'egy': 'ضَرُورِي', 'trMsa': 'ḍarūriyyun', 'trLev': 'ḍaroori', 'trEgy': 'ḍaroori', 'eng': 'necessary / urgent', 'd': 5.4, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c300', 'rank': 300, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ص-ح-ح', 'msa': 'صَحِيحٌ', 'lev': 'صَحّ / مَزْبُوط', 'egy': 'صَحّ / مَظْبُوط', 'trMsa': 'ṣaḥīḥun', 'trLev': 'ṣaḥḥ / mazbooṭ', 'trEgy': 'ṣaḥḥ / maẓbooṭ', 'eng': 'correct / right', 'd': 3.8, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c301', 'rank': 301, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'خ-ط-أ', 'msa': 'خَطَأٌ', 'lev': 'غَلَط', 'egy': 'غَلَط', 'trMsa': 'khaṭaʾun', 'trLev': 'ghalaṭ', 'trEgy': 'ghalaṭ', 'eng': 'wrong / mistake', 'd': 4.2, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c302', 'rank': 302, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ف-ك-ر', 'msa': 'أَعْتَقِدُ', 'lev': 'بَفَكِّر / بَظُنّ', 'egy': 'أَفْتِكِر', 'trMsa': 'aʿtaqidu', 'trLev': 'bafakkir / baẓunn', 'trEgy': 'aftikir', 'eng': 'I think / believe', 'd': 4.6, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c303', 'rank': 303, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ت-م-ن-ي', 'msa': 'أَتَمَنَّى', 'lev': 'بِتْمَنَّى', 'egy': 'بَتْمَنَّى', 'trMsa': 'atamannā', 'trLev': 'bitmanna', 'trEgy': 'batmanna', 'eng': 'I hope / wish', 'd': 5.0, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c304', 'rank': 304, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ح-و-ج', 'msa': 'أَحْتَاجُ', 'lev': 'بَحْتَاج / لَازِمْنِي', 'egy': 'مِحْتَاج', 'trMsa': 'aḥtāju', 'trLev': 'baḥtaaj / laazimni', 'trEgy': 'miḥtaag', 'eng': 'I need', 'd': 5.4, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c305', 'rank': 305, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ف-ت-ح', 'msa': 'فَتَحَ', 'lev': 'فَتَح', 'egy': 'فَتَح', 'trMsa': 'fataḥa', 'trLev': 'fataḥ', 'trEgy': 'fataḥ', 'eng': 'to open', 'd': 3.8, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c306', 'rank': 306, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'غ-ل-ق', 'msa': 'أَغْلَقَ', 'lev': 'سَكَّر', 'egy': 'قَفَل', 'trMsa': 'aghlaqa', 'trLev': 'sakkar', 'trEgy': 'afal', 'eng': 'to close / shut', 'd': 4.2, 's': 2.0, 'elapsed': 1.0, 'reps': 1}, {'id': 'c307', 'rank': 307, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ب-د-أ', 'msa': 'بَدَأَ', 'lev': 'بَلَّش', 'egy': 'بَدَأ', 'trMsa': 'badaʾa', 'trLev': 'ballash', 'trEgy': 'badaʾ', 'eng': 'to start / begin', 'd': 4.6, 's': 1.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c308', 'rank': 308, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'خ-ل-ص', 'msa': 'أَنْهَى', 'lev': 'خَلَّص', 'egy': 'خَلَّص', 'trMsa': 'anhā', 'trLev': 'khallaṣ', 'trEgy': 'khallaṣ', 'eng': 'to finish', 'd': 5.0, 's': 1.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c309', 'rank': 309, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ن-ظ-ر', 'msa': 'اِنْتَظَرَ', 'lev': 'نَطَر / اِسْتَنَّى', 'egy': 'اِسْتَنَّى', 'trMsa': 'intaẓara', 'trLev': 'naṭar / istanna', 'trEgy': 'istanna', 'eng': 'to wait', 'd': 5.4, 's': 1.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c310', 'rank': 310, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'س-ع-د', 'msa': 'سَاعَدَ', 'lev': 'سَاعَد', 'egy': 'سَاعِد', 'trMsa': 'sāʿada', 'trLev': 'saaʿad', 'trEgy': 'saaʿid', 'eng': 'to help', 'd': 3.8, 's': 1.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c311', 'rank': 311, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'س-أ-ل', 'msa': 'سَأَلَ', 'lev': 'سَأَل', 'egy': 'سَأَل', 'trMsa': 'saʾala', 'trLev': 'saʾal', 'trEgy': 'saʾal', 'eng': 'to ask', 'd': 4.2, 's': 1.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c312', 'rank': 312, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ج-و-ب', 'msa': 'أَجَابَ', 'lev': 'جَاوَب / رَدّ', 'egy': 'جَاوِب / رَدّ', 'trMsa': 'ajāba', 'trLev': 'jaawab / radd', 'trEgy': 'gaawib / radd', 'eng': 'to answer / reply', 'd': 4.6, 's': 1.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c313', 'rank': 313, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ن-س-ي', 'msa': 'نَسِيَ', 'lev': 'نِسِي', 'egy': 'نِسِي', 'trMsa': 'nasiya', 'trLev': 'nisi', 'trEgy': 'nisi', 'eng': 'to forget', 'd': 5.0, 's': 1.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c314', 'rank': 314, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ذ-ك-ر', 'msa': 'تَذَكَّرَ', 'lev': 'تْذَكَّر', 'egy': 'اِفْتَكَر', 'trMsa': 'tadhakkara', 'trLev': 'tzhakkar', 'trEgy': 'iftakar', 'eng': 'to remember', 'd': 5.4, 's': 1.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c315', 'rank': 315, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ح-و-ل', 'msa': 'حَاوَلَ', 'lev': 'حَاوَل', 'egy': 'حَاوِل', 'trMsa': 'ḥāwala', 'trLev': 'ḥaawal', 'trEgy': 'ḥaawil', 'eng': 'to try', 'd': 3.8, 's': 1.9, 'elapsed': 1.0, 'reps': 1}, {'id': 'c316', 'rank': 316, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'و-ج-د', 'msa': 'وَجَدَ', 'lev': 'لَقَى', 'egy': 'لَقَى', 'trMsa': 'wajada', 'trLev': 'laqa', 'trEgy': 'laʾa', 'eng': 'to find', 'd': 4.2, 's': 1.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c317', 'rank': 317, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'د-ف-ع', 'msa': 'دَفَعَ', 'lev': 'دَفَع', 'egy': 'دَفَع', 'trMsa': 'dafaʿa', 'trLev': 'dafaʿ', 'trEgy': 'dafaʿ', 'eng': 'to pay', 'd': 4.6, 's': 1.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c318', 'rank': 318, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ر-س-ل', 'msa': 'أَرْسَلَ', 'lev': 'بَعَت', 'egy': 'بَعَت', 'trMsa': 'arsala', 'trLev': 'baʿat', 'trEgy': 'baʿat', 'eng': 'to send', 'd': 5.0, 's': 1.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c319', 'rank': 319, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'و-ص-ل', 'msa': 'وَصَلَ', 'lev': 'وِصِل', 'egy': 'وِصِل', 'trMsa': 'waṣala', 'trLev': 'wiṣil', 'trEgy': 'wiṣil', 'eng': 'to arrive', 'd': 5.4, 's': 1.8, 'elapsed': 1.0, 'reps': 1}, {'id': 'c320', 'rank': 320, 'category': 'Feelings & Daily Verbs', 'icon': '💡', 'root': 'ر-ج-ع', 'msa': 'رَجَعَ', 'lev': 'رِجِع', 'egy': 'رِجِع', 'trMsa': 'rajaʿa', 'trLev': 'rijiʿ', 'trEgy': 'rigiʿ', 'eng': 'to return / come back', 'd': 3.8, 's': 1.8, 'elapsed': 1.0, 'reps': 1}]
        root_map = {
            "ك-ت-ب": ("ktb", "fa3ala"),
            "د-ر-س": ("drs", "fa3ala"),
            "ع-ل-م": ("alm", "fa3il"),
            "ع-م-ل": ("aml", "fa3ala"),
            "ش-ر-ب": ("shrb", "fa3ala"),
            "ف-ه-م": ("fhm", "fa3ala"),
            "س-ف-ر": ("sfr", "fa3ala"),
            "ق-ر-أ": ("qra", "fa3ala"),
            "ح-س-ب": ("hsb", "fa3ala"),
            "ص-ن-ع": ("sn3", "fa3ala"),
        }

        cur.execute(
            """
            INSERT OR REPLACE INTO learner_profiles
            (user_id, display_name, preferred_dialect, tashkeel_mode, show_translit, current_cefr_stage, total_xp)
            VALUES ('usr_stefan', 'Stefan', 'levantine', 'full', 1, 1, 120)
            """
        )

        for item in core_320_words:
            idx = item["rank"]
            lemma_id = f"lem_{idx:03d}"
            r_id, p_id = root_map.get(item["root"], (None, None))
            # Map classic demo IDs for the first K-T-B / Sh-R-B items so existing tests work seamlessly
            if item["trMsa"] == "kataba":
                lemma_id, r_id, p_id = "lem_kataba", "ktb", "fa3ala"
            elif item["trMsa"] == "maktabun":
                lemma_id, r_id, p_id = "lem_maktab", "ktb", "maf3al"
            elif item["trMsa"] == "maktabatun":
                lemma_id, r_id, p_id = "lem_maktaba", "ktb", "maf3al"
            elif item["trMsa"] == "shariba":
                lemma_id, r_id, p_id = "lem_shariba", "shrb", "fa3ala"

            full_v, scaf_50, unvoc = HabibiVowelScaffolder.build_three_tiers(item["msa"])
            stage = 1 if idx <= 70 else (2 if idx <= 140 else 3)
            cur.execute(
                """
                INSERT OR REPLACE INTO lexical_entries
                (lemma_id, root_id, pattern_id, part_of_speech,
                 msa_full_tashkeel, msa_scaffold_50, msa_unvocalized,
                 msa_translit, english_gloss, gender, broken_plural_full,
                 msa_freq_rank, mix_freq_rank, cefr_stage, audio_uri_msa)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lemma_id, r_id, p_id, item["category"],
                    full_v, scaf_50, unvoc,
                    item["trMsa"], item["eng"], None, None,
                    idx, idx, stage, f"common_voice_ar/{lemma_id}.mp3",
                ),
            )

            # Insert Levantine & Egyptian variants into dialect_variants
            cur.execute(
                """
                INSERT OR REPLACE INTO dialect_variants
                (variant_id, lemma_id, dialect_code, surface_vocalized,
                 surface_unvocalized, phonetic_translit, pronunciation_note, da_freq_rank)
                VALUES (?, ?, 'levantine', ?, ?, ?, 'Shami spoken register', ?)
                """,
                (f"var_{lemma_id}_lev", lemma_id, item["lev"], HabibiVowelScaffolder.strip_all_tashkeel(item["lev"]), item["trLev"], idx),
            )
            cur.execute(
                """
                INSERT OR REPLACE INTO dialect_variants
                (variant_id, lemma_id, dialect_code, surface_vocalized,
                 surface_unvocalized, phonetic_translit, pronunciation_note, da_freq_rank)
                VALUES (?, ?, 'egyptian', ?, ?, ?, 'Masri spoken register', ?)
                """,
                (f"var_{lemma_id}_egy", lemma_id, item["egy"], HabibiVowelScaffolder.strip_all_tashkeel(item["egy"]), item["trEgy"], idx),
            )

            # Insert corresponding FSRS card
            card_id = f"card_{lemma_id.replace('lem_', '')}"
            cur.execute(
                """
                INSERT OR REPLACE INTO fsrs_cards
                (card_id, user_id, target_type, target_ref_id, card_modality,
                 state_code, difficulty, stability, retrievability, elapsed_days, scheduled_days, reps_count, lapses_count)
                VALUES (?, 'usr_stefan', 'lemma', ?, 'visual_to_ar', 0, ?, ?, 1.0, 0.0, 0.0, 0, 0)
                """,
                (card_id, lemma_id, item["d"], 0.0),
            )

        # Also ensure lem_katib is present for the K-T-B active participle test
        full_k, scaf_k, unvoc_k = HabibiVowelScaffolder.build_three_tiers("كَاتِبٌ")
        cur.execute(
            """
            INSERT OR REPLACE INTO lexical_entries
            (lemma_id, root_id, pattern_id, part_of_speech,
             msa_full_tashkeel, msa_scaffold_50, msa_unvocalized,
             msa_translit, english_gloss, gender, broken_plural_full,
             msa_freq_rank, mix_freq_rank, cefr_stage, audio_uri_msa)
            VALUES ('lem_katib', 'ktb', 'fa3il', 'People & Family', ?, ?, ?, 'kātib', 'writer / author', 'm', 'كُتَّابٌ', 321, 321, 1, 'common_voice_ar/lem_katib.mp3')
            """,
            (full_k, scaf_k, unvoc_k),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO fsrs_cards
            (card_id, user_id, target_type, target_ref_id, card_modality,
             state_code, difficulty, stability, retrievability, elapsed_days, scheduled_days, reps_count, lapses_count)
            VALUES ('card_katib', 'usr_stefan', 'lemma', 'lem_katib', 'visual_to_ar', 0, 4.5, 0.0, 1.0, 0.0, 0.0, 0, 0)
            """
        )

        # Seed Original Habibi Bilingual Context Sentence
        msa_sent = "هٰذَا الكَاتِبُ يَكْتُبُ كِتَابًا جَدِيدًا فِي المَكْتَبَةِ."
        s_full, s_scaf, s_unvoc = HabibiVowelScaffolder.build_three_tiers(msa_sent)
        cur.execute(
            """
            INSERT OR REPLACE INTO bilingual_sentences
            (sentence_id, primary_lemma_id, source_corpus,
             msa_vocalized, msa_scaffold_50, msa_unvocalized,
             levantine_vocalized, egyptian_vocalized,
             translit_msa, english_translation, cloze_target_word, cefr_stage)
            VALUES ('sent_ktb_01', 'lem_katib', 'habibi_original', ?, ?, ?,
                    'هَادَا الكَاتِب عَم يِكْتُب كْتَاب جْدِيد بِالمَكْتَبِة.',
                    'الكَاتِب دَه بِيِكْتِب كِتَاب جِدِيد فِي المَكْتَبَة.',
                    'hādhā al-kātibu yaktubu kitāban jadīdan fī al-maktabati.',
                    'This writer is writing a new book in the library.', 'الكَاتِبُ', 1)
            """,
            (s_full, s_scaf, s_unvoc),
        )

        self.conn.commit()

    def record_fsrs_review(
        self,
        card_id: str,
        rating: int,
        elapsed_days: float,
        latency_ms: int = 1450,
    ) -> FSRSCardState:
        """
        Loads a card from SQLite, checks if sibling words in the same trilateral
        root are already mastered (Root Synergy Bonus), computes the new FSRS DSR
        state, updates `fsrs_cards`, and appends an audit row to `fsrs_review_logs`.
        """
        cur = self.conn.cursor()
        row = cur.execute("SELECT * FROM fsrs_cards WHERE card_id = ?", (card_id,)).fetchone()
        if not row:
            raise KeyError(f"Card {card_id} not found")

        # Check if the learner already has stability >= 3.0d on another word sharing the same root
        root_synergy = False
        if row["target_type"] == "lemma":
            sib = cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM lexical_entries le1
                JOIN lexical_entries le2 ON le1.root_id = le2.root_id AND le1.lemma_id != le2.lemma_id
                JOIN fsrs_cards fc ON fc.target_ref_id = le2.lemma_id AND fc.user_id = ?
                WHERE le1.lemma_id = ? AND fc.stability >= 3.0
                """,
                (row["user_id"], row["target_ref_id"]),
            ).fetchone()
            root_synergy = bool(sib and sib["cnt"] > 0)

        current_state = FSRSCardState(
            card_id=row["card_id"],
            state_code=row["state_code"],
            difficulty=row["difficulty"],
            stability=row["stability"],
            retrievability=row["retrievability"],
            elapsed_days=row["elapsed_days"],
            scheduled_days=row["scheduled_days"],
            reps_count=row["reps_count"],
            lapses_count=row["lapses_count"],
        )

        updated = FSRSScheduler.review_card(
            card=current_state,
            rating=rating,
            elapsed_days=elapsed_days,
            root_family_known=root_synergy,
        )

        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        due_iso = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=updated.scheduled_days)).isoformat()

        cur.execute(
            """
            UPDATE fsrs_cards
            SET state_code = ?, difficulty = ?, stability = ?, retrievability = ?,
                elapsed_days = ?, scheduled_days = ?, reps_count = ?, lapses_count = ?,
                last_review_ts = ?, due_ts = ?
            WHERE card_id = ?
            """,
            (
                updated.state_code,
                updated.difficulty,
                updated.stability,
                updated.retrievability,
                updated.elapsed_days,
                updated.scheduled_days,
                updated.reps_count,
                updated.lapses_count,
                now_iso,
                due_iso,
                card_id,
            ),
        )

        cur.execute(
            """
            INSERT INTO fsrs_review_logs
            (card_id, user_id, rating, state_before, difficulty_before, stability_before,
             difficulty_after, stability_after, retrievability_at, scheduled_days,
             response_latency_ms, dialect_used, tashkeel_mode_used, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'levantine', 'full', ?)
            """,
            (
                card_id,
                row["user_id"],
                rating,
                current_state.state_code,
                current_state.difficulty,
                current_state.stability,
                updated.difficulty,
                updated.stability,
                updated.retrievability,
                updated.scheduled_days,
                latency_ms,
                now_iso,
            ),
        )
        self.conn.commit()
        return updated


# =============================================================================
# 4. SELF-TEST & VERIFICATION RUNNER
# =============================================================================
def run_verification() -> None:
    base_dir = pathlib.Path(__file__).resolve().parent
    schema_path = base_dir / "habibi_schema.sql"
    db_file = pathlib.Path("/tmp/miftah_arabic_verify.sqlite3")
    if db_file.exists():
        db_file.unlink()

    pipeline = ArabicCurriculumPipeline(str(db_file), str(schema_path))
    pipeline.initialize_schema()
    pipeline.seed_reference_curriculum()

    # Simulate multi-cycle FSRS reviews across the K-T-B root family to trigger
    # Root Synergy and automatic progressive vowel-scaffolding progression
    review_plan = [
        ("card_kataba", 3, 0.0),   # First review (Good) -> S = 3.1d
        ("card_kataba", 3, 3.0),   # Second review 3 days later (Good) -> S jumps
        ("card_katib", 3, 0.0),    # Sibling root K-T-B gets +15% Root Synergy bonus!
        ("card_katib", 4, 3.5),    # Easy rating -> S > 14d
        ("card_maktab", 3, 0.0),   # Sibling root K-T-B
        ("card_maktaba", 3, 0.0),  # Sibling root K-T-B
        ("card_shariba", 2, 0.0),  # Hard rating on drinking
    ]
    for cid, grade, elapsed in review_plan:
        pipeline.record_fsrs_review(cid, rating=grade, elapsed_days=elapsed)

    cur = pipeline.conn.cursor()
    letter_cnt = cur.execute("SELECT COUNT(*) FROM arabic_letters").fetchone()[0]
    sun_cnt = cur.execute("SELECT COUNT(*) FROM arabic_letters WHERE is_sun_letter = 1").fetchone()[0]
    moon_cnt = cur.execute("SELECT COUNT(*) FROM arabic_letters WHERE is_sun_letter = 0").fetchone()[0]
    lemma_cnt = cur.execute("SELECT COUNT(*) FROM lexical_entries").fetchone()[0]
    var_cnt = cur.execute("SELECT COUNT(*) FROM dialect_variants").fetchone()[0]
    print(f"=== 0. HABIBI SQLITE DATABASE INVENTORY ===")
    print(f"  Arabic Letters : {letter_cnt} (Sun: {sun_cnt}, Moon: {moon_cnt})")
    print(f"  Lexical Entries: {lemma_cnt} (320 Core Habibi + Root Family Exemplars)")
    print(f"  Dialect Rows   : {var_cnt} (Levantine + Egyptian)")
    print("=== 1. HABIBI 3-TIER VOWEL SCAFFOLDING CHECK ===")
    for row in cur.execute("SELECT msa_vocalized AS msa_full_tashkeel, msa_scaffold_50, msa_unvocalized FROM bilingual_sentences"):
        print(f"  Full Tashkeel : {row['msa_full_tashkeel']}")
        print(f"  50% Scaffold  : {row['msa_scaffold_50']}")
        print(f"  Unvocalized   : {row['msa_unvocalized']}")

    print("\n=== 2. FSRS CARD STATES AFTER ROOT-SYNERGY REVIEWS ===")
    for row in cur.execute(
        """
        SELECT fc.card_id, le.msa_full_tashkeel, le.english_gloss,
               fc.difficulty, fc.stability, fc.scheduled_days, fc.reps_count
        FROM fsrs_cards fc
        JOIN lexical_entries le ON le.lemma_id = fc.target_ref_id
        ORDER BY fc.stability DESC LIMIT 8
        """
    ):
        print(
            f"  {row['card_id']:12s} | {row['msa_full_tashkeel']:8s} ({row['english_gloss']:19s}) "
            f"| D={row['difficulty']:.2f} | S={row['stability']:5.2f}d | Next={row['scheduled_days']:5.2f}d | Reps={row['reps_count']}"
        )

    print("\n=== 3. ROOT-FAMILY MASTERY VIEW (v_root_family_mastery) ===")
    for row in cur.execute("SELECT * FROM v_root_family_mastery ORDER BY avg_root_stability_days DESC"):
        print(
            f"  Root {row['radicals_arabic']} ({row['core_concept_en']:20s}) | "
            f"Lemmas={row['total_family_lemmas']} | Avg S={row['avg_root_stability_days']:5.2f}d | "
            f"Auto Vowel Mode -> {row['recommended_tashkeel_mode']}"
        )


if __name__ == "__main__":
    run_verification()
