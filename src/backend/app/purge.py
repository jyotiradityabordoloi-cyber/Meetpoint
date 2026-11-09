"""Delete groups (and, by cascade, members and recommendations) older than the
retention period. Run nightly: `python -m app.purge`."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from .config import get_settings
from .db import init_db, session_factory
from .models import Group, Member, Recommendation

log = logging.getLogger("meetpoint.purge")


def purge_old_groups(retention_days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    with session_factory()() as db:
        old_ids = [gid for (gid,) in db.query(Group.id).filter(Group.created_at < cutoff).all()]
        if not old_ids:
            return 0
        # Explicit child deletes so this works even where FK cascades are off.
        db.execute(delete(Recommendation).where(Recommendation.group_id.in_(old_ids)))
        db.execute(delete(Member).where(Member.group_id.in_(old_ids)))
        db.execute(delete(Group).where(Group.id.in_(old_ids)))
        db.commit()
        return len(old_ids)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    s = get_settings()
    init_db(s.database_url)
    n = purge_old_groups(s.group_retention_days)
    log.info("purged %d groups older than %d days", n, s.group_retention_days)
