"""Years of saves in the ledger, sealed and open, for the measurements
(`mise run measure-catalogue`, `mise run measure-labels`)."""

import os

from sqlalchemy import Connection, Engine, text

from inventory_tng.core.db_sessions import make_engine

from databases import OWNER, as_role

# The scale, from the shell: years of saves, and saves a day.
YEARS = int(os.environ.get("MEASURE_YEARS", "5"))
PER_DAY = int(os.environ.get("MEASURE_PER_DAY", "40"))
# Of the open packages made, those still open.
STILL_OPEN = 150

# Saves in blocks of 20, each block of one package at one store and one van,
# by its place in the block: receipts of sealed packages into the store, one
# opened there and cut from four times to installs, two moved sealed to the
# van, and loose stock moved or installed; each but an opening with a loose
# line of another item, as a batch often has two. So sealed stock at the
# stores stays level and the vans fill. Each block's open package is closed
# as the block ends, but the newest STILL_OPEN blocks'.
LEDGER = """
CREATE TEMPORARY TABLE pack AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS n, id, item_id, size
  FROM item_package;
CREATE TEMPORARY TABLE store AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS n, id FROM location
  WHERE kind IN ('store', 'area');
CREATE TEMPORARY TABLE van AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS n, id FROM location
  WHERE kind = 'vehicle';
CREATE TEMPORARY TABLE helper AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS n, id FROM volunteer;
CREATE TEMPORARY TABLE loose AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS n, id FROM item;
CREATE TEMPORARY TABLE plan AS
  SELECT g, g / 20 AS block,
    CASE
      WHEN g % 20 IN (0, 1, 3) THEN 'receipt'
      WHEN g % 20 = 2 THEN 'open'
      WHEN g % 20 IN (4, 5) THEN 'move'
      WHEN g % 20 BETWEEN 7 AND 10 THEN 'cut'
      WHEN g % 20 IN (6, 11, 12, 13) THEN 'loose'
      ELSE 'install' END AS what,
    now() - make_interval(secs => (:saves - g) * 86400.0 / :per_day) AS at
  FROM generate_series(0, :saves - 1) AS g;
CREATE TEMPORARY TABLE block AS
  SELECT b AS block, p.id AS package_id, p.item_id, p.size,
    s.id AS store, v.id AS van, h.id AS volunteer, l.id AS loose_item
  FROM generate_series(0, (:saves - 1) / 20) AS b
  JOIN pack p ON p.n = b % (SELECT count(*) FROM pack)
  JOIN store s ON s.n = b % (SELECT count(*) FROM store)
  JOIN van v ON v.n = b % (SELECT count(*) FROM van)
  JOIN helper h ON h.n = b % (SELECT count(*) FROM helper)
  JOIN loose l ON l.n = (b * 7 + 3) % (SELECT count(*) FROM loose);
INSERT INTO open_package (key, opened_at, item_id, package_id, location_id,
  closed_at, closed_by_admin_id)
  SELECT gen_random_uuid(), plan.at, item_id, package_id, store,
    CASE WHEN block < (:saves - 1) / 20 - :still_open
      THEN plan.at + make_interval(secs => 18 * 86400.0 / :per_day) END,
    CASE WHEN block < (:saves - 1) / 20 - :still_open
      THEN (SELECT min(id) FROM admin) END
  FROM plan JOIN block USING (block) WHERE what = 'open' ORDER BY g;
-- One a block, made in order.
CREATE TEMPORARY TABLE opened AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS block, id AS open_id
  FROM open_package;
INSERT INTO ledger_transaction (created_at, idempotency_key, kind,
  from_location_id, to_location_id, volunteer_id, time_zone)
  SELECT at, gen_random_uuid(),
    CASE what WHEN 'cut' THEN 'install' WHEN 'loose' THEN 'move' ELSE what END,
    CASE what WHEN 'receipt' THEN NULL WHEN 'loose' THEN van
      WHEN 'install' THEN van ELSE store END,
    CASE what WHEN 'move' THEN van WHEN 'cut' THEN NULL
      WHEN 'install' THEN NULL ELSE store END,
    volunteer, 'America/New_York'
  FROM plan JOIN block USING (block) ORDER BY g;
CREATE TEMPORARY TABLE tx AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS g, id FROM ledger_transaction;
INSERT INTO movement (transaction_id, item_id, quantity, from_package_id,
  from_open_package_id, to_package_id, to_open_package_id)
  SELECT tx.id, item_id,
    CASE WHEN what IN ('receipt', 'move', 'open') THEN size
      WHEN what = 'cut' THEN 12 ELSE 3 END,
    CASE WHEN what IN ('move', 'open') THEN package_id END,
    CASE WHEN what = 'cut' THEN open_id END,
    CASE WHEN what IN ('receipt', 'move') THEN package_id END,
    CASE WHEN what = 'open' THEN open_id END
  FROM plan JOIN block USING (block) JOIN tx USING (g)
    LEFT JOIN opened USING (block)
  WHERE what NOT IN ('loose', 'install')
  UNION ALL
  SELECT tx.id, loose_item, 2, NULL, NULL, NULL, NULL
  FROM plan JOIN block USING (block) JOIN tx USING (g)
  WHERE what <> 'open';
"""
# Retired, with stock left: what volunteers may still take, which the
# catalogue and the labels list read from the ledger too.
RETIRE = """
UPDATE item SET retired_at = now() WHERE id IN (
  SELECT item_id FROM item_package ORDER BY id LIMIT 10);
UPDATE item_package SET retired_at = now() WHERE id IN (
  SELECT id FROM item_package ORDER BY id DESC LIMIT 10)
"""


def add_years_of_saves(engine: Engine, database: str) -> None:
    """Saves of the items, packages, places and volunteers already in
    `database`, which has an administrator to close open packages; some
    items and packages retired with stock left; then ANALYZE."""
    with engine.begin() as connection:
        _run(connection, LEDGER)
    # As the owner, who may retire rows outside the app and ANALYZE.
    owner = make_engine(as_role(database, OWNER))
    with owner.begin() as connection:
        _run(connection, RETIRE)
        connection.execute(text("ANALYZE"))
    owner.dispose()


def _run(connection: Connection, statements: str) -> None:
    saves = YEARS * 365 * PER_DAY
    for statement in statements.split(";"):
        if statement.strip():
            connection.execute(
                text(statement),
                {"saves": saves, "per_day": PER_DAY, "still_open": STILL_OPEN},
            )
