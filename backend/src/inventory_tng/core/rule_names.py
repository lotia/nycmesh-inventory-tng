"""Every rule a change can break, by the name db_errors.attempt() gives it
(RuleName): the fixed list a rule refusal is counted by
(db_errors.rule_refused, AGENTS.md rule 7). A domain function's
(db_errors.RuleBroken), a check constraint's, or "unique" for any unique
index. web/forms.py's RULES puts most into words; a page words the rest
itself. Names only, so any module may import it. `ty check` refuses a
rule raised by a name not here (enforce(), RuleBroken take a RuleName),
and tests/codebase/test_rule_refusals.py one here that nothing raises."""

from typing import Literal

RuleName = Literal[
    "unique",
    # Check constraints a form's own check may let by (web/forms.py's
    # RULES words them).
    "ck_item_minimum_whole",
    "ck_item_package_short_below_under_size",
    # The catalogue's.
    "category_has_items",
    "item_alias_is_name",
    "item_category_current",
    "item_name_is_alias",
    "item_packages_whole",
    "item_unit_checks_waiting",
    "item_unit_converted",
    "item_unit_fixed",
    "item_unit_labelled",
    "item_unit_labelled_since_shown",
    "item_unit_moved_since_shown",
    "item_unit_out_of_range",
    "item_unit_revoked_since_shown",
    "item_unit_unconfirmed",
    "names_read_alike",
    "package_size_fixed",
    "package_size_labelled",
    "package_unit_changed",
    "package_whole_units",
    "reading_rule_reading",
    # Places, volunteers and the site's settings.
    "location_area_parent",
    "location_countable",
    "location_custody_volunteer",
    "location_holds_stock",
    "location_no_cycle",
    "location_parent_current",
    "location_time_zone",
    "site_settings_time_zone",
    "volunteer_merge_target",
    # The ledger's, and its forms' (stock/ledger.py).
    "ledger_transaction_lines",
    "movement_form_change",
    "movement_form_outside",
    "movement_item_once",
    "movement_open_package_closed",
    "movement_open_package_of_item",
    "movement_open_package_once",
    "movement_open_package_place",
    "movement_open_package_unopened",
    "movement_open_package_whole",
    "movement_package_of_item",
    "movement_retired_package_none_left",
    "movement_unit",
    "movement_whole_packages",
    "movement_whole_units",
    "open_package_full",
    "open_package_opened_once",
    "opening_follows_setting",
    "opening_from_sealed",
    "receipt_retired_item",
    "receipt_retired_package",
    # Counts, open packages and their checks.
    "count_sent_otherwise",
    "length_unchanged",
    "open_package_key",
    "open_package_untracked",
    "over_recorded_not_whole",
    "over_recorded_unchanged",
    "shown_otherwise",
    # Any row: changed since its form was shown, or retired, so read
    # only.
    "changed_since_shown",
    "retired_read_only",
]
UNIQUE: RuleName = "unique"
