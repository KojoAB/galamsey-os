"""Retrain the mine classifier on Ghana labels (spec 5.5).

Every confirmed alert becomes a 'mine' label and every rejected one a 'not_mine'
label (database function review_alert). Analysts can also draw extra labels in
the dashboard's labelling tool.

    python -m galamsey.ai.retrain --check     # how many labels do we have?
    python -m galamsey.ai.retrain             # fine-tune once ≥ 300 positives exist

Method: fine-tune the Amazon Mining Watch ensemble on Ghana patches with a
geographic hold-out (one region kept out of training) so we can see whether it
generalises. The new model is saved as ghana_finetuned.h5 and picked up
automatically by classifier.py, but ONLY if it beats the original model on the
hold-out — otherwise the old one stays in use.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date

import numpy as np

from .. import db
from . import classifier as clf

log = logging.getLogger("retrain")
MIN_POSITIVES = 300


def label_counts(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("select label, count(*) from labels group by label")
        return dict(cur.fetchall())


def load_labels(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""select l.id, l.label, st_x(st_pointonsurface(l.geom)), st_y(st_pointonsurface(l.geom)),
                              coalesce(l.image_date, current_date), coalesce(a.region, 'Unknown')
                       from labels l left join alerts a on a.id = l.alert_id""")
        return [dict(zip(("id", "label", "lon", "lat", "date", "region"), r)) for r in cur.fetchall()]


def split_by_region(rows: list[dict], holdout_region: str):
    train = [r for r in rows if r["region"] != holdout_region]
    test = [r for r in rows if r["region"] == holdout_region]
    return train, test


def f05(y_true: np.ndarray, prob: np.ndarray, threshold: float) -> float:
    """F0.5 favours precision — false alarms cost analyst time."""
    pred = prob >= threshold
    tp = float(np.sum(pred & (y_true == 1)))
    fp = float(np.sum(pred & (y_true == 0)))
    fn = float(np.sum(~pred & (y_true == 1)))
    if tp == 0:
        return 0.0
    p, r = tp / (tp + fp), tp / (tp + fn)
    return 1.25 * p * r / (0.25 * p + r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--holdout-region", default="Central")
    ap.add_argument("--epochs", type=int, default=15)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    with db.connect() as conn:
        counts = label_counts(conn)
        log.info("Labels: %s", counts)
        if args.check:
            return 0
        if counts.get("mine", 0) < MIN_POSITIVES:
            log.info("Need %d confirmed mines, have %d. Keeping the pre-trained model as a secondary signal.",
                     MIN_POSITIVES, counts.get("mine", 0))
            return 0
        rows = load_labels(conn)

    import tensorflow as tf

    def to_xy(rs):
        x = np.stack([clf.fetch_patch(r["lon"], r["lat"], r["date"]) for r in rs])
        y = np.array([1 if r["label"] == "mine" else 0 for r in rs])
        return clf.normalise(x), y

    train, test = split_by_region(rows, args.holdout_region)
    xtr, ytr = to_xy(train)
    xte, yte = to_xy(test)
    # Flips and rotations: a mine looks like a mine from any angle.
    xtr = np.concatenate([xtr, np.flip(xtr, 1), np.flip(xtr, 2), np.rot90(xtr, 1, (1, 2))])
    ytr = np.concatenate([ytr] * 4)

    base = clf.load_model()
    before = f05(yte, base.predict(xte, verbose=0).reshape(-1), clf.THRESHOLD)

    model = tf.keras.models.clone_model(base)
    model.set_weights(base.get_weights())
    model.compile(optimizer=tf.keras.optimizers.Adam(1e-4), loss="binary_crossentropy", metrics=["AUC"])
    pos = ytr.mean()
    model.fit(xtr, ytr, epochs=args.epochs, batch_size=64, validation_data=(xte, yte),
              class_weight={0: 0.5 / (1 - pos), 1: 0.5 / pos}, verbose=2)
    after = f05(yte, model.predict(xte, verbose=0).reshape(-1), clf.THRESHOLD)

    report = {"date": date.today().isoformat(), "train": len(train), "holdout": len(test),
              "holdout_region": args.holdout_region, "f05_before": round(before, 3), "f05_after": round(after, 3)}
    log.info("Result: %s", json.dumps(report))
    if after > before:
        model.save(clf.CACHE / "ghana_finetuned.h5")
        log.info("Saved Ghana model — it is now used by the daily run.")
    else:
        log.info("Fine-tuned model did not beat the original on the hold-out; not saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
